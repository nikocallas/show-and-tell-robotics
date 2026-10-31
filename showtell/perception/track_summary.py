"""SAM tracks -> what moved, where and in what order (CLAUDE.md 5.5).

`discover` asks Wonder which kinds of objects are on the table (4 keyframes). `build_track_summary`
tracks every kind plus the bowls and the hand with SAM 3.1 and calls `summarize`, a pure function
that turns box tracks into a TrackSummary: start and end centroid per object, whether and when the
hand moved it, and the bowl it ended in (bowls ordered left to right = left_bin, middle_bin,
right_bin). `scene_objects_from_discovery` maps the discovered kinds to simulator object ids for
jobs without a fixed task.

SAM concepts are color + shape phrases ("red block"): size words are dropped from colored phrases
because size is relative, and two sizes of one kind are told apart by median box area instead.
"""

from __future__ import annotations

import itertools
import json
import logging
import math
import statistics
from collections.abc import Callable, Hashable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from showtell.agent.prompts import DISCOVER_INSTRUCTIONS
from showtell.agent.schemas import (
    VOCABULARY,
    DiscoveredObject,
    Discovery,
    ObjectAttrs,
    TrackedObject,
    TrackSummary,
)
from showtell.meta import cache, sam, wonder
from showtell.meta.sam import FrameBox, Tracks
from showtell.perception import video_prep
from showtell.sim.scene import BIN_NAMES, parse_object_id

log = logging.getLogger(__name__)

HAND_CONCEPT = "hand"
DEFAULT_CONTAINER = "bowl"
N_KEYFRAMES = 8  # written per video; the spec call sees all of them
N_DISCOVER_KEYFRAMES = 4
MIN_TRACK_FRAMES = 3  # shorter tracks are noise
MOVE_FRAC = 0.05  # "moved" = displaced more than this fraction of the frame width
BIN_BOX_GROW = 0.10  # a bowl's median box grows 10% (about its center) for containment tests
SIZE_RATIO = 1.4  # median box-area gap that separates "large" from "small"
SAME_BOWL_IOU = 0.5  # two bowl tracks overlapping this much are one bowl tracked twice
BOWL_IOU = 0.5  # a static object track overlapping a bowl track this much is that bowl
OVERSIZE = 4.0  # a surplus static track this many times the median object box is background
CUBE_COLORS = ("red", "blue", "green")  # the only cube colors in the simulator
GLASS_ID = "clear_glass_large"
CYLINDER_ID = "yellow_cylinder_large"
FALLBACK_OBJECT = "red_cube_large"

Box = tuple[float, float, float, float]  # x1, y1, x2, y2 in pixels
Point = tuple[float, float]

_SIZE_WORDS = {
    "large": "large",
    "big": "large",
    "bigger": "large",
    "larger": "large",
    "small": "small",
    "smaller": "small",
    "little": "small",
    "tiny": "small",
    "mini": "small",
}
_COLOR_WORDS = {
    "red": "red",
    "crimson": "red",
    "scarlet": "red",
    "maroon": "red",
    "orange": "orange",
    "amber": "orange",
    "pink": "pink",
    "magenta": "pink",
    "purple": "purple",
    "violet": "purple",
    "indigo": "purple",
    "lilac": "purple",
    "blue": "blue",
    "navy": "blue",
    "cyan": "cyan",
    "turquoise": "cyan",
    "teal": "cyan",
    "green": "green",
    "lime": "green",
    "olive": "green",
    "mint": "green",
    "yellow": "yellow",
    "gold": "yellow",
    "golden": "yellow",
    "brown": "brown",
    "beige": "brown",
    "tan": "brown",
    "black": "black",
    "white": "white",
    "gray": "gray",
    "grey": "gray",
    "silver": "gray",
    "clear": "clear",
    "transparent": "clear",
}
# Simulator cube colors nearest to an off-palette color, best first (default: red, blue, green).
_CUBE_COLOR_PREFS = {
    "orange": ("red", "green", "blue"),
    "pink": ("red", "blue", "green"),
    "brown": ("red", "green", "blue"),
    "yellow": ("red", "green", "blue"),
    "purple": ("blue", "red", "green"),
    "cyan": ("blue", "green", "red"),
}
_GLASS_WORDS = frozenset({"glass", "tumbler", "wineglass", "goblet"})
_CYLINDER_WORDS = frozenset(
    {"cylinder", "marker", "cap", "stick", "gluestick", "can", "tube", "pen", "battery", "roll"}
)
_CUBE_WORDS = frozenset({"block", "cube", "box", "brick", "cup", "mug", "lego", "die", "dice"})


