"""Export a finished job as a static bundle for the web app's demo mode (CLAUDE.md 5.10).

`python -m showtell export-demo --job <job id | job folder> [--out web/public/demo]` writes the
bundle web/README.md documents ("Demo bundle"):
    index.json                       [{id, rules, status, attempts, created_at, ..., title?,
                                     note?}], newest first
    bench.json                       a copy of bench/results.json (when it exists)
    tasks.json                       the benchmark task presets (same shape as GET /api/tasks)
    <job_id>/report.json             the JobReport with media paths -> "media/<job_id>/<rel path>",
                                     plus demo_note (how the run was made) when one was given
    <job_id>/events.json             events.jsonl as a JSON list (ts kept for realistic replay;
                                     media paths rewritten the same way)
    <job_id>/job.json                the GET /api/jobs/<id> shape, same media paths
    <job_id>/report.md, program.py   the downloads
    media/<job_id>/<rel path>        per seed per attempt: full.mp4, thumb.jpg, judge.mp4,
                                     judge_angle.mp4; the input and normalized videos, keyframes
                                     and repair frames
Media paths are relative to the bundle root, so the web app (web/src/demo.ts demoMediaUrl) loads
them from /demo/media/<job_id>/<rel path>. `--title` and `--note` label a run in the hosted demo
(for example "Stand-in video: ..."); a later export of the same job keeps them unless replaced.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from showtell.api import db
from showtell.api.jobs import (
    attempt_view_from_report,
    failure_frames,
    folder_media,
    is_media_path,
    job_view,
    map_strings,
    media_rel,
    read_events,
    valid_job_id,
)
from showtell.config import ROOT, settings

DEFAULT_OUT = Path("web/public/demo")
SEED_MEDIA = ("full.mp4", "thumb.jpg", "judge.mp4", "judge_angle.mp4")


class ExportResult(BaseModel):
    job_id: str
    out: Path
    files: int = 0  # media files copied
    bytes: int = 0
    missing: list[str] = []  # media referenced by the report/events but not on disk


class _Bundle:
    """Rewrites media paths in a job's JSON to bundle paths and collects the files to copy."""

    def __init__(self, job_dir: Path, job_id: str) -> None:
        self.job_dir = job_dir
        self.prefix = f"media/{job_id}/"
        self.files: dict[str, Path | None] = {}  # bundle-relative path -> source (None: missing)

    def add(self, rel: str) -> None:
        src = self.job_dir / rel
        self.files[rel] = src if src.is_file() else None

    def _external(self, value: str) -> Path | None:
        """A media file outside the job folder (e.g. a CLI run's --video bench/videos/x.mp4)."""
        p = Path(value)
        for cand in [p] if p.is_absolute() else [Path.cwd() / p, ROOT / p]:
            if cand.is_file():
                return cand.resolve()
        return None

    def _map(self, value: str) -> str:
        if not is_media_path(value):
            return value
        rel = media_rel(value, self.job_dir)
        if rel is not None and (self.job_dir / rel).is_file():
            self.files[rel] = self.job_dir / rel
            return self.prefix + rel
        external = self._external(value)
        if external is not None:
            rel = f"input/{external.name}"
            self.files[rel] = external
            return self.prefix + rel
        if rel is not None:
            self.files.setdefault(rel, None)
            return self.prefix + rel
        return value

    def rewrite(self, obj: Any) -> Any:
        return map_strings(obj, self._map)

    def copy_to(self, dest_root: Path) -> tuple[int, int, list[str]]:
        n = size = 0
        missing: list[str] = []
        for rel, src in sorted(self.files.items()):
            if src is None:
                missing.append(rel)
                continue
            dest = dest_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            n, size = n + 1, size + dest.stat().st_size
        return n, size, missing


