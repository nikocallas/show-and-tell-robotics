"""Background job runner for the API (CLAUDE.md 5.9).

`JobManager` runs `showtell.pipeline.run_job` on a ThreadPoolExecutor (max_workers=1 by default:
the sim process pool lives inside the pipeline). Per job it keeps an in-memory event list guarded
by a threading.Condition that SSE subscribers wait on, and saves rows as events arrive:
program_ready/repair_*/attempt_* -> Attempt, seed_done -> SeedRun, judge_done -> SeedRun.sam_json,
spec_ready/awaiting_approval/spec_approved -> Job.spec_json/status, job_done -> Job.report_json.
Interactive jobs block in the pipeline's `approve()` callback until `submit_approval()`
(POST /api/jobs/{id}/spec). On startup, jobs a previous process left unfinished are marked error
("server restarted"); events of jobs not run by this process are reloaded from
<job>/events.jsonl, so SSE replay works after restarts. A job that could call the paid Meta API
does not start once the logged spend (data/usage.jsonl) has reached the budget (`budget_error`).

The views built here keep media paths relative to the job folder until `map_media` turns them into
URLs (`/media/jobs/<id>/...` for the API, `media/<id>/...` for the demo bundle).
"""

from __future__ import annotations

import importlib
import logging
import re
import secrets
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import wait as wait_futures
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import BaseModel
from sqlalchemy.engine import Engine

from showtell.agent.schemas import TaskSpec
from showtell.api import db
from showtell.config import settings
from showtell.contracts import Event, JobConfig, JobReport

log = logging.getLogger("showtell.api.jobs")

RunJob = Callable[..., Any]  # showtell.pipeline.run_job(config, on_event=..., approve=...)
MEDIA_EXTS = (".mp4", ".mov", ".m4v", ".webm", ".jpg", ".jpeg", ".png", ".gif")
IMAGE_EXTS = (".jpg", ".jpeg", ".png")
RESTART_ERROR = "server restarted"
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_ATTEMPT_EVENTS = (
    "program_ready",
    "repair_started",
    "repair_done",
    "attempt_started",
    "attempt_done",
)


class UnknownJob(KeyError):
    """No job with this id in the database."""


class NotAwaitingApproval(RuntimeError):
    """POST /spec arrived while the job was not waiting for one."""


class ApprovalCancelled(RuntimeError):
    """The server stopped while a job was waiting for spec approval."""


class BudgetExceeded(RuntimeError):
    """The logged Meta API spend has reached the budget, so a paid job may not start."""


def new_job_id() -> str:
    """Sortable, URL-safe id like 20261031-134501-a1b2c3 (same shape as the pipeline's)."""
    return time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)


def valid_job_id(job_id: str) -> bool:
    return bool(_JOB_ID_RE.match(job_id)) and ".." not in job_id


def resolve_run_job() -> RunJob:
    """Import the pipeline lazily, so the API starts even when the pipeline cannot be imported."""
    return importlib.import_module("showtell.pipeline").run_job


# ------------------------------------------------------------------------------------- budget
def api_spend_usd() -> float:
    """Total Meta Model API spend logged in data/usage.jsonl (local cache hits cost $0)."""
    from showtell.meta import usage

    return float(usage.summarize().total_usd)


def paid_job(config: JobConfig) -> bool:
    """Could this job call the paid Meta API? Only a given spec and program, without the SAM
    judge and without repairs, is free."""
    return (
        config.spec is None
        or config.program_source is None
        or config.use_judge
        or config.max_repairs > 0
    )


def budget_error(config: JobConfig, budget_usd: float) -> str | None:
    """Why `config` may not run: the logged spend has reached `budget_usd` (None = it may run)."""
    if not settings.has_api_key or not paid_job(config):
        return None  # without a key nothing can be billed
    spent = api_spend_usd()
    if spent < budget_usd:
        return None
    return (
        f"the Meta API budget is used up: ${spent:.2f} spent of ${budget_usd:.2f}; until it is "
        "raised (SHOWTELL_BUDGET_USD), only jobs with a given spec and program and "
        "use_judge=false can run"
    )