# ---------------------------------------------------------------- text helpers
def _clean(text: str) -> str:
    return " ".join(str(text).split())


def _norm(text: str) -> str:
    return _clean(text).lower()


def _words(text: str) -> list[str]:
    return [w for w in (t.strip(".,;:!?()[]\"'").lower() for t in str(text).split()) if w]


def _sub(prefix: str, stage: str) -> str:
    """Usage tag for one stage under a job prefix ("job1:" or "job1" -> "job1:discover")."""
    if not prefix:
        return stage
    return f"{prefix}{stage}" if prefix[-1] in ":./" else f"{prefix}:{stage}"


def sam_phrase(concept: str) -> str:
    """The SAM concept for a discovered phrase: size words dropped when a color word remains."""
    words = _clean(concept).lower().split()
    if not any(w.strip(".,;:!?()[]\"'") in _COLOR_WORDS for w in words):
        return " ".join(words)
    kept = [w for w in words if w.strip(".,;:!?()[]\"'") not in _SIZE_WORDS]
    return " ".join(kept or words)


# ---------------------------------------------------------------- discovery
def clean_discovery(discovery: Discovery) -> Discovery:
    """Tidy Wonder's object list: SAM-friendly lowercase phrases, no bowls or hands, counts >= 1,
    exact duplicates merged. Entries that share a phrase but not attributes stay separate (two
    sizes of one kind); `summarize` then sizes their tracks by box area."""
    notes = [discovery.notes.strip()] if discovery.notes.strip() else []
    container = _norm(discovery.container_concept) or DEFAULT_CONTAINER
    head = container.split()[-1]
    skip_heads = {head, f"{head}s", "hand", "hands"}
    out: list[DiscoveredObject] = []
    for obj in discovery.objects:
        phrase = _norm(obj.concept)
        if not phrase or phrase.split()[-1] in skip_heads:
            notes.append(f"dropped object entry {obj.concept!r} (a container, a hand or empty)")
            continue
        concept = sam_phrase(phrase)
        if concept != phrase:
            notes.append(f"SAM concept {phrase!r} -> {concept!r} (SAM gets color and shape only)")
        count = max(1, obj.count)
        twin = next((o for o in out if o.concept == concept and o.attrs == obj.attrs), None)
        if twin is not None:
            twin.count += count
            notes.append(f"merged a repeated {concept!r} entry")
            continue
        out.append(DiscoveredObject(concept=concept, attrs=obj.attrs.model_copy(), count=count))
    return Discovery(objects=out, container_concept=container, notes="; ".join(notes))


def discover(keyframes: Sequence[Path], *, tag: str = "") -> Discovery:
    """Ask Wonder (effort medium) what kinds of objects are on the table, from 4 evenly spaced
    keyframes; the reply is tidied by `clean_discovery`. `tag` is the usage-log tag as is."""
    if not keyframes:
        raise ValueError("discover needs at least one keyframe")
    idx = video_prep.keyframe_indices(len(keyframes), N_DISCOVER_KEYFRAMES)
    picks = [Path(keyframes[i]) for i in idx]
    vocabulary = {k: v for k, v in VOCABULARY.items() if k != "bins"}
    instructions = DISCOVER_INSTRUCTIONS.format(
        vocabulary=json.dumps(vocabulary), schema=json.dumps(Discovery.model_json_schema())
    )
    content = [
        wonder.text_part(f"{len(picks)} keyframes from the demonstration video, in time order."),
        *(wonder.image_part(p) for p in picks),
    ]
    raw = wonder.call_json(Discovery, instructions, content, effort="medium", tag=tag or "discover")
    return clean_discovery(raw)


