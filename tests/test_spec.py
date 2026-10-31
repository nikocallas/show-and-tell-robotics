"""Spec generation with a fake Wonder, plus the deterministic spec checks (offline)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import cv2
import numpy as np
import openai
import pytest

from showtell.agent import spec as sp
from showtell.agent.prompts import SPEC_INSTRUCTIONS, SPEC_NO_VIDEO_NOTE
from showtell.agent.schemas import (
    VOCABULARY,
    ObjectAttrs,
    TaskSpec,
    TrackedObject,
    TrackSummary,
)
from showtell.config import settings
from showtell.meta import files, wonder
from showtell.sim.scene import get_task, load_tasks

TASK_IDS = list(load_tasks())
SENTENCE = "Sort by size, and don't touch the glass."


def reference_spec(task_id: str) -> TaskSpec:
    return TaskSpec.model_validate({**get_task(task_id).reference, "rationale": "reference"})


def object_ids(task_id: str) -> list[str]:
    return [o.id for o in get_task(task_id).objects]


def make(rules: list[dict[str, Any]], checks: list[dict[str, Any]]) -> TaskSpec:
    return TaskSpec.model_validate({"rules": rules, "checks": checks, "rationale": "test"})


def in_bin(cid: str, select: dict[str, Any], bin_name: str) -> dict[str, Any]:
    return {"id": cid, "kind": "in_bin", "select": select, "bin": bin_name, "text": cid}


def stay(cid: str, kind: str, select: dict[str, Any]) -> dict[str, Any]:
    return {"id": cid, "kind": kind, "select": select, "text": cid}


def bad_request(message: str) -> openai.BadRequestError:
    body = {"message": message, "type": "invalid_request_error", "param": None, "code": None}
    response = SimpleNamespace(request=None, status_code=400, headers={})
    return openai.BadRequestError(f"Error code: 400 - {message}", response=response, body=body)


class FakeWonder:
    """Stands in for wonder.call_json: records requests, optionally rejects video parts."""

    def __init__(self, result: Any, *, reject_video: bool = False) -> None:
        self.result = result
        self.reject_video = reject_video
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
                "max_output_tokens": max_output_tokens,
                **kw,
            }
        )
        if self.reject_video and any(p["type"] == "input_video" for p in content):
            raise bad_request("input_video is not supported for this model")
        return self.result


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(settings, "SHOWTELL_DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(settings, "MODEL_API_KEY", "")

    def no_upload(*a: Any, **k: Any) -> str:
        raise AssertionError("tests must not upload files")

    monkeypatch.setattr(files, "upload", no_upload)
    return tmp_path


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeWonder:
    f = FakeWonder(reference_spec("task03_size_glass"))
    monkeypatch.setattr(wonder, "call_json", f)
    return f


@pytest.fixture
def keyframes(tmp_path: Path) -> list[Path]:
    paths = []
    for i in range(3):
        p = tmp_path / f"keyframe_{i:02d}.jpg"
        cv2.imwrite(str(p), np.full((48, 64, 3), 60 * i, np.uint8))
        paths.append(p)
    return paths


@pytest.fixture
def summary() -> TrackSummary:
    cube = {"color": "red", "shape": "cube", "fragile": False}
    return TrackSummary(
        objects=[
            TrackedObject(
                track_id="red block#3",
                concept="red block",
                attrs=ObjectAttrs(size="large", **cube),
                moved=True,
                start_px=(300.0, 520.0),
                end_px=(250.0, 110.0),
                end_bin="left_bin",
                move_order=0,
                first_move_frame=21,
            ),
            TrackedObject(
                track_id="glass#5",
                concept="glass",
                attrs=ObjectAttrs(color="clear", shape="glass", fragile=True),
                moved=False,
                start_px=(640.0, 400.0),
                end_px=(640.0, 400.0),
            ),
        ],
        frames=100,
        width=1280,
        height=720,
        notes=["bins: left_bin=bowl#4 (x=250)"],
    )


def kinds(content: list[dict[str, Any]]) -> list[str]:
    return [p["type"] for p in content]


# ---------------------------------------------------------------- vocabulary and prompt
def test_object_vocabulary() -> None:
    v = sp.object_vocabulary(object_ids("task03_size_glass"))
    assert v["allowed"] == VOCABULARY
    assert v["objects_present"][0] == {
        "id": "red_cube_large",
        "size": "large",
        "color": "red",
        "shape": "cube",
        "fragile": False,
    }
    assert v["objects_present"][-1] == {
        "id": "clear_glass_large",
        "size": "large",
        "color": "clear",
        "shape": "glass",
        "fragile": True,
    }
    with pytest.raises(ValueError):
        sp.object_vocabulary(["red_cube_large", "red_cube_large"])
    with pytest.raises(ValueError):
        sp.object_vocabulary(["purple_cone_huge"])


def test_spec_instructions() -> None:
    video = sp.spec_instructions(sentence_only=False)
    assert video == SPEC_INSTRUCTIONS.format(
        schema=json.dumps(TaskSpec.model_json_schema()), vocabulary=json.dumps(VOCABULARY)
    )
    assert sp.spec_instructions(sentence_only=True) == f"{video}\n\n{SPEC_NO_VIDEO_NOTE}"


# ---------------------------------------------------------------- make_spec
def test_make_spec_sentence_only_sends_no_media(fake: FakeWonder, keyframes: list[Path]) -> None:
    ids = object_ids("task03_size_glass")
    spec, media = sp.make_spec(
        SENTENCE,
        ids,
        track_summary=None,
        keyframes=keyframes,
        video_file_id="file-x",
        tag="job1:spec",
    )
    assert spec == reference_spec("task03_size_glass") and media == "none"
    (call,) = fake.calls
    assert call["model_cls"] is TaskSpec
    assert (call["effort"], call["tag"]) == ("high", "job1:spec")  # passed through as is
    assert call["max_output_tokens"] == sp.SPEC_MAX_TOKENS
    assert call["instructions"].endswith(SPEC_NO_VIDEO_NOTE)
    assert kinds(call["content"]) == ["input_text"]  # no video, no keyframes
    text = call["content"][0]["text"]
    assert SENTENCE in text
    assert json.dumps(sp.object_vocabulary(ids), indent=1) in text
    assert "Demonstration summary" not in text and "follows" not in text


def test_make_spec_video_mode_sends_summary_and_video(
    fake: FakeWonder, keyframes: list[Path], summary: TrackSummary
) -> None:
    spec, media = sp.make_spec(
        SENTENCE,
        object_ids("task03_size_glass"),
        track_summary=summary,
        keyframes=keyframes,
        video_file_id="file-abc",
    )
    assert media == "video"
    (call,) = fake.calls
    assert SPEC_NO_VIDEO_NOTE not in call["instructions"]
    assert call["tag"] == "spec"
    assert call["content"][1:] == [{"type": "input_video", "file_id": "file-abc"}]
    text = call["content"][0]["text"]
    assert summary.model_dump_json(indent=1) in text
    assert '"track_id": "red block#3"' in text
    assert "The demonstration video (or keyframes from it, in time order) follows." in text


def test_make_spec_falls_back_to_keyframes(
    monkeypatch: pytest.MonkeyPatch, keyframes: list[Path], summary: TrackSummary
) -> None:
    fake = FakeWonder(reference_spec("task03_size_glass"), reject_video=True)
    monkeypatch.setattr(wonder, "call_json", fake)
    spec, media = sp.make_spec(
        SENTENCE,
        object_ids("task03_size_glass"),
        track_summary=summary,
        keyframes=keyframes,
        video_file_id="file-abc",
    )
    assert media == "keyframes" and spec == reference_spec("task03_size_glass")
    first, second = fake.calls
    assert kinds(first["content"]) == ["input_text", "input_video"]
    assert kinds(second["content"]) == ["input_text"] + ["input_image"] * 3
    assert second["content"][1:] == [wonder.image_part(k) for k in keyframes]


def test_make_spec_keyframes_only(
    fake: FakeWonder, keyframes: list[Path], summary: TrackSummary
) -> None:
    _, media = sp.make_spec(
        SENTENCE, object_ids("task03_size_glass"), track_summary=summary, keyframes=keyframes
    )
    assert media == "keyframes"
    assert kinds(fake.calls[0]["content"]) == ["input_text"] + ["input_image"] * 3


def test_make_spec_summary_without_media(fake: FakeWonder, summary: TrackSummary) -> None:
    _, media = sp.make_spec(SENTENCE, object_ids("task03_size_glass"), track_summary=summary)
    assert media == "none"
    (call,) = fake.calls
    assert kinds(call["content"]) == ["input_text"]
    assert summary.model_dump_json(indent=1) in call["content"][0]["text"]
    assert "follows" not in call["content"][0]["text"]
    assert SPEC_NO_VIDEO_NOTE not in call["instructions"]


def test_make_spec_rejects_empty_inputs(fake: FakeWonder) -> None:
    with pytest.raises(ValueError):
        sp.make_spec("  ", ["red_cube_large"], track_summary=None)
    with pytest.raises(ValueError):
        sp.make_spec(SENTENCE, [], track_summary=None)
    assert fake.calls == []


# ---------------------------------------------------------------- validate_spec / ensure_checks
@pytest.mark.parametrize("task_id", TASK_IDS)
def test_reference_specs_are_clean(task_id: str) -> None:
    spec, ids = reference_spec(task_id), object_ids(task_id)
    assert sp.validate_spec(spec, ids) == []
    new, notes = sp.ensure_checks(spec, ids)
    assert notes == [] and new == spec


def test_validate_flags_selectors_matching_nothing() -> None:
    spec = make(
        [{"select": {"color": "yellow"}, "place_in": "middle_bin"}],
        [in_bin("yellow_middle", {"color": "yellow"}, "middle_bin")],
    )
    assert sp.validate_spec(spec, object_ids("task01_size")) == [
        "rule 1 (yellow objects -> middle_bin) selects no object in the scene",
        "check yellow_middle selects no object in the scene (yellow objects)",
    ]


def test_validate_flags_rules_without_checks() -> None:
    spec = make(
        [
            {"select": {"size": "large", "fragile": False}, "place_in": "left_bin"},
            {"select": {"size": "small", "fragile": False}, "place_in": "right_bin"},
            {"select": {"shape": "glass"}, "leave": True},
        ],
        [
            in_bin("large_left", {"size": "large", "fragile": False}, "left_bin"),
            in_bin("small_left", {"size": "small", "fragile": False}, "left_bin"),  # wrong bin
            stay("glass_upright", "upright", {"shape": "glass"}),  # upright alone is not enough
        ],
    )
    warnings = sp.validate_spec(spec, object_ids("task03_size_glass"))
    assert (
        "rule 2 (non-fragile small objects -> right_bin) has no matching check "
        "(needs an in_bin check for right_bin)" in warnings
    )
    assert (
        "rule 3 (glasses -> leave) has no matching check (needs an untouched or unmoved check)"
        in warnings
    )
    assert "check small_left expects red_cube_small in left_bin, but the rules send it to " in (
        "\n".join(warnings)
    )


def test_validate_flags_fragile_moves_and_contradictions() -> None:
    """{size: large} also selects the glass (it is large): the classic spec bug."""
    spec = make(
        [
            {"select": {"size": "large"}, "place_in": "left_bin"},
            {"select": {"size": "small", "fragile": False}, "place_in": "right_bin"},
            {"select": {"shape": "glass"}, "leave": True},
        ],
        [
            in_bin("large_left", {"size": "large"}, "left_bin"),
            in_bin("small_right", {"size": "small", "fragile": False}, "right_bin"),
            stay("glass_untouched", "untouched", {"shape": "glass"}),
        ],
    )
    assert sp.validate_spec(spec, object_ids("task03_size_glass")) == [
        "rule 1 (large objects -> left_bin) moves fragile clear_glass_large; add fragile: false "
        "unless the sentence asks to move it",
        "clear_glass_large is both moved (rule 1) and left in place (rule 3)",
        "check glass_untouched expects clear_glass_large to stay untouched, but rule 1 moves it",
    ]


def test_validate_flags_check_contradicting_a_leave_rule() -> None:
    data = get_task("task03_size_glass").reference
    checks = [dict(c) for c in data["checks"]]
    checks[0]["select"] = {"size": "large"}  # dropped fragile: false -> includes the glass
    spec = TaskSpec.model_validate({**data, "checks": checks, "rationale": "test"})
    assert sp.validate_spec(spec, object_ids("task03_size_glass")) == [
        "check large_left expects clear_glass_large in left_bin, but rule 3 leaves it in place"
    ]


def test_validate_flags_conflicting_bins_and_unplanned_checks() -> None:
    spec = make(
        [
            {"select": {"color": "red"}, "place_in": "left_bin"},
            {"select": {"size": "large"}, "place_in": "middle_bin"},
        ],
        [
            in_bin("red_left", {"color": "red"}, "left_bin"),
            in_bin("large_middle", {"size": "large"}, "middle_bin"),
            in_bin("green_right", {"color": "green"}, "right_bin"),
        ],
    )
    assert sp.validate_spec(spec, object_ids("task01_size")) == [
        "red_cube_large is sent to different bins: left_bin by rule 1; middle_bin by rule 2",
        "check green_right expects green_cube_small in right_bin, but no rule moves it",
    ]


def test_ensure_checks_adds_only_missing_checks() -> None:
    spec = make(
        [
            {"select": {"size": "large", "fragile": False}, "place_in": "left_bin"},
            {"select": {"size": "small", "fragile": False}, "place_in": "right_bin"},
            {"select": {"shape": "glass"}, "leave": True},
        ],
        [
            in_bin("large_left", {"size": "large", "fragile": False}, "left_bin"),
            stay("glass_upright", "upright", {"shape": "glass"}),
        ],
    )
    ids = object_ids("task03_size_glass")
    new, notes = sp.ensure_checks(spec, ids)
    assert new.checks[:2] == spec.checks  # existing checks untouched, in order
    added = new.checks[2:]
    assert [(c.id, c.kind, c.select.as_dict(), c.bin, c.text) for c in added] == [
        (
            "small_nonfragile_in_right",
            "in_bin",
            {"size": "small", "fragile": False},
            "right_bin",
            "All non-fragile small objects end in the right bin.",
        ),
        (
            "glass_unmoved",
            "unmoved",
            {"shape": "glass"},
            None,
            "All glasses stay where they started.",
        ),
    ]
    assert len(notes) == 2 and notes[0].startswith("added check small_nonfragile_in_right")
    assert new.rules == spec.rules and new.rationale == spec.rationale
    assert sp.validate_spec(new, ids) == []
    again, more = sp.ensure_checks(new, ids)
    assert more == [] and again == new
    assert spec.checks[1].id == "glass_upright" and len(spec.checks) == 2  # input not mutated


def test_ensure_checks_partial_coverage_unique_ids_and_empty_selectors() -> None:
    spec = make(
        [
            {"select": {"color": "red"}, "place_in": "left_bin"},
            {"select": {"color": "yellow"}, "place_in": "middle_bin"},
        ],
        [in_bin("red_in_left", {"color": "red", "size": "large"}, "left_bin")],
    )
    new, notes = sp.ensure_checks(spec, object_ids("task01_size"))
    assert [c.id for c in new.checks] == ["red_in_left", "red_in_left_2", "yellow_in_middle"]
    assert new.checks[1].select.as_dict() == {"color": "red"}  # covers the small red cube too
    assert len(notes) == 2

    everything = make([{"select": {}, "place_in": "middle_bin"}], [])
    (check,) = sp.ensure_checks(everything, object_ids("task01_size"))[0].checks
    assert (check.id, check.text) == ("all_in_middle", "Every object ends in the middle bin.")
    leave_all = make([{"select": {}, "leave": True}], [])
    (check,) = sp.ensure_checks(leave_all, object_ids("task01_size"))[0].checks
    assert (check.id, check.kind, check.text) == (
        "all_unmoved",
        "unmoved",
        "Every object stays where it started.",
    )


def _in_bin_ids(spec: TaskSpec, ids: list[str]) -> dict[str, set[str]]:
    """check id -> object ids each in_bin check selects."""
    objs = sp._objects(ids)
    return {c.id: {o.id for o in sp._select(objs, c.select)} for c in spec.checks if c.bin}


def test_ensure_checks_never_puts_kept_objects_in_a_bin() -> None:
    """A broad place rule plus a leave rule or a stay check: the added check leaves those out."""
    ids = object_ids("task04_clear")  # red/blue cubes, yellow cylinder, glass
    glass = "clear_glass_large"
    everything_but_glass = [
        {"select": {}, "place_in": "middle_bin"},
        {"select": {"shape": "glass"}, "leave": True},
    ]
    untouched = stay("glass_untouched", "untouched", {"shape": "glass"})
    covered = make(everything_but_glass, [in_bin("all_middle", {"fragile": False}, "middle_bin"),
                                          untouched])  # fmt: skip
    new, notes = sp.ensure_checks(covered, ids)
    assert (new, notes) == (covered, [])  # was: an in_bin check for every object, glass included

    uncovered = make(everything_but_glass, [untouched])
    new, notes = sp.ensure_checks(uncovered, ids)
    (added,) = new.checks[1:]
    assert (added.id, added.select.as_dict(), added.bin) == (
        "nonfragile_in_middle", {"fragile": False}, "middle_bin"
    )  # fmt: skip
    assert notes == [f"added check nonfragile_in_middle ({added.text}) for rule 1 (every object "
                     "-> middle_bin)"]  # fmt: skip
    assert not any(glass in objs for objs in _in_bin_ids(new, ids).values())

    by_check_only = make([{"select": {}, "place_in": "middle_bin"}], [untouched])
    new, _ = sp.ensure_checks(by_check_only, ids)
    assert [c.select.as_dict() for c in new.checks[1:]] == [{"fragile": False}]

    all_kept = make([{"select": {"shape": "glass"}, "place_in": "left_bin"},
                     {"select": {"shape": "glass"}, "leave": True}], [untouched])  # fmt: skip
    new, notes = sp.ensure_checks(all_kept, ids)
    assert new.checks == all_kept.checks and notes == []


def test_ensure_checks_splits_a_rule_when_no_attribute_separates_kept_objects() -> None:
    ids = object_ids("task06_color_exc")  # red, green and two blue cubes
    spec = make([{"select": {}, "place_in": "middle_bin"},
                 {"select": {"color": "blue"}, "leave": True}], [])  # fmt: skip
    new, notes = sp.ensure_checks(spec, ids)
    selected = _in_bin_ids(new, ids)
    assert sorted(o for objs in selected.values() for o in objs) == [
        "green_cube_small", "red_cube_large"
    ]  # fmt: skip
    assert all(len(objs) == 1 for objs in selected.values())
    assert [c.kind for c in new.checks] == ["in_bin", "in_bin", "unmoved"] and len(notes) == 3
    assert sp.ensure_checks(new, ids)[1] == []  # idempotent


def test_unique_check_ids_stay_within_the_pattern() -> None:
    taken = {"x" * 40}
    cid = sp._unique_id("x" * 45, taken)
    assert cid == "x" * 38 + "_2" and len(cid) == 40
