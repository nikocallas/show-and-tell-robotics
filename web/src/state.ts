/**
 * Job state and the event reducer. Live SSE and demo replay feed the same `applyEvent`, so a
 * replayed run renders exactly like a live one. Event payloads follow showtell/contracts.py.
 */

import type {
  AgreementStats,
  ApiAttempt,
  ApiJobResponse,
  AttemptKind,
  AttemptReport,
  JobReport,
  JobStatus,
  PipelineEvent,
  SamVerdict,
  SeedSummary,
  SpecSource,
  TaskSpec,
  TrackSummary,
} from "./types";
import { SPEC_SOURCES } from "./types";
import { toEpochSeconds } from "./lib/format";

// ------------------------------------------------------------------ stages
/** Stages shown in the strip; the pipeline's "summary" folds into track and "approval" into spec. */
export const STAGES = [
  "prep",
  "discover",
  "track",
  "spec",
  "program",
  "verify",
  "judge",
  "repair",
  "report",
] as const;
export type StageKey = (typeof STAGES)[number];

const STAGE_ALIAS: Record<string, StageKey> = { summary: "track", approval: "spec" };

export function stageKey(name: unknown): StageKey | null {
  if (typeof name !== "string") return null;
  if ((STAGES as readonly string[]).includes(name)) return name as StageKey;
  return STAGE_ALIAS[name] ?? null;
}

export type StageStatus = "pending" | "running" | "waiting" | "done" | "skipped" | "error";

export interface StageState {
  status: StageStatus;
  seconds: number; // accumulated over repeated runs (verify/judge/repair run once per attempt)
  attempt: number | null;
  startedAt: number | null;
}

// ------------------------------------------------------------------ attempts
export type AttemptStatus = "writing" | "ready" | "running" | "done";

export interface AttemptState {
  n: number;
  kind: AttemptKind;
  program: string | null;
  notes: string;
  diagnosis: string | null;
  seeds: number[];
  results: Record<number, SeedSummary>;
  sam: Record<number, SamVerdict[]>;
  passed: number | null; // set by attempt_done
  total: number | null;
  failedSeeds: number[];
  agreement: AgreementStats | null;
  status: AttemptStatus;
  startedAt: number | null;
  endedAt: number | null;
  wallSeconds: number | null;
}

export interface LogLine {
  ts: number | null;
  message: string;
  level: "info" | "error";
}

export interface JobState {
  jobId: string;
  rules: string;
  useVideo: boolean;
  taskId: string | null;
  objectIds: string[];
  nSeeds: number;
  seeds: number[];
  status: JobStatus;
  createdAt: number | null;
  startedAt: number | null;
  endedAt: number | null;
  lastTs: number | null;
  stages: Record<StageKey, StageState>;
  activeStage: StageKey | null;
  trackSummary: TrackSummary | null;
  spec: TaskSpec | null;
  specSource: SpecSource | null;
  mediaPath: string | null;
  clarifyingQuestion: string | null;
  awaitingApproval: boolean;
  specApproved: boolean;
  specEdited: boolean;
  attempts: AttemptState[];
  report: JobReport | null;
  error: string | null;
  logs: LogLine[];
  videoPath: string | null;
  eventCount: number;
}

export interface JobMeta {
  rules?: string;
  useVideo?: boolean;
  taskId?: string | null;
  objectIds?: string[];
  nSeeds?: number;
  seeds?: number[];
  createdAt?: number | null;
  videoPath?: string | null;
  status?: JobStatus;
}

const MAX_LOGS = 200;
const DEFAULT_SEEDS = 20;

function freshStages(): Record<StageKey, StageState> {
  const out = {} as Record<StageKey, StageState>;
  for (const k of STAGES) out[k] = { status: "pending", seconds: 0, attempt: null, startedAt: null };
  return out;
}

