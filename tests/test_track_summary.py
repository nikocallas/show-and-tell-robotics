"""Track summary: the pure summarize step, discovery tidy-up and sim object mapping (offline)."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

from showtell.agent.prompts import DISCOVER_INSTRUCTIONS
from showtell.agent.schemas import (
    DiscoveredObject,
    Discovery,
    ObjectAttrs,
    TrackedObject,
    TrackSummary,
)
from showtell.config import settings
from showtell.meta import files, sam, wonder
from showtell.meta.sam import FrameBox
from showtell.perception import track_summary as ts
from showtell.perception import video_prep
from showtell.sim.scene import parse_object_id

W, H = 1280, 720
FRAMES = range(100)


# ---------------------------------------------------------------- synthetic SAM tracks
def fb(frame: int, cx: float, cy: float, side: float, w: int = W, h: int = H) -> FrameBox:
    half = side / 2
    return FrameBox(
        frame=frame, x1=cx - half, y1=cy - half, x2=cx + half, y2=cy + half, w=w, h=h, conf=0.9
    )


def static(cx: float, cy: float, side: float, frames: Iterable[int] = FRAMES) -> list[FrameBox]:
    return [fb(f, cx, cy, side) for f in frames]


def carried(
    start: tuple[float, float],
    end: tuple[float, float],
    t0: int,
    t1: int,
    side: float,
    frames: Iterable[int] = FRAMES,
) -> list[FrameBox]:
    """At `start` until frame t0, a straight carry to `end` by frame t1, then at `end`."""
    out = []
    for f in frames:
        a = min(1.0, max(0.0, (f - t0) / (t1 - t0)))
        x = start[0] + a * (end[0] - start[0])
        y = start[1] + a * (end[1] - start[1])
        out.append(fb(f, x, y, side))
    return out


def attrs(**kw: Any) -> ObjectAttrs:
    return ObjectAttrs(**kw)


RED_KW: dict[str, Any] = {"color": "red", "shape": "cube", "fragile": False}
RED = attrs(**RED_KW)
BLUE_LARGE = attrs(size="large", color="blue", shape="cube", fragile=False)
GLASS = attrs(color="clear", shape="glass", fragile=True)
BOWLS = {  # SAM ids are sparse and not in left-to-right order
    17: static(1030, 110, 200),
    4: static(250, 110, 200),
    9: static(640, 110, 200),
}


def scene_tracks() -> dict[str, dict[int, list[FrameBox]]]:
    """Three bowls; a big and a small red block and a blue block carried into bowls in turn; a
    static glass; a 2-frame noise track; hand tracks (ignored)."""
    return {
        "red block": {
            3: carried((300, 520), (250, 110), 20, 24, 90),
            8: carried((900, 560), (1030, 115), 50, 54, 60),
            11: static(100, 600, 60, frames=[40, 41]),
        },
        "blue block": {0: carried((600, 600), (230, 95), 70, 74, 88)},
        "glass": {5: static(640, 400, 70)},
        "bowl": BOWLS,
        "hand": {1: static(700, 650, 150), 2: static(10, 10, 20, frames=[3])},
    }


def scene_discovery() -> Discovery:
    return Discovery(
        objects=[
            DiscoveredObject(concept="red block", attrs=RED, count=2),
            DiscoveredObject(concept="blue block", attrs=BLUE_LARGE),
            DiscoveredObject(concept="glass", attrs=GLASS),
        ],
        container_concept="bowl",
    )


def run(tracks: dict[str, dict[int, list[FrameBox]]], discovery: Discovery, **kw: Any):
    args: dict[str, Any] = {"fps": 10.0, "frames": 100, "width": W, "height": H, **kw}
    return ts.summarize(tracks, discovery, container_concept="bowl", **args)


def by_id(summary: TrackSummary) -> dict[str, TrackedObject]:
    return {o.track_id: o for o in summary.objects}


# ---------------------------------------------------------------- summarize
def test_summarize_moves_bins_order_and_sizes() -> None:
    s = run(scene_tracks(), scene_discovery())
    assert (s.fps, s.frames, s.width, s.height) == (10.0, 100, W, H)
    assert [o.track_id for o in s.objects] == [
        "red block#3",
        "red block#8",
        "blue block#0",
        "glass#5",
    ]
    o = by_id(s)
    big, small, blue, glass = o["red block#3"], o["red block#8"], o["blue block#0"], o["glass#5"]
    assert big.start_px == (300.0, 520.0) and big.end_px == (250.0, 110.0)
    assert [(x.moved, x.end_bin, x.move_order, x.first_move_frame) for x in (big, small, blue)] == [
        (True, "left_bin", 0, 21),
        (True, "right_bin", 1, 51),
        (True, "left_bin", 2, 71),
    ]
    assert (glass.moved, glass.end_bin, glass.move_order, glass.first_move_frame) == (
        False,
        None,
        None,
        None,
    )
    assert big.concept == "red block"
    assert big.attrs == attrs(size="large", color="red", shape="cube", fragile=False)
    assert small.attrs.size == "small"
    assert blue.attrs == BLUE_LARGE  # a discovered size is kept
    assert glass.attrs == GLASS  # a single glass: size stays unknown
    assert "ignored 1 track(s) seen on fewer than 3 frames" in s.notes
    assert (
        "bins: left_bin=bowl#4 (x=250), middle_bin=bowl#9 (x=640), right_bin=bowl#17 (x=1030)"
        in (s.notes)
    )
    assert "size from box area (same color and shape): red block#3 large, red block#8 small" in (
        s.notes
    )
    assert not any("SAM tracked" in n for n in s.notes)  # counts match the discovery
    assert TrackSummary.model_validate_json(s.model_dump_json()) == s


def test_summarize_two_bowls_uses_frame_thirds() -> None:
    tracks = {
        "red block": {
            0: carried((300, 500), (640, 110), 10, 14, 80),  # ends between the bowls
            1: carried((900, 500), (1030, 115), 30, 34, 80),  # ends in the right bowl
            2: static(100, 600, 80),  # never moves
        },
        "bowl": {0: static(250, 110, 200), 1: static(1030, 110, 200)},
    }
    s = run(tracks, Discovery(objects=[DiscoveredObject(concept="red block", attrs=RED)]))
    o = by_id(s)
    assert o["red block#0"].end_bin == "middle_bin"  # x = 640 is in the middle third
    assert o["red block#1"].end_bin == "right_bin"
    assert o["red block#2"].end_bin is None
    assert any(n.startswith("found 2 bowl(s), need 3") for n in s.notes)
    assert "bins: left_bin=bowl#0 (x=250), right_bin=bowl#1 (x=1030)" in s.notes
    assert "'red block': SAM tracked 3, discovery counted 1" in s.notes


def test_summarize_without_bowls_maps_moved_objects_by_thirds() -> None:
    tracks = {
        "red block": {
            0: carried((300, 500), (1200, 100), 10, 14, 80),
            1: carried((900, 500), (150, 100), 30, 34, 80),
            2: static(1200, 600, 80),
        }
    }
    discovery = Discovery(objects=[DiscoveredObject(concept="red block", attrs=RED, count=3)])
    o = by_id(run(tracks, discovery))
    assert o["red block#0"].end_bin == "right_bin"
    assert o["red block#1"].end_bin == "left_bin"
    assert o["red block#2"].end_bin is None  # unmoved objects are not guessed into a bin


def test_summarize_extra_and_duplicate_bowls() -> None:
    tracks = {
        "bowl": {
            0: static(250, 110, 200),
            1: static(640, 110, 200),
            5: static(642, 112, 200, frames=range(95)),  # bowl 1 tracked twice
            2: static(1030, 110, 200, frames=range(90)),
            3: static(1180, 600, 120, frames=range(40)),  # seen least: not a bin
        },
        "red block": {7: carried((300, 500), (1035, 100), 10, 14, 80)},
    }
    s = run(tracks, Discovery(objects=[DiscoveredObject(concept="red block", attrs=RED)]))
    assert "merged 1 duplicate bowl track(s)" in s.notes
    assert "found 4 bowls; using the 3 seen on the most frames" in s.notes
    assert (
        "bins: left_bin=bowl#0 (x=250), middle_bin=bowl#1 (x=640), right_bin=bowl#2 (x=1030)"
        in (s.notes)
    )
    assert s.objects[0].end_bin == "right_bin"


def test_size_inference_needs_a_clear_gap() -> None:
    tracks = {
        "red block": {0: static(300, 500, 80), 1: static(600, 500, 74)},
        "bowl": BOWLS,
    }
    discovery = Discovery(objects=[DiscoveredObject(concept="red block", attrs=RED, count=2)])
    s = run(tracks, discovery)
    assert [o.attrs.size for o in s.objects] == [None, None]
    assert not any(n.startswith("size from box area") for n in s.notes)
    assert "no object moved more than 5% of the frame width" in s.notes


def test_size_inference_falls_back_to_same_shape() -> None:
    tracks = {
        "red block": {0: static(300, 500, 90)},
        "blue block": {1: static(600, 500, 88)},
        "green block": {2: static(900, 500, 60)},
        "bowl": BOWLS,
    }
    discovery = Discovery(
        objects=[
            DiscoveredObject(concept="red block", attrs=attrs(size="large", **RED_KW)),
            DiscoveredObject(concept="blue block", attrs=attrs(color="blue", shape="cube")),
            DiscoveredObject(concept="green block", attrs=attrs(color="green", shape="cube")),
        ]
    )
    s = run(tracks, discovery)
    o = by_id(s)
    assert [o[k].attrs.size for k in ("red block#0", "blue block#1", "green block#2")] == [
        "large",
        "large",
        "small",
    ]
    assert "size from box area (same shape): blue block#1 large, green block#2 small" in s.notes


def test_put_back_object_is_not_moved() -> None:
    lift = carried((400, 500), (400, 300), 10, 12, 80)
    back = carried((400, 300), (400, 500), 30, 32, 80)
    path = lift[:30] + back[30:]
    s = run({"red block": {0: path}, "bowl": BOWLS}, scene_discovery())
    (obj,) = s.objects
    assert (obj.moved, obj.move_order, obj.end_bin) == (False, None, None)
    assert obj.first_move_frame == 11  # it was disturbed, then put back


def test_frame_size_and_count_fallbacks_and_unknown_concepts() -> None:
    tracks = {"Red  Block": {0: static(300, 500, 80)}, "mug": {4: static(800, 500, 80)}}
    s = ts.summarize(
        tracks, scene_discovery(), container_concept="bowl", fps=0, frames=0, width=0, height=0
    )
    assert (s.width, s.height, s.frames, s.fps) == (W, H, 100, 10.0)
    o = by_id(s)
    assert o["Red Block#0"].attrs == RED  # concepts match case- and space-insensitively
    assert o["mug#4"].attrs == ObjectAttrs()
    assert "'mug' was tracked but not discovered: attributes unknown" in s.notes
    assert "'glass': SAM tracked 0, discovery counted 1" in s.notes
    assert any(n.startswith("found 0 bowl(s)") for n in s.notes)


def test_bowls_and_bins_matched_as_objects_are_dropped() -> None:
    tracks = {
        "blue block": {
            0: carried((600, 600), (250, 110), 20, 24, 80),  # the real blue block
            1: static(1030, 110, 230),  # a blue bin that SAM also calls a block: surplus, 5x
        },
        "red block": {2: static(250, 110, 200)},  # overlaps the left bowl: it is the bowl
        "glass": {3: static(640, 400, 120)},  # bigger than the blocks, but expected: kept
        "bowl": {4: static(250, 110, 200), 5: static(640, 110, 200)},
    }
    discovery = Discovery(
        objects=[
            DiscoveredObject(concept="blue block", attrs=BLUE_LARGE),
            DiscoveredObject(concept="red block", attrs=RED),
            DiscoveredObject(concept="glass", attrs=GLASS),
        ]
    )
    s = run(tracks, discovery)
    assert [o.track_id for o in s.objects] == ["blue block#0", "glass#3"]
    assert any(n.startswith("dropped red block#2: it is the left_bin bowl") for n in s.notes)
    assert any(n.startswith("dropped blue block#1: an extra static 'blue block'") for n in s.notes)
    assert "'red block': SAM tracked 0, discovery counted 1" in s.notes  # counted after the drop


def test_summarize_empty() -> None:
    s = run({}, Discovery(objects=[]))
    assert s.objects == [] and s.width == W


# ---------------------------------------------------------------- discover (fake Wonder)
@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(settings, "SHOWTELL_DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(settings, "MODEL_API_KEY", "")

    def no_upload(*a: Any, **k: Any) -> str:
        raise AssertionError("tests must not upload files")

    monkeypatch.setattr(files, "upload", no_upload)
    return tmp_path


class FakeWonder:
    """Stands in for wonder.call_json: records each request and returns a fixed result."""

    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        model_cls: type,
        instructions: str,
        content: list[dict[str, Any]],
        effort: str = "high",
        max_output_tokens: int = 8000,
        **kw: Any,
    ) -> Any:
        self.calls.append(
            {
                "model_cls": model_cls,
                "instructions": instructions,
                "content": content,
                "effort": effort,
                **kw,
            }
        )
        return self.result


def write_keyframes(folder: Path, n: int = 8) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(n):
        p = folder / f"keyframe_{i:02d}.jpg"
        cv2.imwrite(str(p), np.full((48, 64, 3), 20 * i, np.uint8))
        paths.append(p)
    return paths


def test_discover_request_and_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    raw = Discovery(
        objects=[
            DiscoveredObject(concept="Large  Red block", attrs=attrs(size="large", **RED_KW)),
            DiscoveredObject(concept="small red block", attrs=attrs(size="small", **RED_KW)),
            DiscoveredObject(concept="glass", attrs=GLASS, count=0),
            DiscoveredObject(concept="white bowl", attrs=attrs()),
            DiscoveredObject(concept="hand", attrs=attrs()),
            DiscoveredObject(concept="yellow cylinder", attrs=attrs(color="yellow")),
            DiscoveredObject(concept="Yellow cylinder", attrs=attrs(color="yellow")),
            DiscoveredObject(concept="small block", attrs=attrs(size="small", shape="cube")),
        ],
        container_concept=" Bowl ",
        notes="two sizes of red",
    )
    fake = FakeWonder(raw)
    monkeypatch.setattr(wonder, "call_json", fake)
    kfs = write_keyframes(tmp_path / "kf")
    d = ts.discover(kfs, tag="job1:discover")

    (call,) = fake.calls
    assert call["model_cls"] is Discovery
    assert call["effort"] == "medium" and call["tag"] == "job1:discover"
    assert call["instructions"] == DISCOVER_INSTRUCTIONS.format(
        vocabulary=json.dumps(
            {
                "size": ["small", "large"],
                "color": ["red", "blue", "green", "yellow", "clear"],
                "shape": ["cube", "cylinder", "glass"],
                "fragile": [True, False],
            }
        ),
        schema=json.dumps(Discovery.model_json_schema()),
    )
    content = call["content"]
    assert content[0]["type"] == "input_text"
    assert content[1:] == [wonder.image_part(kfs[i]) for i in (0, 2, 5, 7)]  # evenly spaced

    assert d.container_concept == "bowl"
    assert [(o.concept, o.attrs.size, o.count) for o in d.objects] == [
        ("red block", "large", 1),
        ("red block", "small", 1),
        ("glass", None, 1),
        ("yellow cylinder", None, 2),
        ("small block", "small", 1),  # no color word: the size word stays for SAM
    ]
    assert d.notes.startswith("two sizes of red")
    assert "dropped object entry 'white bowl' (a container, a hand or empty)" in d.notes
    assert "dropped object entry 'hand' (a container, a hand or empty)" in d.notes
    assert "SAM concept 'large red block' -> 'red block' (SAM gets color and shape only)" in d.notes
    assert "merged a repeated 'yellow cylinder' entry" in d.notes


def test_discover_needs_keyframes() -> None:
    with pytest.raises(ValueError):
        ts.discover([])


def test_sizes_of_merged_concept_come_from_box_area() -> None:
    """Two sizes discovered under one SAM phrase: summarize sizes each track by area."""
    d = ts.clean_discovery(
        Discovery(
            objects=[
                DiscoveredObject(concept="large red block", attrs=attrs(size="large", **RED_KW)),
                DiscoveredObject(concept="small red block", attrs=attrs(size="small", **RED_KW)),
            ]
        )
    )
    tracks = {"red block": {3: static(300, 500, 90), 8: static(600, 500, 60)}, "bowl": BOWLS}
    o = by_id(run(tracks, d))
    assert (o["red block#3"].attrs.size, o["red block#8"].attrs.size) == ("large", "small")
    assert ts.scene_objects_from_discovery(d)[0] == ["red_cube_large", "red_cube_small"]


# ---------------------------------------------------------------- build_track_summary
def test_build_track_summary_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clip = video_prep.make_test_clip(tmp_path / "demo.mp4", seconds=3)
    discovery = Discovery(
        objects=[
            DiscoveredObject(concept="red block", attrs=RED),
            DiscoveredObject(concept="glass", attrs=GLASS),
        ]
    )
    fake = FakeWonder(discovery)
    monkeypatch.setattr(wonder, "call_json", fake)
    seen: dict[str, Any] = {}
    w, h, n = 960, 720, range(30)  # 640x480 @ 25 fps normalizes to 960x720 @ 10 fps

    def fake_track(video_path: Path, concepts: list[str], **kw: Any) -> dict[str, Any]:
        seen.update(video=Path(video_path), concepts=list(concepts), **kw)
        return {
            "red block": {
                2: [fb(f, 200 + (f > 10) * 560, 400 - (f > 10) * 320, 60, w, h) for f in n]
            },
            "glass": {6: [fb(f, 480, 300, 50, w, h) for f in n]},
            "bowl": {
                i: [fb(f, x, 80, 150, w, h) for f in n] for i, x in enumerate((180, 480, 780))
            },
            "hand": {},
        }

    monkeypatch.setattr(sam, "track", fake_track)
    out = tmp_path / "job"
    summary, d, kfs = ts.build_track_summary(clip, out, tag="job7")

    assert seen["concepts"] == ["red block", "glass", "bowl", "hand"]
    assert seen["video"] == out / "prep" / "normalized.mp4" and seen["video"].is_file()
    assert seen["tag"] == "job7:track" and fake.calls[0]["tag"] == "job7:discover"
    assert [p.parent for p in kfs] == [out / "prep" / "keyframes"] * 8
    assert all(p.is_file() for p in kfs)
    assert (summary.width, summary.height, summary.frames, summary.fps) == (960, 720, 30, 10.0)
    red, glass = summary.objects
    assert (red.track_id, red.moved, red.end_bin, red.first_move_frame) == (
        "red block#2",
        True,
        "right_bin",
        11,
    )
    assert (glass.moved, glass.end_bin) == (False, None)
    assert Discovery.model_validate_json((out / "discovery.json").read_text()) == d
    assert TrackSummary.model_validate_json((out / "track_summary.json").read_text()) == summary


# ---------------------------------------------------------------- discovery -> sim objects
def obj(concept: str, count: int = 1, **kw: Any) -> DiscoveredObject:
    return DiscoveredObject(concept=concept, attrs=attrs(**kw), count=count)


def check_ids(ids: list[str]) -> None:
    assert len(set(ids)) == len(ids)
    for i in ids:
        parse_object_id(i)


def test_scene_objects_counts_cylinders_and_one_glass() -> None:
    d = Discovery(
        objects=[
            obj("red block", 2, size="large", color="red", shape="cube"),
            obj("blue block", size="small", color="blue", shape="cube"),
            obj("glass", 2, color="clear", shape="glass", fragile=True),
            obj("yellow cylinder", color="yellow", shape="cylinder"),
            obj("tumbler", fragile=True),
        ]
    )
    ids, notes = ts.scene_objects_from_discovery(d)
    assert ids == [
        "red_cube_large",
        "red_cube_large_2",
        "blue_cube_small",
        "clear_glass_large",
        "yellow_cylinder_large",
    ]
    check_ids(ids)
    assert "'glass': the simulator has one glass; dropped 1 more" in notes
    assert "'tumbler': the simulator has one glass; dropped 1 more" in notes


def test_scene_objects_map_colors_to_distinct_sim_colors() -> None:
    d = Discovery(
        objects=[
            obj("red block", color="red", shape="cube"),
            obj("orange block", shape="cube"),
            obj("purple block", shape="cube"),
            obj("block", shape="cube"),
        ]
    )
    ids, notes = ts.scene_objects_from_discovery(d)
    # orange -> red is taken -> green; purple -> blue; no color and all taken -> red again
    assert ids == ["red_cube_large", "green_cube_large", "blue_cube_large", "red_cube_large_2"]
    check_ids(ids)
    assert len([n for n in notes if "only red, blue and green cubes" in n]) == 3
    assert ts.scene_objects_from_discovery(Discovery(objects=[obj("orange cube")]))[0] == [
        "red_cube_large"
    ]
    assert ts.scene_objects_from_discovery(Discovery(objects=[obj("violet block")]))[0] == [
        "blue_cube_large"
    ]
    yellow = Discovery(objects=[obj("yellow block", color="yellow", shape="cube", size="small")])
    assert ts.scene_objects_from_discovery(yellow)[0] == ["red_cube_small"]


def test_scene_objects_infer_shapes_and_flag_approximations() -> None:
    d = Discovery(
        objects=[
            obj("red marker", color="red"),
            obj("green glue stick"),
            obj("thing"),
            obj("blue mug", shape="cube", fragile=True),
            obj("tiny green block"),
        ]
    )
    ids, notes = ts.scene_objects_from_discovery(d)
    assert ids == [
        "yellow_cylinder_large",
        "yellow_cylinder_large_2",
        "red_cube_large",
        "blue_cube_large",
        "green_cube_small",
    ]
    check_ids(ids)
    assert "'red marker': only yellow cylinders exist in the simulator" in notes
    assert "'green glue stick': only yellow cylinders exist in the simulator" in notes
    assert "'thing': unknown shape, simulated as a cube" in notes
    assert any(n.startswith("'blue mug': only the glass is fragile") for n in notes)


def test_scene_objects_limit_keeps_the_glass() -> None:
    d = Discovery(
        objects=[
            obj("red block", 4, color="red", shape="cube"),
            obj("blue block", 3, color="blue", shape="cube"),
            obj("glass", color="clear", shape="glass", fragile=True),
        ]
    )
    ids, notes = ts.scene_objects_from_discovery(d)
    assert ids == [
        "red_cube_large",
        "red_cube_large_2",
        "red_cube_large_3",
        "red_cube_large_4",
        "blue_cube_large",
        "clear_glass_large",
    ]
    assert any(n.startswith("kept 6 of 8 objects") for n in notes)
    assert ts.scene_objects_from_discovery(d, max_objects=1)[0] == ["clear_glass_large"]


def test_scene_objects_never_empty() -> None:
    ids, notes = ts.scene_objects_from_discovery(Discovery(objects=[]))
    assert ids == ["red_cube_large"]
    assert notes == ["no objects discovered; using one red_cube_large"]


def test_scene_objects_take_unknown_sizes_from_the_summary() -> None:
    d = scene_discovery()  # "red block" x2 with unknown size
    assert ts.scene_objects_from_discovery(d)[0] == [
        "red_cube_large",
        "red_cube_large_2",
        "blue_cube_large",
        "clear_glass_large",
    ]
    summary = run(scene_tracks(), d)
    ids, _ = ts.scene_objects_from_discovery(d, summary=summary)
    assert ids == ["red_cube_large", "red_cube_small", "blue_cube_large", "clear_glass_large"]