# ---------------------------------------------------------------- tracks -> summary
@dataclass
class _Track:
    concept: str
    oid: int
    boxes: list[FrameBox]  # sorted by frame, one per frame

    @property
    def track_id(self) -> str:
        return f"{self.concept}#{self.oid}"

    def median_box(self) -> Box:
        x1, y1, x2, y2 = (
            float(statistics.median(getattr(b, k) for b in self.boxes))
            for k in ("x1", "y1", "x2", "y2")
        )
        return (x1, y1, x2, y2)


@dataclass
class _Obj:
    track: _Track
    attrs: ObjectAttrs
    area: float  # median box area (px^2)
    start: Point
    end: Point
    moved: bool
    first_move_frame: int | None
    end_bin: str | None = None
    move_order: int | None = None


@dataclass(frozen=True)
class _Bin:
    name: str
    track: _Track
    box: Box  # grown median box

    @property
    def center(self) -> Point:
        return ((self.box[0] + self.box[2]) / 2, (self.box[1] + self.box[3]) / 2)


def _frame_size(concept_tracks: Mapping[str, Tracks], width: int, height: int) -> tuple[int, int]:
    """The given frame size, or the source size SAM reports on its boxes."""
    if width > 0 and height > 0:
        return width, height
    for tracks in concept_tracks.values():
        for boxes in tracks.values():
            for b in boxes:
                return (width if width > 0 else b.w, height if height > 0 else b.h)
    return max(width, 0), max(height, 0)


def _split_tracks(
    concept_tracks: Mapping[str, Tracks], container_key: str, hand_key: str
) -> tuple[list[_Track], list[_Track], int]:
    """(container tracks, object tracks, number of noise tracks dropped); hand tracks skipped."""
    containers: list[_Track] = []
    objects: list[_Track] = []
    short = 0
    for concept, tracks in concept_tracks.items():
        key = _norm(concept)
        if key == hand_key:
            continue
        for oid, boxes in tracks.items():
            seen = sorted({b.frame: b for b in boxes}.values(), key=lambda b: b.frame)
            if len(seen) < MIN_TRACK_FRAMES:
                short += 1
                continue
            track = _Track(_clean(concept), int(oid), seen)
            (containers if key == container_key else objects).append(track)
    return containers, objects, short


def _iou(a: Box, b: Box) -> float:
    iw = min(a[2], b[2]) - max(a[0], b[0])
    ih = min(a[3], b[3]) - max(a[1], b[1])
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _grow(box: Box, frac: float) -> Box:
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    hw, hh = (box[2] - box[0]) * (1 + frac) / 2, (box[3] - box[1]) * (1 + frac) / 2
    return (cx - hw, cy - hh, cx + hw, cy + hh)


def _contains(box: Box, p: Point) -> bool:
    return box[0] <= p[0] <= box[2] and box[1] <= p[1] <= box[3]


def _third(x: float, width: float) -> int:
    return min(2, max(0, int(3 * x / width))) if width > 0 else 1


def _names_by_thirds(cxs: Sequence[float], width: float) -> list[str]:
    """Distinct bin names in left-to-right order, each nearest the frame third its bowl is in."""
    centers = [width * (2 * i + 1) / 6 for i in range(3)]
    best = min(
        itertools.combinations(range(3), len(cxs)),
        key=lambda idx: sum(abs(centers[i] - cx) for i, cx in zip(idx, cxs, strict=True)),
    )
    return [BIN_NAMES[i] for i in best]


def _bins(containers: list[_Track], width: float, notes: list[str]) -> list[_Bin]:
    """Up to 3 bowls (most frames seen first, duplicates skipped), named left to right."""
    distinct: list[tuple[_Track, Box]] = []
    for t in sorted(containers, key=lambda t: (-len(t.boxes), t.oid)):
        box = t.median_box()
        if all(_iou(box, other) <= SAME_BOWL_IOU for _, other in distinct):
            distinct.append((t, box))
    if len(distinct) < len(containers):
        notes.append(f"merged {len(containers) - len(distinct)} duplicate bowl track(s)")
    if len(distinct) > 3:
        notes.append(f"found {len(distinct)} bowls; using the 3 seen on the most frames")
    chosen = sorted(distinct[:3], key=lambda tb: (tb[1][0] + tb[1][2]) / 2)
    if len(chosen) == 3:
        names = list(BIN_NAMES)
    else:
        names = _names_by_thirds([(b[0] + b[2]) / 2 for _, b in chosen], width)
        notes.append(
            f"found {len(chosen)} bowl(s), need 3: bins assigned by frame thirds of x (a bowl "
            "is named by its third; a moved object outside every bowl gets its end third)"
        )
    bins = [_Bin(n, t, _grow(b, BIN_BOX_GROW)) for n, (t, b) in zip(names, chosen, strict=True)]
    if bins:
        notes.append(
            "bins: " + ", ".join(f"{b.name}={b.track.track_id} (x={b.center[0]:.0f})" for b in bins)
        )
    return bins