def _locate(job: str | Path, data_dir: Path) -> tuple[Path, db.Job | None]:
    """(job folder, DB row if any) for a folder path or a job id."""
    path = Path(job).expanduser()
    if path.is_dir():
        return path.resolve(), None
    job_id = str(job)
    if valid_job_id(job_id):
        row = None
        db_file = db.db_path(data_dir)
        if db_file.is_file():
            engine = db.make_engine(db_file)
            try:
                with db.session(engine) as s:
                    row = s.get(db.Job, job_id)
            finally:
                engine.dispose()
        folder = data_dir / "jobs" / job_id
        if folder.is_dir():
            return folder.resolve(), row
    raise FileNotFoundError(f"no job folder or job id {str(job)!r} (looked in {data_dir / 'jobs'})")


def _load_report(job_dir: Path, row: db.Job | None) -> dict[str, Any]:
    path = job_dir / "report.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    if row is not None and row.report_json:
        return json.loads(row.report_json)
    raise FileNotFoundError(f"{path} not found: has the job finished?")


def _default_media(job_dir: Path) -> list[str]:
    """Media the web shows even when the report does not reference it."""
    rels: list[str] = []
    for seed_dir in sorted(job_dir.glob("attempts/*/seed_*")):
        rels += [f"{seed_dir.relative_to(job_dir).as_posix()}/{n}" for n in SEED_MEDIA]
    media = folder_media(job_dir)
    rels += [p.relative_to(job_dir).as_posix() for p in sorted((job_dir / "input").glob("*"))]
    rels += [m for m in (media["normalized_video"],) if m] + media["keyframes"]
    for d in sorted(job_dir.glob("attempts/*")):
        if d.name.isdigit():
            rels += failure_frames(job_dir, int(d.name))
    return [r for r in rels if is_media_path(r) and (job_dir / r).is_file()]


def _job_row(report: dict[str, Any], row: db.Job | None, job_id: str) -> db.Job:
    """The DB row, or an unsaved one built from the report (folder-only exports)."""
    if row is not None:
        return row
    return db.Job(
        id=job_id,
        created_at=float(report.get("created_at") or time.time()),
        status=str(report.get("status") or "error"),
        rules_text=str(report.get("rules") or ""),
        use_video=bool(report.get("use_video")),
        video_path=report.get("video"),
        task_id=report.get("task_id"),
        object_ids_json=db.dumps(report.get("object_ids") or []),
        n_seeds=len(report.get("seeds") or []) or 20,
        spec_json=db.dumps(report.get("spec")),
        approved=report.get("spec") is not None,
        error=report.get("error"),
    )


def _job_detail(report: dict[str, Any], row: db.Job | None, job_dir: Path, job_id: str) -> dict:
    """GET /api/jobs/<id> shape (relative media paths), built from the final report."""
    job = job_view(_job_row(report, row, job_id))
    # The report's video path is relative to the job folder; the database row may hold an
    # absolute local path, which must not end up in a public bundle.
    job.update(
        status=report.get("status") or job["status"],
        error=report.get("error"),
        video=report.get("video"),
    )
    job["links"] = {
        "events": f"{job_id}/events.json",
        "report_md": f"{job_id}/report.md",
        "report_json": f"{job_id}/report.json",
        "program": f"{job_id}/program.py",
    }
    return {
        "job": job,
        "attempts": [attempt_view_from_report(a, job_dir) for a in report.get("attempts") or []],
        "report": report,
        "media": folder_media(job_dir),
    }


