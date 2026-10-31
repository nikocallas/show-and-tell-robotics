"""SAM judge: camera-only verdicts on each check, from the rendered judge clips.

Per seed the runner writes `judge.mp4` (overhead camera) and `judge_angle.mp4` (45 degrees):
50 frames at 25 fps, the first 25 from the start of the run and the last 25 from the end, with the
arm parked out of view. SAM 3.1 tracks one noun phrase per request ("red cube",
"light blue cylinder", ...) and only its boxes are used. Ground truth decides pass/fail; this
judge is reported next to it with the agreement rate (`showtell.verify.agreement`).

How a verdict is reached (frames <= 2 are the start, the last 3 frames the end):
- identity: each phase is matched on its own, because SAM ids need not survive the cut in the
  clip. Within a concept, boxes go to objects by size (large vs small cube, corrected for
  perspective). When a check selects every object of a concept (two yellow cylinders, both blue
  cubes) identity does not matter: start and end boxes are paired by least motion instead.
- in_bin: the end box centroid lies inside the bin interior projected into the overhead view.
- unmoved/untouched: SAM cannot see touches, so both use displacement: the centroid moves less than
  2 cm (projected at the object's depth, at least 6 px) in every camera that saw the object. An
  object seen at the start and gone at the end in every camera has left the view (moved).
- upright: the judge_angle box keeps > 70% of its start aspect (h/w), and the overhead box still
  fits the box an upright object would have at that spot (within 1.3x per side). The second test
  catches a glass that fell toward the 45-degree camera, whose aspect barely changes. Only tall
  objects are judged: a cube looks the same on any face.
`passed` is None when the cameras cannot decide (object not visible, identity unclear).
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any

import numpy as np
from scipy.optimize import linear_sum_assignment

from showtell.agent.schemas import Check
from showtell.contracts import SamVerdict
from showtell.meta import sam
from showtell.meta.sam import FrameBox, Tracks
from showtell.sim import scene as S
from showtell.sim.checks import select

log = logging.getLogger(__name__)

N_FRAMES = 50  # judge clips: 25 frames from the start of the run + 25 from the end
PHASE_FRAMES = 3  # frames 0..2 are the start, the last 3 frames the end
MOVE_TOL_M = 0.02  # same tolerance as the ground-truth `unmoved` check
MIN_MOVE_PX = 6.0
ASPECT_KEEP = 0.70  # upright: the side-view h/w must keep this fraction of its start value
TOP_FIT_MAX = 1.3  # upright: top-view box side vs an upright object's box at the same spot
TALL = 1.5  # upright is judged only for objects this many times taller than wide
NMS_IOU = 0.6
GHOST_IOU = 0.7  # an end box this close to a bin SAM mistook for an object is that bin again
MAX_EXTENT = 1.5  # a box longer than this x the object's diagonal is not that object
ANGLE_KINDS = ("untouched", "unmoved", "upright")
VIEWS = {"overhead": "overhead", "angle": "judge_angle"}  # view -> scene camera
CLIPS = {"overhead": "judge.mp4", "angle": "judge_angle.mp4"}
OK_TEXT = {
    "in_bin": "all in {bin}",
    "untouched": "none moved",
    "unmoved": "none moved",
    "upright": "all upright",
}


@dataclass(frozen=True)
class Box:
    """One SAM track over one phase (median of its frames), in scene-camera pixels.

    Edges are continuous coordinates (SAM's inclusive x2/y2 + 1), so w = x2 - x1.
    """

    x1: float
    y1: float
    x2: float
    y2: float
    score: float = 1.0  # mean confidence x fraction of the phase's frames it appears in

    @property
    def cx(self) -> float:
        return (self.x1 + self.x2) / 2

    @property
    def cy(self) -> float:
        return (self.y1 + self.y2) / 2

    @property
    def w(self) -> float:
        return max(self.x2 - self.x1, 1e-6)

    @property
    def h(self) -> float:
        return max(self.y2 - self.y1, 1e-6)

    @property
    def area(self) -> float:
        return self.w * self.h


Pair = tuple[Box | None, Box | None]  # (start box, end box) of one object in one view


GLASS_CONCEPT = "light blue cylinder"  # what the rendered glass looks like to SAM


def concept_for(spec: S.ObjSpec) -> str:
    """SAM noun phrase for a sim object: "red cube", "yellow cylinder", "light blue cylinder".

    The rendered glass is a translucent light-cyan cylinder. Measured on real judge clips, SAM 3.1
    finds nothing for "glass", "drinking glass", "glass cup" or "transparent cylinder", and finds
    it reliably as "light blue cylinder" (c 0.94-0.96 in the 45-degree view).
    """
    return GLASS_CONCEPT if spec.shape == "glass" else f"{spec.color} {spec.shape}"


def concepts_needed(
    checks: Sequence[Check | Mapping[str, Any]], objects: Sequence[S.ObjSpec]
) -> tuple[list[str], list[str]]:
    """Concepts to track in the overhead clip (every checked object) and in the angle clip
    (objects under untouched/unmoved/upright checks). Whole concepts are tracked, so both red
    cubes are found even when a check selects only the large one."""
    overhead: dict[str, None] = {}
    angle: dict[str, None] = {}
    for c in _as_checks(checks):
        for o in select(objects, c.select.as_dict()):
            if c.kind == "upright" and not _tall(o):
                continue  # undecided without looking (see `_upright`)
            overhead[concept_for(o)] = None
            if c.kind in ANGLE_KINDS:
                angle[concept_for(o)] = None
    return list(overhead), list(angle)


def _tall(spec: S.ObjSpec) -> bool:
    """Tall enough that tipping it over changes its silhouette (the glass, not a cube)."""
    return spec.height >= TALL * 2 * spec.half_xy


# ---------------------------------------------------------------- camera geometry
def _ray_to_plane(u: float, v: float, z: float, cam: Mapping) -> tuple[np.ndarray, float]:
    """World point on the horizontal plane at height `z` seen at pixel (u, v), and its depth."""
    rot, pos = np.asarray(cam["rot"]), np.asarray(cam["pos"], dtype=float)
    ray = rot @ np.array([(u - cam["cx"]) / cam["fx"], -(v - cam["cy"]) / cam["fy"], -1.0])
    depth = (z - pos[2]) / ray[2] if ray[2] < -1e-9 else 0.0
    depth = max(depth, 0.05)  # the ray has camera-frame z = -1, so `depth` is along the axis
    return pos + depth * ray, depth


def _upright_hull(spec: S.ObjSpec, x: float, y: float) -> np.ndarray:
    """Hull points of `spec` standing upright on the table at (x, y)."""
    r, z0, z1 = spec.half_xy, S.TABLE_TOP_Z, S.TABLE_TOP_Z + spec.height
    if spec.shape == "cube":  # yaw unknown: 22.5 deg gives the mean bounding-box size
        c, s = math.cos(math.pi / 8), math.sin(math.pi / 8)
        xy = [(c * dx - s * dy, s * dx + c * dy) for dx in (-r, r) for dy in (-r, r)]
    else:
        a = np.linspace(0.0, 2 * math.pi, 24, endpoint=False)
        xy = list(zip(r * np.cos(a), r * np.sin(a), strict=True))
    return np.array([(x + dx, y + dy, z) for dx, dy in xy for z in (z0, z1)])


def _upright_box(spec: S.ObjSpec, box: Box, cam: Mapping) -> tuple[float, float]:
    """(w, h) in pixels of `spec` standing upright where `box` is seen."""
    (x, y, _), _ = _ray_to_plane(box.cx, box.cy, S.TABLE_TOP_Z + spec.height / 2, cam)
    uv = S.project(_upright_hull(spec, x, y), cam)
    return max(float(np.ptp(uv[:, 0])), 1e-6), max(float(np.ptp(uv[:, 1])), 1e-6)


def _extent(spec: S.ObjSpec) -> float:
    """Longest straight line through the object (its space diagonal)."""
    across = 2 * spec.half_xy * (math.sqrt(2.0) if spec.shape == "cube" else 1.0)
    return math.hypot(across, spec.height)


def _inside(u: float, v: float, poly: np.ndarray) -> bool:
    """Point-in-polygon by ray casting."""
    inside = False
    n = len(poly)
    for i in range(n):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % n]
        if (y1 > v) != (y2 > v) and u < x1 + (v - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def _bin_of(box: Box, spec: S.ObjSpec, scene_meta: Mapping) -> str | None:
    """Bin whose interior (projected at the object's mid-height) contains the box centroid."""
    cam = scene_meta["cameras"]["overhead"]
    z = S.bin_floor_top() + spec.height / 2
    for name, b in scene_meta["bins"].items():
        if "interior_world" in b:
            x0, x1, y0, y1 = b["interior_world"]
            poly = S.project([(x0, y0, z), (x0, y1, z), (x1, y1, z), (x1, y0, z)], cam)
        else:
            poly = np.asarray(b["overhead_px"], dtype=float)
        if _inside(box.cx, box.cy, poly):
            return name
    return None


# ---------------------------------------------------------------- tracks -> phase boxes
def _iou(a: Box, b: Box) -> float:
    ix = max(0.0, min(a.x2, b.x2) - max(a.x1, b.x1))
    iy = max(0.0, min(a.y2, b.y2) - max(a.y1, b.y1))
    inter = ix * iy
    return inter / (a.area + b.area - inter)


def _phase_boxes(tracks: Tracks, lo: int, hi: int, cam: Mapping) -> list[Box]:
    """One box per SAM track seen in frames lo..hi, best first, duplicates suppressed."""
    boxes: list[Box] = []
    for frames in tracks.values():
        sel = [b for b in frames if lo <= b.frame <= hi]
        if not sel:
            continue
        sx = cam["width"] / sel[0].w if sel[0].w else 1.0  # SAM boxes are in source pixels
        sy = cam["height"] / sel[0].h if sel[0].h else 1.0
        conf = [b.conf for b in sel if b.conf is not None]
        boxes.append(
            Box(
                x1=median(b.x1 for b in sel) * sx,
                y1=median(b.y1 for b in sel) * sy,
                x2=(median(b.x2 for b in sel) + 1) * sx,
                y2=(median(b.y2 for b in sel) + 1) * sy,
                score=(sum(conf) / len(conf) if conf else 1.0) * len(sel) / (hi - lo + 1),
            )
        )
    boxes.sort(key=lambda b: -b.score)
    kept: list[Box] = []
    for b in boxes:
        if all(_iou(b, k) < NMS_IOU for k in kept):
            kept.append(b)
    return kept


def _plausible(boxes: list[Box], group: list[S.ObjSpec], cam: Mapping) -> list[Box]:
    """Drop boxes far too long for any object of the concept (e.g. a bin called "blue cube")."""
    z = S.TABLE_TOP_Z + max(o.height for o in group) / 2
    limit = MAX_EXTENT * max(_extent(o) for o in group)
    out = []
    for b in boxes:
        _, depth = _ray_to_plane(b.cx, b.cy, z, cam)
        if max(b.w, b.h) * depth / cam["fx"] <= limit:
            out.append(b)
    return out


def _drop_ghosts(start: list[Box], end: list[Box], cam: Mapping) -> tuple[list[Box], list[Box]]:
    """Nothing starts inside a bin (objects start on the table), so a start box there is a bin
    SAM took for the concept (the slate-blue bin as a "light blue cylinder"): drop it, and the
    same box at the end."""
    z = S.bin_floor_top() + 0.025
    polys = []
    for name in S.BIN_NAMES:
        x0, x1, y0, y1 = S.bin_interior(name)
        polys.append(S.project([(x0, y0, z), (x0, y1, z), (x1, y1, z), (x1, y0, z)], cam))
    ghosts = [b for b in start if any(_inside(b.cx, b.cy, p) for p in polys)]
    start = [b for b in start if b not in ghosts]
    end = [b for b in end if all(_iou(b, g) < GHOST_IOU for g in ghosts)]
    return start, end


def _by_size(group: list[S.ObjSpec], boxes: list[Box], cam: Mapping) -> dict[str, Box]:
    """Tie boxes to objects by size: each box is compared with the box each candidate would
    have at that spot, so perspective cancels; with equal counts this is a size ranking."""
    if not boxes:
        return {}
    cost = np.zeros((len(group), len(boxes)))
    for j, b in enumerate(boxes):
        for i, o in enumerate(group):
            pw, ph = _upright_box(o, b, cam)
            cost[i, j] = abs(0.5 * math.log(b.area / (pw * ph)))
    rows, cols = linear_sum_assignment(cost)
    return {group[i].id: boxes[j] for i, j in zip(rows, cols, strict=True)}


def _pair_by_motion(start: list[Box], end: list[Box], k: int) -> list[Pair]:
    """Pair start and end boxes of interchangeable objects by least total motion (k pairs)."""
    pairs: list[Pair] = []
    rows: list[int] = []
    cols: list[int] = []
    if start and end:
        dist = np.array([[math.hypot(e.cx - s.cx, e.cy - s.cy) for e in end] for s in start])
        r, c = linear_sum_assignment(dist)
        rows, cols = [int(i) for i in r], [int(j) for j in c]
        pairs += [(start[i], end[j]) for i, j in zip(rows, cols, strict=True)]
    pairs += [(s, None) for i, s in enumerate(start) if i not in rows]
    pairs += [(None, e) for j, e in enumerate(end) if j not in cols]
    return (pairs + [(None, None)] * k)[:k]


@dataclass
class _GroupView:
    """One concept's objects as seen by one camera."""

    by_id: dict[str, Pair]
    anon: list[Pair]  # least-motion pairs; valid whenever the whole group is judged together
    ambiguous: bool  # sizes differ but some boxes are missing: cannot tell which is which


def _same_size(group: list[S.ObjSpec]) -> bool:
    return len({(o.half_xy, o.height) for o in group}) == 1


def _view(group: list[S.ObjSpec], tracks: Tracks, cam: Mapping, n_frames: int) -> _GroupView:
    k = len(group)
    phases = ((0, PHASE_FRAMES - 1), (n_frames - PHASE_FRAMES, n_frames - 1))
    start, end = (_plausible(_phase_boxes(tracks, lo, hi, cam), group, cam) for lo, hi in phases)
    start, end = _drop_ghosts(start, end, cam)
    start, end = start[:k], end[:k]  # best SAM scores first
    anon = _pair_by_motion(start, end, k)
    if _same_size(group):
        return _GroupView({o.id: anon[i] for i, o in enumerate(group)}, anon, False)
    s_ids, e_ids = _by_size(group, start, cam), _by_size(group, end, cam)
    by_id = {o.id: (s_ids.get(o.id), e_ids.get(o.id)) for o in group}
    return _GroupView(by_id, anon, any(0 < len(b) < k for b in (start, end)))


# ---------------------------------------------------------------- per-object judgments
@dataclass
class _Unit:
    """What a check judges: one object, or a group of interchangeable objects as a whole."""

    label: str  # object id, or "a red cube" for interchangeable objects
    spec: S.ObjSpec  # geometry for depth and projections (any member of a group)
    pairs: dict[str, list[Pair]]  # view -> (start, end) per member
    note: str = ""  # why a view has no usable boxes (identity unclear)


def _units(
    chosen: list[S.ObjSpec],
    groups: dict[str, list[S.ObjSpec]],
    views: dict[str, dict[str, _GroupView]],
) -> list[_Unit]:
    ids = {o.id for o in chosen}
    units: list[_Unit] = []
    for concept, group in groups.items():
        sel = [o for o in group if o.id in ids]
        if not sel:
            continue
        gv = {v: per[concept] for v, per in views.items()}
        if len(group) > 1 and len(sel) == len(group):  # judged together: identity is irrelevant
            units.append(_Unit(f"a {concept}", group[0], {v: g.anon for v, g in gv.items()}))
            continue
        unclear = any(g.ambiguous for g in gv.values())
        for o in sel:
            others = ", ".join(x.id for x in group if x.id != o.id)
            pairs = {v: [(None, None)] if g.ambiguous else [g.by_id[o.id]] for v, g in gv.items()}
            note = f"could not tell {o.id} from {others}" if unclear else ""
            units.append(_Unit(o.id, o, pairs, note))
    return units


def _in_bin(unit: _Unit, bin_name: str, scene_meta: Mapping) -> tuple[bool | None, str]:
    ends = [e for _, e in unit.pairs.get("overhead", [])]
    for e in ends:
        if e is not None and (where := _bin_of(e, unit.spec, scene_meta)) != bin_name:
            return False, f"{unit.label} seen {f'in {where}' if where else 'outside the bins'}"
    if not ends or any(e is None for e in ends):
        return None, unit.note or f"{unit.label} not visible at the end"
    return True, ""


def _motion(pairs: list[Pair], spec: S.ObjSpec, cam: Mapping) -> tuple[str, float]:
    """One view's reading: moved | vanished | still | partial | unseen, and the largest move (m)."""
    moved_m, vanished, still = 0.0, 0, 0
    for s, e in pairs:
        if s is None:
            continue
        if e is None:
            vanished += 1
            continue
        _, depth = _ray_to_plane(s.cx, s.cy, S.TABLE_TOP_Z + spec.height / 2, cam)
        d_px = math.hypot(e.cx - s.cx, e.cy - s.cy)
        if d_px > max(MIN_MOVE_PX, cam["fx"] * MOVE_TOL_M / depth):
            moved_m = max(moved_m, d_px * depth / cam["fx"])
        else:
            still += 1
    if moved_m:
        return "moved", moved_m
    if vanished:
        return "vanished", 0.0
    if still and still == len(pairs):
        return "still", 0.0
    return ("partial" if still else "unseen"), 0.0


def _unmoved(unit: _Unit, scene_meta: Mapping) -> tuple[bool | None, str]:
    reads = {
        v: _motion(p, unit.spec, scene_meta["cameras"][VIEWS[v]]) for v, p in unit.pairs.items()
    }
    moved = max((m for _, m in reads.values()), default=0.0)
    if moved:
        return False, f"{unit.label} moved ~{moved * 100:.0f} cm"
    vanished = [v for v, (r, _) in reads.items() if r == "vanished"]
    still = [v for v, (r, _) in reads.items() if r == "still"]
    if vanished and still:
        return None, f"{unit.label} lost in the {VIEWS[vanished[0]]} view"
    if vanished:
        return False, f"{unit.label} left the view"
    if still:
        return True, ""
    return None, unit.note or f"{unit.label} not visible at the start"


def _tilt_side(pairs: list[Pair]) -> tuple[str, str]:
    """judge_angle reading: tipped | upright | vanished | unseen."""
    ok, vanished = 0, 0
    for s, e in pairs:
        if s is None:
            continue
        if e is None:
            vanished += 1
            continue
        a0, a1 = s.h / s.w, e.h / e.w
        if a1 < ASPECT_KEEP * a0:
            return "tipped", f"side view h/w {a0:.1f} -> {a1:.1f}"
        ok += 1
    if vanished:
        return "vanished", ""
    return ("upright" if ok and ok == len(pairs) else "unseen"), ""


def _tilt_top(pairs: list[Pair], spec: S.ObjSpec, cam: Mapping) -> str:
    """Overhead reading: a reason if the end box no longer fits an upright object, else "".

    Each box side is divided by the side an upright object would have at that spot (perspective
    cancels) and compared with the same ratio at the start (SAM's box scale cancels). A lying
    glass is much longer than an upright one along its axis and thinner across it.
    """

    def fit(b: Box) -> tuple[float, float]:
        pw, ph = _upright_box(spec, b, cam)
        return b.w / pw, b.h / ph

    for s, e in pairs:
        if s is None or e is None:
            continue
        (sw, sh), (ew, eh) = fit(s), fit(e)
        off = math.exp(max(abs(math.log(ew / sw)), abs(math.log(eh / sh))))
        if off > TOP_FIT_MAX:
            return f"top view {off:.1f}x off an upright one"
    return ""


def _upright(unit: _Unit, scene_meta: Mapping) -> tuple[bool | None, str]:
    if not _tall(unit.spec):
        return None, f"{unit.label} looks alike tipped or upright"
    side, why = _tilt_side(unit.pairs.get("angle", []))
    top = _tilt_top(unit.pairs.get("overhead", []), unit.spec, scene_meta["cameras"]["overhead"])
    if side == "tipped" or top:
        return False, f"{unit.label} tipped over ({why or top})"
    if side == "upright":
        return True, ""
    if side == "vanished":
        return None, f"{unit.label} not visible at the end"
    return None, unit.note or f"no {VIEWS['angle']} view of {unit.label}"


def _as_checks(checks: Sequence[Check | Mapping[str, Any]]) -> list[Check]:
    return [c if isinstance(c, Check) else Check.model_validate(c) for c in checks]


def _groups(objects: Sequence[S.ObjSpec]) -> dict[str, list[S.ObjSpec]]:
    groups: dict[str, list[S.ObjSpec]] = {}
    for o in objects:
        groups.setdefault(concept_for(o), []).append(o)
    return groups


def verdicts_from_tracks(
    checks: Sequence[Check | Mapping[str, Any]],
    objects: Sequence[S.ObjSpec],
    overhead: Mapping[str, Tracks],
    angle: Mapping[str, Tracks] | None,
    scene_meta: Mapping[str, Any],
    *,
    n_frames: int = N_FRAMES,
) -> list[SamVerdict]:
    """Judge every check from SAM tracks ({concept: {sam id: [FrameBox]}}) of both clips.

    Pure: no API calls. `angle` is None when the judge_angle clip was not tracked.
    """
    objs = list(objects)
    groups = _groups(objs)
    tracks = {"overhead": overhead, "angle": angle}
    views = {
        v: {
            concept: _view(group, t.get(concept, {}), scene_meta["cameras"][VIEWS[v]], n_frames)
            for concept, group in groups.items()
        }
        for v, t in tracks.items()
        if t is not None
    }
    out: list[SamVerdict] = []
    for c in _as_checks(checks):
        chosen = select(objs, c.select.as_dict())
        if not chosen:
            out.append(SamVerdict(check_id=c.id, kind=c.kind, passed=True, detail="no objects"))
            continue
        results = []
        used = {"overhead": views["overhead"]} if c.kind == "in_bin" else views
        for unit in _units(chosen, groups, used):
            if c.kind == "in_bin":
                results.append(_in_bin(unit, c.bin or "", scene_meta))
            elif c.kind == "upright":
                results.append(_upright(unit, scene_meta))
            else:  # unmoved, untouched
                results.append(_unmoved(unit, scene_meta))
        fails = [why for ok, why in results if ok is False]
        unsure = [why for ok, why in results if ok is None]
        passed: bool | None = False if fails else (None if unsure else True)
        detail = "; ".join(fails or unsure) or OK_TEXT[c.kind].format(bin=c.bin)
        out.append(SamVerdict(check_id=c.id, kind=c.kind, passed=passed, detail=detail))
    return out


# ---------------------------------------------------------------- judging a seed folder
def _frame_count(path: Path) -> int:
    try:
        from showtell.perception.video_prep import probe

        return probe(path).frames or N_FRAMES
    except Exception:  # probing is advisory; the clips are written with exactly 50 frames
        return N_FRAMES


def _max_frame(track_sets: Iterable[Mapping[str, Tracks] | None]) -> int:
    """Highest frame index in any SAM track (-1 if there is none)."""
    frames = [-1]
    for ts in track_sets:
        for per in (ts or {}).values():
            frames += [b.frame for boxes in per.values() for b in boxes]
    return max(frames)


def _short(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}".splitlines()[0]
    return text if len(text) <= 160 else text[:157] + "..."


def dump_tracks(tracks: Mapping[str, Tracks] | None) -> dict[str, Any] | None:
    """JSON form of {concept: {sam id: [FrameBox]}} (for sam.json)."""
    if tracks is None:
        return None
    return {
        concept: {str(oid): [b.model_dump() for b in boxes] for oid, boxes in per.items()}
        for concept, per in tracks.items()
    }


def load_tracks(data: Mapping[str, Any] | None) -> dict[str, Tracks] | None:
    """Inverse of `dump_tracks`."""
    if data is None:
        return None
    return {
        concept: {
            int(oid): [FrameBox.model_validate(b) for b in boxes] for oid, boxes in per.items()
        }
        for concept, per in data.items()
    }


def judge(
    seed_dir: Path,
    checks: Sequence[Check | Mapping[str, Any]],
    objects: Sequence[S.ObjSpec],
    scene_meta: Mapping[str, Any],
    *,
    tag: str = "",
) -> list[SamVerdict]:
    """Run SAM on a seed's judge clips, save the raw boxes as `sam.json`, and judge every check.

    SAM is asked once per concept per clip (cached by file hash; 50 billed frames per request).
    A missing clip, an API error or a judge bug never raises: the affected verdicts come back
    with passed=None and the reason.
    """
    seed_dir = Path(seed_dir)
    specs = _as_checks(checks)
    objs = list(objects)
    wanted = dict(zip(VIEWS, concepts_needed(specs, objs), strict=True))
    tracks: dict[str, dict[str, Tracks] | None] = {"overhead": {}, "angle": None}
    errors: dict[str, str] = {}
    for view, concepts in wanted.items():
        if not concepts:
            continue
        clip = seed_dir / CLIPS[view]
        try:
            if not clip.is_file():
                raise FileNotFoundError(f"{clip.name} is missing")
            tracks[view] = sam.track(clip, concepts, tag=tag)
        except Exception as exc:  # report, never break the pipeline
            log.warning("SAM judge failed on %s: %s", clip, exc)
            errors[view] = _short(exc)
            tracks[view] = None
    n_frames = max(_frame_count(seed_dir / CLIPS["overhead"]), _max_frame(tracks.values()) + 1)
    record: dict[str, Any] = {
        "n_frames": n_frames,
        "concepts": wanted,
        "overhead": dump_tracks(tracks["overhead"]),
        "angle": dump_tracks(tracks["angle"]),
        "errors": errors,
    }
    _write(seed_dir / "sam.json", record)
    try:
        verdicts = verdicts_from_tracks(
            specs, objs, tracks["overhead"] or {}, tracks["angle"], scene_meta, n_frames=n_frames
        )
    except Exception as exc:  # a judge bug must not cost the run
        log.exception("SAM judge crashed in %s", seed_dir)
        errors["judge"] = _short(exc)
        verdicts = [
            SamVerdict(
                check_id=c.id, kind=c.kind, passed=None, detail=f"judge error: {_short(exc)}"
            )
            for c in specs
        ]
    for v in verdicts:  # say why SAM was blind rather than "not visible"
        if v.passed is None and "overhead" in errors:
            v.detail = f"SAM failed on {CLIPS['overhead']}: {errors['overhead']}"
        elif v.passed is None and v.kind in ANGLE_KINDS and "angle" in errors:
            v.detail = f"SAM failed on {CLIPS['angle']}: {errors['angle']}"
    record["verdicts"] = [v.model_dump() for v in verdicts]
    _write(seed_dir / "sam.json", record)
    return verdicts


def _write(path: Path, data: Mapping[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=1))
    except OSError as exc:  # pragma: no cover - disk problems are logged, not fatal
        log.warning("could not write %s: %s", path, exc)