export function initialJobState(jobId: string, meta: JobMeta = {}): JobState {
  const seeds = meta.seeds ?? [];
  return {
    jobId,
    rules: meta.rules ?? "",
    useVideo: meta.useVideo ?? true,
    taskId: meta.taskId ?? null,
    objectIds: meta.objectIds ?? [],
    nSeeds: meta.nSeeds ?? (seeds.length || DEFAULT_SEEDS),
    seeds,
    status: meta.status ?? "queued",
    createdAt: meta.createdAt ?? null,
    startedAt: null,
    endedAt: null,
    lastTs: null,
    stages: freshStages(),
    activeStage: null,
    trackSummary: null,
    spec: null,
    specSource: null,
    mediaPath: null,
    clarifyingQuestion: null,
    awaitingApproval: false,
    specApproved: false,
    specEdited: false,
    attempts: [],
    report: null,
    error: null,
    logs: [],
    videoPath: meta.videoPath ?? null,
    eventCount: 0,
  };
}

function newAttempt(n: number, kind: AttemptKind): AttemptState {
  return {
    n,
    kind,
    program: null,
    notes: "",
    diagnosis: null,
    seeds: [],
    results: {},
    sam: {},
    passed: null,
    total: null,
    failedSeeds: [],
    agreement: null,
    status: "writing",
    startedAt: null,
    endedAt: null,
    wallSeconds: null,
  };
}

// ------------------------------------------------------------------ defensive readers
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const str = (v: unknown): string | null => (typeof v === "string" ? v : null);
const bool = (v: unknown): boolean | null => (typeof v === "boolean" ? v : null);
const obj = <T>(v: unknown): T | null =>
  v !== null && typeof v === "object" && !Array.isArray(v) ? (v as T) : null;
const numList = (v: unknown): number[] =>
  Array.isArray(v) ? v.filter((x): x is number => typeof x === "number") : [];
const specSource = (v: unknown): SpecSource | null =>
  (SPEC_SOURCES as readonly unknown[]).includes(v) ? (v as SpecSource) : null;

function setStage(s: JobState, key: StageKey, patch: Partial<StageState>): JobState {
  return { ...s, stages: { ...s.stages, [key]: { ...s.stages[key], ...patch } } };
}

function withAttempt(
  s: JobState,
  n: number,
  fn: (a: AttemptState) => AttemptState,
  kind?: AttemptKind,
): JobState {
  const idx = s.attempts.findIndex((a) => a.n === n);
  const base = idx >= 0 ? s.attempts[idx] : newAttempt(n, kind ?? (n === 0 ? "initial" : "repair"));
  const updated = fn(base);
  const attempts =
    idx >= 0
      ? s.attempts.map((a, i) => (i === idx ? updated : a))
      : [...s.attempts, updated].sort((x, y) => x.n - y.n);
  return { ...s, attempts };
}

export function normalizeStatus(v: unknown, fallback: JobStatus = "running"): JobStatus {
  const allowed: JobStatus[] = [
    "queued",
    "running",
    "awaiting_approval",
    "verified",
    "failed",
    "error",
  ];
  return typeof v === "string" && (allowed as string[]).includes(v) ? (v as JobStatus) : fallback;
}

export function isTerminal(status: JobStatus): boolean {
  return status === "verified" || status === "failed" || status === "error";
}

/** When the job ends: never-started stages were skipped; open ones finished (or failed). */
function finalizeStages(
  stages: Record<StageKey, StageState>,
  failed: boolean,
): Record<StageKey, StageState> {
  const out = { ...stages };
  for (const k of STAGES) {
    const st = out[k];
    if (st.status === "pending") out[k] = { ...st, status: "skipped" };
    else if (st.status === "running" || st.status === "waiting")
      out[k] = { ...st, status: failed ? "error" : "done" };
  }
  return out;
}