def _agree(items: Sequence[ObjectAttrs]) -> ObjectAttrs:
    """Field-wise agreement of several entries' attributes (a field that differs -> None)."""
    values: dict[str, object] = {}
    for name in ObjectAttrs.model_fields:
        seen = {getattr(a, name) for a in items}
        values[name] = seen.pop() if len(seen) == 1 else None
    return ObjectAttrs.model_validate(values)


def _attrs_by_concept(discovery: Discovery, skip: set[str]) -> dict[str, ObjectAttrs]:
    grouped: dict[str, list[ObjectAttrs]] = {}
    for o in discovery.objects:
        key = _norm(o.concept)
        if key and key not in skip:
            grouped.setdefault(key, []).append(o.attrs)
    return {k: _agree(v) for k, v in grouped.items()}


def _expected_counts(discovery: Discovery, skip: set[str]) -> dict[str, int]:
    expected: dict[str, int] = {}
    for o in discovery.objects:
        key = _norm(o.concept)
        if key and key not in skip:
            expected[key] = expected.get(key, 0) + max(1, o.count)
    return expected


def _count_notes(
    tracks: list[_Track], discovery: Discovery, skip: set[str], notes: list[str]
) -> None:
    expected = _expected_counts(discovery, skip)
    found: dict[str, int] = {}
    for t in tracks:
        found[_norm(t.concept)] = found.get(_norm(t.concept), 0) + 1
    for key in dict.fromkeys([*expected, *found]):
        n = found.get(key, 0)
        if key not in expected:
            notes.append(f"{key!r} was tracked but not discovered: attributes unknown")
        elif n != expected[key]:
            notes.append(f"{key!r}: SAM tracked {n}, discovery counted {expected[key]}")


def _describe(t: _Track, attrs: ObjectAttrs, width: float) -> _Obj:
    start = (t.boxes[0].cx, t.boxes[0].cy)
    end = (t.boxes[-1].cx, t.boxes[-1].cy)
    limit = MOVE_FRAC * width
    first = next((b.frame for b in t.boxes if math.dist((b.cx, b.cy), start) > limit), None)
    area = float(statistics.median(b.area for b in t.boxes))
    return _Obj(t, attrs.model_copy(), area, start, end, math.dist(start, end) > limit, first)


def _drop_non_objects(
    objs: list[_Obj], bins: list[_Bin], expected: Mapping[str, int], notes: list[str]
) -> list[_Obj]:
    """Drop static tracks that are a bowl or the background, not an object (SAM can match "blue
    block" to a blue bowl or bin). A static track overlapping a bowl track (IoU > 0.5) is that
    bowl. When SAM finds more tracks of a concept than the discovery counted, surplus static tracks
    whose median box is over 4x the median of the other object boxes are dropped, largest first.
    Moved tracks are always kept."""
    kept = list(objs)
    for o in objs:
        box = o.track.median_box()
        bowl = next((b for b in bins if _iou(box, b.track.median_box()) > BOWL_IOU), None)
        if bowl is not None and not o.moved:
            kept.remove(o)
            notes.append(f"dropped {o.track.track_id}: it is the {bowl.name} bowl, not an object")
    groups: dict[str, list[_Obj]] = {}
    for o in kept:
        groups.setdefault(_norm(o.track.concept), []).append(o)
    for key, members in groups.items():
        surplus = len(members) - expected.get(key, len(members))
        for o in sorted((m for m in members if not m.moved), key=lambda m: -m.area):
            others = [p.area for p in kept if p is not o]
            ratio = o.area / max(statistics.median(others), 1e-9) if others else 1.0
            if surplus <= 0 or ratio <= OVERSIZE:
                break
            kept.remove(o)
            surplus -= 1
            notes.append(
                f"dropped {o.track.track_id}: an extra static {key!r} track {ratio:.0f}x the "
                "median object box (a bowl or the background)"
            )
    return kept


