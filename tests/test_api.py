"""API tests, fully offline: a fake `showtell.pipeline.run_job` (injected via sys.modules) drives
jobs through the real JobManager, SQLite tables, SSE stream and FastAPI routes."""

from __future__ import annotations

import contextlib
import json
import re
import sys
import threading
import time
import types
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from showtell.agent.schemas import TaskSpec
from showtell.api import app as api_app
from showtell.api import db
from showtell.api import jobs as jobs_mod
from showtell.api.jobs import JobManager, map_media, media_rel
from showtell.config import settings
from showtell.contracts import (
    AgreementStats,
    AttemptReport,
    Event,
    JobConfig,
    JobReport,
    SamVerdict,
    SeedSummary,
)
from showtell.verify.runner import CheckResult

ORIGIN = "http://localhost:5173"
LOCAL = ("127.0.0.1", 50000)  # TestClient's client address: this machine
WEB_TYPES = Path(__file__).resolve().parent.parent / "web" / "src" / "types.ts"
RULES = "Sort by size, and don't touch the glass."
SPEC: dict[str, Any] = {
    "rules": [
        {"select": {"size": "large", "fragile": False}, "place_in": "left_bin"},
        {"select": {"size": "small", "fragile": False}, "place_in": "right_bin"},
        {"select": {"shape": "glass"}, "leave": True},
    ],
    "checks": [
        {
            "id": "large_left",
            "kind": "in_bin",
            "select": {"size": "large", "fragile": False},
            "bin": "left_bin",
            "text": "Every large block ends in the left bin.",
        },
        {
            "id": "glass_untouched",
            "kind": "untouched",
            "select": {"shape": "glass"},
            "text": "Nothing touches the glass.",
        },
    ],
    "clarifying_question": None,
    "rationale": "Big blocks went left and small ones right; the sentence protects the glass.",
}
EDITED_SPEC: dict[str, Any] = {
    **SPEC,
    "checks": [
        *SPEC["checks"],
        {
            "id": "glass_upright",
            "kind": "upright",
            "select": {"shape": "glass"},
            "text": "The glass is still standing.",
        },
    ],
}
PROGRAM_0 = (
    "def run(scene):\n"
    "    for obj in scene.objects(size='large', fragile=False):\n"
    "        pick(obj)\n"
    "        place('left_bin')\n"
)
PROGRAM_1 = (
    "def run(scene):\n"
    "    glass = scene.objects(shape='glass')\n"
    "    for obj in scene.objects(size='large', fragile=False):\n"
    "        pick(obj, avoid=glass)\n"
    "        place('left_bin', avoid=glass)\n"
)
DIAGNOSIS = "The carried block swept through the glass on the way to the left bin."
FAKE_MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 64


# ------------------------------------------------------------------------------- fake pipeline
class FakePipeline:
    """Stands in for showtell.pipeline.run_job: same event sequence, a tiny job folder.

    Attempt 0 fails one scene (the glass is touched); repair 1 passes every scene.
    """

    def __init__(self, crash: str | None = None) -> None:
        self.crash = crash
        self.calls: list[JobConfig] = []
        self.approved: list[TaskSpec] = []

    def __call__(
        self,
        config: JobConfig,
        on_event: Callable[[Event], None] | None = None,
        approve: Callable[[TaskSpec, str | None], TaskSpec] | None = None,
    ) -> JobReport:
        self.calls.append(config)
        root = Path(config.out_dir)
        root.mkdir(parents=True, exist_ok=True)

        def emit(type_: str, **data: Any) -> None:
            """Like pipeline.Emitter: append to events.jsonl first, then call the listener."""
            ev = Event(type=type_, job_id=config.job_id, data=_jsonable(data))
            with (root / "events.jsonl").open("a") as f:
                f.write(ev.model_dump_json() + "\n")
            if on_event is not None:
                with contextlib.suppress(Exception):  # a broken listener must not kill the job
                    on_event(ev)

        seeds = list(range(9000, 9000 + config.n_seeds))
        video = str(Path(config.video_path).relative_to(root)) if config.video_path else None
        report = JobReport(
            job_id=config.job_id,
            created_at=time.time(),
            rules=config.rules_text,
            use_video=config.use_video,
            task_id=config.task_id,
            object_ids=["red_cube_large", "red_cube_small", "clear_glass_large"],
            video=video,
            seeds=seeds,
        )
        emit("job_started", rules=config.rules_text, use_video=config.use_video,
             task_id=config.task_id, n_seeds=config.n_seeds)  # fmt: skip
        if self.crash:
            raise RuntimeError(self.crash)
        try:
            self._run(config, root, emit, approve, report, seeds)
        except Exception as exc:  # like the real pipeline: errors end up in the report
            emit("error", message=f"{type(exc).__name__}: {exc}")
            report.status, report.error = "error", f"{type(exc).__name__}: {exc}"
        (root / "report.json").write_text(report.model_dump_json(indent=2))
        (root / "report.md").write_text(f"# Show & Tell job `{config.job_id}`\n\n{report.status}\n")
        emit("job_done", status=report.status, report=report)
        return report

    def _run(self, config, root, emit, approve, report, seeds) -> None:
        spec = TaskSpec.model_validate(SPEC)
        emit("stage_started", stage="spec")
        emit("stage_done", stage="spec", seconds=0.01)
        emit("spec_ready", spec=spec, source="sentence", media_path="none")
        edited = False
        if config.interactive and approve is not None:
            emit("stage_started", stage="approval")
            emit("awaiting_approval", spec=spec, clarifying_question=spec.clarifying_question)
            approved = approve(spec, spec.clarifying_question)
            self.approved.append(approved)
            edited = approved.model_dump() != spec.model_dump()
            spec = approved
            emit("stage_done", stage="approval", seconds=0.01)
        report.spec = spec
        emit("spec_approved", spec=spec, edited=edited)
        plan = [(PROGRAM_0, "initial", None), (PROGRAM_1, "repair", DIAGNOSIS)]
        for n, (source, kind, diagnosis) in enumerate(plan):
            if n:
                emit("repair_started", attempt=n)
                emit("repair_done", attempt=n, diagnosis=diagnosis, source=source)
            emit(
                "program_ready", attempt=n, source=source, notes="", kind=kind, diagnosis=diagnosis
            )
            emit("attempt_started", attempt=n, seeds=seeds)
            emit("stage_started", stage="verify", attempt=n)
            summaries = []
            for seed in seeds:
                ok = not (n == 0 and seed == seeds[1 % len(seeds)])
                summary = SeedSummary(
                    seed=seed,
                    passed=ok,
                    checks=[
                        CheckResult(check_id="large_left", kind="in_bin", passed=True, detail="ok"),
                        CheckResult(
                            check_id="glass_untouched",
                            kind="untouched",
                            passed=ok,
                            detail="no contacts" if ok else "touched by red_cube_large",
                        ),
                    ],
                    videos=_write_seed_media(root, n, seed),
                    sim_seconds=12.5,
                    touch_events=[]
                    if ok
                    else [
                        {
                            "t": 3.2,
                            "obj": "clear_glass_large",
                            "by": "red_cube_large",
                            "during": "transit",
                        }
                    ],
                )
                emit("seed_done", attempt=n, seed=summary)
                verdicts = [SamVerdict(check_id="glass_untouched", kind="untouched", passed=ok)]
                emit("judge_done", attempt=n, seed=seed, verdicts=verdicts)
                summaries.append(summary.model_copy(update={"sam": verdicts}))
            passed = sum(s.passed for s in summaries)
            agreement = AgreementStats(agree=len(seeds), n=len(seeds))
            report.attempts.append(
                AttemptReport(
                    n=n,
                    kind=kind,
                    program=source,
                    diagnosis=diagnosis,
                    passed=passed,
                    total=len(seeds),
                    seeds=summaries,
                    agreement=agreement,
                )
            )
            emit("attempt_done", attempt=n, passed=passed, total=len(seeds),
                 failed_seeds=[s.seed for s in summaries if not s.passed], agreement=agreement,
                 diagnosis=diagnosis)  # fmt: skip
        last = report.attempts[-1]
        report.status = "verified" if last.passed == last.total else "failed"
        report.final_program, report.final_passed, report.final_total = (
            last.program,
            last.passed,
            last.total,
        )
        report.agreement = AgreementStats(agree=2 * len(seeds), n=2 * len(seeds))