// ------------------------------------------------------------------ reports -> attempts
function attemptFromSeeds(
  base: AttemptState,
  seeds: SeedSummary[],
  passed: number,
  total: number,
): AttemptState {
  const results: Record<number, SeedSummary> = {};
  const sam: Record<number, SamVerdict[]> = { ...base.sam };
  for (const sd of seeds) {
    const verdicts = sd.sam ?? base.sam[sd.seed] ?? null;
    results[sd.seed] = { ...sd, sam: verdicts };
    if (verdicts) sam[sd.seed] = verdicts;
  }
  return {
    ...base,
    seeds: seeds.length ? seeds.map((sd) => sd.seed) : base.seeds,
    results,
    sam,
    passed,
    total,
    failedSeeds: seeds.filter((sd) => !sd.passed).map((sd) => sd.seed),
    status: "done",
  };
}

function attemptFromReport(a: AttemptReport, prev: AttemptState | undefined): AttemptState {
  const base: AttemptState = {
    ...(prev ?? newAttempt(a.n, a.kind)),
    n: a.n,
    kind: a.kind,
    program: a.program ?? prev?.program ?? null,
    notes: a.notes ?? prev?.notes ?? "",
    diagnosis: a.diagnosis ?? prev?.diagnosis ?? null,
    agreement: a.agreement ?? prev?.agreement ?? null,
    wallSeconds: a.wall_seconds ?? prev?.wallSeconds ?? null,
  };
  return attemptFromSeeds(base, a.seeds ?? [], a.passed, a.total);
}

/** An attempt of GET /api/jobs/{id} (jobs.py attempt_view). `jobDone`: the job is over. */
function attemptFromApi(a: ApiAttempt, jobDone: boolean): AttemptState {
  const base: AttemptState = {
    ...newAttempt(a.n, a.kind),
    program: a.program || null,
    notes: a.notes ?? "",
    diagnosis: a.diagnosis ?? null,
    agreement: a.agreement ?? null,
    wallSeconds: a.wall_seconds || null,
  };
  const seeds = a.seeds ?? [];
  const st = attemptFromSeeds(base, seeds, a.passed, a.total);
  const complete =
    jobDone || (typeof a.done === "boolean" ? a.done : a.total > 0 && seeds.length >= a.total);
  if (complete) return st;
  // Not done: `passed` counts finished seeds only, `total` is the attempt's seed count once it
  // started. No program yet = still being written.
  const status = !base.program ? "writing" : seeds.length || a.total ? "running" : "ready";
  return { ...st, status, passed: null, total: a.total || null };
}

/** Fold a JobReport (job_done payload, report.json, or the API snapshot) into the state. */
export function mergeReport(s: JobState, report: JobReport): JobState {
  const attempts = (report.attempts ?? []).map((a) =>
    attemptFromReport(
      a,
      s.attempts.find((x) => x.n === a.n),
    ),
  );
  const seeds = report.seeds?.length ? report.seeds : s.seeds;
  return {
    ...s,
    report,
    rules: report.rules || s.rules,
    useVideo: report.use_video ?? s.useVideo,
    taskId: report.task_id ?? s.taskId,
    objectIds: report.object_ids?.length ? report.object_ids : s.objectIds,
    seeds,
    nSeeds: seeds.length || s.nSeeds,
    createdAt: report.created_at ?? s.createdAt,
    trackSummary: report.track_summary ?? s.trackSummary,
    spec: report.spec ?? s.spec,
    specSource: specSource(report.spec_source) ?? s.specSource,
    mediaPath: report.media_path ?? s.mediaPath,
    videoPath: s.videoPath ?? report.video ?? null,
    error: report.error ?? s.error,
    attempts: attempts.length ? attempts : s.attempts,
  };
}

