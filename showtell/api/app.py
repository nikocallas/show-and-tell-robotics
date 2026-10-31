"""FastAPI backend (CLAUDE.md 5.9): create jobs, approve specs, stream events, download reports.

Run with `uvicorn showtell.api.app:app` in ONE worker process (jobs and their event streams live in
memory). Add `--timeout-graceful-shutdown 3` so open event streams do not hold up Ctrl+C.

Endpoints (all JSON unless noted):
    POST /api/jobs                    multipart: rules, video?, use_video, auto_approve, task_id?,
                                      seeds, use_judge, objects?, spec? (TaskSpec JSON), program?
                                      -> {job_id}
    GET  /api/jobs                    recent jobs, newest first
    GET  /api/jobs/{id}               {job, attempts: [{..., seeds: [...]}], report, media}
    POST /api/jobs/{id}/spec          {"approve": true} or a full TaskSpec -> {ok}
    GET  /api/jobs/{id}/events        text/event-stream, one message per Event:
                                      "id: <n>\\nevent: <type>\\ndata: <Event JSON>\\n\\n"
                                      (replays past events, then streams; ends after job_done;
                                      honours Last-Event-ID / ?after=<id>; ": ping" every 15 s)
    GET  /api/jobs/{id}/report.md | report.json | program.py      downloads
    GET  /api/bench | /api/tasks | /api/health
    GET  /media/...                   files under SHOWTELL_DATA_DIR (videos, images, reports)
Media paths in responses and events are URL paths, e.g.
/media/jobs/<id>/attempts/0/seed_9000/full.mp4.

Who may start jobs (every POST under /api, checked before the body is read): with
SHOWTELL_API_TOKEN set, requests need "Authorization: Bearer <token>" (or X-Showtell-Token);
without it, only clients on this machine, and from a browser only pages on an allowed web origin
(SHOWTELL_WEB_ORIGIN), so another site cannot start jobs on a developer's server. Set the token
for any server other machines can reach, including through a local proxy or tunnel that does not
forward the client address. Reads stay open (EventSource and <video> cannot send headers).
A job that could call the paid Meta API is refused (402) once the logged spend reaches the budget
(SHOWTELL_BUDGET_USD, default BUDGET_WARN_USD).
"""

from __future__ import annotations

import contextlib
import ipaddress
import json
import os
import re
import secrets
import shutil
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.util import find_spec
from pathlib import Path, PurePosixPath
from typing import Any