def _area_cut(areas: Sequence[float]) -> float | None:
    """Area threshold at the widest gap between sorted areas, if that gap exceeds SIZE_RATIO."""
    a = sorted(areas)
    best, best_ratio = None, SIZE_RATIO
    for i in range(len(a) - 1):
        ratio = a[i + 1] / max(a[i], 1e-9)
        if ratio > best_ratio:
            best, best_ratio = i, ratio
    return None if best is None else math.sqrt(a[best] * a[best + 1])


def _infer_sizes(objs: list[_Obj], notes: list[str]) -> None:
    """Fill unknown sizes from median box area: within color + shape, then within shape."""
    passes: list[tuple[str, Callable[[_Obj], Hashable]]] = [
        ("color and shape", lambda o: (o.attrs.color, o.attrs.shape)),
        ("shape", lambda o: o.attrs.shape),
    ]
    for label, key in passes:
        groups: dict[Hashable, list[_Obj]] = {}
        for o in objs:
            if o.attrs.shape is not None:
                groups.setdefault(key(o), []).append(o)
        for members in groups.values():
            todo = [o for o in members if o.attrs.size is None]
            cut = _area_cut([o.area for o in members]) if todo and len(members) > 1 else None
            if cut is None:
                continue
            for o in todo:
                o.attrs = o.attrs.model_copy(update={"size": "large" if o.area > cut else "small"})
            notes.append(
                f"size from box area (same {label}): "
                + ", ".join(f"{o.track.track_id} {o.attrs.size}" for o in todo)
            )


def _end_bin(o: _Obj, bins: list[_Bin], width: float) -> str | None:
    inside = [b for b in bins if _contains(b.box, o.end)]
    if inside:
        return min(inside, key=lambda b: math.dist(b.center, o.end)).name
    if len(bins) < 3 and o.moved:
        return BIN_NAMES[_third(o.end[0], width)]
    return None


def _px(p: Point) -> tuple[float, float]:
    return (round(p[0], 1), round(p[1], 1))


def summarize(
    concept_tracks: Mapping[str, Tracks],
    discovery: Discovery,
    *,
    container_concept: str,
    hand_concept: str = HAND_CONCEPT,
    fps: float,
    frames: int,
    width: int,
    height: int,
) -> TrackSummary:
    """Pure: SAM tracks per concept -> TrackSummary.

    Tracks seen on fewer than 3 frames are noise. Every concept except the container and the hand
    is an object; track_id = "<concept>#<sam id>". start/end = box centroid on the first/last frame
    seen; moved = start-to-end displacement > 5% of the frame width; first_move_frame = first
    frame more than 5% of the width from the start (set even if the object was put back);
    move_order ranks moved objects by first_move_frame. Static tracks that are really a bowl or
    the background are dropped (see _drop_non_objects). Up to 3 bowls (most frames first) are
    named left_bin/middle_bin/right_bin by median x; end_bin = the bowl whose median box (grown
    10%) holds the end centroid. With fewer than 3 bowls, names come from frame thirds of x.
    attrs come from the discovered entries with the same concept; unknown sizes are filled from
    median box area when same-kind tracks differ by more than 1.4x.
    """
    notes: list[str] = []
    width, height = _frame_size(concept_tracks, width, height)
    container_key, hand_key = _norm(container_concept), _norm(hand_concept)
    containers, tracks, short = _split_tracks(concept_tracks, container_key, hand_key)
    if short:
        notes.append(f"ignored {short} track(s) seen on fewer than {MIN_TRACK_FRAMES} frames")
    bins = _bins(containers, width, notes)
    skip = {container_key, hand_key}
    attrs = _attrs_by_concept(discovery, skip)
    objs = [_describe(t, attrs.get(_norm(t.concept), ObjectAttrs()), width) for t in tracks]
    objs = _drop_non_objects(objs, bins, _expected_counts(discovery, skip), notes)
    _count_notes([o.track for o in objs], discovery, skip, notes)
    _infer_sizes(objs, notes)
    for o in objs:
        o.end_bin = _end_bin(o, bins, width)
    moved = sorted(
        (o for o in objs if o.moved), key=lambda o: (o.first_move_frame or 0, o.track.track_id)
    )
    for rank, o in enumerate(moved):
        o.move_order = rank
    if objs and not moved:
        notes.append(f"no object moved more than {MOVE_FRAC:.0%} of the frame width")
    rank_of = {k: i for i, k in enumerate(attrs)}

    def order(o: _Obj) -> tuple[int, int, str, int]:  # moved first (in move order), then the rest
        if o.moved:
            return (0, o.move_order or 0, "", 0)
        return (1, rank_of.get(_norm(o.track.concept), len(rank_of)), o.track.concept, o.track.oid)

    objs.sort(key=order)
    last_frame = max((b.frame for t in [*containers, *tracks] for b in t.boxes), default=-1)
    return TrackSummary(
        objects=[
            TrackedObject(
                track_id=o.track.track_id,
                concept=o.track.concept,
                attrs=o.attrs,
                moved=o.moved,
                start_px=_px(o.start),
                end_px=_px(o.end),
                end_bin=o.end_bin,
                move_order=o.move_order,
                first_move_frame=o.first_move_frame,
            )
            for o in objs
        ],
        fps=fps if fps > 0 else 10.0,
        frames=frames if frames > 0 else last_frame + 1,
        width=width,
        height=height,
        notes=notes,
    )


