"""SQLite persistence for API jobs: SQLModel tables Job -> Attempt -> SeedRun.

The database lives at SHOWTELL_DATA_DIR/showtell.db. Columns ending in `_json` hold JSON dumps of
the pydantic contracts; media paths are stored relative to the job folder (data/jobs/<id>/).
"""

import json
import time
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import UniqueConstraint, event, inspect, text
from sqlalchemy.engine import URL, Engine
from sqlmodel import Field, Session, SQLModel, col, create_engine, func, select

from showtell.config import settings

JobStatus = Literal["queued", "running", "awaiting_approval", "verified", "failed", "error"]
ACTIVE_STATUSES: tuple[str, ...] = ("queued", "running", "awaiting_approval")
FINAL_STATUSES: tuple[str, ...] = ("verified", "failed", "error")
DB_NAME = "showtell.db"


class Job(SQLModel, table=True):
    id: str = Field(primary_key=True, max_length=64)
    created_at: float = Field(default_factory=time.time, index=True)
    status: str = Field(default="queued", index=True)  # one of JobStatus
    rules_text: str
    use_video: bool = True
    video_path: str | None = None  # relative to the job folder, e.g. "input/demo.mp4"
    task_id: str | None = None
    object_ids_json: str | None = None
    n_seeds: int = 20
    auto_approve: bool = False
    use_judge: bool = True
    config_json: str | None = None  # the JobConfig handed to the pipeline
    spec_json: str | None = None
    approved: bool = False
    report_json: str | None = None
    error: str | None = None
    finished_at: float | None = None


class Attempt(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("job_id", "n"),)

    id: int | None = Field(default=None, primary_key=True)
    job_id: str = Field(foreign_key="job.id", index=True)
    n: int
    kind: str = "initial"  # "initial" | "repair"
    program_src: str = ""
    notes: str = ""
    diagnosis: str | None = None
    passed: int = 0
    total: int = 0
    done: bool = False
    agreement_json: str | None = None
    wall_seconds: float = 0.0


class SeedRun(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("attempt_id", "seed"),)

    id: int | None = Field(default=None, primary_key=True)
    attempt_id: int = Field(foreign_key="attempt.id", index=True)
    seed: int
    passed: bool = False
    checks_json: str = "[]"
    sam_json: str | None = None
    full_video: str | None = None
    judge_video: str | None = None  # overhead judge clip (judge.mp4)
    judge_angle_video: str | None = None
    thumb: str | None = None
    error: str | None = None
    sim_seconds: float = 0.0
    touch_events_json: str = "[]"


# ------------------------------------------------------------------------------------- engine
def db_path(data_dir: Path | None = None) -> Path:
    return Path(data_dir or settings.SHOWTELL_DATA_DIR) / DB_NAME


def _sqlite_pragmas(dbapi_conn: Any, _record: Any) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")  # readers never block the job thread's writes
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


def _add_missing_columns(engine: Engine) -> None:
    """Tiny forward migration: add columns that a newer schema has and an old file lacks."""
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for column in table.columns:
                if column.name not in have:
                    kind = column.type.compile(dialect=engine.dialect)
                    conn.execute(
                        text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {kind}')
                    )


def make_engine(path: Path | None = None) -> Engine:
    """SQLite engine usable from any thread; creates the file and tables if needed."""
    path = Path(path or db_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        URL.create("sqlite", database=str(path)),
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    event.listen(engine, "connect", _sqlite_pragmas)
    SQLModel.metadata.create_all(engine)
    _add_missing_columns(engine)
    return engine


def session(engine: Engine) -> Session:
    """A session whose objects stay readable after commit/close."""
    return Session(engine, expire_on_commit=False)


# ------------------------------------------------------------------------------------- JSON
def dumps(obj: Any) -> str | None:
    return None if obj is None else json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def loads(raw: str | None, default: Any = None) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except ValueError:
        return default


# ------------------------------------------------------------------------------------- queries
def list_jobs(s: Session, limit: int = 50) -> list[Job]:
    stmt = select(Job).order_by(col(Job.created_at).desc()).limit(limit)
    return list(s.exec(stmt).all())


def job_attempts(s: Session, job_ids: list[str]) -> dict[str, list[Attempt]]:
    """Attempts per job, ordered by attempt number."""
    out: dict[str, list[Attempt]] = {j: [] for j in job_ids}
    if not job_ids:
        return out
    stmt = select(Attempt).where(col(Attempt.job_id).in_(job_ids)).order_by(col(Attempt.n))
    for a in s.exec(stmt).all():
        out[a.job_id].append(a)
    return out


def attempt_seeds(s: Session, attempt_ids: list[int]) -> dict[int, list[SeedRun]]:
    """Seed runs per attempt, ordered by seed."""
    out: dict[int, list[SeedRun]] = {a: [] for a in attempt_ids}
    if not attempt_ids:
        return out
    stmt = (
        select(SeedRun).where(col(SeedRun.attempt_id).in_(attempt_ids)).order_by(col(SeedRun.seed))
    )
    for r in s.exec(stmt).all():
        out[r.attempt_id].append(r)
    return out


def jobs_with_status(s: Session, statuses: tuple[str, ...]) -> list[Job]:
    return list(s.exec(select(Job).where(col(Job.status).in_(statuses))).all())


def status_counts(s: Session) -> dict[str, int]:
    """Number of jobs per status."""
    rows = s.exec(select(Job.status, func.count()).group_by(Job.status)).all()
    return {status: int(n) for status, n in rows}


def upsert_attempt(s: Session, job_id: str, n: int, **fields: Any) -> Attempt:
    """Create or update attempt `n` of a job (flushes so the row has an id)."""
    row = s.exec(select(Attempt).where(Attempt.job_id == job_id, Attempt.n == n)).first()
    if row is None:
        row = Attempt(job_id=job_id, n=n, kind="initial" if n == 0 else "repair")
    for k, v in fields.items():
        setattr(row, k, v)
    s.add(row)
    s.flush()
    return row


def upsert_seed_run(s: Session, attempt_id: int, seed: int, **fields: Any) -> SeedRun:
    row = s.exec(
        select(SeedRun).where(SeedRun.attempt_id == attempt_id, SeedRun.seed == seed)
    ).first()
    if row is None:
        row = SeedRun(attempt_id=attempt_id, seed=seed)
    for k, v in fields.items():
        setattr(row, k, v)
    s.add(row)
    s.flush()
    return row