// ------------------------------------------------------------------ the reducer
export function applyEvent(state: JobState, ev: PipelineEvent): JobState {
  const d: Record<string, unknown> = ev.data ?? {};
  const ts = num(ev.ts);
  let s: JobState = {
    ...state,
    eventCount: state.eventCount + 1,
    lastTs: ts ?? state.lastTs,
    startedAt: state.startedAt ?? ts,
  };

  switch (ev.type) {
    case "job_started": {
      s.rules = str(d.rules) ?? s.rules;
      s.useVideo = bool(d.use_video) ?? s.useVideo;
      s.taskId = str(d.task_id) ?? s.taskId;
      s.nSeeds = num(d.n_seeds) ?? s.nSeeds;
      s.startedAt = ts ?? s.startedAt;
      if (s.status === "queued") s.status = "running";
      if (!s.useVideo) {
        for (const k of ["prep", "discover", "track"] as const) {
          s = setStage(s, k, { status: "skipped" });
        }
      }
      break;
    }
    case "stage_started": {
      const key = stageKey(d.stage);
      if (!key) break;
      const attempt = num(d.attempt);
      s = setStage(s, key, {
        status: d.stage === "approval" ? "waiting" : "running",
        startedAt: ts,
        attempt: attempt ?? s.stages[key].attempt,
      });
      s.activeStage = key;
      if (s.status === "queued") s.status = "running";
      if (key === "repair" && attempt != null) {
        s = withAttempt(s, attempt, (a) => ({ ...a, status: "writing" }), "repair");
      }
      break;
    }
    case "stage_done": {
      const key = stageKey(d.stage);
      if (!key) break;
      const prev = s.stages[key];
      s = setStage(s, key, {
        status: "done",
        seconds: prev.seconds + (num(d.seconds) ?? 0),
        attempt: num(d.attempt) ?? prev.attempt,
      });
      break;
    }
    case "track_summary": {
      s.trackSummary = obj<TrackSummary>(d.track_summary) ?? s.trackSummary;
      break;
    }
    case "spec_ready": {
      const spec = obj<TaskSpec>(d.spec);
      if (spec) {
        s.spec = spec;
        s.clarifyingQuestion = spec.clarifying_question ?? null;
      }
      s.specSource = specSource(d.source) ?? s.specSource;
      s.mediaPath = str(d.media_path) ?? s.mediaPath;
      break;
    }
    case "awaiting_approval": {
      const spec = obj<TaskSpec>(d.spec);
      if (spec) s.spec = spec;
      s.clarifyingQuestion =
        str(d.clarifying_question) ?? s.spec?.clarifying_question ?? s.clarifyingQuestion;
      s.awaitingApproval = true;
      if (!isTerminal(s.status)) s.status = "awaiting_approval";
      s = setStage(s, "spec", { status: "waiting" });
      s.activeStage = "spec";
      break;
    }
    case "spec_approved": {
      const spec = obj<TaskSpec>(d.spec);
      if (spec) s.spec = spec;
      s.specApproved = true;
      s.specEdited = bool(d.edited) ?? false;
      s.awaitingApproval = false;
      if (s.status === "awaiting_approval" || s.status === "queued") s.status = "running";
      if (s.stages.spec.status === "waiting") s = setStage(s, "spec", { status: "done" });
      break;
    }
    case "program_ready": {
      const n = num(d.attempt) ?? 0;
      const kind: AttemptKind = d.kind === "repair" || n > 0 ? "repair" : "initial";
      s = withAttempt(
        s,
        n,
        (a) => ({
          ...a,
          kind,
          program: str(d.source) ?? a.program,
          notes: str(d.notes) || a.notes,
          diagnosis: str(d.diagnosis) ?? a.diagnosis,
          status: a.status === "writing" ? "ready" : a.status,
        }),
        kind,
      );
      break;
    }
    case "attempt_started": {
      const n = num(d.attempt) ?? 0;
      const seeds = numList(d.seeds);
      s = withAttempt(s, n, (a) => ({
        ...a,
        seeds: seeds.length ? seeds : a.seeds,
        status: "running",
        startedAt: ts,
      }));
      if (!s.seeds.length && seeds.length) s.seeds = seeds;
      if (seeds.length) s.nSeeds = seeds.length;
      break;
    }
    case "seed_done": {
      const n = num(d.attempt) ?? 0;
      const seed = obj<SeedSummary>(d.seed);
      if (!seed || typeof seed.seed !== "number") break;
      s = withAttempt(s, n, (a) => ({
        ...a,
        results: { ...a.results, [seed.seed]: { ...seed, sam: seed.sam ?? a.sam[seed.seed] ?? null } },
        status: a.status === "done" ? "done" : "running",
      }));
      break;
    }
    case "judge_done": {
      const n = num(d.attempt) ?? 0;
      const seedNo = num(d.seed);
      if (seedNo == null) break;
      const verdicts = Array.isArray(d.verdicts) ? (d.verdicts as SamVerdict[]) : [];
      // The SAM judge starts on each scene as soon as it finishes, before the judge stage opens.
      if (s.stages.judge.status !== "running") {
        s = setStage(s, "judge", { status: "running", attempt: n });
      }
      s = withAttempt(s, n, (a) => {
        const prev = a.results[seedNo];
        return {
          ...a,
          sam: { ...a.sam, [seedNo]: verdicts },
          results: prev ? { ...a.results, [seedNo]: { ...prev, sam: verdicts } } : a.results,
        };
      });
      break;
    }
    case "attempt_done": {
      const n = num(d.attempt) ?? 0;
      s = withAttempt(s, n, (a) => ({
        ...a,
        passed: num(d.passed) ?? a.passed,
        total: num(d.total) ?? a.total,
        failedSeeds: Array.isArray(d.failed_seeds) ? numList(d.failed_seeds) : a.failedSeeds,
        agreement: obj<AgreementStats>(d.agreement) ?? a.agreement,
        diagnosis: str(d.diagnosis) ?? a.diagnosis,
        status: "done",
        endedAt: ts,
        wallSeconds: a.startedAt != null && ts != null ? ts - a.startedAt : a.wallSeconds,
      }));
      break;
    }
    case "repair_started": {
      const n = num(d.attempt);
      if (n == null) break;
      s = withAttempt(s, n, (a) => ({ ...a, kind: "repair", status: "writing" }), "repair");
      break;
    }
    case "repair_done": {
      const n = num(d.attempt);
      if (n == null) break;
      s = withAttempt(
        s,
        n,
        (a) => ({
          ...a,
          kind: "repair",
          diagnosis: str(d.diagnosis) ?? a.diagnosis,
          program: str(d.source) ?? a.program,
          status: a.status === "writing" ? "ready" : a.status,
        }),
        "repair",
      );
      break;
    }
    case "job_done": {
      const report = obj<JobReport>(d.report);
      s = completeJob(s, normalizeStatus(d.status ?? report?.status, "failed"), report);
      s.endedAt = ts;
      break;
    }
    case "error": {
      const message = str(d.message) ?? "Unknown error";
      s.error = message;
      s.logs = [...s.logs, { ts, message, level: "error" as const }].slice(-MAX_LOGS);
      const stages = { ...s.stages };
      for (const k of STAGES) {
        if (stages[k].status === "running" || stages[k].status === "waiting") {
          stages[k] = { ...stages[k], status: "error" };
        }
      }
      s.stages = stages;
      if (!isTerminal(s.status)) s.status = "error";
      break;
    }
    case "log": {
      const message = str(d.message);
      if (message) s.logs = [...s.logs, { ts, message, level: "info" as const }].slice(-MAX_LOGS);
      break;
    }
    default:
      break;
  }
  return s;
}