def _jsonable(x: Any) -> Any:
    if hasattr(x, "model_dump"):
        return x.model_dump(mode="json")
    if isinstance(x, dict):
        return {k: _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    return x


def _write_seed_media(root: Path, n: int, seed: int) -> dict[str, str]:
    rel = f"attempts/{n}/seed_{seed}"
    (root / rel).mkdir(parents=True, exist_ok=True)
    names = {"full": "full.mp4", "overhead": "judge.mp4", "judge_angle": "judge_angle.mp4"}
    for name in names.values():
        (root / rel / name).write_bytes(FAKE_MP4 + str(seed).encode())
    (root / rel / "thumb.jpg").write_bytes(b"\xff\xd8\xff\xe0 fake jpeg \xff\xd9")
    return {**{k: f"{rel}/{v}" for k, v in names.items()}, "thumb": f"{rel}/thumb.jpg"}


# ------------------------------------------------------------------------------------- helpers
def wait_for(fn: Callable[[], Any], timeout: float = 10.0) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("timed out waiting for condition")


def job_json(client: TestClient, job_id: str) -> dict[str, Any]:
    r = client.get(f"/api/jobs/{job_id}")
    assert r.status_code == 200, r.text
    return r.json()


def wait_status(client: TestClient, job_id: str, *statuses: str) -> dict[str, Any]:
    def check() -> dict[str, Any] | None:
        d = job_json(client, job_id)
        return d if d["job"]["status"] in statuses else None

    return wait_for(check)


def parse_sse(lines: Iterable[str]) -> Iterator[dict[str, Any]]:
    """Yield SSE messages as {"id", "event", "data" (parsed JSON)} as they complete."""
    cur: dict[str, Any] = {}
    for line in lines:
        if line == "":
            if "data" in cur:
                cur["data"] = json.loads(cur["data"])
                yield cur
            cur = {}
        elif line.startswith(":"):
            continue  # heartbeat comment
        else:
            key, _, value = line.partition(":")
            cur[key] = value[1:] if value.startswith(" ") else value


def start_job(client: TestClient, **form: Any) -> str:
    data = {"rules": RULES, "task_id": "task03_size_glass", "use_video": "false", "seeds": "3"}
    data.update({k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in form.items()})
    r = client.post("/api/jobs", data=data)
    assert r.status_code == 200, r.text
    return r.json()["job_id"]


@pytest.fixture()
def fake(monkeypatch: pytest.MonkeyPatch) -> FakePipeline:
    pipeline = FakePipeline()
    module = types.ModuleType("showtell.pipeline")
    module.run_job = pipeline  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "showtell.pipeline", module)
    return pipeline


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture()
def client(data_dir: Path, tmp_path: Path, fake: FakePipeline) -> Iterator[TestClient]:
    app = api_app.create_app(
        data_dir,
        origins=[ORIGIN],
        bench_path=tmp_path / "results.json",
        max_upload_bytes=4096,
        api_token="",  # no token: local requests may write
        budget_usd=float("inf"),
    )
    with TestClient(app, client=LOCAL) as c:
        yield c


