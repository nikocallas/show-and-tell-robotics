/**
 * TypeScript mirrors of the Python contracts.
 * - showtell/agent/schemas.py: Selector, Rule, Check, TaskSpec, TrackSummary
 * - showtell/verify/runner.py: CheckResult
 * - showtell/contracts.py: Event, SamVerdict, SeedSummary, AgreementStats, AttemptReport, JobReport
 * - the FastAPI responses (GET /api/jobs/{id}, /api/tasks, /api/bench)
 * Fields the Python side always sends are required; everything else is optional so older or
 * partial payloads still render.
 */

export type Size = "small" | "large";
export type Color = "red" | "blue" | "green" | "yellow" | "clear";
export type Shape = "cube" | "cylinder" | "glass";
export type BinName = "left_bin" | "middle_bin" | "right_bin";
export type CheckKind = "in_bin" | "untouched" | "upright" | "unmoved";

export const SIZES: readonly Size[] = ["small", "large"];
export const COLORS: readonly Color[] = ["red", "blue", "green", "yellow", "clear"];
export const SHAPES: readonly Shape[] = ["cube", "cylinder", "glass"];
export const BINS: readonly BinName[] = ["left_bin", "middle_bin", "right_bin"];
export const CHECK_KINDS: readonly CheckKind[] = ["in_bin", "untouched", "upright", "unmoved"];

// ------------------------------------------------------------------ spec
/** Attribute filter; unset (null/undefined) fields match anything. */
export interface Selector {
  size?: Size | null;
  color?: Color | null;
  shape?: Shape | null;
  fragile?: boolean | null;
}
export type ObjectAttrs = Selector;

/** A rule either places objects in a bin (place_in) or leaves them (leave = true). */
export interface Rule {
  select: Selector;
  place_in?: BinName | null;
  leave?: boolean;
}

export interface Check {
  id: string; // ^[a-z0-9_]{1,40}$, unique within the spec
  kind: CheckKind;
  select: Selector;
  bin?: BinName | null; // required for in_bin
  text: string;
}

export interface TaskSpec {
  rules: Rule[];
  checks: Check[];
  clarifying_question?: string | null;
  rationale: string;
}

// ------------------------------------------------------------------ perception
export interface TrackedObject {
  track_id: string;
  concept: string;
  attrs: ObjectAttrs;
  moved: boolean;
  start_px: [number, number];
  end_px: [number, number];
  end_bin?: BinName | null;
  move_order?: number | null;
  first_move_frame?: number | null;
}

export interface TrackSummary {
  objects: TrackedObject[];
  bins: BinName[];
  fps: number;
  frames: number;
  width?: number;
  height?: number;
  notes?: string[];
}

// ------------------------------------------------------------------ verification
export interface CheckResult {
  check_id: string;
  kind: string;
  passed: boolean;
  detail: string;
  objects?: string[];
}

/** First contact between an object and anything but the table (robot.touch_events). */
export interface TouchEvent {
  t: number;
  obj: string;
  by: string;
  during: string;
}

export interface SamVerdict {
  check_id: string;
  kind: string;
  passed: boolean | null; // null = SAM could not decide
  detail?: string;
}

/** Video paths: full (hero cam), overhead (judge.mp4), judge_angle, thumb. */
export type SeedVideos = Partial<Record<"full" | "overhead" | "judge_angle" | "thumb", string>> &
  Record<string, string | undefined>;

export interface SeedSummary {
  seed: number;
  passed: boolean;
  checks: CheckResult[];
  error?: string | null;
  videos?: SeedVideos;
  sim_seconds?: number;
  touch_events?: TouchEvent[];
  sam?: SamVerdict[] | null;
}

export interface KindAgreement {
  agree: number;
  n: number;
}

export interface AgreementStats {
  agree: number;
  n: number; // (seed, check) pairs where SAM gave a verdict
  undecided: number;
  by_kind: Record<string, KindAgreement>;
}

export type AttemptKind = "initial" | "repair";

export interface AttemptReport {
  n: number;
  kind: AttemptKind;
  program: string;
  notes?: string;
  diagnosis?: string | null;
  passed: number;
  total: number;
  seeds: SeedSummary[];
  agreement?: AgreementStats | null;
  wall_seconds?: number;
}

export interface CostSummary {
  usd: number;
  wonder_usd: number;
  sam_usd: number;
  calls: number;
}

export type ReportStatus = "verified" | "failed" | "error" | "running";

