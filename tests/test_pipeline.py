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
IDLE = "def run(scene):\n    return\n"  # moves nothing: fails every in_bin check on any seed


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
    events = []
    report = pipeline.run_job(config, on_event=events.append)
    assert report.status == "verified" and report.spec_source == "given"
    assert doubles == {"spec": 0, "program": 0, "repair": 0}
    assert next(e for e in events if e.type == "spec_ready").data["source"] == "given"
    # the caller wrote this spec: it was not approved without review, so auto is false
    approved = next(e for e in events if e.type == "spec_approved").data
    assert approved["auto"] is False and approved["edited"] is False
    assert "awaiting_approval" not in [e.type for e in events]


def test_a_supplied_spec_approved_interactively_is_not_auto_either(tmp_path, doubles) -> None:
    task = get_task("task01_size")
    config = JobConfig(
        job_id="byo-interactive",
        rules_text=task.rules,
        use_video=False,
        task_id=task.id,
        n_seeds=1,
        use_judge=False,
        interactive=True,
        max_repairs=0,
        spec=TaskSpec.model_validate({**task.reference, "rationale": "given"}),
        program_source=IDLE,
        out_dir=tmp_path / "byo",
    )
    events = []
    report = pipeline.run_job(config, on_event=events.append, approve=lambda spec, q: spec)
    assert report.error is None and report.status == "failed"  # the idle program
    types = [e.type for e in events]
    assert types.index("awaiting_approval") < types.index("spec_approved")
    assert next(e for e in events if e.type == "spec_approved").data["auto"] is False


# ------------------------------------------------------------------ cached flags and the usage log
@pytest.fixture()
def usage_doubles(doubles, monkeypatch):
    """The Muse Wonder doubles also log usage under the tag the pipeline gives them, like the real
    calls do: discovery, spec and program as local cache hits, repair as a billed call. The first
    program is idle, so attempt 0 fails without depending on physics and costs little sim time.
    For runs with a video, SAM tracking, the track summary and the upload are doubled too."""
    from showtell.agent import program as program_mod
    from showtell.agent import repair as repair_mod
    from showtell.agent import spec as spec_mod
    from showtell.agent.schemas import DiscoveredObject, Discovery, ObjectAttrs, TrackSummary
    from showtell.meta import files, sam
    from showtell.meta.usage import log_usage
    from showtell.perception import track_summary as ts

    def logged(fn, cached: bool):
        def call(*args, **kw):  # 10k input tokens: $0.04 at list price
            log_usage(kind="wonder", tag=kw["tag"], cached_locally=cached, input_tokens=10_000)
            return fn(*args, **kw)

        return call

    def discover(keyframes, *, tag=""):
        assert keyframes, "discovery needs the keyframes of the normalized video"
        return Discovery(objects=[DiscoveredObject(concept="red block", attrs=ObjectAttrs())])

    def write_program(spec, object_ids, **kw):
        doubles["program"] += 1
        return Program(source=IDLE, notes="idle double")

    monkeypatch.setattr(ts, "discover", logged(discover, True))
    monkeypatch.setattr(ts, "summarize", lambda *a, **k: TrackSummary(objects=[]))
    monkeypatch.setattr(sam, "track", lambda *a, **k: {})
    monkeypatch.setattr(files, "upload", lambda path: "file-test")
    monkeypatch.setattr(spec_mod, "make_spec", logged(spec_mod.make_spec, True))
    monkeypatch.setattr(program_mod, "write_program", logged(write_program, True))
    monkeypatch.setattr(repair_mod, "repair", logged(repair_mod.repair, False))
    return doubles


def _stage_done(events) -> list[dict]:
    return [e.data for e in events if e.type == "stage_done"]


def test_stage_done_says_which_model_stages_came_from_the_cache(
    tmp_path, usage_doubles, monkeypatch
) -> None:
    """Every Muse Wonder stage reports `cached` from the calls logged under its tag; no other
    stage does. The demo video lives outside the job folder, as in the benchmark."""
    from showtell.agent import repair as repair_mod
    from showtell.perception.video_prep import make_test_clip

    logged_repair = repair_mod.repair  # the outcome does not matter here, only that it ran

    def idle_repair(*args, **kw):
        return logged_repair(*args, **kw).model_copy(update={"source": IDLE})

    monkeypatch.setattr(repair_mod, "repair", idle_repair)
    video = make_test_clip(tmp_path / "bench videos" / "task03_size_glass.mp4", seconds=1.0)
    config = JobConfig(
        job_id="cached",
        rules_text="Sort by size, and don't touch the glass.",
        video_path=video,
        use_video=True,
        task_id="task03_size_glass",
        n_seeds=1,
        use_judge=False,
        max_repairs=1,
        out_dir=tmp_path / "job",
    )
    events = []
    report = pipeline.run_job(config, on_event=events.append)
    assert report.error is None and [a.passed for a in report.attempts] == [0, 0]
    assert usage_doubles == {"spec": 1, "program": 1, "repair": 1}
    done = _stage_done(events)
    flags = {d["stage"]: d["cached"] for d in done if "cached" in d}
    assert flags == {"discover": True, "spec": True, "program": True, "repair": False}
    others = {d["stage"] for d in done} - set(flags)
    assert others == {"prep", "track", "summary", "verify", "report"}
    # the same tags carry the cost: four calls at list price, only the repair billed
    assert report.cost.calls == 4
    assert (report.cost.usd, report.cost.paid_usd) == (pytest.approx(0.16), pytest.approx(0.04))
    # the video was linked into the job folder, and the report names it there
    assert report.video == "input/task03_size_glass.mp4"
    assert (tmp_path / "job" / report.video).read_bytes() == video.read_bytes()
    assert (tmp_path / "job" / "prep" / "normalized.mp4").is_file()
    assert str(video.parent) not in (tmp_path / "job" / "report.json").read_text()


