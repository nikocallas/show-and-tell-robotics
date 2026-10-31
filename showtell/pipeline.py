"""End-to-end orchestration (CLAUDE.md 5.8), used by the CLI, the API and the benchmark.

prep video -> discover objects -> SAM tracks -> track summary -> spec (approval) -> program
-> attempt 0: verify on N seeds (+ SAM judge) -> repair while failures (max 3) -> report.
Every stage emits an Event (see showtell.contracts) to `on_event` and to <job>/events.jsonl.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from showtell.agent.schemas import TaskSpec, TrackSummary
from showtell.config import settings
from showtell.contracts import (
    AgreementStats,
    AttemptReport,
    CostSummary,
    Event,
    JobConfig,
    JobReport,
    SamVerdict,
    SeedSummary,
)
from showtell.report import report_markdown
from showtell.verify.runner import SeedResult

log = logging.getLogger("showtell.pipeline")

Approve = Callable[[TaskSpec, str | None], TaskSpec]
AD_HOC_SEED_BASE = 9000
JUDGE_WORKERS = 4


def new_job_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]


def job_dir(config: JobConfig) -> Path:
    return Path(config.out_dir or settings.SHOWTELL_DATA_DIR / "jobs" / config.job_id)


class Emitter:
    """Thread-safe event sink: appends to events.jsonl and forwards to the callback."""

    def __init__(self, path: Path, job_id: str, on_event: Callable[[Event], None] | None) -> None:
        self.path, self.job_id, self.on_event = path, job_id, on_event
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, type_: str, **data: Any) -> Event:
        ev = Event(type=type_, job_id=self.job_id, data=_jsonable(data))
        with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(ev.model_dump_json() + "\n")
            if self.on_event:
                try:
                    self.on_event(ev)
                except Exception:  # a broken listener must not kill the job
                    log.exception("event listener failed")
        return ev


def _jsonable(x: Any) -> Any:
    if hasattr(x, "model_dump"):
        return x.model_dump(mode="json")
    if isinstance(x, dict):
        return {k: _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, Path):
        return str(x)
    return x


class Stage:
    """Context manager that emits stage_started / stage_done with timing."""

    def __init__(self, emit: Emitter, name: str, **extra: Any) -> None:
        self.emit, self.name, self.extra = emit, name, extra

    def __enter__(self) -> Stage:
        self.t0 = time.time()
        self.emit("stage_started", stage=self.name, **self.extra)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc is None:
            self.emit(
                "stage_done", stage=self.name, seconds=round(time.time() - self.t0, 2), **self.extra
            )


# ------------------------------------------------------------------------------------- helpers
def _rel(path: str | Path | None, root: Path) -> str | None:
    if not path:
        return None
    try:
        return str(Path(path).resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def seed_summary(res: SeedResult, root: Path, sam: list[SamVerdict] | None = None) -> SeedSummary:
    videos = {k: _rel(v, root) for k, v in res.videos.items() if v}
    return SeedSummary(
        seed=res.seed,
        passed=res.passed,
        checks=res.checks,
        error=res.error,
        videos={k: v for k, v in videos.items() if v},
        sim_seconds=res.sim_seconds,
        touch_events=res.touch_events[:12],
        sam=sam,
    )


def _cost(job_id: str) -> CostSummary:
    from showtell.meta.usage import summarize

    s = summarize(tag_prefix=f"{job_id}:")
    by = s.by_kind
    return CostSummary(
        usd=round(s.total.uncached_usd, 4),
        wonder_usd=round(by["wonder"].uncached_usd, 4) if "wonder" in by else 0.0,
        sam_usd=round(by["sam"].uncached_usd, 4) if "sam" in by else 0.0,
        calls=s.total.n_calls,
    )


def resolve_objects(config: JobConfig) -> list[str] | None:
    if config.object_ids:
        return list(config.object_ids)
    if config.task_id:
        from showtell.sim.scene import get_task

        return [o.id for o in get_task(config.task_id).objects]
    return None


def resolve_seeds(config: JobConfig) -> list[int]:
    base = config.seed_base
    if base is None and config.task_id:
        from showtell.sim.scene import get_task

        base = get_task(config.task_id).index * 1000
    base = AD_HOC_SEED_BASE if base is None else base
    return list(range(base, base + config.n_seeds))


# ------------------------------------------------------------------------------------- stages
def _perceive(config: JobConfig, root: Path, emit: Emitter, tag: str):
    """Video -> (track summary, discovery, keyframes, normalized video, file id)."""
    from showtell.meta import files, sam
    from showtell.perception import track_summary as ts
    from showtell.perception import video_prep

    prep = root / "prep"
    with Stage(emit, "prep"):
        normalized = video_prep.normalize(Path(config.video_path), prep / "normalized.mp4")
        keyframes = video_prep.keyframes(normalized, n=8, width=768, out_dir=prep / "keyframes")
        info = video_prep.probe(normalized)
    with Stage(emit, "discover"):
        discovery = ts.discover(keyframes, tag=f"{tag}discover")
        (root / "discovery.json").write_text(discovery.model_dump_json(indent=2))
    with Stage(emit, "track"):
        concepts = [o.concept for o in discovery.objects]
        container = discovery.container_concept or "bowl"
        tracks = sam.track(normalized, [*concepts, container, "hand"], tag=f"{tag}track")
    with Stage(emit, "summary"):
        summary = ts.summarize(
            tracks,
            discovery,
            container_concept=container,
            fps=info.fps,
            frames=info.frames,
            width=info.width,
            height=info.height,
        )
        (root / "track_summary.json").write_text(summary.model_dump_json(indent=2))
    emit("track_summary", track_summary=summary)
    file_id = files.upload(normalized)
    return summary, discovery, keyframes, normalized, file_id


def _judge_seed(
    root: Path, res: SeedResult, spec: TaskSpec, object_ids: list[str], scene_meta: dict, tag: str
) -> list[SamVerdict]:
    from showtell.sim.scene import parse_object_id
    from showtell.verify import judge_sam

    seed_dir = Path(res.videos["full"]).parent if res.videos.get("full") else None
    if seed_dir is None:
        return [
            SamVerdict(check_id=c.id, kind=c.kind, passed=None, detail="no video")
            for c in spec.checks
        ]
    objects = [parse_object_id(o) for o in object_ids]
    return judge_sam.judge(seed_dir, spec.checks, objects, scene_meta, tag=tag)


def _verify_attempt(
    *,
    n: int,
    kind: str,
    source: str,
    notes: str,
    diagnosis: str | None,
    spec: TaskSpec,
    object_ids: list[str],
    seeds: list[int],
    root: Path,
    config: JobConfig,
    emit: Emitter,
    tag: str,
    scene_meta: dict | None,
) -> tuple[AttemptReport, list[SeedResult]]:
    from showtell.verify import agreement as agr
    from showtell.verify.runner import run_seeds

    t0 = time.time()
    att_dir = root / "attempts" / str(n)
    att_dir.mkdir(parents=True, exist_ok=True)
    (att_dir / "program.py").write_text(source)
    emit("attempt_started", attempt=n, seeds=seeds)
    judging = config.use_judge and scene_meta is not None
    pool = ThreadPoolExecutor(max_workers=JUDGE_WORKERS) if judging else None
    futures: dict[int, Future] = {}
    results: dict[int, SeedResult] = {}

    def on_result(res: SeedResult) -> None:
        results[res.seed] = res
        seed_dir = att_dir / f"seed_{res.seed}"
        seed_dir.mkdir(parents=True, exist_ok=True)
        (seed_dir / "result.json").write_text(res.model_dump_json(indent=2))
        emit("seed_done", attempt=n, seed=seed_summary(res, root))
        if pool is not None:

            def job(r: SeedResult = res) -> list[SamVerdict]:
                verdicts = _judge_seed(root, r, spec, object_ids, scene_meta, f"{tag}judge")
                emit("judge_done", attempt=n, seed=r.seed, verdicts=verdicts)
                return verdicts

            futures[res.seed] = pool.submit(job)

    with Stage(emit, "verify", attempt=n):
        ordered = run_seeds(
            object_ids,
            source,
            spec.checks,
            seeds,
            out_root=att_dir,
            record=True,
            hero_size=config.hero_size,
            on_result=on_result,
        )
    verdicts: dict[int, list[SamVerdict]] = {}
    agreement: AgreementStats | None = None
    if pool is not None:
        with Stage(emit, "judge", attempt=n):
            for seed, fut in futures.items():
                try:
                    verdicts[seed] = fut.result()
                except Exception as exc:  # judging is best effort; ground truth decides
                    log.warning("SAM judge failed on seed %s: %s", seed, exc)
                    verdicts[seed] = [
                        SamVerdict(
                            check_id=c.id, kind=c.kind, passed=None, detail=f"judge error: {exc}"
                        )
                        for c in spec.checks
                    ]
            pool.shutdown(wait=True)
        pairs = [p for r in ordered for p in agr.pairs(r.checks, verdicts.get(r.seed, []))]
        agreement = agr.agreement(pairs)
    seeds_out = [seed_summary(r, root, verdicts.get(r.seed)) for r in ordered]
    passed = sum(r.passed for r in ordered)
    report = AttemptReport(
        n=n,
        kind=kind,
        program=source,
        notes=notes,
        diagnosis=diagnosis,
        passed=passed,
        total=len(ordered),
        seeds=seeds_out,
        agreement=agreement,
        wall_seconds=round(time.time() - t0, 1),
    )
    (att_dir / "attempt.json").write_text(report.model_dump_json(indent=2))
    emit(
        "attempt_done",
        attempt=n,
        passed=passed,
        total=len(ordered),
        failed_seeds=[r.seed for r in ordered if not r.passed],
        agreement=agreement,
        diagnosis=diagnosis,
    )
    return report, ordered


def _scene_meta(object_ids: list[str], size: tuple[int, int] = (640, 480)) -> dict:
    from showtell.sim.scene import build_model, parse_object_id, scene_metadata

    model, _ = build_model([parse_object_id(o) for o in object_ids])
    return scene_metadata(model, *size)


# ------------------------------------------------------------------------------------- main entry
def run_job(
    config: JobConfig,
    on_event: Callable[[Event], None] | None = None,
    approve: Approve | None = None,
) -> JobReport:
    """Run one job end to end. Never raises for pipeline errors: they end up in report.error."""
    from showtell.agent import program as program_mod
    from showtell.agent import repair as repair_mod
    from showtell.agent import spec as spec_mod

    t0 = time.time()
    root = job_dir(config)
    root.mkdir(parents=True, exist_ok=True)
    emit = Emitter(root / "events.jsonl", config.job_id, on_event)
    tag = f"{config.job_id}:"
    report = JobReport(
        job_id=config.job_id,
        created_at=t0,
        rules=config.rules_text,
        use_video=config.use_video,
        task_id=config.task_id,
        video=_rel(config.video_path, root) if config.video_path else None,
    )

    def finish(status: str, error: str | None = None) -> JobReport:
        report.status = status  # type: ignore[assignment]
        report.error = error
        report.wall_seconds = round(time.time() - t0, 1)
        try:
            report.cost = _cost(config.job_id)
        except Exception:  # usage log problems never hide the result
            log.exception("cost summary failed")
        if report.attempts:
            last = report.attempts[-1]
            report.final_program, report.final_passed, report.final_total = (
                last.program,
                last.passed,
                last.total,
            )
            stats = [a.agreement for a in report.attempts if a.agreement is not None]
            if stats:
                from showtell.verify import agreement as agr

                report.agreement = agr.merge(stats)
        with Stage(emit, "report"):
            (root / "report.json").write_text(report.model_dump_json(indent=2))
            (root / "report.md").write_text(report_markdown(report))
        emit("job_done", status=report.status, report=report)
        return report

    seeds = resolve_seeds(config)
    report.seeds = seeds
    emit(
        "job_started",
        rules=config.rules_text,
        use_video=config.use_video,
        task_id=config.task_id,
        n_seeds=config.n_seeds,
    )
    try:
        object_ids = resolve_objects(config)
        summary: TrackSummary | None = None
        keyframes: list[Path] = []
        file_id: str | None = None
        if config.use_video and config.video_path and config.spec is None:
            summary, discovery, keyframes, _normalized, file_id = _perceive(config, root, emit, tag)
            report.track_summary = summary
            if object_ids is None:
                from showtell.perception.track_summary import scene_objects_from_discovery

                object_ids, notes = scene_objects_from_discovery(discovery, summary=summary)
                for note in notes:
                    emit("log", message=note)
        if not object_ids:
            raise ValueError("no sim objects: give a demo video, a task preset or explicit objects")
        report.object_ids = object_ids

        with Stage(emit, "spec"):
            if config.spec is not None:
                spec, media_path = config.spec, "none"
            else:
                spec, media_path = spec_mod.make_spec(
                    config.rules_text,
                    object_ids,
                    track_summary=summary,
                    keyframes=keyframes,
                    video_file_id=file_id,
                    tag=f"{tag}spec",
                )
            spec, notes = spec_mod.ensure_checks(spec, object_ids)
            for note in notes + spec_mod.validate_spec(spec, object_ids):
                emit("log", message=note)
        report.spec, report.media_path = spec, media_path  # type: ignore[assignment]
        report.spec_source = (
            "given" if config.spec is not None else "video" if summary is not None else "sentence"
        )
        (root / "spec.json").write_text(spec.model_dump_json(indent=2))
        emit("spec_ready", spec=spec, source=report.spec_source, media_path=media_path)

        if config.interactive and approve is not None:
            with Stage(emit, "approval"):
                emit("awaiting_approval", spec=spec, clarifying_question=spec.clarifying_question)
                approved = approve(spec, spec.clarifying_question)
            edited = approved.model_dump() != spec.model_dump()
            spec = approved
        else:
            edited = False
        report.spec = spec
        (root / "spec_approved.json").write_text(spec.model_dump_json(indent=2))
        emit("spec_approved", spec=spec, edited=edited)

        with Stage(emit, "program"):
            if config.program_source is not None:
                from showtell.agent.sandbox import strip_fences, validate_program
                from showtell.agent.schemas import Program

                src = strip_fences(config.program_source)
                validate_program(src)
                program = Program(source=src, notes="program supplied by the user")
            else:
                program = program_mod.write_program(spec, object_ids, tag=f"{tag}program")
        emit("program_ready", attempt=0, source=program.source, notes=program.notes, kind="initial")

        scene_meta = None
        if config.use_judge:
            if settings.has_api_key:
                scene_meta = _scene_meta(object_ids)
                (root / "scene_meta.json").write_text(json.dumps(scene_meta, indent=2))
            else:
                emit("log", message="SAM judge skipped: MODEL_API_KEY is not set")

        n, source, notes_text, diagnosis, kind = 0, program.source, program.notes, None, "initial"
        while True:
            attempt, results = _verify_attempt(
                n=n,
                kind=kind,
                source=source,
                notes=notes_text,
                diagnosis=diagnosis,
                spec=spec,
                object_ids=object_ids,
                seeds=seeds,
                root=root,
                config=config,
                emit=emit,
                tag=tag,
                scene_meta=scene_meta,
            )
            report.attempts.append(attempt)
            if attempt.passed == attempt.total or n >= config.max_repairs:
                break
            if not settings.has_api_key:
                emit("log", message="repair skipped: MODEL_API_KEY is not set")
                break
            emit("repair_started", attempt=n + 1)
            try:
                with Stage(emit, "repair", attempt=n + 1):
                    first_fail = next(r for r in results if not r.passed)
                    seed_dir = root / "attempts" / str(n) / f"seed_{first_fail.seed}"
                    frames = repair_mod.failure_frames(
                        seed_dir,
                        first_fail,
                        n=8,
                        out_dir=root / "attempts" / str(n) / "repair_frames",
                    )
                    fix = repair_mod.repair(
                        spec, source, results, frames, attempt=n + 1, tag=f"{tag}repair{n + 1}"
                    )
            except repair_mod.RepairError as exc:  # the patch failed the sandbox twice: stop here
                emit("log", message=str(exc))
                break
            emit("repair_done", attempt=n + 1, diagnosis=fix.diagnosis, source=fix.source)
            n, source, diagnosis, kind = n + 1, fix.source, fix.diagnosis, "repair"
            notes_text = ""
            emit(
                "program_ready",
                attempt=n,
                source=source,
                notes="",
                kind="repair",
                diagnosis=diagnosis,
            )
        last = report.attempts[-1]
        return finish("verified" if last.passed == last.total else "failed")
    except Exception as exc:
        log.exception("job %s failed", config.job_id)
        emit("error", message=f"{type(exc).__name__}: {exc}")
        return finish("error", f"{type(exc).__name__}: {exc}")


# ------------------------------------------------------------------------------------- CLI
def _console_approve(spec: TaskSpec, question: str | None) -> TaskSpec:
    from rich.console import Console

    con = Console()
    con.print("[bold]Proposed spec[/]")
    for rule in spec.rules:
        con.print(f"  rule: {rule.select.describe()} -> {rule.place_in or 'leave in place'}")
    for c in spec.checks:
        con.print(f"  check [{c.kind}] {c.text}")
    if question:
        con.print(f"[yellow]Question:[/] {question}")
    answer = input("Approve this spec? [Y/n] ").strip().lower()
    if answer.startswith("n"):
        raise RuntimeError("spec rejected by the user")
    return spec


def run_cli(
    *,
    video: Path | None,
    rules: str,
    task_id: str | None,
    n_seeds: int,
    use_video: bool,
    interactive: bool,
    use_judge: bool,
    out_dir: Path | None,
) -> bool:
    from rich.console import Console

    con = Console()
    if task_id is None and video is not None:
        from showtell.sim.scene import load_tasks

        for t in load_tasks().values():  # bench videos map to their task's sim objects
            if Path(video).name == t.video:
                task_id = t.id
                break
    job_id = new_job_id()
    config = JobConfig(
        job_id=job_id,
        rules_text=rules,
        video_path=video if use_video else None,
        use_video=use_video and video is not None,
        task_id=task_id,
        n_seeds=n_seeds,
        use_judge=use_judge,
        interactive=interactive,
        out_dir=out_dir,
    )

    def show(ev: Event) -> None:
        d = ev.data
        if ev.type == "stage_started":
            con.print(
                f"[dim]-> {d['stage']}" + (f" (attempt {d['attempt']})" if "attempt" in d else "")
            )
        elif ev.type == "spec_ready":
            con.print(
                f"spec: {len(d['spec']['rules'])} rules, {len(d['spec']['checks'])} checks "
                f"(from {d['source']}, media: {d['media_path']})"
            )
        elif ev.type == "attempt_done":
            con.print(f"[bold]attempt {d['attempt']}: {d['passed']}/{d['total']} passed[/]")
        elif ev.type == "repair_done":
            con.print(f"[yellow]diagnosis:[/] {d['diagnosis']}")
        elif ev.type in ("log", "error"):
            con.print(f"[dim]{d.get('message')}")

    report = run_job(config, on_event=show, approve=_console_approve if interactive else None)
    root = job_dir(config)
    con.print(f"\nstatus: [bold]{report.status}[/] ({report.final_passed}/{report.final_total})")
    con.print(f"report: {root / 'report.md'}")
    if report.error:
        con.print(f"[red]{report.error}")
    return report.status == "verified"