import anyio
from fastapi import (
    APIRouter,
    Body,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from showtell.agent.schemas import TaskSpec
from showtell.api import db
from showtell.api.jobs import (
    JobManager,
    JobState,
    NotAwaitingApproval,
    RunJob,
    UnknownJob,
    budget_error,
    map_media,
    media_prefix,
    new_job_id,
)
from showtell.config import ROOT, settings
from showtell.contracts import Event, JobConfig

try:
    from showtell import __version__
except ImportError:  # `showtell` imported as a namespace package (editable install, other cwd)
    __version__ = "unknown"

MB = 1024 * 1024
MAX_UPLOAD_BYTES = 300 * MB
UPLOAD_SLACK_BYTES = 1 * MB  # multipart framing and the other form fields
VIDEO_EXTS = (".mp4", ".mov")
MP4_BOXES = (b"ftyp", b"moov", b"mdat", b"wide", b"free", b"skip", b"pnot")  # ISO-BMFF / QuickTime
MAX_SEEDS = 100
MAX_OBJECTS = 12
HEARTBEAT_S = 15.0
SSE_RETRY_MS = 3000
DEFAULT_WEB_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"
API_TOKEN_ENV = "SHOWTELL_API_TOKEN"
READ_METHODS = ("GET", "HEAD", "OPTIONS")
MEDIA_FILE_EXTS = {
    ".mp4", ".mov", ".m4v", ".webm", ".jpg", ".jpeg", ".png", ".gif", ".json", ".md", ".py", ".txt",
}  # fmt: skip


def web_origins() -> list[str]:
    """CORS origins from SHOWTELL_WEB_ORIGIN (comma-separated)."""
    raw = os.environ.get("SHOWTELL_WEB_ORIGIN", "").strip() or DEFAULT_WEB_ORIGINS
    return [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]


def api_token_from_env() -> str | None:
    """The shared secret for POST routes (SHOWTELL_API_TOKEN), or None when unset."""
    return os.environ.get(API_TOKEN_ENV, "").strip() or None


def budget_from_env() -> float:
    """Spend limit for paid jobs in USD: SHOWTELL_BUDGET_USD, else settings.BUDGET_WARN_USD."""
    try:
        return float(os.environ.get("SHOWTELL_BUDGET_USD", "") or settings.BUDGET_WARN_USD)
    except ValueError:
        return settings.BUDGET_WARN_USD


def _mb(n: int) -> str:
    return f"{n / MB:g} MB"


def is_loopback(host: str) -> bool:
    """Is `host` (a client address) this machine?"""
    if host == "localhost":
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return addr.is_loopback


def write_refusal(scope: Scope, token: str | None, origins: list[str]) -> tuple[int, str] | None:
    """(status, detail) when a write request may not proceed, None when it may."""
    headers = Headers(scope=scope)
    if token:
        scheme, _, value = headers.get("authorization", "").partition(" ")
        given = value.strip() if scheme.lower() == "bearer" else headers.get("x-showtell-token", "")
        if given and secrets.compare_digest(given.encode(), token.encode()):
            return None
        return (
            401,
            f"this server needs its API token: send 'Authorization: Bearer <{API_TOKEN_ENV}>'",
        )
    client = scope.get("client")
    if not client or not is_loopback(str(client[0])):
        return 403, (
            f"this server only accepts jobs from its own machine; set {API_TOKEN_ENV} on the "
            "server to accept other clients"
        )
    origin = headers.get("origin")
    if origin and origin.rstrip("/") not in origins:
        return 403, f"requests from pages at {origin} are not allowed (see SHOWTELL_WEB_ORIGIN)"
    return None


class MediaFiles(StaticFiles):
    """StaticFiles limited to media and report types (never the SQLite file or usage logs)."""

    def lookup_path(self, path: str) -> tuple[str, os.stat_result | None]:
        p = PurePosixPath(path.replace(os.sep, "/"))
        if p.suffix.lower() not in MEDIA_FILE_EXTS or any(x.startswith(".") for x in p.parts):
            return "", None
        return super().lookup_path(path)


class UploadLimit:
    """ASGI middleware: refuse POST bodies over `max_bytes` on `path` before they are spooled."""

    def __init__(
        self, app: ASGIApp, max_bytes: int, file_limit: int, path: str = "/api/jobs"
    ) -> None:
        self.app, self.max_bytes, self.path = app, max_bytes, path
        self.detail = f"the video is larger than {_mb(file_limit)}"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or scope["path"].rstrip("/") != self.path
        ):
            await self.app(scope, receive, send)
            return
        length = Headers(scope=scope).get("content-length", "")
        if length.isdigit() and int(length) > self.max_bytes:
            await JSONResponse({"detail": self.detail}, status_code=413)(scope, receive, send)
            return
        seen = 0

        async def limited() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_bytes:  # FastAPI re-raises HTTPExceptions from body parsing
                    raise HTTPException(413, self.detail)
            return message

        await self.app(scope, limited, send)


class WriteGuard:
    """ASGI middleware: refuse POST/PUT/PATCH/DELETE under /api/ that `write_refusal` rejects,
    before the request body is read."""

    def __init__(self, app: ASGIApp, token: str | None, origins: list[str]) -> None:
        self.app, self.token, self.origins = app, token, origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and scope["method"] not in READ_METHODS
            and scope["path"].startswith("/api/")
        ):
            refusal = write_refusal(scope, self.token, self.origins)
            if refusal is not None:
                status, detail = refusal
                headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
                response = JSONResponse({"detail": detail}, status_code=status, headers=headers)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