export function reduceEvents(events: PipelineEvent[], init: JobState): JobState {
  return events.reduce(applyEvent, init);
}

/** The end of a job (job_done, or a snapshot that says the job is over). */
export function completeJob(s: JobState, status: JobStatus, report: JobReport | null): JobState {
  const out = report ? mergeReport(s, report) : { ...s };
  const finalStatus: JobStatus = isTerminal(status) ? status : "failed";
  return {
    ...out,
    status: finalStatus,
    awaitingApproval: false,
    endedAt: out.endedAt ?? out.lastTs,
    stages: finalizeStages(out.stages, finalStatus === "error"),
  };
}

// ------------------------------------------------------------------ snapshots
export function metaFromReport(report: JobReport): JobMeta {
  return {
    rules: report.rules,
    useVideo: report.use_video,
    taskId: report.task_id ?? null,
    objectIds: report.object_ids ?? [],
    seeds: report.seeds ?? [],
    nSeeds: report.seeds?.length || undefined,
    createdAt: report.created_at ?? null,
    videoPath: report.video ?? null,
  };
}

/** Final state straight from a report (demo bundles without an event log). */
export function stateFromReport(jobId: string, report: JobReport): JobState {
  let s = mergeReport(initialJobState(jobId, metaFromReport(report)), report);
  s.status = normalizeStatus(report.status, "failed");
  s.specApproved = report.spec != null;
  s = { ...s, stages: deriveStages(s) };
  s.stages = finalizeStages(s.stages, s.status === "error");
  return s;
}