# ------------------------------------------------------------------------------------- jobs
def test_approval_flow_and_job_shape(client: TestClient, fake: FakePipeline, data_dir: Path):
    job_id = start_job(client)
    d = wait_status(client, job_id, "awaiting_approval")
    assert d["job"]["awaiting_approval"] and d["job"]["stage"] == "approval"
    assert d["job"]["spec"]["checks"][1]["id"] == "glass_untouched"
    assert d["job"]["object_ids"] == [
        "red_cube_large",
        "blue_cube_large",
        "red_cube_small",
        "green_cube_small",
        "clear_glass_large",
    ]
    assert d["report"] is None and d["attempts"] == []

    r = client.post(f"/api/jobs/{job_id}/spec", json=EDITED_SPEC)
    assert r.status_code == 200 and r.json() == {"ok": True}
    again = client.post(f"/api/jobs/{job_id}/spec", json={"approve": True})
    assert again.status_code == 409

    d = wait_status(client, job_id, "verified")
    assert fake.approved[0].checks[-1].id == "glass_upright"  # the edit reached the pipeline
    assert fake.calls[0].interactive and fake.calls[0].task_id == "task03_size_glass"
    assert fake.calls[0].out_dir == data_dir / "jobs" / job_id
    job = d["job"]
    assert job["approved"] and job["error"] is None and job["finished_at"]
    assert [c["id"] for c in job["spec"]["checks"]][-1] == "glass_upright"
    assert job["links"]["events"] == f"/api/jobs/{job_id}/events"

    a0, a1 = d["attempts"]
    assert (a0["n"], a0["kind"], a0["passed"], a0["total"], a0["done"]) == (
        0,
        "initial",
        2,
        3,
        True,
    )
    assert (a1["kind"], a1["passed"], a1["diagnosis"], a1["program"]) == (
        "repair", 3, DIAGNOSIS, PROGRAM_1
    )  # fmt: skip
    failing = a0["seeds"][1]
    assert failing["seed"] == 9001 and not failing["passed"]
    assert failing["checks"][1]["detail"] == "touched by red_cube_large"
    assert failing["sam"] == [
        {"check_id": "glass_untouched", "kind": "untouched", "passed": False, "detail": ""}
    ]
    assert failing["touch_events"][0]["by"] == "red_cube_large"
    prefix = f"/media/jobs/{job_id}/attempts/0/seed_9001/"
    assert failing["videos"] == {
        "full": prefix + "full.mp4",
        "overhead": prefix + "judge.mp4",
        "judge_angle": prefix + "judge_angle.mp4",
        "thumb": prefix + "thumb.jpg",
    }
    report = d["report"]
    assert report["status"] == "verified" and report["final_passed"] == 3
    assert report["attempts"][0]["seeds"][0]["videos"]["full"].startswith(f"/media/jobs/{job_id}/")
    # the media URLs really serve the files
    video = client.get(failing["videos"]["full"])
    assert video.status_code == 200 and video.content.startswith(FAKE_MP4)

    # rows were written as events arrived, then reconciled from the report
    engine = db.make_engine(db.db_path(data_dir))
    with db.session(engine) as s:
        attempts = db.job_attempts(s, [job_id])[job_id]
        runs = db.attempt_seeds(s, [a.id for a in attempts])
    engine.dispose()
    assert [a.n for a in attempts] == [0, 1] and all(a.done for a in attempts)
    assert sum(len(v) for v in runs.values()) == 6
    assert all(r.sam_json and r.full_video for v in runs.values() for r in v)

    listing = client.get("/api/jobs").json()
    assert listing[0]["id"] == job_id and listing[0]["attempts"] == 2
    assert (listing[0]["status"], listing[0]["passed"], listing[0]["total"]) == ("verified", 3, 3)


def test_auto_approve_runs_without_waiting(client: TestClient, fake: FakePipeline):
    job_id = start_job(client, auto_approve=True)
    d = wait_status(client, job_id, "verified")
    assert not fake.calls[0].interactive and fake.approved == []
    assert d["job"]["approved"] and d["job"]["auto_approve"]
    r = client.post(f"/api/jobs/{job_id}/spec", json={"approve": True})
    assert r.status_code == 409


def test_list_jobs_newest_first(client: TestClient):
    first = start_job(client, auto_approve=True)
    wait_status(client, first, "verified")
    second = start_job(client, auto_approve=True)
    wait_status(client, second, "verified")
    ids = [j["id"] for j in client.get("/api/jobs").json()]
    assert ids.index(second) < ids.index(first)
    assert client.get("/api/jobs/nope").status_code == 404
    assert client.get("/api/jobs/..%2F..%2Fetc").status_code == 404


def test_spec_endpoint_errors(client: TestClient):
    assert client.post("/api/jobs/missing/spec", json={"approve": True}).status_code == 404
    job_id = start_job(client)
    wait_status(client, job_id, "awaiting_approval")
    assert client.post(f"/api/jobs/{job_id}/spec", json={"approve": False}).status_code == 400
    bad = {**SPEC, "checks": [{"id": "x", "kind": "in_bin", "select": {}, "text": "no bin"}]}
    r = client.post(f"/api/jobs/{job_id}/spec", json=bad)
    assert r.status_code == 422 and "in_bin needs a bin" in r.text
    assert client.post(f"/api/jobs/{job_id}/spec", json={"approve": True}).status_code == 200
    wait_status(client, job_id, "verified")


def test_pipeline_crash_ends_with_error(client: TestClient, fake: FakePipeline, data_dir: Path):
    fake.crash = "simulator exploded"
    job_id = start_job(client, auto_approve=True)
    d = wait_status(client, job_id, "error")
    assert "simulator exploded" in d["job"]["error"]
    assert d["report"]["status"] == "error" and "simulator exploded" in d["report"]["error"]
    lines = (data_dir / "jobs" / job_id / "events.jsonl").read_text().splitlines()
    assert [json.loads(x)["type"] for x in lines] == ["job_started", "error", "job_done"]