# ------------------------------------------------------------------------------------- helpers
def _jobs(request: Request) -> JobManager:
    return request.app.state.jobs


def _job_or_404(request: Request, job_id: str) -> db.Job:
    job = _jobs(request).get_job(job_id)
    if job is None:
        raise HTTPException(404, f"no job {job_id!r}")
    return job


def _validation_detail(exc: ValidationError) -> Any:
    return json.loads(exc.json(include_url=False))


def _task_or_400(task_id: str | None):
    if not task_id or not task_id.strip():
        return None
    from showtell.sim.scene import get_task

    try:
        return get_task(task_id.strip())
    except KeyError as exc:
        raise HTTPException(400, f"unknown task_id {task_id.strip()!r}") from exc


def _objects_or_400(objects: str | None) -> list[str] | None:
    """Explicit sim objects: a JSON list or comma/space separated ids like red_cube_large."""
    if not objects or not objects.strip():
        return None
    from showtell.sim.scene import parse_object_id

    raw = objects.strip()
    try:
        ids = json.loads(raw) if raw.startswith("[") else re.split(r"[,\s]+", raw)
    except ValueError as exc:
        raise HTTPException(400, "objects must be a JSON list or comma-separated ids") from exc
    ids = [str(o).strip() for o in ids if str(o).strip()]
    if not ids or len(ids) > MAX_OBJECTS or len(set(ids)) != len(ids):
        raise HTTPException(400, f"objects must be 1-{MAX_OBJECTS} distinct ids")
    for o in ids:
        try:
            parse_object_id(o)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    return ids


def _spec_or_422(spec: str | None) -> TaskSpec | None:
    if not spec or not spec.strip():
        return None
    try:
        return TaskSpec.model_validate_json(spec)
    except ValidationError as exc:
        raise HTTPException(422, _validation_detail(exc)) from exc


def _bench_video(task) -> Path | None:
    path = ROOT / "bench" / "videos" / task.video
    return path if path.is_file() else None


def _save_upload(upload: UploadFile, dest_dir: Path, max_bytes: int) -> Path:
    """Stream an upload to dest_dir; .mp4/.mov only, size-capped, MP4/QuickTime magic checked."""
    name = upload.filename or ""
    ext = Path(name).suffix.lower()
    if ext not in VIDEO_EXTS:
        raise HTTPException(415, "the video must be an .mp4 or .mov file")
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(name).stem).strip("_")[:80] or "video"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{stem}{ext}"
    part = dest_dir / f".{stem}{ext}.part"
    size, head = 0, b""
    try:
        with part.open("wb") as f:
            while chunk := upload.file.read(MB):
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(413, f"the video is larger than {_mb(max_bytes)}")
                head += chunk[: max(0, 12 - len(head))]
                f.write(chunk)
        if size == 0:
            raise HTTPException(400, "the uploaded video is empty")
        if head[4:8] not in MP4_BOXES:
            raise HTTPException(415, "the file is not an MP4/MOV video")
        part.replace(dest)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return dest