export function metaFromSnapshot(resp: ApiJobResponse): JobMeta {
  const j = resp.job;
  return {
    rules: j.rules ?? "",
    useVideo: j.use_video,
    taskId: j.task_id ?? null,
    objectIds: j.object_ids ?? [],
    nSeeds: num(j.n_seeds) ?? undefined,
    createdAt: toEpochSeconds(j.created_at),
    videoPath: j.video ?? null,
    seeds: resp.report?.seeds ?? [],
  };
}

/** State from GET /api/jobs/{id} alone (used until the event stream delivers, or without it). */
export function stateFromSnapshot(resp: ApiJobResponse): JobState {
  const j = resp.job;
  const status = normalizeStatus(j.status, "queued");
  let s = initialJobState(j.id, { ...metaFromSnapshot(resp), status });
  if (resp.report) s = mergeReport(s, resp.report);
  else if (resp.attempts?.length) {
    const done = isTerminal(status);
    s.attempts = resp.attempts.map((a) => attemptFromApi(a, done)).sort((x, y) => x.n - y.n);
  }
  s.spec = j.spec ?? s.spec;
  s.clarifyingQuestion = s.spec?.clarifying_question ?? null;
  s.specApproved = Boolean(j.approved) || s.attempts.length > 0;
  s.awaitingApproval = status === "awaiting_approval";
  s.error = j.error ?? s.error;
  s.status = status;
  s.stages = deriveStages(s);
  if (isTerminal(status)) s.stages = finalizeStages(s.stages, status === "error");
  return s;
}

/** Rough stage statuses from data alone (no stage events available). */
function deriveStages(s: JobState): Record<StageKey, StageState> {
  const st = freshStages();
  const mark = (k: StageKey, status: StageStatus) => (st[k] = { ...st[k], status });
  if (!s.useVideo) (["prep", "discover", "track"] as const).forEach((k) => mark(k, "skipped"));
  else if (s.trackSummary) (["prep", "discover", "track"] as const).forEach((k) => mark(k, "done"));
  if (s.spec) mark("spec", s.awaitingApproval ? "waiting" : "done");
  if (s.attempts.length) mark("program", "done");
  const last = s.attempts[s.attempts.length - 1];
  if (last) {
    mark("verify", last.status === "done" ? "done" : "running");
    if (s.attempts.some((a) => a.agreement)) mark("judge", "done");
    if (s.attempts.some((a) => a.kind === "repair")) mark("repair", "done");
  }
  if (s.report) mark("report", "done");
  return st;
}

// ------------------------------------------------------------------ derived values
export interface AttemptCounts {
  passed: number;
  failed: number;
  done: number;
  total: number;
}

export function attemptCounts(a: AttemptState, nSeeds: number): AttemptCounts {
  const results = Object.values(a.results);
  const total = a.total ?? (a.seeds.length || nSeeds);
  if (a.status === "done" && a.passed != null) {
    return { passed: a.passed, failed: Math.max(0, total - a.passed), done: total, total };
  }
  const passed = results.filter((r) => r.passed).length;
  return { passed, failed: results.length - passed, done: results.length, total };
}

export function lastAttempt(s: JobState): AttemptState | null {
  return s.attempts.length ? s.attempts[s.attempts.length - 1] : null;
}