def _previous_labels(out: Path, job_id: str) -> tuple[str | None, str | None]:
    """(title, note) of this job from an earlier export into the same bundle, if any."""
    try:
        index = json.loads((out / "index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    if isinstance(index, dict):
        index = index.get("jobs")
    for entry in index if isinstance(index, list) else []:
        if isinstance(entry, dict) and entry.get("id") == job_id:
            return entry.get("title"), entry.get("note")
    return None, None


def _index_entry(report: dict[str, Any], job_id: str) -> dict[str, Any]:
    return {
        "id": job_id,
        "rules": report.get("rules") or "",
        "status": report.get("status"),
        "attempts": len(report.get("attempts") or []),
        "created_at": report.get("created_at"),
        "task_id": report.get("task_id"),
        "use_video": report.get("use_video"),
        "passed": report.get("final_passed"),
        "total": report.get("final_total"),
    }


def _write_json(path: Path, obj: Any, pretty: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, indent=2 if pretty else None, ensure_ascii=False)
    path.write_text(text + "\n", encoding="utf-8")


def _update_index(path: Path, entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Add or replace `entry` in index.json (a list; a {"jobs": [...]} wrapper is read too)."""
    try:
        index = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        index = []
    if isinstance(index, dict):
        index = index.get("jobs")
    if not isinstance(index, list):
        index = []
    jobs = [e for e in index if isinstance(e, dict) and e.get("id") != entry["id"]] + [entry]
    jobs.sort(key=lambda e: float(e.get("created_at") or 0.0), reverse=True)
    _write_json(path, jobs)
    return jobs


def export_demo(
    job: str | Path,
    out: Path = DEFAULT_OUT,
    *,
    data_dir: Path | None = None,
    bench_results: Path | None = None,
    title: str | None = None,
    note: str | None = None,
    verbose: bool = True,
) -> ExportResult:
    """Copy a finished job's report, events and media into the demo bundle at `out`. `title` and
    `note` label the run in the hosted demo; None keeps the labels of an earlier export."""
    data_dir = Path(data_dir or settings.SHOWTELL_DATA_DIR)
    job_dir, row = _locate(job, data_dir)
    report = _load_report(job_dir, row)
    job_id = str(report.get("job_id") or job_dir.name)
    if not valid_job_id(job_id):
        raise ValueError(f"unsafe job id {job_id!r}")
    out = Path(out)
    bundle = _Bundle(job_dir, job_id)
    for rel in _default_media(job_dir):
        bundle.add(rel)
    old_title, old_note = _previous_labels(Path(out), job_id)
    title = old_title if title is None else title.strip() or None
    note = old_note if note is None else note.strip() or None
    report_out = bundle.rewrite(report)
    if note:
        report_out["demo_note"] = note
    events_out = bundle.rewrite(
        [ev.model_dump(mode="json") for ev in read_events(job_dir / "events.jsonl")]
    )
    detail_out = bundle.rewrite(_job_detail(report, row, job_dir, job_id))
    files, size, missing = bundle.copy_to(out / "media" / job_id)

    _write_json(out / job_id / "report.json", report_out)
    _write_json(out / job_id / "events.json", events_out, pretty=False)
    _write_json(out / job_id / "job.json", detail_out, pretty=False)
    if (job_dir / "report.md").is_file():
        md = (job_dir / "report.md").read_text(encoding="utf-8")
        if note:  # the downloaded report says how the run was made, too
            head, _, rest = md.partition("\n")
            md = f"{head}\n\n> {note}\n{rest}"
        (out / job_id / "report.md").write_text(md, encoding="utf-8")
    if report.get("final_program"):
        (out / job_id / "program.py").write_text(report["final_program"], encoding="utf-8")
    entry = _index_entry(report, job_id)
    entry.update({k: v for k, v in (("title", title), ("note", note)) if v})
    _update_index(out / "index.json", entry)
    bench = Path(bench_results or ROOT / "bench" / "results.json")
    if bench.is_file():
        shutil.copy2(bench, out / "bench.json")
    from showtell.api.app import list_tasks  # the benchmark page shows each task's sentence

    _write_json(out / "tasks.json", list_tasks())

    result = ExportResult(job_id=job_id, out=out, files=files, bytes=size, missing=missing)
    if verbose:
        print(
            f"exported job {job_id}: {len(events_out)} events, {files} media files "
            f"({size / 1e6:.1f} MB) -> {out}"
        )
        if missing:
            print(f"warning: {len(missing)} referenced media file(s) not found, e.g. {missing[0]}")
    return result
