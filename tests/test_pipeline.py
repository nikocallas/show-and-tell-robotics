"""Pipeline orchestration with test doubles for the model calls (physics and checks are real).

The doubles stand in for Muse Wonder only inside this test: spec = the task's reference spec,
first program = the naive size sort, repair = the size sort with avoid. Whether each attempt passes
is decided by the simulator.
"""

import json
from pathlib import Path

import pytest

from showtell import pipeline
from showtell.agent.schemas import Program, RepairResult, TaskSpec
from showtell.contracts import JobConfig
from showtell.sim.scene import get_task

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def doubles(monkeypatch):
    from showtell.agent import program as program_mod
    from showtell.agent import repair as repair_mod
    from showtell.agent import spec as spec_mod

    task = get_task("task03_size_glass")
    ref = TaskSpec.model_validate({**task.reference, "rationale": "test double"})
    calls: dict[str, int] = {"spec": 0, "program": 0, "repair": 0}

    def make_spec(rules_text, object_ids, **kw):
        calls["spec"] += 1
        return ref, "none"

    def write_program(spec, object_ids, **kw):
        calls["program"] += 1
        return Program(source=(ROOT / "examples/size_sort_naive.py").read_text(), notes="double")

    def repair(spec, source, results, frames, **kw):
        calls["repair"] += 1
        return RepairResult(
            diagnosis="The carried blocks hit the glass on the way to the bins.",
            source=(ROOT / "examples/size_sort.py").read_text(),
        )

    from showtell.config import settings

    monkeypatch.setattr(settings, "MODEL_API_KEY", "test-key-not-used")  # repairs need a key
    monkeypatch.setattr(spec_mod, "make_spec", make_spec)
    monkeypatch.setattr(program_mod, "write_program", write_program)
    monkeypatch.setattr(repair_mod, "repair", repair)
    monkeypatch.setattr(repair_mod, "failure_frames", lambda *a, **k: [])
    return calls


def test_job_repairs_a_failing_program_until_verified(tmp_path, doubles) -> None:
    events = []
    config = JobConfig(
        job_id="test-job",
        rules_text="Sort by size, and don't touch the glass.",
        use_video=False,
        task_id="task03_size_glass",
        n_seeds=3,
        use_judge=False,
        out_dir=tmp_path / "job",
    )
    report = pipeline.run_job(config, on_event=events.append)
    assert report.error is None, report.error
    assert report.status == "verified"
    assert len(report.attempts) == 2 and report.attempts[0].passed < 3
    assert report.attempts[1].passed == 3 and report.attempts[1].diagnosis
    assert doubles == {"spec": 1, "program": 1, "repair": 1}
    types = [e.type for e in events]
    for t in (
        "job_started",
        "spec_ready",
        "spec_approved",
        "program_ready",
        "seed_done",
        "attempt_done",
        "repair_done",
        "job_done",
    ):
        assert t in types
    assert types.count("seed_done") == 6
    assert next(e for e in events if e.type == "spec_approved").data["auto"] is True
    root = tmp_path / "job"
    assert (root / "report.json").exists() and (root / "report.md").exists()
    assert "VERIFIED" in (root / "report.md").read_text()
    lines = (root / "events.jsonl").read_text().splitlines()
    assert json.loads(lines[-1])["type"] == "job_done"
    seed = report.attempts[1].seeds[0]
    assert seed.videos["full"].startswith("attempts/1/seed_3000/")
    assert (root / seed.videos["full"]).exists()


def test_job_reports_errors_instead_of_raising(tmp_path, doubles) -> None:
    config = JobConfig(
        job_id="bad",
        rules_text="x",
        use_video=False,
        n_seeds=1,
        use_judge=False,
        out_dir=tmp_path / "bad",
    )
    report = pipeline.run_job(config)
    assert report.status == "error" and "no sim objects" in (report.error or "")
    assert (tmp_path / "bad" / "report.json").exists()


def test_given_spec_and_program_run_without_model_calls(tmp_path, doubles, monkeypatch) -> None:
    from showtell.config import settings

    monkeypatch.setattr(settings, "MODEL_API_KEY", "")
    task = get_task("task01_size")
    spec = TaskSpec.model_validate({**task.reference, "rationale": "given"})
    config = JobConfig(
        job_id="byo",
        rules_text=task.rules,
        use_video=False,
        task_id=task.id,
        n_seeds=2,
        use_judge=True,
        spec=spec,
        program_source=(ROOT / "examples/reference/task01_size.py").read_text(),
        out_dir=tmp_path / "byo",
    )
    report = pipeline.run_job(config)
    assert report.status == "verified" and report.spec_source == "given"
    assert doubles == {"spec": 0, "program": 0, "repair": 0}


def test_interactive_approval_can_edit_the_spec(tmp_path, doubles) -> None:
    seen = {}

    def approve(spec, question):
        seen["q"] = question
        return spec.model_copy(update={"rationale": "edited by the user"})

    config = JobConfig(
        job_id="appr",
        rules_text="Sort by size.",
        use_video=False,
        task_id="task03_size_glass",
        n_seeds=1,
        use_judge=False,
        interactive=True,
        max_repairs=0,
        out_dir=tmp_path / "appr",
    )
    report = pipeline.run_job(config, approve=approve)
    assert report.spec is not None and report.spec.rationale == "edited by the user"
    assert "q" in seen
    events = [json.loads(line) for line in (tmp_path / "appr" / "events.jsonl").open()]
    approved = next(e for e in events if e["type"] == "spec_approved")
    assert approved["data"]["auto"] is False and approved["data"]["edited"] is True


def test_rejected_repair_ends_the_job_as_failed(tmp_path, doubles, monkeypatch) -> None:
    from showtell.agent import repair as repair_mod

    def rejected(*a, **kw):
        raise repair_mod.RepairError("repair 1: the program was rejected twice: line 1: import")

    monkeypatch.setattr(repair_mod, "repair", rejected)
    config = JobConfig(
        job_id="rej",
        rules_text="Sort by size, and don't touch the glass.",
        use_video=False,
        task_id="task03_size_glass",
        n_seeds=3,
        use_judge=False,
        out_dir=tmp_path / "rej",
    )
    report = pipeline.run_job(config)
    assert report.status == "failed" and report.error is None
    assert len(report.attempts) == 1 and report.attempts[0].passed < 3
    events = (tmp_path / "rej" / "events.jsonl").read_text()
    assert "rejected twice" in events