def _short_job(tmp_path, job_id: str) -> JobConfig:
    return JobConfig(
        job_id=job_id,
        rules_text="Sort by size, and don't touch the glass.",
        use_video=False,
        task_id="task03_size_glass",
        n_seeds=1,
        use_judge=False,
        max_repairs=0,
        out_dir=tmp_path / job_id,
    )


def test_a_corrupt_usage_log_never_fails_a_job(tmp_path, usage_doubles) -> None:
    from showtell.config import settings

    log = settings.usage_log
    log.parent.mkdir(parents=True, exist_ok=True)
    good = json.dumps({"ts": 1.0, "kind": "sam", "tag": "other:track", "cost_usd": 0.01})
    torn = (
        b'{"ts": 2.0, "kind": "sam", "tag": "other:track", "concept": "r\xc3'  # cut mid-character
    )
    log.write_bytes(good.encode() + b"\n" + torn + b"\n" + b'{"ts": "soon", "kind": "wonder"}\n')
    events = []
    report = pipeline.run_job(_short_job(tmp_path, "corrupt"), on_event=events.append)
    assert report.error is None and report.status == "failed"  # the idle program, unrepaired
    flags = {d["stage"]: d["cached"] for d in _stage_done(events) if "cached" in d}
    assert flags == {"spec": True, "program": True}
    assert report.cost.calls == 2  # this job's calls, read past the bad line


def test_a_failing_cache_lookup_only_leaves_the_flag_out(tmp_path, usage_doubles, monkeypatch):
    from showtell.meta import usage

    def broken(*args, **kw):
        raise OSError("the usage log cannot be read")

    monkeypatch.setattr(usage, "tag_cached", broken)
    events = []
    report = pipeline.run_job(_short_job(tmp_path, "nolabel"), on_event=events.append)
    assert report.error is None and report.status == "failed"
    done = _stage_done(events)
    assert {"spec", "program", "verify", "report"} <= {d["stage"] for d in done}
    assert all("cached" not in d and d["seconds"] >= 0 for d in done)


def test_a_stage_reads_only_its_own_calls_and_times_without_the_lookup(tmp_path, monkeypatch):
    import time

    from showtell.meta import usage

    emit = pipeline.Emitter(tmp_path / "events.jsonl", "j", None)

    def last_done() -> dict:
        lines = (tmp_path / "events.jsonl").read_text().splitlines()
        return [json.loads(x)["data"] for x in lines if '"stage_done"' in x][-1]

    usage.log_usage(kind="wonder", tag="j:spec", input_tokens=10)  # billed, before the stage
    with pipeline.Stage(emit, "spec", usage_tag="j:spec"):
        usage.log_usage(kind="wonder", tag="j:spec", cached_locally=True, input_tokens=10)
    assert last_done()["cached"] is True  # the earlier billed call is not part of this stage

    with pipeline.Stage(emit, "discover", usage_tag="j:discover"):
        pass  # no model call: no flag
    assert "cached" not in last_done()

    real = usage.tag_cached

    def slow(*args, **kw):
        time.sleep(0.4)
        return real(*args, **kw)

    monkeypatch.setattr(usage, "tag_cached", slow)
    with pipeline.Stage(emit, "program", usage_tag="j:program"):
        usage.log_usage(kind="wonder", tag="j:program", input_tokens=10)
    done = last_done()
    assert done["cached"] is False
    assert done["seconds"] < 0.4  # the stage's time leaves out the log lookup after it


def test_job_video_links_an_outside_video_into_the_job_folder(tmp_path, monkeypatch) -> None:
    import os

    root = tmp_path / "job"
    upload = root / "input" / "upload.mp4"
    upload.parent.mkdir(parents=True)
    upload.write_bytes(b"uploaded")
    assert pipeline._job_video(upload, root) == upload  # already in the job folder

    outside = tmp_path / "videos" / "my clip (1).MOV"
    outside.parent.mkdir()
    outside.write_bytes(b"clip")
    linked = pipeline._job_video(outside, root)
    assert linked == root / "input" / "my_clip_1.mov" and linked.read_bytes() == b"clip"
    assert os.stat(linked).st_ino == os.stat(outside).st_ino  # a hard link: no copy on one disk
    assert pipeline._rel(linked, root) == "input/my_clip_1.mov"

    def cross_device(src, dst):
        raise OSError(18, "Invalid cross-device link")

    monkeypatch.setattr(pipeline.os, "link", cross_device)
    copied = pipeline._job_video(outside, root)  # replaces the earlier link
    assert copied == linked and copied.read_bytes() == b"clip"
    assert os.stat(copied).st_ino != os.stat(outside).st_ino

    missing = tmp_path / "videos" / "gone.mp4"
    assert pipeline._job_video(missing, root) == missing  # left for the prep stage to report


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