/** All scenes of the final attempt pass (the job is VERIFIED). */
export function isVerified(s: JobState): boolean {
  if (s.status === "verified") return true;
  if (s.status === "failed" || s.status === "error") return false;
  const last = lastAttempt(s);
  return Boolean(
    last && last.status === "done" && last.total != null && last.total > 0 && last.passed === last.total,
  );
}

export interface SeedAgreement {
  agree: number;
  n: number;
  undecided: number;
  disagree: string[]; // check ids where SAM and ground truth differ
}

export function seedAgreement(seed: SeedSummary): SeedAgreement | null {
  if (!seed.sam || seed.sam.length === 0) return null;
  const out: SeedAgreement = { agree: 0, n: 0, undecided: 0, disagree: [] };
  for (const c of seed.checks) {
    const v = seed.sam.find((x) => x.check_id === c.check_id);
    if (!v) continue;
    if (v.passed == null) {
      out.undecided++;
      continue;
    }
    out.n++;
    if (v.passed === c.passed) out.agree++;
    else out.disagree.push(c.check_id);
  }
  return out;
}

/** The attempt's agreement: the pipeline's numbers when final, else computed from what has arrived. */
export function attemptAgreement(a: AttemptState): AgreementStats | null {
  if (a.agreement) return a.agreement;
  const stats: AgreementStats = { agree: 0, n: 0, undecided: 0, by_kind: {} };
  let any = false;
  for (const seed of Object.values(a.results)) {
    if (!seed.sam?.length) continue;
    for (const c of seed.checks) {
      const v = seed.sam.find((x) => x.check_id === c.check_id);
      if (!v) continue;
      any = true;
      if (v.passed == null) {
        stats.undecided++;
        continue;
      }
      const k = (stats.by_kind[c.kind] ??= { agree: 0, n: 0 });
      k.n++;
      stats.n++;
      if (v.passed === c.passed) {
        k.agree++;
        stats.agree++;
      }
    }
  }
  return any ? stats : null;
}

export function pct(stats: { agree: number; n: number } | null | undefined): number | null {
  return stats && stats.n > 0 ? (100 * stats.agree) / stats.n : null;
}

/** One line describing what the pipeline is doing right now. */
export function activityText(s: JobState): string {
  if (s.status === "queued") return "Queued";
  if (s.status === "awaiting_approval") return "Waiting for you to approve the task spec";
  if (s.status === "verified") return "Verified on every test scene";
  if (s.status === "failed") return "Not verified: some scenes still fail";
  if (s.status === "error") return "Stopped with an error";
  const last = lastAttempt(s);
  const between = s.activeStage != null && s.stages[s.activeStage].status === "done";
  if (between && last?.status === "done") {
    const c = attemptCounts(last, s.nSeeds);
    const name = last.kind === "repair" ? `Repair ${last.n}` : `Attempt ${last.n}`;
    return `${name}: ${c.passed}/${c.total} scenes passed`;
  }
  if (between && last?.status === "ready") return "Program ready, starting the test scenes";
  switch (s.activeStage) {
    case "prep":
      return "Preparing the demonstration video";
    case "discover":
      return "Finding the objects in the video";
    case "track":
      return "Tracking hands and objects with SAM 3.1";
    case "spec":
      return "Writing the task spec";
    case "program":
      return "Writing the robot program";
    case "verify": {
      if (!last) return "Running test scenes";
      const c = attemptCounts(last, s.nSeeds);
      return `Running test scenes: ${c.done}/${c.total} done`;
    }
    case "judge":
      return "SAM 3.1 is judging the rendered scenes";
    case "repair":
      return "Diagnosing the failures and patching the program";
    case "report":
      return "Writing the report";
    default:
      return "Starting";
  }
}

/** Stable key for de-duplicating events (SSE reconnects replay everything). */
export function eventKey(ev: PipelineEvent): string {
  let h = 0x811c9dc5; // FNV-1a over the payload
  const text = JSON.stringify(ev.data ?? {});
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return `${ev.type}|${ev.ts}|${(h >>> 0).toString(36)}|${text.length}`;
}