def build_track_summary(
    video: Path, out_dir: Path, *, tag: str = ""
) -> tuple[TrackSummary, Discovery, list[Path]]:
    """Normalize the demo video, write 8 keyframes, discover the objects, track them (plus the
    bowls and the hand) with SAM 3.1 and summarize.

    Writes out_dir/prep/normalized.mp4, out_dir/prep/keyframes/, out_dir/discovery.json and
    out_dir/track_summary.json. `tag` is a job prefix: usage is logged as "<tag>:discover" and
    "<tag>:track". Returns (summary, discovery, keyframe paths).
    """
    out_dir = Path(out_dir)
    prep = out_dir / "prep"
    normalized = video_prep.normalize(Path(video), prep / "normalized.mp4")
    keyframes = video_prep.keyframes(normalized, n=N_KEYFRAMES, out_dir=prep / "keyframes")
    if not keyframes:
        raise ValueError(f"could not read any frame from {video}")
    discovery = discover(keyframes, tag=_sub(tag, "discover"))
    cache.atomic_write_text(out_dir / "discovery.json", discovery.model_dump_json(indent=2))
    container = discovery.container_concept or DEFAULT_CONTAINER
    concepts = [*(o.concept for o in discovery.objects), container, HAND_CONCEPT]
    tracks = sam.track(normalized, list(dict.fromkeys(concepts)), tag=_sub(tag, "track"))
    info = video_prep.probe(normalized)
    summary = summarize(
        tracks,
        discovery,
        container_concept=container,
        fps=info.fps,
        frames=info.frames,
        width=info.width,
        height=info.height,
    )
    cache.atomic_write_text(out_dir / "track_summary.json", summary.model_dump_json(indent=2))
    log.info(
        "track summary: %d objects (%d moved), %d notes",
        len(summary.objects),
        sum(o.moved for o in summary.objects),
        len(summary.notes),
    )
    return summary, discovery, keyframes


# ---------------------------------------------------------------- discovery -> sim objects
def _named_color(o: DiscoveredObject) -> str | None:
    if o.attrs.color:
        return o.attrs.color
    return next((_COLOR_WORDS[w] for w in _words(o.concept) if w in _COLOR_WORDS), None)


def _sim_shape(o: DiscoveredObject, notes: list[str]) -> str:
    words = set(_words(o.concept))
    if o.attrs.shape:
        shape: str = o.attrs.shape
    elif words & _GLASS_WORDS or o.attrs.fragile:
        shape = "glass"
    elif words & _CYLINDER_WORDS:
        shape = "cylinder"
    elif words & _CUBE_WORDS:
        shape = "cube"
    else:
        shape = "cube"
        notes.append(f"{o.concept!r}: unknown shape, simulated as a cube")
    if o.attrs.fragile and shape != "glass":
        notes.append(
            f"{o.concept!r}: only the glass is fragile in the simulator; "
            f"simulated as a non-fragile {shape}"
        )
    return shape