/** Where the spec came from: Wonder with the video, Wonder from the sentence, or the job's `spec` field. */
export type SpecSource = "video" | "sentence" | "given";
export const SPEC_SOURCES: readonly SpecSource[] = ["video", "sentence", "given"];

export interface JobReport {
  job_id: string;
  created_at: number;
  rules: string;
  use_video: boolean;
  task_id?: string | null;
  object_ids: string[];
  video?: string | null;
  spec?: TaskSpec | null;
  spec_source?: SpecSource;
  media_path?: "video" | "keyframes" | "none";
  track_summary?: TrackSummary | null;
  attempts: AttemptReport[];
  status: ReportStatus;
  final_program: string;
  final_passed: number;
  final_total: number;
  agreement?: AgreementStats | null;
  cost?: CostSummary;
  wall_seconds?: number;
  error?: string | null;
  seeds: number[];
}

// ------------------------------------------------------------------ events
export const EVENT_TYPES = [
  "job_started",
  "stage_started",
  "stage_done",
  "track_summary",
  "spec_ready",
  "awaiting_approval",
  "spec_approved",
  "program_ready",
  "attempt_started",
  "seed_done",
  "judge_done",
  "attempt_done",
  "repair_started",
  "repair_done",
  "job_done",
  "error",
  "log",
] as const;
export type EventType = (typeof EVENT_TYPES)[number];

/** contracts.Event. `data` is untyped JSON; the reducer reads it defensively. */
export interface PipelineEvent {
  type: EventType | string;
  ts: number;
  job_id?: string | null;
  data: Record<string, unknown>;
}

// ------------------------------------------------------------------ API
export type JobStatus =
  | "queued"
  | "running"
  | "awaiting_approval"
  | "verified"
  | "failed"
  | "error";

/** `job` of GET /api/jobs/{id} (showtell/api/jobs.py job_view). Media paths are /media/... URLs. */
export interface ApiJob {
  id: string;
  created_at: string | number;
  status: JobStatus;
  stage?: string | null;
  rules: string;
  use_video: boolean;
  n_seeds?: number | null;
  task_id?: string | null;
  object_ids?: string[] | null;
  video?: string | null; // the demonstration video, e.g. /media/jobs/<id>/input/demo.mp4
  spec?: TaskSpec | null;
  clarifying_question?: string | null;
  approved?: boolean;
  awaiting_approval?: boolean;
  error?: string | null;
}

/** One of `attempts` in GET /api/jobs/{id} (jobs.py attempt_view). */
export interface ApiAttempt {
  n: number;
  kind: AttemptKind;
  program: string; // "" until program_ready
  notes?: string | null;
  diagnosis?: string | null;
  passed: number; // while running: finished seeds that passed
  total: number;
  done?: boolean; // attempt_done arrived
  agreement?: AgreementStats | null;
  wall_seconds?: number | null;
  seeds: SeedSummary[];
  failure_frames?: string[];
}

export interface ApiJobResponse {
  job: ApiJob;
  attempts: ApiAttempt[];
  report: JobReport | null;
  media?: { input_video?: string | null; normalized_video?: string | null; keyframes?: string[] };
}

/** GET /api/tasks (bench/tasks.yaml) or demo/tasks.json. */
export interface TaskPreset {
  id: string;
  rules: string;
  objects: string[];
  demo?: string;
  video?: string;
  /** bench/videos/<video> exists: use_video without an upload runs on that recording. */
  has_video?: boolean;
}

/** A pass rate cell: {passed, total}, "17/20", or a word such as "no video" / "error". */
export type PassValue = string | number | { passed: number; total: number } | null | undefined;

/** One row of the benchmark table (built from bench/results.json by lib/bench.ts). */
export interface BenchRow {
  task: string;
  rules?: string;
  first_try_video?: PassValue;
  final_video?: PassValue;
  repairs?: number | string | null;
  final_sentence_only?: PassValue;
  judge_agreement?: number | string | null; // "93%" (or a fraction / percent number)
  cost_usd?: number | string | null; // both conditions
  time_s?: number | string | null; // the video + sentence run
}

export interface BenchResults {
  tasks: BenchRow[];
  generated_at?: string | number | null;
  n_seeds?: number | null;
}

/** One entry of demo/index.json (export_demo writes {id, rules, status, attempts, passed, total, ...}). */
export interface DemoIndexEntry {
  id: string;
  title?: string;
  rules?: string;
  task_id?: string | null;
  status?: string;
  final_passed?: number;
  final_total?: number;
  passed?: number | null;
  total?: number | null;
  attempts?: number;
  use_video?: boolean;
  created_at?: number | string;
  wall_seconds?: number;
}
