"""Shared contracts: pipeline events (streamed to the web app), job config, and the job report.

Job folder layout (under settings.SHOWTELL_DATA_DIR / "jobs" / job_id):
    input/<video>                     uploaded demo video (optional)
    prep/normalized.mp4, prep/keyframes/kf_00.jpg ...
    track_summary.json, discovery.json, spec.json, spec_approved.json
    attempts/<n>/program.py
    attempts/<n>/seed_<seed>/{full.mp4, judge.mp4, judge_angle.mp4, thumb.jpg, result.json,
                              sam.json}
    events.jsonl, report.json, report.md
Video paths inside reports and events are relative to the job folder (served at
/media/jobs/<id>/...).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from showtell.agent.schemas import TaskSpec, TrackSummary
from showtell.verify.runner import CheckResult

EventType = Literal[
    "job_started",  # {rules, use_video, task_id, n_seeds}
    "stage_started",  # {stage, attempt?}  stage in STAGES
    "stage_done",  # {stage, seconds, attempt?, info?}
    "track_summary",  # {track_summary: TrackSummary}
    # spec_ready: {spec, source: "video"|"sentence"|"given", media_path: "video"|"keyframes"|"none"}
    "spec_ready",
    "awaiting_approval",  # {spec, clarifying_question}
    "spec_approved",  # {spec, edited: bool}
    "program_ready",  # {attempt, source, notes, kind: "initial"|"repair", diagnosis?}
    "attempt_started",  # {attempt, seeds: [int]}
    "seed_done",  # {attempt, seed: SeedSummary}
    "judge_done",  # {attempt, seed, verdicts: [SamVerdict]}
    "attempt_done",  # {attempt, passed, total, failed_seeds, agreement?}
    "repair_started",  # {attempt}  (attempt = the new attempt number)
    "repair_done",  # {attempt, diagnosis, source}
    "job_done",  # {status, report: JobReport}
    "error",  # {message, stage?}
    "log",  # {message}
]
STAGES = (
    "prep",
    "discover",
    "track",
    "summary",
    "spec",
    "approval",
    "program",
    "verify",
    "judge",
    "repair",
    "report",
)


class Event(BaseModel):
    type: EventType
    ts: float = Field(default_factory=time.time)
    job_id: str | None = None
    data: dict[str, Any] = {}


class SamVerdict(BaseModel):
    check_id: str
    kind: str
    passed: bool | None  # None = SAM could not decide (object not found, etc.)
    detail: str = ""


class SeedSummary(BaseModel):
    seed: int
    passed: bool
    checks: list[CheckResult] = []
    error: str | None = None
    videos: dict[
        str, str
    ] = {}  # full, overhead (judge.mp4), judge_angle, thumb: paths relative to job dir
    sim_seconds: float = 0.0
    touch_events: list[dict[str, Any]] = []
    sam: list[SamVerdict] | None = None


class KindAgreement(BaseModel):
    agree: int = 0
    n: int = 0

    @property
    def pct(self) -> float | None:
        return None if self.n == 0 else 100.0 * self.agree / self.n


class AgreementStats(BaseModel):
    agree: int = 0
    n: int = 0  # (seed, check) pairs where SAM gave a verdict
    undecided: int = 0  # pairs where SAM gave no verdict
    by_kind: dict[str, KindAgreement] = {}

    @property
    def pct(self) -> float | None:
        return None if self.n == 0 else 100.0 * self.agree / self.n


class AttemptReport(BaseModel):
    n: int
    kind: Literal["initial", "repair"]
    program: str
    notes: str = ""
    diagnosis: str | None = None
    passed: int = 0
    total: int = 0
    seeds: list[SeedSummary] = []
    agreement: AgreementStats | None = None
    wall_seconds: float = 0.0


class CostSummary(BaseModel):
    usd: float = 0.0
    wonder_usd: float = 0.0
    sam_usd: float = 0.0
    calls: int = 0


class JobConfig(BaseModel):
    job_id: str
    rules_text: str
    video_path: Path | None = None
    use_video: bool = True
    task_id: str | None = None  # fixes the sim objects (bench); otherwise inferred from the video
    object_ids: list[str] | None = None  # explicit sim objects (overrides task_id)
    n_seeds: int = 20
    seed_base: int | None = None  # default: task_index*1000, or 9000 for ad-hoc jobs
    use_judge: bool = True
    interactive: bool = False
    max_repairs: int = 3
    out_dir: Path | None = None  # default: data/jobs/<job_id>
    hero_size: tuple[int, int] = (640, 480)
    spec: TaskSpec | None = None  # use this spec instead of generating one
    program_source: str | None = None  # test this program instead of generating one


class JobReport(BaseModel):
    job_id: str
    created_at: float
    rules: str
    use_video: bool
    task_id: str | None = None
    object_ids: list[str] = []
    video: str | None = None
    spec: TaskSpec | None = None
    spec_source: Literal["video", "sentence", "given"] = "sentence"
    media_path: Literal["video", "keyframes", "none"] = "none"
    track_summary: TrackSummary | None = None
    attempts: list[AttemptReport] = []
    status: Literal["verified", "failed", "error", "running"] = "running"
    final_program: str = ""
    final_passed: int = 0
    final_total: int = 0
    agreement: AgreementStats | None = None
    cost: CostSummary = CostSummary()
    wall_seconds: float = 0.0
    error: str | None = None
    seeds: list[int] = []