def _cube_color(o: DiscoveredObject, used: set[str], notes: list[str]) -> str:
    """Red, blue or green: the named color, else the nearest one not already in the scene."""
    color = _named_color(o)
    if color is not None and color in CUBE_COLORS:
        return color
    prefs = _CUBE_COLOR_PREFS.get(color or "", CUBE_COLORS)
    pick = next((c for c in prefs if c not in used), prefs[0])
    used.add(pick)
    notes.append(
        f"{o.concept!r}: the simulator has only red, blue and green cubes; "
        f"{color or 'unknown color'} -> {pick}"
    )
    return pick


def _cube_sizes(o: DiscoveredObject, n: int, summary: TrackSummary | None) -> list[str]:
    if o.attrs.size:
        return [o.attrs.size] * n
    named = [_SIZE_WORDS[w] for w in _words(o.concept) if w in _SIZE_WORDS]
    if named:
        return [named[0]] * n
    tracked = [
        t.attrs.size
        for t in (summary.objects if summary else [])
        if _norm(t.concept) == _norm(o.concept) and t.attrs.size
    ]
    return [tracked[i] if i < len(tracked) else "large" for i in range(n)]


def scene_objects_from_discovery(
    discovery: Discovery, max_objects: int = 6, *, summary: TrackSummary | None = None
) -> tuple[list[str], list[str]]:
    """Simulator object ids for the discovered kinds, plus notes on every approximation.

    Cubes -> "<color>_cube_<size or large>" (colors other than red/blue/green map to the nearest
    one not already present, so a color sort keeps distinct colors); any cylinder ->
    yellow_cylinder_large; a glass -> clear_glass_large (at most one). `count` repeats an id with
    suffixes _2, _3. Unknown cube sizes are taken from `summary` (box-area sizes) when given. At
    most `max_objects` ids (the glass is kept); never empty.
    """
    notes: list[str] = []
    shapes = [_sim_shape(o, notes) for o in discovery.objects]
    cube_colors = [
        _named_color(o) for o, s in zip(discovery.objects, shapes, strict=True) if s == "cube"
    ]
    used = {c for c in cube_colors if c in CUBE_COLORS}  # off-palette colors avoid these
    bases: list[str] = []
    for o, shape in zip(discovery.objects, shapes, strict=True):
        n = max(1, o.count)
        if shape == "glass":
            extra = n if GLASS_ID in bases else n - 1
            if extra:
                notes.append(f"{o.concept!r}: the simulator has one glass; dropped {extra} more")
            if GLASS_ID not in bases:
                bases.append(GLASS_ID)
        elif shape == "cylinder":
            if _named_color(o) not in (None, "yellow"):
                notes.append(f"{o.concept!r}: only yellow cylinders exist in the simulator")
            bases += [CYLINDER_ID] * n
        else:
            color = _cube_color(o, used, notes)
            bases += [f"{color}_cube_{size}" for size in _cube_sizes(o, n, summary)]
    limit = max(1, max_objects)
    if len(bases) > limit:
        keep = [bases.index(GLASS_ID)] if GLASS_ID in bases else []
        keep += [i for i in range(len(bases)) if i not in keep][: limit - len(keep)]
        dropped = [b for i, b in enumerate(bases) if i not in keep]
        notes.append(
            f"kept {limit} of {len(bases)} objects (scene limit); dropped {', '.join(dropped)}"
        )
        bases = [bases[i] for i in sorted(keep)]
    if not bases:
        bases = [FALLBACK_OBJECT]
        notes.append(f"no objects discovered; using one {FALLBACK_OBJECT}")
    seen: dict[str, int] = {}
    ids: list[str] = []
    for base in bases:
        seen[base] = seen.get(base, 0) + 1
        ids.append(base if seen[base] == 1 else f"{base}_{seen[base]}")
    for obj_id in ids:
        parse_object_id(obj_id)  # every id must build in the simulator
    return ids, notes