# ------------------------------------------------------------------------------------- media paths
def is_media_path(value: Any) -> bool:
    """A path-like string to a video or image (not a URL, not free text, not already mapped)."""
    return (
        isinstance(value, str)
        and 0 < len(value) <= 1024
        and value.lower().endswith(MEDIA_EXTS)
        and not any(c.isspace() for c in value)
        and "://" not in value
        and not value.startswith(("data:", "/media/", "media/"))
    )


def media_rel(value: Any, job_dir: Path | None = None) -> str | None:
    """The job-relative posix path of a media path (relative, or absolute inside `job_dir`)."""
    if not is_media_path(value):
        return None
    p = Path(value)
    if p.is_absolute():
        if job_dir is None:
            return None
        try:
            p = p.resolve().relative_to(job_dir.resolve())
        except (OSError, ValueError):
            return None
    parts = PurePosixPath(p.as_posix()).parts
    if not parts or ".." in parts:
        return None
    return "/".join(parts)


def map_strings(obj: Any, fn: Callable[[str], str]) -> Any:
    """Apply `fn` to every string leaf of a JSON-like structure (keys untouched)."""
    if isinstance(obj, dict):
        return {k: map_strings(v, fn) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [map_strings(v, fn) for v in obj]
    if isinstance(obj, str):
        return fn(obj)
    return obj


def map_media(obj: Any, prefix: str, job_dir: Path | None = None) -> Any:
    """Prefix every media path in `obj` (e.g. "/media/jobs/<id>/") so a browser can load it."""

    def fn(s: str) -> str:
        rel = media_rel(s, job_dir)
        return f"{prefix}{rel}" if rel else s

    return map_strings(obj, fn)


def media_prefix(job_id: str) -> str:
    return f"/media/jobs/{job_id}/"


def folder_media(job_dir: Path) -> dict[str, Any]:
    """Input video, normalized video and keyframes present in a job folder (relative paths)."""
    inputs = sorted(
        p
        for p in (job_dir / "input").glob("*")
        if p.is_file() and p.suffix.lower() in (".mp4", ".mov")
    )
    normalized = job_dir / "prep" / "normalized.mp4"
    keyframes = sorted(
        p for p in (job_dir / "prep" / "keyframes").glob("*") if p.suffix.lower() in IMAGE_EXTS
    )
    return {
        "input_video": inputs[0].relative_to(job_dir).as_posix() if inputs else None,
        "normalized_video": "prep/normalized.mp4" if normalized.is_file() else None,
        "keyframes": [k.relative_to(job_dir).as_posix() for k in keyframes],
    }


def failure_frames(job_dir: Path, n: int) -> list[str]:
    """Frames of attempt `n`'s first failing scene that were sent to the repair call."""
    d = job_dir / "attempts" / str(n) / "repair_frames"
    return [
        p.relative_to(job_dir).as_posix()
        for p in sorted(d.glob("*"))
        if p.suffix.lower() in IMAGE_EXTS
    ]


# ------------------------------------------------------------------------------------- views
def links(job_id: str) -> dict[str, str]:
    base = f"/api/jobs/{job_id}"
    return {
        "self": base,
        "events": f"{base}/events",
        "report_md": f"{base}/report.md",
        "report_json": f"{base}/report.json",
        "program": f"{base}/program.py",
    }


def seed_fields(sd: dict[str, Any]) -> dict[str, Any]:
    """SeedRun columns from a SeedSummary dict (`sam_json` only when SAM verdicts are present)."""
    videos = sd.get("videos") or {}
    fields: dict[str, Any] = {
        "passed": bool(sd.get("passed")),
        "checks_json": db.dumps(sd.get("checks") or []),
        "full_video": videos.get("full"),
        "judge_video": videos.get("overhead") or videos.get("judge"),
        "judge_angle_video": videos.get("judge_angle"),
        "thumb": videos.get("thumb"),
        "error": sd.get("error"),
        "sim_seconds": float(sd.get("sim_seconds") or 0.0),
        "touch_events_json": db.dumps(sd.get("touch_events") or []),
    }
    if sd.get("sam") is not None:
        fields["sam_json"] = db.dumps(sd["sam"])
    return fields


def seed_view(row: db.SeedRun) -> dict[str, Any]:
    """A SeedRun as a SeedSummary-shaped dict (the same shape as seed_done.data.seed)."""
    videos = {
        "full": row.full_video,
        "overhead": row.judge_video,
        "judge_angle": row.judge_angle_video,
        "thumb": row.thumb,
    }
    return {
        "seed": row.seed,
        "passed": row.passed,
        "checks": db.loads(row.checks_json, []),
        "error": row.error,
        "videos": {k: v for k, v in videos.items() if v},
        "sim_seconds": row.sim_seconds,
        "touch_events": db.loads(row.touch_events_json, []),
        "sam": db.loads(row.sam_json),
    }


def attempt_view(a: db.Attempt, seeds: list[db.SeedRun], job_dir: Path) -> dict[str, Any]:
    """An attempt with its seeds; `passed` counts finished seeds while the attempt still runs."""
    views = [seed_view(r) for r in seeds]
    return {
        "n": a.n,
        "kind": a.kind,
        "program": a.program_src,
        "notes": a.notes,
        "diagnosis": a.diagnosis,
        "passed": a.passed if a.done else sum(1 for v in views if v["passed"]),
        "total": a.total or len(views),
        "done": a.done,
        "agreement": db.loads(a.agreement_json),
        "wall_seconds": a.wall_seconds,
        "seeds": views,
        "failure_frames": failure_frames(job_dir, a.n),
    }


def attempt_view_from_report(a: dict[str, Any], job_dir: Path) -> dict[str, Any]:
    """The same shape as `attempt_view`, from an AttemptReport dict (demo export)."""
    n = int(a.get("n") or 0)
    return {
        "n": n,
        "kind": a.get("kind") or ("initial" if n == 0 else "repair"),
        "program": a.get("program") or "",
        "notes": a.get("notes") or "",
        "diagnosis": a.get("diagnosis"),
        "passed": int(a.get("passed") or 0),
        "total": int(a.get("total") or 0),
        "done": True,
        "agreement": a.get("agreement"),
        "wall_seconds": float(a.get("wall_seconds") or 0.0),
        "seeds": list(a.get("seeds") or []),
        "failure_frames": failure_frames(job_dir, n),
    }


def job_view(job: db.Job, stage: str | None = None) -> dict[str, Any]:
    spec = db.loads(job.spec_json)
    return {
        "id": job.id,
        "created_at": job.created_at,
        "status": job.status,
        "stage": stage,
        "rules": job.rules_text,
        "use_video": job.use_video,
        "auto_approve": job.auto_approve,
        "use_judge": job.use_judge,
        "n_seeds": job.n_seeds,
        "task_id": job.task_id,
        "object_ids": db.loads(job.object_ids_json, []),
        "video": job.video_path,
        "spec": spec,
        "clarifying_question": spec.get("clarifying_question") if isinstance(spec, dict) else None,
        "approved": job.approved,
        "awaiting_approval": job.status == "awaiting_approval",
        "error": job.error,
        "finished_at": job.finished_at,
        "links": links(job.id),
    }


def job_summary(job: db.Job, attempts: list[db.Attempt]) -> dict[str, Any]:
    """One row of GET /api/jobs."""
    last = attempts[-1] if attempts else None
    return {
        "id": job.id,
        "created_at": job.created_at,
        "status": job.status,
        "rules": job.rules_text,
        "task_id": job.task_id,
        "use_video": job.use_video,
        "attempts": len(attempts),
        "passed": last.passed if last is not None and last.done else None,
        "total": last.total if last is not None else None,
        "error": job.error,
    }


def fallback_report(job: db.Job, error: str) -> JobReport:
    """A minimal JobReport for a job that ended without one (crash or restart)."""
    spec = None
    raw = db.loads(job.spec_json)
    if raw:
        try:
            spec = TaskSpec.model_validate(raw)
        except ValueError:
            spec = None
    return JobReport(
        job_id=job.id,
        created_at=job.created_at,
        rules=job.rules_text,
        use_video=job.use_video,
        task_id=job.task_id,
        object_ids=db.loads(job.object_ids_json, []) or [],
        video=job.video_path,
        spec=spec,
        status="error",
        error=error,
    )


# ------------------------------------------------------------------------------------- events
def read_events(path: Path) -> list[Event]:
    """Events from an events.jsonl file (bad lines are skipped)."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    events: list[Event] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            events.append(Event.model_validate_json(line))
        except ValueError:
            log.warning("skipping a bad line in %s", path)
    return events


def append_events(path: Path, events: list[Event]) -> None:
    if not events:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for ev in events:
            f.write(ev.model_dump_json() + "\n")


def _normalize(event: Event | dict[str, Any], job_id: str) -> Event:
    """A private, JSON-safe copy of an event with job_id filled in."""
    raw = event.model_dump(mode="json") if isinstance(event, BaseModel) else dict(event)
    ev = Event.model_validate(raw)
    if ev.job_id is None:
        ev.job_id = job_id
    return ev


@dataclass(eq=False)
class JobState:
    """In-memory side of one job: its events (for SSE) and the spec-approval handshake."""

    job_id: str
    job_dir: Path
    auto_approve: bool = False
    events: list[Event] = field(default_factory=list)
    cond: threading.Condition = field(default_factory=threading.Condition)
    done: bool = False
    stage: str | None = None
    saw_job_done: bool = False
    future: Future | None = None
    awaiting: bool = False
    approval: threading.Event = field(default_factory=threading.Event)
    decision: TaskSpec | None = None
    cancelled: bool = False
    extra: list[Event] = field(
        default_factory=list
    )  # terminal events made here, not by the pipeline

    def snapshot(self, cursor: int) -> tuple[list[Event], bool]:
        """Events after `cursor` and whether the job is over (read atomically)."""
        with self.cond:
            return self.events[cursor:], self.done

    def wait(self, cursor: int, timeout: float) -> tuple[list[Event], bool]:
        """Block until there are events after `cursor`, the job is over, or `timeout` passes."""
        with self.cond:
            self.cond.wait_for(lambda: self.done or len(self.events) > cursor, timeout=timeout)
            return self.events[cursor:], self.done


# ------------------------------------------------------------------------------------- manager
class JobManager:
    """Queues pipeline jobs, records their events and rows, and brokers spec approval."""

    def __init__(
        self,
        data_dir: Path | None = None,
        *,
        engine: Engine | None = None,
        max_workers: int = 1,
        run_job: RunJob | None = None,
        budget_usd: float | None = None,
    ) -> None:
        self.data_dir = Path(data_dir or settings.SHOWTELL_DATA_DIR)
        self.budget_usd = settings.BUDGET_WARN_USD if budget_usd is None else budget_usd
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.engine = engine or db.make_engine(db.db_path(self.data_dir))
        self._run_job = run_job
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="showtell-job")
        self._states: dict[str, JobState] = {}
        self._lock = threading.RLock()  # the states dict and approval handshakes
        self._db_lock = threading.Lock()  # one SQLite writer at a time
        self._closing = False
        self.recovered = self.recover()

    # ---------------------------------------------------------------- lookups
    def job_dir(self, job_id: str) -> Path:
        return self.data_dir / "jobs" / job_id

    def get_job(self, job_id: str) -> db.Job | None:
        if not valid_job_id(job_id):
            return None
        with db.session(self.engine) as s:
            return s.get(db.Job, job_id)

    def state(self, job_id: str) -> JobState:
        """The live state of a job; jobs not run by this process replay their events.jsonl."""
        with self._lock:
            st = self._states.get(job_id)
        if st is not None:
            return st
        job = self.get_job(job_id)
        if job is None:
            raise UnknownJob(job_id)
        st = JobState(job.id, self.job_dir(job.id), auto_approve=job.auto_approve, done=True)
        st.events = read_events(st.job_dir / "events.jsonl")
        st.saw_job_done = any(e.type == "job_done" for e in st.events)
        with self._lock:
            return self._states.setdefault(job_id, st)

    def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        """Recent jobs, newest first."""
        with db.session(self.engine) as s:
            jobs = db.list_jobs(s, limit)
            attempts = db.job_attempts(s, [j.id for j in jobs])
        return [job_summary(j, attempts[j.id]) for j in jobs]

    def detail(self, job_id: str) -> dict[str, Any]:
        """{job, attempts: [{..., seeds}], report, media} with media paths as /media URLs."""
        job = self.get_job(job_id)
        if job is None:
            raise UnknownJob(job_id)
        with self._lock:
            st = self._states.get(job_id)
        stage = st.stage if st is not None and not st.done else None
        with db.session(self.engine) as s:
            attempts = db.job_attempts(s, [job_id])[job_id]
            seeds = db.attempt_seeds(s, [a.id for a in attempts if a.id is not None])
        jd = self.job_dir(job_id)
        view = {
            "job": job_view(job, stage),
            "attempts": [attempt_view(a, seeds.get(a.id or -1, []), jd) for a in attempts],
            "report": db.loads(job.report_json),
            "media": folder_media(jd),
        }
        return map_media(view, media_prefix(job_id), jd)

    def counts(self) -> dict[str, int]:
        with db.session(self.engine) as s:
            return db.status_counts(s)

    # ---------------------------------------------------------------- submit and run
    def create_job(self, config: JobConfig, *, object_ids: list[str] | None = None) -> db.Job:
        """Save the job row and queue the pipeline. `object_ids` is for display (task objects)."""
        jd = self.job_dir(config.job_id)
        config = config.model_copy(update={"out_dir": jd})
        jd.mkdir(parents=True, exist_ok=True)
        video_rel = None
        if config.video_path is not None:
            video_rel = media_rel(str(Path(config.video_path).resolve()), jd) or str(
                config.video_path
            )
        job = db.Job(
            id=config.job_id,
            created_at=time.time(),
            status="queued",
            rules_text=config.rules_text,
            use_video=config.use_video,
            video_path=video_rel,
            task_id=config.task_id,
            object_ids_json=db.dumps(object_ids or config.object_ids),
            n_seeds=config.n_seeds,
            auto_approve=not config.interactive,
            use_judge=config.use_judge,
            config_json=config.model_dump_json(),
        )
        with self._db_lock, db.session(self.engine) as s:
            s.add(job)
            s.commit()
        st = JobState(job.id, jd, auto_approve=job.auto_approve)
        with self._lock:
            self._states[job.id] = st
        try:
            st.future = self._pool.submit(self._execute, job.id)
        except RuntimeError:  # the pool is shut down: the server is stopping
            self._update_job(
                job.id, status="error", error="server is shutting down", finished_at=time.time()
            )
            with st.cond:
                st.done = True
                st.cond.notify_all()
        return job

    def _execute(self, job_id: str) -> None:
        """Worker: run the pipeline; make sure the job always ends with a job_done event."""
        st = self.state(job_id)
        job = self.get_job(job_id)
        try:
            if job is None or not job.config_json:
                raise UnknownJob(job_id)
            config = JobConfig.model_validate_json(job.config_json)
            problem = budget_error(config, self.budget_usd)  # spend may have grown while queued
            if problem:
                raise BudgetExceeded(problem)
            self._update_job(job_id, status="running")
            run_job = self._run_job or resolve_run_job()
            result = run_job(
                config,
                on_event=lambda ev: self.publish(job_id, ev),
                approve=lambda spec, question: self._approve(job_id, spec, question),
            )
            if not st.saw_job_done:  # the pipeline returned without announcing its report
                if result is None:
                    raise RuntimeError("the pipeline returned no report")
                report = (
                    result if isinstance(result, JobReport) else JobReport.model_validate(result)
                )
                self._publish_extra(
                    st,
                    "job_done",
                    {"status": report.status, "report": report.model_dump(mode="json")},
                )
        except Exception as exc:
            log.exception("job %s failed", job_id)
            msg = f"{type(exc).__name__}: {exc}"
            if not st.saw_job_done:
                self._publish_extra(st, "error", {"message": msg, "stage": st.stage})
                report = fallback_report(job, msg).model_dump(mode="json") if job else None
                self._publish_extra(st, "job_done", {"status": "error", "report": report})
        finally:
            self._finish(st)

    def _finish(self, st: JobState) -> None:
        job = self.get_job(st.job_id)
        if job is not None and job.status in db.ACTIVE_STATUSES:
            self._update_job(
                st.job_id,
                status="error",
                error=job.error or "the pipeline ended without a final status",
                finished_at=time.time(),
            )
        with st.cond:
            st.done = True
            st.awaiting = False
            st.cond.notify_all()
        path = st.job_dir / "events.jsonl"
        try:  # the pipeline writes events.jsonl itself; add what only this process saw
            append_events(path, st.extra if path.exists() else st.snapshot(0)[0])
        except OSError:
            log.exception("could not write %s", path)

    # ---------------------------------------------------------------- events
    def publish(self, job_id: str, event: Event | dict[str, Any]) -> Event:
        """The pipeline's on_event callback: save rows, then wake SSE subscribers."""
        return self._publish(self.state(job_id), _normalize(event, job_id))

    def _publish(self, st: JobState, ev: Event) -> Event:
        if ev.type == "awaiting_approval" and not st.auto_approve:
            with self._lock:
                self._arm(st)
        try:
            self._persist(st, ev)
        except Exception:  # bookkeeping must never break the job or the stream
            log.exception("could not save %s for job %s", ev.type, st.job_id)
        with st.cond:
            st.events.append(ev)
            if ev.type == "stage_started":
                st.stage = ev.data.get("stage")
            elif ev.type == "job_done":
                st.saw_job_done = True
                st.stage = None
            st.cond.notify_all()
        return ev

    def _publish_extra(self, st: JobState, type_: str, data: dict[str, Any]) -> None:
        ev = Event(type=type_, job_id=st.job_id, data=data)
        st.extra.append(ev)
        self._publish(st, ev)

    def _persist(self, st: JobState, ev: Event) -> None:
        d, t = ev.data, ev.type
        if t in ("spec_ready", "awaiting_approval", "spec_approved"):
            fields: dict[str, Any] = {}
            if d.get("spec") is not None:
                fields["spec_json"] = db.dumps(d["spec"])
            if t == "awaiting_approval" and not st.auto_approve and not st.approval.is_set():
                fields["status"] = "awaiting_approval"
            if t == "spec_approved":
                with self._lock:
                    st.awaiting = False
                fields.update(approved=True, status="running")
            self._update_job(st.job_id, **fields)
        elif t in _ATTEMPT_EVENTS:
            self._persist_attempt(st.job_id, t, d)
        elif t in ("seed_done", "judge_done"):
            self._persist_seed(st.job_id, t, d)
        elif t == "job_done":
            self._store_report(st.job_id, d)
        elif t == "error":
            self._update_job(st.job_id, error=str(d.get("message") or "error"))

    def _persist_attempt(self, job_id: str, t: str, d: dict[str, Any]) -> None:
        n = d.get("attempt")
        if not isinstance(n, int):
            return
        fields: dict[str, Any] = {}
        if t == "program_ready":
            fields = {"program_src": d.get("source") or "", "notes": d.get("notes") or ""}
            if d.get("kind") in ("initial", "repair"):
                fields["kind"] = d["kind"]
        elif t in ("repair_started", "repair_done"):
            fields = {"kind": "repair"}
            if d.get("source"):
                fields["program_src"] = d["source"]
        elif t == "attempt_started":
            fields = {"total": len(d.get("seeds") or [])}
        elif t == "attempt_done":
            fields = {
                "passed": int(d.get("passed") or 0),
                "total": int(d.get("total") or 0),
                "done": True,
                "agreement_json": db.dumps(d.get("agreement")),
            }
        if d.get("diagnosis"):
            fields["diagnosis"] = d["diagnosis"]
        with self._db_lock, db.session(self.engine) as s:
            db.upsert_attempt(s, job_id, n, **fields)
            s.commit()

    def _persist_seed(self, job_id: str, t: str, d: dict[str, Any]) -> None:
        n = d.get("attempt")
        if not isinstance(n, int):
            return
        if t == "seed_done":
            sd = d.get("seed")
            if not isinstance(sd, dict) or not isinstance(sd.get("seed"), int):
                return
            seed, fields = sd["seed"], seed_fields(sd)
        else:
            seed = d.get("seed")
            if not isinstance(seed, int):
                return
            fields = {"sam_json": db.dumps(d.get("verdicts") or [])}
        with self._db_lock, db.session(self.engine) as s:
            attempt = db.upsert_attempt(s, job_id, n)
            db.upsert_seed_run(s, attempt.id, seed, **fields)
            s.commit()

    def _store_report(self, job_id: str, d: dict[str, Any]) -> None:
        """job_done: final status + report, and the attempt/seed rows reconciled from it."""
        report = d.get("report") if isinstance(d.get("report"), dict) else None
        status = d.get("status") or (report or {}).get("status")
        error = (report or {}).get("error")
        if status not in db.FINAL_STATUSES:
            status = "error"
            error = error or f"the pipeline finished with status {d.get('status')!r}"
        if status == "error" and not error:
            error = "the pipeline ended with an error"
        with self._db_lock, db.session(self.engine) as s:
            job = s.get(db.Job, job_id)
            if job is None:
                return
            job.status, job.error, job.finished_at = status, error, time.time()
            if report is not None:
                job.report_json = db.dumps(report)
                if report.get("object_ids"):
                    job.object_ids_json = db.dumps(report["object_ids"])
                if report.get("spec"):
                    job.spec_json = db.dumps(report["spec"])
                for a in report.get("attempts") or []:
                    if not isinstance(a, dict) or not isinstance(a.get("n"), int):
                        continue
                    row = db.upsert_attempt(
                        s,
                        job_id,
                        a["n"],
                        kind=a.get("kind") or ("initial" if a["n"] == 0 else "repair"),
                        program_src=a.get("program") or "",
                        notes=a.get("notes") or "",
                        diagnosis=a.get("diagnosis"),
                        passed=int(a.get("passed") or 0),
                        total=int(a.get("total") or 0),
                        done=True,
                        agreement_json=db.dumps(a.get("agreement")),
                        wall_seconds=float(a.get("wall_seconds") or 0.0),
                    )
                    for sd in a.get("seeds") or []:
                        if isinstance(sd, dict) and isinstance(sd.get("seed"), int):
                            db.upsert_seed_run(s, row.id, sd["seed"], **seed_fields(sd))
            s.add(job)
            s.commit()

    def _update_job(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        with self._db_lock, db.session(self.engine) as s:
            job = s.get(db.Job, job_id)
            if job is None:
                return
            for k, v in fields.items():
                setattr(job, k, v)
            s.add(job)
            s.commit()

    # ---------------------------------------------------------------- approval
    def _arm(self, st: JobState) -> None:
        """Open the approval handshake (caller holds self._lock); keeps an early decision."""
        if not st.awaiting:
            st.awaiting = True
            st.decision = None
            st.cancelled = False
            st.approval.clear()

    def _announced(self, st: JobState) -> bool:
        """Did the pipeline already emit awaiting_approval for the current spec?"""
        with st.cond:
            for ev in reversed(st.events):
                if ev.type == "awaiting_approval":
                    return True
                if ev.type in ("spec_ready", "spec_approved"):
                    return False
        return False

    def _approve(self, job_id: str, spec: TaskSpec, question: str | None) -> TaskSpec:
        """The pipeline's approve callback: block until POST /spec approves or edits the spec."""
        st = self.state(job_id)
        if not isinstance(spec, TaskSpec):
            spec = TaskSpec.model_validate(spec)
        if st.auto_approve:
            return spec
        with self._lock:
            self._arm(st)
        if not st.approval.is_set():
            self._update_job(job_id, status="awaiting_approval", spec_json=spec.model_dump_json())
        if not self._announced(st):
            data = {"spec": spec.model_dump(mode="json"), "clarifying_question": question}
            self._publish(st, Event(type="awaiting_approval", job_id=job_id, data=data))
        while not st.approval.wait(timeout=0.5):
            if self._closing:
                break
        with self._lock:
            st.awaiting = False
            if st.cancelled or not st.approval.is_set():
                raise ApprovalCancelled("the server stopped before the spec was approved")
            decision = st.decision or spec
        self._update_job(
            job_id, status="running", approved=True, spec_json=decision.model_dump_json()
        )
        return decision

    def submit_approval(self, job_id: str, spec: TaskSpec | None = None) -> None:
        """Release a job waiting for approval; `spec` (if given) replaces the proposed spec."""
        with self._lock:
            st = self._states.get(job_id)
            if st is None or st.done or not st.awaiting or st.approval.is_set():
                raise NotAwaitingApproval(job_id)
            st.decision = spec
            st.approval.set()
        fields: dict[str, Any] = {"approved": True, "status": "running"}
        if spec is not None:
            fields["spec_json"] = spec.model_dump_json()
        self._update_job(job_id, **fields)

    # ---------------------------------------------------------------- lifecycle
    def recover(self) -> list[str]:
        """Mark jobs a previous process left unfinished as error ("server restarted")."""
        now = time.time()
        with self._db_lock, db.session(self.engine) as s:
            stale = db.jobs_with_status(s, db.ACTIVE_STATUSES)
            reports = {}
            for job in stale:
                reports[job.id] = fallback_report(job, RESTART_ERROR).model_dump(mode="json")
                job.status, job.error, job.finished_at = "error", RESTART_ERROR, now
                job.report_json = db.dumps(reports[job.id])
                s.add(job)
            s.commit()
        for job in stale:
            path = self.job_dir(job.id) / "events.jsonl"
            past = read_events(path)
            if not path.parent.is_dir() or (past and past[-1].type == "job_done"):
                continue
            try:
                append_events(
                    path,
                    [
                        Event(type="error", job_id=job.id, data={"message": RESTART_ERROR}),
                        Event(
                            type="job_done",
                            job_id=job.id,
                            data={"status": "error", "report": reports[job.id]},
                        ),
                    ],
                )
            except OSError:
                log.exception("could not write %s", path)
        if stale:
            log.warning("marked %d unfinished job(s) as error: %s", len(stale), RESTART_ERROR)
        return [j.id for j in stale]

    def shutdown(self, timeout: float = 5.0) -> None:
        """Stop taking jobs, release approval waits and give a running job `timeout` s to end.

        A job inside the simulator cannot be interrupted; it keeps the process alive until done.
        """
        self._closing = True
        with self._lock:
            states = list(self._states.values())
            for st in states:
                if st.awaiting and not st.approval.is_set():
                    st.cancelled = True
                    st.approval.set()
        self._pool.shutdown(wait=False, cancel_futures=True)
        for st in states:
            if st.future is not None and st.future.cancelled() and not st.done:
                self._update_job(
                    st.job_id,
                    status="error",
                    error="server stopped before the job ran",
                    finished_at=time.time(),
                )
                with st.cond:
                    st.done = True
                    st.cond.notify_all()
        running = [st.future for st in states if st.future is not None and not st.future.done()]
        if running:
            wait_futures(running, timeout=timeout)
        self.engine.dispose()
