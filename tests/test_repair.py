"""Repair: failure report, failure frames and the repair call, on REAL failing physics runs.

The naive size sort (no avoid=glass) runs on 4 seeds of task 3 with videos, so the failures, the
contact log and the frames all come from the simulator. Muse Wonder is replaced by a scripted fake
of `wonder.call_json`, and the sandbox (written in parallel) by a small AST checker; one test uses
the real sandbox when it exists.
"""

from __future__ import annotations

import ast
import re
import sys
import types
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

from showtell.agent import repair as repair_mod
from showtell.agent.repair import (
    RepairError,
    failure_focus,
    failure_frames,
    failure_report,
    first_sentence,
    repair,
    strip_code_fences,
)
from showtell.agent.schemas import RepairResult, TaskSpec
from showtell.sim.render import write_mp4
from showtell.sim.scene import get_task
from showtell.verify.runner import CheckResult, SeedResult, run_seeds, task_seeds

ROOT = Path(__file__).resolve().parent.parent
NAIVE = (ROOT / "examples" / "size_sort_naive.py").read_text()
GOOD = (ROOT / "examples" / "size_sort.py").read_text()
BAD = "import os\n\ndef run(scene):\n    while True:\n        pass\n"
DIAGNOSIS = (
    "The carried blocks sweep through the glass at the 0.12 m transit height. "
    "Pass avoid=glass to every pick and place."
)


# ------------------------------------------------------------------------- fixtures
@pytest.fixture(scope="module")
def naive_run(tmp_path_factory: pytest.TempPathFactory) -> tuple[TaskSpec, list[SeedResult], Path]:
    """4 real seeds of task 3 with the naive program, recorded (full.mp4 per seed)."""
    task = get_task("task03_size_glass")
    out = tmp_path_factory.mktemp("naive_task03")
    results = run_seeds(
        [o.id for o in task.objects],
        NAIVE,
        task.reference["checks"],
        task_seeds(task.index, 4),
        out_root=out,
        record=True,
    )
    spec = TaskSpec.model_validate({**task.reference, "rationale": "reference spec"})
    return spec, results, out


def _glass_failure(results: list[SeedResult]) -> SeedResult:
    return next(r for r in results if any(c.kind == "untouched" and not c.passed for c in r.checks))


def _first_glass_touch(result: SeedResult) -> dict[str, Any]:
    return min(
        (e for e in result.touch_events if e["obj"] == "clear_glass_large"), key=lambda e: e["t"]
    )