def _link_bench_video(src: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    try:
        os.link(src, dest)  # same disk: no copy
    except OSError:
        shutil.copy2(src, dest)
    return dest


def _remove_empty(job_dir: Path) -> None:
    for d in (job_dir / "input", job_dir):
        with contextlib.suppress(OSError):
            d.rmdir()  # only removes empty folders


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _cursor(last_event_id: str | None, after: int | None) -> int:
    """Index of the first event to send: one past Last-Event-ID (header) or ?after=."""
    for raw in (last_event_id, after):
        if raw is None:
            continue
        try:
            return max(0, int(raw) + 1)
        except (TypeError, ValueError):
            continue
    return 0


def sse_frame(index: int, ev: Event, job_id: str, job_dir: Path) -> bytes:
    """One SSE message; media paths in the payload become /media/jobs/<id>/... URLs."""
    payload = map_media(ev.model_dump(mode="json"), media_prefix(job_id), job_dir)
    data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return f"id: {index}\nevent: {ev.type}\ndata: {data}\n\n".encode()


async def _event_stream(
    st: JobState, cursor: int, limiter: anyio.CapacityLimiter
) -> AsyncIterator[bytes]:
    """Replay events from `cursor`, then stream new ones until job_done (or the job is over)."""
    yield f"retry: {SSE_RETRY_MS}\n\n".encode()
    last_sent = time.monotonic()
    while True:
        events, done = st.snapshot(cursor)
        if not events and not done:
            timeout = max(0.05, min(1.0, HEARTBEAT_S - (time.monotonic() - last_sent)))
            events, done = await anyio.to_thread.run_sync(
                st.wait, cursor, timeout, abandon_on_cancel=True, limiter=limiter
            )
        for ev in events:
            ev = ev if ev.job_id else ev.model_copy(update={"job_id": st.job_id})
            yield sse_frame(cursor, ev, st.job_id, st.job_dir)
            cursor += 1
            last_sent = time.monotonic()
            if ev.type == "job_done":
                return
        if done:  # every event of a finished job has been sent
            return
        if time.monotonic() - last_sent >= HEARTBEAT_S:
            yield b": ping\n\n"
            last_sent = time.monotonic()


# ------------------------------------------------------------------------------------- routes
router = APIRouter(prefix="/api")


def _pipeline_present() -> bool:
    """Is showtell.pipeline importable? (Checked without importing it.)"""
    try:
        return find_spec("showtell.pipeline") is not None
    except (ImportError, ValueError):  # ValueError: a module object without __spec__
        return "showtell.pipeline" in sys.modules and sys.modules["showtell.pipeline"] is not None


@router.get("/health")
def health(request: Request) -> dict[str, Any]:
    return {
        "ok": True,
        "version": __version__,
        "has_api_key": settings.has_api_key,
        "auth": "token" if request.app.state.api_token else "local",
        "pipeline": _pipeline_present(),
        "jobs": _jobs(request).counts(),
    }


@router.get("/tasks")
def list_tasks() -> list[dict[str, Any]]:
    """Benchmark task presets from bench/tasks.yaml (reference specs stay hidden)."""
    from showtell.sim.scene import load_tasks

    return [
        {
            "id": t.id,
            "index": t.index,
            "rules": t.rules,
            "objects": [o.id for o in t.objects],
            "demo": t.demo,
            "video": t.video,
            "has_video": _bench_video(t) is not None,
        }
        for t in load_tasks().values()
    ]


@router.get("/bench")
def bench(request: Request) -> Response:
    path: Path = request.app.state.bench_path
    if not path.is_file():
        return JSONResponse(
            {"detail": "no benchmark results yet: run `python -m showtell bench`"}, status_code=404
        )
    return FileResponse(path, media_type="application/json")


@router.get("/jobs")
def list_jobs(request: Request, limit: int = Query(50, ge=1, le=500)) -> list[dict[str, Any]]:
    return _jobs(request).list_jobs(limit)


@router.post("/jobs")
def create_job(
    request: Request,
    rules: str = Form(..., max_length=2000),
    use_video: bool = Form(True),
    auto_approve: bool = Form(False),
    task_id: str | None = Form(None),
    seeds: int = Form(20, ge=1, le=MAX_SEEDS),
    use_judge: bool = Form(True),
    objects: str | None = Form(None),
    spec: str | None = Form(None),
    program: str | None = Form(None, max_length=20000),
    video: UploadFile | None = File(None),
) -> dict[str, str]:
    """Start a job. Without an upload, use_video takes the task preset's bench video if recorded."""
    mgr = _jobs(request)
    rules = rules.strip()
    if not rules:
        raise HTTPException(422, "rules must not be empty")
    task = _task_or_400(task_id)
    object_ids = _objects_or_400(objects)
    given_spec = _spec_or_422(spec)
    program_src = program.replace("\r\n", "\n") if program and program.strip() else None
    if video is not None and not video.filename:
        video = None  # an empty file field
    bench_video = _bench_video(task) if (task is not None and video is None and use_video) else None
    if use_video and video is None and bench_video is None:
        raise HTTPException(
            400,
            "use_video is on but there is no video: upload an .mp4/.mov, pick a task preset with a "
            "recorded video, or set use_video=false",
        )
    if not use_video and task is None and not object_ids:
        raise HTTPException(
            400, "no sim objects: use a demo video, a task preset (task_id) or objects=..."
        )
    if given_spec is not None and task is None and not object_ids:
        raise HTTPException(
            400,
            "a given spec needs the sim objects (task_id or objects=...): the demo video is not "
            "analysed when the spec is supplied",
        )

    job_id = new_job_id()
    job_dir = mgr.job_dir(job_id)
    config = JobConfig(
        job_id=job_id,
        rules_text=rules,
        use_video=use_video,
        task_id=task.id if task is not None else None,
        object_ids=object_ids,
        n_seeds=seeds,
        use_judge=use_judge,
        interactive=not auto_approve,
        out_dir=job_dir,
        spec=given_spec,
        program_source=program_src,
    )
    refusal = budget_error(config, request.app.state.budget_usd)
    if refusal:
        if given_spec is None or program_src is None or use_judge:
            raise HTTPException(402, refusal)
        config = config.model_copy(update={"max_repairs": 0})  # repairs are the only paid step
    video_path: Path | None = None
    try:
        if video is not None:
            video_path = _save_upload(video, job_dir / "input", request.app.state.max_upload_bytes)
        elif bench_video is not None:
            video_path = _link_bench_video(bench_video, job_dir / "input")
    except BaseException:
        _remove_empty(job_dir)
        raise

    config = config.model_copy(
        update={"video_path": video_path, "use_video": bool(use_video and video_path is not None)}
    )
    shown = object_ids or ([o.id for o in task.objects] if task is not None else None)
    mgr.create_job(config, object_ids=shown)
    return {"job_id": job_id}


@router.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request) -> dict[str, Any]:
    try:
        return _jobs(request).detail(job_id)
    except UnknownJob as exc:
        raise HTTPException(404, f"no job {job_id!r}") from exc