def test_missing_pipeline_is_a_job_error(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(sys.modules, "showtell.pipeline", None)  # import fails
    job_id = start_job(client, auto_approve=True)
    d = wait_status(client, job_id, "error")
    assert "showtell.pipeline" in d["job"]["error"]


# ------------------------------------------------------------------------------------- events (SSE)
def test_sse_streams_live_and_ends_after_job_done(client: TestClient):
    job_id = start_job(client)
    seen: list[dict[str, Any]] = []
    with client.stream("GET", f"/api/jobs/{job_id}/events") as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        for msg in parse_sse(r.iter_lines()):
            seen.append(msg)
            if msg["event"] == "awaiting_approval":  # the stream is live: approve mid-stream
                assert client.post(f"/api/jobs/{job_id}/spec", json={"approve": True}).is_success
    types_ = [m["event"] for m in seen]
    assert types_[0] == "job_started" and types_[-1] == "job_done"
    assert types_.index("awaiting_approval") < types_.index("spec_approved")
    assert types_.count("seed_done") == 6 and types_.count("judge_done") == 6
    assert types_.count("attempt_done") == 2 and "repair_done" in types_
    assert [int(m["id"]) for m in seen] == list(range(len(seen)))
    assert all(m["data"]["type"] == m["event"] and m["data"]["job_id"] == job_id for m in seen)
    seed = next(m for m in seen if m["event"] == "seed_done")["data"]["data"]["seed"]
    assert seed["videos"]["full"] == f"/media/jobs/{job_id}/attempts/0/seed_9000/full.mp4"
    done = seen[-1]["data"]["data"]
    assert done["status"] == "verified"
    assert done["report"]["attempts"][1]["seeds"][0]["videos"]["thumb"].startswith("/media/jobs/")


def test_sse_heartbeat_while_waiting(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(api_app, "HEARTBEAT_S", 0.05)
    job_id = start_job(client)
    pings, waiting, approved, last = 0, False, False, ""
    with client.stream("GET", f"/api/jobs/{job_id}/events") as r:
        for line in r.iter_lines():
            pings += line == ": ping"
            waiting = waiting or line == "event: awaiting_approval"
            if waiting and pings >= 2 and not approved:
                approved = client.post(
                    f"/api/jobs/{job_id}/spec", json={"approve": True}
                ).is_success
            last = line if line.startswith("event:") else last
    assert approved and pings >= 2 and last == "event: job_done"


def test_sse_replay_and_resume(client: TestClient, data_dir: Path):
    job_id = start_job(client, auto_approve=True)
    wait_status(client, job_id, "verified")
    r = client.get(f"/api/jobs/{job_id}/events")
    assert r.status_code == 200
    replay = list(parse_sse(r.text.splitlines()))
    on_disk = (data_dir / "jobs" / job_id / "events.jsonl").read_text().splitlines()
    assert len(replay) == len(on_disk) and replay[-1]["event"] == "job_done"
    assert replay[0]["data"]["ts"] == json.loads(on_disk[0])["ts"]
    assert r.text.startswith("retry: ") and "\nevent: job_done\ndata: {" in r.text

    tail = list(parse_sse(client.get(f"/api/jobs/{job_id}/events?after=2").text.splitlines()))
    assert tail[0]["id"] == "3" and len(tail) == len(replay) - 3
    last_id = replay[-1]["id"]
    resumed = client.get(f"/api/jobs/{job_id}/events", headers={"Last-Event-ID": last_id})
    assert resumed.status_code == 204  # nothing left: EventSource stops reconnecting
    assert client.get("/api/jobs/missing/events").status_code == 404


def test_restart_marks_unfinished_jobs_and_replays_from_disk(tmp_path: Path, fake: FakePipeline):
    data = tmp_path / "data"
    engine = db.make_engine(db.db_path(data))
    with db.session(engine) as s:
        s.add(db.Job(id="20261031-120000-aaaaaa", rules_text=RULES, status="awaiting_approval"))
        s.add(db.Job(id="20261031-110000-bbbbbb", rules_text=RULES, status="verified"))
        s.add(db.Job(id="20261031-100000-cccccc", rules_text=RULES, status="queued"))  # no folder
        s.commit()
    engine.dispose()
    old = [Event(type="job_started", job_id="20261031-120000-aaaaaa", data={"rules": RULES})]
    (data / "jobs" / "20261031-120000-aaaaaa").mkdir(parents=True)
    (data / "jobs" / "20261031-120000-aaaaaa" / "events.jsonl").write_text(
        "".join(e.model_dump_json() + "\n" for e in old)
    )
    finished = [
        Event(type="job_started", job_id="20261031-110000-bbbbbb", data={"rules": RULES}),
        Event(type="job_done", job_id="20261031-110000-bbbbbb", data={"status": "verified"}),
    ]
    (data / "jobs" / "20261031-110000-bbbbbb").mkdir(parents=True)
    (data / "jobs" / "20261031-110000-bbbbbb" / "events.jsonl").write_text(
        "".join(e.model_dump_json() + "\n" for e in finished)
    )

    app = api_app.create_app(data, origins=[ORIGIN], api_token="")
    with TestClient(app, client=LOCAL) as client:
        assert sorted(app.state.jobs.recovered) == [
            "20261031-100000-cccccc",
            "20261031-120000-aaaaaa",
        ]
        assert not (data / "jobs" / "20261031-100000-cccccc").exists()
        assert job_json(client, "20261031-100000-cccccc")["job"]["status"] == "error"
        assert client.get("/api/jobs/20261031-100000-cccccc/events").status_code == 204
        d = job_json(client, "20261031-120000-aaaaaa")
        assert d["job"]["status"] == "error" and d["job"]["error"] == "server restarted"
        replay = list(
            parse_sse(client.get("/api/jobs/20261031-120000-aaaaaa/events").text.splitlines())
        )
        assert [m["event"] for m in replay] == ["job_started", "error", "job_done"]
        assert replay[1]["data"]["data"]["message"] == "server restarted"
        assert replay[2]["data"]["data"]["status"] == "error"
        replay = list(
            parse_sse(client.get("/api/jobs/20261031-110000-bbbbbb/events").text.splitlines())
        )
        assert [m["event"] for m in replay] == ["job_started", "job_done"]
        assert job_json(client, "20261031-110000-bbbbbb")["job"]["status"] == "verified"
    # a second restart does not touch them again
    manager = JobManager(data)
    assert manager.recovered == []
    manager.shutdown()


def test_shutdown_releases_a_job_waiting_for_approval(tmp_path: Path, fake: FakePipeline):
    data = tmp_path / "data"
    app = api_app.create_app(data, origins=[ORIGIN], api_token="", budget_usd=float("inf"))
    with TestClient(app, client=LOCAL) as client:
        job_id = start_job(client)
        wait_status(client, job_id, "awaiting_approval")
    engine = db.make_engine(db.db_path(data))  # the lifespan shutdown has already run
    with db.session(engine) as s:
        job = s.get(db.Job, job_id)
    engine.dispose()
    assert job.status == "error" and "ApprovalCancelled" in (job.error or "")


# ------------------------------------------------------------------------------------- downloads
def test_downloads(client: TestClient):
    job_id = start_job(client, auto_approve=True)
    wait_status(client, job_id, "verified")
    md = client.get(f"/api/jobs/{job_id}/report.md")
    assert md.status_code == 200 and md.headers["content-type"].startswith("text/markdown")
    assert f'filename="{job_id}_report.md"' in md.headers["content-disposition"]
    assert md.text.startswith("# Show & Tell job")
    rj = client.get(f"/api/jobs/{job_id}/report.json")
    assert rj.status_code == 200 and "attachment" in rj.headers["content-disposition"]
    assert rj.json()["final_passed"] == 3
    assert rj.json()["attempts"][0]["seeds"][0]["videos"]["full"].startswith("attempts/0/")
    prog = client.get(f"/api/jobs/{job_id}/program.py")
    assert prog.status_code == 200 and prog.text.endswith(PROGRAM_1)
    assert "# Result: 3/3 test scenes passed (verified)" in prog.text
    compile(prog.text, "program.py", "exec")


def test_downloads_before_the_report_exists(client: TestClient):
    job_id = start_job(client)
    wait_status(client, job_id, "awaiting_approval")
    assert client.get(f"/api/jobs/{job_id}/report.md").status_code == 404
    assert client.get(f"/api/jobs/{job_id}/report.json").status_code == 404
    assert client.get(f"/api/jobs/{job_id}/program.py").status_code == 404
    client.post(f"/api/jobs/{job_id}/spec", json={"approve": True})
    wait_status(client, job_id, "verified")


# ------------------------------------------------------------------------------------- uploads
def test_upload_is_saved_and_served(client: TestClient, fake: FakePipeline, data_dir: Path):
    files = {"video": ("My demo (1).MP4", FAKE_MP4, "video/mp4")}
    r = client.post(
        "/api/jobs", data={"rules": RULES, "auto_approve": "true", "seeds": "3"}, files=files
    )
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    d = wait_status(client, job_id, "verified")
    saved = data_dir / "jobs" / job_id / "input" / "My_demo_1.mp4"
    assert saved.read_bytes() == FAKE_MP4
    assert fake.calls[0].video_path == saved and fake.calls[0].use_video
    url = f"/media/jobs/{job_id}/input/My_demo_1.mp4"
    assert d["job"]["video"] == url and d["media"]["input_video"] == url
    assert d["report"]["video"] == url
    part = client.get(url, headers={"Range": "bytes=0-7"})
    assert part.status_code == 206 and part.content == FAKE_MP4[:8]


@pytest.mark.parametrize(
    ("form", "files", "status", "message"),
    [
        ({}, {"video": ("notes.txt", b"hello", "text/plain")}, 415, ".mp4 or .mov"),
        ({}, {"video": ("fake.mp4", b"0123456789abcdef" * 4, "video/mp4")}, 415, "not an MP4"),
        ({}, {"video": ("big.mov", FAKE_MP4 + b"\x00" * 8192, "video/quicktime")}, 413, "larger"),
        ({}, {"video": ("empty.mp4", b"", "video/mp4")}, 400, "empty"),
        ({"use_video": "true", "task_id": ""}, None, 400, "no video"),
        ({"use_video": "false", "task_id": ""}, None, 400, "no sim objects"),
        ({"task_id": "task99"}, None, 400, "unknown task_id"),
        ({"objects": "red_cube_huge"}, None, 400, "unknown object"),
        ({"objects": "red_cube_large,red_cube_large"}, None, 400, "distinct"),
        ({"spec": '{"rules": []}'}, None, 422, "checks"),
        (
            {"use_video": "true", "task_id": "", "spec": json.dumps(SPEC)},
            {"video": ("demo.mp4", FAKE_MP4, "video/mp4")},
            400,
            "a given spec needs the sim objects",
        ),
        ({"rules": "   "}, None, 422, "rules"),
        ({"seeds": "0"}, None, 422, "seeds"),
    ],
)
def test_upload_and_form_validation(
    client: TestClient, data_dir: Path, form: dict, files: dict | None, status: int, message: str
):
    data = {"rules": RULES, "task_id": "task03_size_glass", "use_video": "false", **form}
    r = client.post("/api/jobs", data=data, files=files)
    assert r.status_code == status, r.text
    assert message in r.text
    assert not list((data_dir / "jobs").glob("*"))  # nothing left behind


def test_oversized_request_is_refused_before_parsing(client: TestClient):
    big = FAKE_MP4 + b"\x00" * (api_app.UPLOAD_SLACK_BYTES + 8192)
    r = client.post(
        "/api/jobs",
        data={"rules": RULES},
        files={"video": ("big.mp4", big, "video/mp4")},
        headers={"Origin": ORIGIN},
    )
    assert r.status_code == 413 and "larger than" in r.json()["detail"]
    assert r.headers["access-control-allow-origin"] == ORIGIN  # the browser can read the error


def test_explicit_spec_program_and_objects_reach_the_pipeline(
    client: TestClient, fake: FakePipeline
):
    form = {
        "task_id": "",
        "objects": "red_cube_large, red_cube_small clear_glass_large",
        "spec": json.dumps(SPEC),
        "program": PROGRAM_1,
        "use_judge": "false",
        "auto_approve": "true",
    }
    job_id = start_job(client, **form)
    d = wait_status(client, job_id, "verified")
    cfg = fake.calls[0]
    assert cfg.object_ids == ["red_cube_large", "red_cube_small", "clear_glass_large"]
    assert cfg.spec == TaskSpec.model_validate(SPEC) and cfg.program_source == PROGRAM_1
    assert not cfg.use_judge and cfg.task_id is None and cfg.n_seeds == 3
    assert d["job"]["object_ids"][0] == "red_cube_large"


def test_task_preset_uses_the_bench_video(
    client: TestClient, fake: FakePipeline, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "repo"
    (root / "bench" / "videos").mkdir(parents=True)
    (root / "bench" / "videos" / "task03_size_glass.mp4").write_bytes(FAKE_MP4)
    monkeypatch.setattr(api_app, "ROOT", root)
    presets = {t["id"]: t for t in client.get("/api/tasks").json()}
    assert presets["task03_size_glass"]["has_video"] and not presets["task01_size"]["has_video"]
    job_id = start_job(client, use_video=True, auto_approve=True)
    d = wait_status(client, job_id, "verified")
    assert fake.calls[0].use_video and fake.calls[0].video_path.name == "task03_size_glass.mp4"
    assert d["job"]["video"] == f"/media/jobs/{job_id}/input/task03_size_glass.mp4"


# ------------------------------------------------------------------------------------- misc routes
def test_cors_headers(client: TestClient):
    pre = client.options(
        "/api/jobs",
        headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST"},
    )
    assert pre.status_code == 200 and pre.headers["access-control-allow-origin"] == ORIGIN
    r = client.get("/api/health", headers={"Origin": ORIGIN})
    assert r.headers["access-control-allow-origin"] == ORIGIN
    other = client.get("/api/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in other.headers


def test_web_origins_from_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("SHOWTELL_WEB_ORIGIN", raising=False)
    assert api_app.web_origins() == ["http://localhost:5173", "http://127.0.0.1:5173"]
    monkeypatch.setenv("SHOWTELL_WEB_ORIGIN", "https://showtell.vercel.app/, http://localhost:4173")
    assert api_app.web_origins() == ["https://showtell.vercel.app", "http://localhost:4173"]


def test_health_tasks_and_bench(client: TestClient, tmp_path: Path):
    health = client.get("/api/health").json()
    assert health["ok"] and "has_api_key" in health and isinstance(health["jobs"], dict)
    tasks = client.get("/api/tasks").json()
    assert [t["id"] for t in tasks][:2] == ["task01_size", "task02_color"] and len(tasks) == 6
    assert tasks[2]["objects"][-1] == "clear_glass_large" and "reference" not in tasks[2]
    missing = client.get("/api/bench")
    assert missing.status_code == 404 and "bench" in missing.json()["detail"]
    (tmp_path / "results.json").write_text(json.dumps({"tasks": [{"id": "task01_size"}]}))
    assert client.get("/api/bench").json() == {"tasks": [{"id": "task01_size"}]}


def test_media_only_serves_media_types(
    client: TestClient, data_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    job_id = start_job(client, auto_approve=True)
    wait_status(client, job_id, "verified")
    assert (data_dir / "showtell.db").exists()
    assert client.get("/media/showtell.db").status_code == 404
    assert client.get(f"/media/jobs/{job_id}/events.jsonl").status_code == 404
    assert client.get(f"/media/jobs/{job_id}/report.json").status_code == 200

    # traversal out of the media root, with a file type /media would otherwise serve
    (data_dir.parent / "secret.json").write_text('{"key": "do-not-serve"}')
    seen: list[str] = []
    lookup = api_app.MediaFiles.lookup_path

    def spy(self: api_app.MediaFiles, path: str) -> tuple[str, Any]:
        seen.append(path)
        return lookup(self, path)

    monkeypatch.setattr(api_app.MediaFiles, "lookup_path", spy)
    for url in (
        "/media/%2e%2e/secret.json",
        "/media/..%2fsecret.json",
        f"/media/jobs/{job_id}/..%2f..%2f..%2fsecret.json",
    ):
        seen.clear()
        r = client.get(url)
        assert r.status_code == 404 and "do-not-serve" not in r.text, url
        assert any(".." in p for p in seen), f"{url} never reached MediaFiles: {seen}"


def test_media_path_mapping(tmp_path: Path):
    job_dir = tmp_path / "jobs" / "j1"
    assert media_rel("attempts/0/seed_1/full.mp4") == "attempts/0/seed_1/full.mp4"
    assert media_rel("./prep/keyframes/kf_00.jpg") == "prep/keyframes/kf_00.jpg"
    assert media_rel(str(job_dir / "input" / "a.mov"), job_dir) == "input/a.mov"
    assert media_rel(str(tmp_path / "elsewhere.mp4"), job_dir) is None
    assert media_rel("../secret.mp4") is None
    assert media_rel("https://x.test/a.mp4") is None
    assert media_rel("put the red block.mp4 away") is None
    mapped = map_media(
        {"videos": {"full": "a/full.mp4"}, "text": "a.mp4 is", "n": 3, "l": ["b.jpg"]}, "/m/"
    )
    assert mapped == {
        "videos": {"full": "/m/a/full.mp4"},
        "text": "a.mp4 is",
        "n": 3,
        "l": ["/m/b.jpg"],
    }


def test_concurrent_event_publishers_are_safe(tmp_path: Path):
    """judge_done events arrive from a thread pool: rows and the event list stay consistent."""
    manager = JobManager(tmp_path / "data", run_job=lambda *a, **k: None)
    config = JobConfig(job_id="20261031-000000-cccccc", rules_text=RULES, task_id="task01_size")
    with db.session(manager.engine) as s:  # register the job without running it
        s.add(db.Job(id=config.job_id, rules_text=RULES, status="running"))
        s.commit()

    def publish(seed: int) -> None:
        summary = {
            "seed": seed,
            "passed": True,
            "videos": {"full": f"attempts/0/seed_{seed}/full.mp4"},
        }
        manager.publish(
            config.job_id, Event(type="seed_done", data={"attempt": 0, "seed": summary})
        )
        verdicts = [{"check_id": "c", "kind": "in_bin", "passed": True, "detail": ""}]
        manager.publish(
            config.job_id,
            {"type": "judge_done", "data": {"attempt": 0, "seed": seed, "verdicts": verdicts}},
        )

    threads = [threading.Thread(target=publish, args=(9000 + i,)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    events, _ = manager.state(config.job_id).snapshot(0)
    assert len(events) == 32 and all(e.job_id == config.job_id for e in events)
    with db.session(manager.engine) as s:
        attempt = db.job_attempts(s, [config.job_id])[config.job_id][0]
        runs = db.attempt_seeds(s, [attempt.id])[attempt.id]
    assert len(runs) == 16 and all(r.sam_json and r.passed for r in runs)
    manager.shutdown()


# ------------------------------------------------------------------------------------- web contract
def web_required_fields(interface: str) -> set[str]:
    """Non-optional fields of `export interface <name>` in web/src/types.ts."""
    text = WEB_TYPES.read_text(encoding="utf-8")
    m = re.search(rf"export interface {interface} \{{\n(.*?)\n\}}", text, re.S)
    assert m, f"interface {interface} not found in {WEB_TYPES}"
    fields = (re.match(r"\s*(\w+)(\??):", line) for line in m.group(1).splitlines())
    return {f.group(1) for f in fields if f and not f.group(2)}


@pytest.mark.skipif(not WEB_TYPES.is_file(), reason="web/src/types.ts is not present")
def test_snapshots_have_the_fields_the_web_reads(tmp_path: Path):
    """GET /api/jobs/{id} while an attempt runs and after the job ends, against web/src/types.ts."""
    release = threading.Event()

    def run_job(config: JobConfig, on_event=None, approve=None) -> JobReport:
        def emit(type_: str, **data: Any) -> None:
            on_event(Event(type=type_, job_id=config.job_id, data=_jsonable(data)))

        emit("job_started", rules=config.rules_text, use_video=False, n_seeds=2)
        emit("spec_ready", spec=SPEC, source="sentence", media_path="none")
        emit("spec_approved", spec=SPEC, edited=False)
        emit("program_ready", attempt=0, source=PROGRAM_0, notes="", kind="initial")
        emit("attempt_started", attempt=0, seeds=[9000, 9001])
        seed = SeedSummary(seed=9000, passed=True, videos=_write_seed_media(
            Path(config.out_dir), 0, 9000))  # fmt: skip
        emit("seed_done", attempt=0, seed=seed)
        release.wait(10)  # the attempt is half done until the test looks at it
        report = JobReport(job_id=config.job_id, created_at=time.time(), rules=config.rules_text,
                           use_video=False, status="failed")  # fmt: skip
        emit("job_done", status="failed", report=report)
        return report

    app = api_app.create_app(
        tmp_path / "data", run_job=run_job, origins=[ORIGIN], api_token="", budget_usd=float("inf")
    )
    with TestClient(app, client=LOCAL) as c:
        job_id = start_job(c, auto_approve=True)
        running = wait_for(
            lambda: (d := job_json(c, job_id))["attempts"] and d["attempts"][0]["seeds"] and d
        )
        release.set()
        done = wait_status(c, job_id, "failed")
    for snap in (running, done):
        assert web_required_fields("ApiJobResponse") <= snap.keys()
        assert web_required_fields("ApiJob") <= snap["job"].keys()
    attempt = running["attempts"][0]
    assert web_required_fields("ApiAttempt") <= attempt.keys()
    assert attempt["program"] == PROGRAM_0 and running["job"]["rules"] == RULES
    assert web_required_fields("SeedSummary") <= attempt["seeds"][0].keys()


# ------------------------------------------------------------------------------------- write guard
def guarded_app(tmp_path: Path, token: str) -> Any:
    return api_app.create_app(
        tmp_path / "data", origins=[ORIGIN], api_token=token, budget_usd=float("inf")
    )


def test_writes_need_the_token_when_one_is_set(tmp_path: Path, fake: FakePipeline):
    with TestClient(guarded_app(tmp_path, "s3cret"), client=("203.0.113.9", 4000)) as c:
        form = {"rules": RULES, "task_id": "task03_size_glass", "use_video": "false"}
        files = {"video": ("demo.mp4", FAKE_MP4, "video/mp4")}
        r = c.post("/api/jobs", data=form, files=files, headers={"Origin": ORIGIN})
        assert r.status_code == 401 and "SHOWTELL_API_TOKEN" in r.json()["detail"]
        assert r.headers["www-authenticate"] == "Bearer"
        assert r.headers["access-control-allow-origin"] == ORIGIN  # the page can read why
        wrong = c.post("/api/jobs", data=form, headers={"Authorization": "Bearer nope"})
        assert wrong.status_code == 401
        assert not list((tmp_path / "data" / "jobs").glob("*"))  # nothing was saved
        assert fake.calls == []

        ok = c.post("/api/jobs", data=form, headers={"Authorization": "Bearer s3cret"})
        assert ok.status_code == 200, ok.text
        job_id = ok.json()["job_id"]
        wait_status(c, job_id, "awaiting_approval")  # reads need no token
        spec_url = f"/api/jobs/{job_id}/spec"
        assert c.post(spec_url, json={"approve": True}).status_code == 401
        approved = c.post(spec_url, json={"approve": True}, headers={"X-Showtell-Token": "s3cret"})
        assert approved.status_code == 200
        wait_status(c, job_id, "verified")
        events = c.get(f"/api/jobs/{job_id}/events")  # the stream ends after job_done
        assert events.status_code == 200 and "event: job_done" in events.text
        assert c.get(f"/api/jobs/{job_id}/report.json").status_code == 200
        assert c.get("/api/health").json()["auth"] == "token"


@pytest.mark.parametrize(
    ("host", "origin", "status"),
    [
        ("127.0.0.1", None, 200),
        ("::1", ORIGIN, 200),
        ("::ffff:127.0.0.1", None, 200),
        ("127.0.0.1", "http://evil.example", 403),  # another site's page in a local browser
        ("127.0.0.1", "null", 403),
        ("192.168.1.20", ORIGIN, 403),  # another machine
        ("testclient", None, 403),
    ],
)
def test_without_a_token_only_local_pages_may_write(
    tmp_path: Path, fake: FakePipeline, host: str, origin: str | None, status: int
):
    with TestClient(guarded_app(tmp_path, ""), client=(host, 4000)) as c:
        headers = {"Origin": origin} if origin else {}
        form = {"rules": RULES, "task_id": "task03_size_glass", "use_video": "false"}
        r = c.post("/api/jobs", data={**form, "auto_approve": "true"}, headers=headers)
        assert r.status_code == status, r.text
        if status == 403:
            assert fake.calls == [] and "detail" in r.json()
        assert c.get("/api/health").json()["auth"] == "local"


def test_loopback_detection():
    assert api_app.is_loopback("127.0.0.1") and api_app.is_loopback("127.8.9.10")
    assert api_app.is_loopback("::1") and api_app.is_loopback("localhost")
    assert not api_app.is_loopback("10.0.0.1") and not api_app.is_loopback("")
    assert not api_app.is_loopback("::ffff:10.0.0.1") and not api_app.is_loopback("testclient")


# ------------------------------------------------------------------------------------- budget
def test_budget_guard_refuses_paid_jobs(
    tmp_path: Path, fake: FakePipeline, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "MODEL_API_KEY", "test-key")
    spend = {"usd": 120.0}
    monkeypatch.setattr(jobs_mod, "api_spend_usd", lambda: spend["usd"])
    app = api_app.create_app(tmp_path / "data", origins=[ORIGIN], api_token="", budget_usd=100.0)
    with TestClient(app, client=LOCAL) as c:
        form = {"rules": RULES, "task_id": "task03_size_glass", "use_video": "false"}
        r = c.post("/api/jobs", data={**form, "auto_approve": "true"})
        assert r.status_code == 402 and "$120.00 spent of $100.00" in r.json()["detail"]
        judged = {**form, "spec": json.dumps(SPEC), "program": PROGRAM_1, "use_judge": "true"}
        assert c.post("/api/jobs", data=judged).status_code == 402
        assert not list((tmp_path / "data" / "jobs").glob("*"))

        free = {**judged, "use_judge": "false", "auto_approve": "true"}
        ok = c.post("/api/jobs", data=free)  # given spec + program, no judge: runs, no repairs
        assert ok.status_code == 200, ok.text
        wait_status(c, ok.json()["job_id"], "verified", "failed")
        assert fake.calls[-1].max_repairs == 0 and not fake.calls[-1].use_judge

        spend["usd"] = 99.0
        under = c.post("/api/jobs", data={**form, "auto_approve": "true"})
        assert under.status_code == 200
        wait_status(c, under.json()["job_id"], "verified")
        assert fake.calls[-1].max_repairs == 3


def test_budget_is_checked_again_when_a_queued_job_starts(
    tmp_path: Path, fake: FakePipeline, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "MODEL_API_KEY", "test-key")
    monkeypatch.setattr(jobs_mod, "api_spend_usd", lambda: 250.0)
    manager = JobManager(tmp_path / "data", run_job=fake, budget_usd=100.0)
    config = JobConfig(job_id="20261031-000000-dddddd", rules_text=RULES, task_id="task01_size")
    manager.create_job(config)
    wait_for(lambda: manager.state(config.job_id).done)
    job = manager.get_job(config.job_id)
    assert job is not None and job.status == "error" and "budget" in (job.error or "")
    assert fake.calls == []  # the pipeline never ran
    manager.shutdown()


def test_free_jobs_and_keyless_servers_skip_the_budget(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(jobs_mod, "api_spend_usd", lambda: 1e6)
    paid = JobConfig(job_id="j", rules_text=RULES)
    free = paid.model_copy(
        update={"spec": TaskSpec.model_validate(SPEC), "program_source": PROGRAM_1,
                "use_judge": False, "max_repairs": 0}
    )  # fmt: skip
    monkeypatch.setattr(settings, "MODEL_API_KEY", "")
    assert jobs_mod.budget_error(paid, 100.0) is None  # nothing can be billed without a key
    monkeypatch.setattr(settings, "MODEL_API_KEY", "test-key")
    assert "budget" in (jobs_mod.budget_error(paid, 100.0) or "")
    assert jobs_mod.budget_error(free, 100.0) is None
    assert jobs_mod.paid_job(free.model_copy(update={"max_repairs": 1}))