@pytest.fixture()
def fake_sandbox(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Stand-in for showtell.agent.sandbox: rejects imports, while loops and dunder names."""
    mod = types.ModuleType("showtell.agent.sandbox")

    class SandboxError(ValueError):
        pass

    def validate_program(source: str) -> ast.Module:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom, ast.While)):
                raise SandboxError(f"{type(node).__name__} is not allowed (line {node.lineno})")
            if isinstance(node, ast.Name) and node.id.startswith("__"):
                raise SandboxError(f"dunder name {node.id} is not allowed")
        return tree

    mod.SandboxError = SandboxError  # type: ignore[attr-defined]
    mod.validate_program = validate_program  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "showtell.agent.sandbox", mod)
    return mod


class FakeWonder:
    """Scripted replacement for wonder.call_json; records every call."""

    def __init__(self, *replies: RepairResult) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, model_cls: type, instructions: str, content: list, **kw: Any) -> Any:
        self.calls.append(
            {"model_cls": model_cls, "instructions": instructions, "content": list(content), **kw}
        )
        return self.replies.pop(0)


def _fake(monkeypatch: pytest.MonkeyPatch, *replies: RepairResult) -> FakeWonder:
    fake = FakeWonder(*replies)
    monkeypatch.setattr(repair_mod.wonder, "call_json", fake)
    return fake


# ------------------------------------------------------------------------- failure report
def test_naive_program_really_hits_the_glass(naive_run) -> None:
    _, results, _ = naive_run
    assert len(results) == 4
    fail = _glass_failure(results)  # raises StopIteration if physics never hit the glass
    assert fail.touch_events and _first_glass_touch(fail)["during"]


def test_report_names_the_glass_the_touch_phase_and_the_failed_checks(naive_run) -> None:
    spec, results, _ = naive_run
    text = failure_report(spec, results)
    failed = [r for r in results if not r.passed]
    assert text.startswith(f"{len(failed)} of {len(results)} test scenes failed.")
    assert len(text) <= 4000
    assert "clear_glass_large" in text
    for r in failed:
        assert re.search(rf"- seed {r.seed}:", text)
        for c in r.failed_checks:
            assert c.check_id in text and c.detail[:60] in text
        if r.error:
            assert r.error.splitlines()[0][:60] in text
    ev = _first_glass_touch(_glass_failure(results))
    assert f'"{ev["during"]}"' in text  # the exact skill phase of the first contact
    assert f"{ev['t']:.2f} s" in text
    assert '"Nothing touches the glass."' in text  # check sentence from the spec


def test_report_groups_repeated_patterns(naive_run) -> None:
    spec, results, _ = naive_run
    fail = _glass_failure(results)
    copies = [fail.model_copy(update={"seed": 90000 + i}) for i in range(9)]
    everything = [*results, *copies]
    text = failure_report(spec, everything, max_seeds=3)
    failing = {r.seed for r in everything if not r.passed}

    blocks = re.findall(r"^Pattern (\d+) \((\d+) scenes?\)", text, re.M)
    assert 1 <= len(blocks) < len(failing)
    assert int(blocks[0][1]) >= 10  # the repeated pattern comes first
    listed = [int(s) for s in re.findall(r"^  - seed (\d+):", text, re.M)]
    more = [
        int(s)
        for group in re.findall(r"Same pattern in \d+ more seeds?: ([\d, ]+)\.", text)
        for s in group.split(", ")
    ]
    assert sorted(listed + more) == sorted(failing)  # every failing seed exactly once
    assert len(re.findall(r"^  - seed 9000\d:", text, re.M)) <= 3  # max_seeds per pattern
    assert re.search(r"Same pattern in \d+ more seeds: .*90008", text)
    assert len(text) <= 4000


def test_report_stays_under_budget_with_many_patterns(naive_run) -> None:
    spec, _, _ = naive_run
    many = [
        SeedResult(
            seed=i,
            passed=False,
            error=f"SkillError: case {chr(65 + i % 26)} " + "x" * 300,
            checks=[
                CheckResult(
                    check_id=f"check_{i}",
                    kind="in_bin",
                    passed=False,
                    detail="red_cube_large ended in table; " * 20,
                    objects=["red_cube_large"],
                )
            ],
        )
        for i in range(30)
    ]
    text = failure_report(spec, [*many, SeedResult(seed=99, passed=True)])
    assert text.startswith("30 of 31 test scenes failed.\nPassing scenes (1): 99.")
    assert len(text) <= 4000
    assert re.search(r"\d+ more failure patterns not shown \(seeds ", text)


def test_report_when_everything_passes(naive_run) -> None:
    spec, _, _ = naive_run
    ok = [SeedResult(seed=s, passed=True) for s in (7, 3)]
    assert failure_report(spec, ok) == "0 of 2 test scenes failed.\nPassing scenes (2): 3, 7."
    assert failure_report(spec, []) == "No test scenes were run."


# ------------------------------------------------------------------------- failure frames
def _frame_time(p: Path) -> float:
    m = re.search(r"_t(\d+\.\d+)s\.jpg$", p.name)
    assert m, p.name
    return float(m.group(1))


def test_failure_frames_are_8_jpegs_around_the_first_contact(naive_run) -> None:
    _, results, out = naive_run
    fail = _glass_failure(results)
    focus = failure_focus(fail)
    assert focus is not None and focus[0] == pytest.approx(_first_glass_touch(fail)["t"])
    seed_dir = out / f"seed_{fail.seed}"
    paths = failure_frames(seed_dir, fail, n=8)
    assert len(paths) == 8
    assert all(p.parent == seed_dir / "repair_frames" for p in paths)
    for p in paths:
        assert p.read_bytes()[:3] == b"\xff\xd8\xff"
        img = cv2.imread(str(p))
        assert img is not None and img.shape[1] == 640
    times = [_frame_time(p) for p in paths]
    assert times[0] <= focus[0] <= times[-1]  # the window covers the first contact ...
    gaps = [b - a for a, b in zip(times, times[1:], strict=False)]
    assert all(g == pytest.approx(0.4, abs=0.02) for g in gaps)  # ... in 0.4 s steps
    assert min(abs(t - focus[0]) for t in times) <= 0.4


def test_failure_frames_spread_over_the_clip_without_a_focus(naive_run, tmp_path: Path) -> None:
    _, results, out = naive_run
    fail = _glass_failure(results)
    blind = fail.model_copy(update={"touch_events": [], "trace": []})
    assert failure_focus(blind) is None
    paths = failure_frames(out / f"seed_{fail.seed}", blind, n=8, out_dir=tmp_path / "f")
    times = [_frame_time(p) for p in paths]
    assert len(paths) == 8 and times[0] == 0.0
    assert times[-1] >= fail.sim_seconds - 0.2


def test_failure_frames_resize_wide_videos_and_center_on_the_error(tmp_path: Path) -> None:
    frames = [np.full((720, 1280, 3), (i * 2) % 255, dtype=np.uint8) for i in range(90)]
    write_mp4(tmp_path / "seed_1" / "full.mp4", frames, fps=30)
    result = SeedResult(
        seed=1,
        passed=False,
        error="SkillError: arm blocked",
        trace=[{"t": 1.5, "event": "error", "message": "SkillError: arm blocked"}],
    )
    paths = failure_frames(tmp_path / "seed_1", result, out_dir=tmp_path / "out")
    assert len(paths) == 8
    assert all(cv2.imread(str(p)).shape[:2] == (360, 640) for p in paths)
    times = [_frame_time(p) for p in paths]
    assert times[0] <= 1.5 <= times[-1] and times[-1] <= 3.0


def test_failure_frames_without_a_video(tmp_path: Path) -> None:
    result = SeedResult(seed=5, passed=False, error="timeout")
    assert failure_frames(tmp_path / "seed_5", result) == []


# ------------------------------------------------------------------------- repair call
def test_repair_returns_the_parsed_result_and_sends_frames(
    naive_run, fake_sandbox, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spec, results, out = naive_run
    fail = _glass_failure(results)
    frames = failure_frames(out / f"seed_{fail.seed}", fail, out_dir=tmp_path / "frames")
    fake = _fake(monkeypatch, RepairResult(diagnosis=DIAGNOSIS, source=f"```python\n{GOOD}```\n"))

    fix = repair(spec, NAIVE, results, frames, attempt=1, tag="job:repair1")

    assert fix.diagnosis == DIAGNOSIS.split(". ")[0] + "."
    assert fix.source == GOOD.rstrip() + "\n"
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["model_cls"] is RepairResult
    assert call["effort"] == "high" and call["tag"] == "job:repair1"
    assert "pick(obj, avoid=None)" in call["instructions"]  # the generated skill API doc
    texts = [p for p in call["content"] if p["type"] == "input_text"]
    images = [p for p in call["content"] if p["type"] == "input_image"]
    assert len(texts) == 1 and len(images) == len(frames) == 8
    assert all(p["detail"] == "low" for p in images)
    assert all(p["image_url"].startswith("data:image/jpeg;base64,") for p in images)
    text = texts[0]["text"]
    assert "Repair round 1." in text
    assert '"glass_untouched"' in text  # spec JSON
    assert NAIVE.strip() in text  # current program
    assert failure_report(spec, results) in text
    assert f"failing seed {fail.seed}" in text and "They are centered on t = " in text


def test_repair_retries_once_when_the_sandbox_rejects(
    naive_run, fake_sandbox, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spec, results, _ = naive_run
    fake = _fake(
        monkeypatch,
        RepairResult(diagnosis="Needs a loop.", source=BAD),
        RepairResult(diagnosis="Diagnosis: the arm sweeps through the glass", source=GOOD),
    )
    fix = repair(spec, NAIVE, results, [tmp_path / "missing.jpg"], attempt=2)

    assert len(fake.calls) == 2
    first, second = fake.calls
    assert not [p for p in first["content"] if p["type"] == "input_image"]  # missing file skipped
    assert first["tag"] == second["tag"] == "repair2"
    assert second["content"][:-1] == first["content"]
    retry = second["content"][-1]["text"]
    assert "Import is not allowed" in retry and "import os" in retry
    assert fix.source == GOOD.rstrip() + "\n"
    assert fix.diagnosis == "the arm sweeps through the glass."


def test_repair_raises_after_two_rejections(
    naive_run, fake_sandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec, results, _ = naive_run
    fake = _fake(
        monkeypatch,
        RepairResult(diagnosis="a", source=BAD),
        RepairResult(diagnosis="b", source="def run(scene):\n    __import__('os')\n"),
    )
    with pytest.raises(RepairError, match="rejected twice"):
        repair(spec, NAIVE, results, [], attempt=1)
    assert len(fake.calls) == 2


def test_repair_rejects_the_unchanged_program(
    naive_run, fake_sandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec, results, _ = naive_run
    same = "# no change\n" + NAIVE.replace("\n", "\n\n")
    fake = _fake(
        monkeypatch,
        RepairResult(diagnosis="Looks fine.", source=same),
        RepairResult(diagnosis="The glass is in the way.", source=GOOD),
    )
    fix = repair(spec, NAIVE, results, [], attempt=1)
    assert len(fake.calls) == 2
    assert "same program that failed" in fake.calls[1]["content"][-1]["text"]
    assert fix.source == GOOD.rstrip() + "\n"

    _fake(monkeypatch, *(RepairResult(diagnosis="x", source=NAIVE) for _ in range(2)))
    with pytest.raises(RepairError, match="same program"):
        repair(spec, NAIVE, results, [], attempt=2)


def test_repair_with_the_real_sandbox(naive_run, monkeypatch: pytest.MonkeyPatch) -> None:
    sandbox = pytest.importorskip("showtell.agent.sandbox")
    spec, results, _ = naive_run
    with pytest.raises(sandbox.SandboxError):
        sandbox.validate_program(BAD)
    fake = _fake(
        monkeypatch,
        RepairResult(diagnosis="x", source=BAD),
        RepairResult(diagnosis="The glass is in the way.", source=GOOD),
    )
    fix = repair(spec, NAIVE, results, [], attempt=1)
    assert len(fake.calls) == 2 and fix.source == GOOD.rstrip() + "\n"


# ------------------------------------------------------------------------- text helpers
def test_strip_code_fences() -> None:
    body = "def run(scene):\n    pass\n"
    assert strip_code_fences(f"```python\n{body}```") == body
    assert strip_code_fences(body.rstrip()) == body
    assert strip_code_fences(f"```py\n{body}") == body  # unterminated fence
    two = f"Old:\n```\nprint(1)\n```\nNew:\n```python\n{body}```"
    assert strip_code_fences(two) == body
    assert strip_code_fences("    def run(scene):\n        pass") == body  # dedented


def test_first_sentence() -> None:
    assert first_sentence(DIAGNOSIS) == (
        "The carried blocks sweep through the glass at the 0.12 m transit height."
    )
    assert first_sentence("**Root cause:** e.g. the hand hits it. More text.") == (
        "e.g. the hand hits it."
    )
    assert first_sentence("Diagnosis\n\nThe arm hits the glass\nsecond line") == (
        "The arm hits the glass."
    )
    assert first_sentence("1. The arm hits the glass. 2. Fix it.") == "The arm hits the glass."
    assert first_sentence("- *The hand clips the rim*") == "The hand clips the rim."
    long = first_sentence("word " * 100)
    assert len(long) <= 200 and long.endswith("…")
    assert first_sentence("   ") == "No diagnosis given."