@router.post("/jobs/{job_id}/spec")
def post_spec(job_id: str, request: Request, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Approve the proposed spec ({"approve": true}) or replace it (a TaskSpec or {"spec": ...})."""
    job = _job_or_404(request, job_id)
    if "rules" in body or "checks" in body:
        raw: dict[str, Any] | None = body
    elif isinstance(body.get("spec"), dict):
        raw = body["spec"]
    elif body.get("approve") is True:
        raw = None
    else:
        raise HTTPException(
            400,
            'send {"approve": true} to approve the proposed spec, or a full TaskSpec to edit it',
        )
    edited = None
    if raw is not None:
        try:
            edited = TaskSpec.model_validate(raw)
        except ValidationError as exc:
            raise HTTPException(422, _validation_detail(exc)) from exc
    try:
        _jobs(request).submit_approval(job_id, edited)
    except NotAwaitingApproval as exc:
        raise HTTPException(409, f"job {job_id} is {job.status}, not awaiting approval") from exc
    return {"ok": True}


@router.get("/jobs/{job_id}/events")
async def job_events(
    job_id: str, request: Request, after: int | None = Query(None, ge=-1)
) -> Response:
    """Server-sent events: past events first, then live ones; the stream ends after job_done."""
    try:
        st = await run_in_threadpool(_jobs(request).state, job_id)
    except UnknownJob as exc:
        raise HTTPException(404, f"no job {job_id!r}") from exc
    cursor = _cursor(request.headers.get("last-event-id"), after)
    pending, done = st.snapshot(cursor)
    if done and not pending:
        return Response(status_code=204)  # nothing left: tells EventSource to stop reconnecting
    return StreamingResponse(
        _event_stream(st, cursor, request.app.state.sse_limiter),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/jobs/{job_id}/report.md")
def download_report_md(job_id: str, request: Request) -> Response:
    _job_or_404(request, job_id)
    path = _jobs(request).job_dir(job_id) / "report.md"
    if not path.is_file():
        raise HTTPException(404, "the report is not ready yet")
    return FileResponse(
        path, media_type="text/markdown; charset=utf-8", filename=f"{job_id}_report.md"
    )


@router.get("/jobs/{job_id}/report.json")
def download_report_json(job_id: str, request: Request) -> Response:
    job = _job_or_404(request, job_id)
    path = _jobs(request).job_dir(job_id) / "report.json"
    name = f"{job_id}_report.json"
    if path.is_file():
        return FileResponse(path, media_type="application/json", filename=name)
    if job.report_json:
        return Response(
            job.report_json,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )
    raise HTTPException(404, "the report is not ready yet")


@router.get("/jobs/{job_id}/program.py")
def download_program(job_id: str, request: Request) -> Response:
    """The final program (or the latest one while the job runs), with a provenance header."""
    job = _job_or_404(request, job_id)
    report = db.loads(job.report_json) or {}
    source = report.get("final_program") or ""
    if not source:
        attempts = _jobs(request).detail(job_id)["attempts"]
        source = next((a["program"] for a in reversed(attempts) if a["program"]), "")
    if not source:
        raise HTTPException(404, "no program yet")
    header = [
        f"# Show & Tell Robotics program, job {job_id}",
        f"# Rules: {_one_line(job.rules_text)}",
    ]
    if report.get("final_total"):
        header.append(
            f"# Result: {report['final_passed']}/{report['final_total']} test scenes passed "
            f"({report.get('status')})"
        )
    else:
        header.append(f"# Status: {job.status}")
    return Response(
        "\n".join(header) + "\n\n" + source.rstrip() + "\n",
        media_type="text/x-python; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{job_id}_program.py"'},
    )


# ------------------------------------------------------------------------------------- app
def create_app(
    data_dir: Path | None = None,
    *,
    run_job: RunJob | None = None,
    max_workers: int = 1,
    origins: list[str] | None = None,
    bench_path: Path | None = None,
    max_upload_bytes: int = MAX_UPLOAD_BYTES,
    api_token: str | None = None,
    budget_usd: float | None = None,
) -> FastAPI:
    """Build the app. `run_job` replaces showtell.pipeline.run_job (tests). `api_token` None reads
    SHOWTELL_API_TOKEN ("" = no token: local writes only); `budget_usd` None reads
    SHOWTELL_BUDGET_USD / settings.BUDGET_WARN_USD."""
    data_dir = Path(data_dir or settings.SHOWTELL_DATA_DIR)
    token = (api_token_from_env() if api_token is None else api_token.strip()) or None
    budget = budget_from_env() if budget_usd is None else budget_usd
    allowed = origins if origins is not None else web_origins()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        manager = JobManager(data_dir, max_workers=max_workers, run_job=run_job, budget_usd=budget)
        app.state.jobs = manager
        app.state.sse_limiter = anyio.CapacityLimiter(256)
        try:
            yield
        finally:
            await run_in_threadpool(manager.shutdown, 5.0)

    app = FastAPI(title="Show & Tell Robotics API", version=__version__, lifespan=lifespan)
    app.state.data_dir = data_dir
    app.state.bench_path = Path(bench_path or ROOT / "bench" / "results.json")
    app.state.max_upload_bytes = max_upload_bytes
    app.state.api_token = token
    app.state.budget_usd = budget
    app.include_router(router)
    app.mount("/media", MediaFiles(directory=data_dir, check_dir=False), name="media")
    app.add_middleware(
        UploadLimit, max_bytes=max_upload_bytes + UPLOAD_SLACK_BYTES, file_limit=max_upload_bytes
    )
    app.add_middleware(WriteGuard, token=token, origins=allowed)  # before any body is read
    app.add_middleware(  # outermost, so errors (401, 413, 404, ...) carry CORS headers too
        CORSMiddleware,
        allow_origins=allowed,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition"],
    )
    return app


app = create_app()
