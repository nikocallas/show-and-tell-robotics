/**
 * Demo mode: load recorded runs from the static bundle and replay their events with compressed
 * timing through the same reducer the live stream uses.
 *
 * Bundle layout (written by `python -m showtell export-demo --job ID`, showtell/api/export_demo.py;
 * see web/README.md):
 *   demo/index.json            [{id, rules?, status?, attempts?, passed?, total?, ...}], newest first
 *                              ({"jobs": [...]} is accepted too)
 *   demo/<id>/report.json      JobReport; media paths rewritten to "media/<id>/<path>"
 *   demo/<id>/events.json      the job's events (JSON array; JSONL also accepted), same media paths
 *   demo/<id>/report.md        optional
 *   demo/<id>/job.json         optional GET /api/jobs/<id> shape; read for attempts[].failure_frames
 *   demo/media/<id>/<path>     videos, thumbnails and keyframes at their job-folder paths
 *   demo/bench.json            optional copy of bench/results.json
 *   demo/tasks.json            optional task presets
 */

import { DEMO_BASE } from "./config";
import { normalizeBench } from "./lib/bench";
import { failureFramesFromApi, normalizeStatus } from "./state";
import type {
  ApiAttempt,
  BenchResults,
  DemoIndexEntry,
  JobReport,
  PipelineEvent,
  TaskPreset,
} from "./types";

async function fetchText(url: string): Promise<string | null> {
  let res: Response;
  try {
    res = await fetch(url, { cache: "no-cache" });
  } catch {
    return null;
  }
  if (!res.ok) return null;
  // SPA fallbacks answer missing files with index.html: treat that as "not there".
  if ((res.headers.get("content-type") ?? "").includes("text/html")) return null;
  return res.text();
}

async function fetchJson<T>(url: string): Promise<T | null> {
  const text = await fetchText(url);
  if (text == null) return null;
  try {
    return JSON.parse(text) as T;
  } catch {
    return null;
  }
}

export function demoUrl(path: string): string {
  return `${DEMO_BASE}${path.replace(/^\/+/, "")}`;
}

export async function loadDemoIndex(): Promise<DemoIndexEntry[]> {
  const body = await fetchJson<DemoIndexEntry[] | { jobs?: DemoIndexEntry[] }>(demoUrl("index.json"));
  const list = Array.isArray(body) ? body : (body?.jobs ?? []);
  return list.filter((j) => j && typeof j.id === "string");
}

/** Accepts a JSON array, {"events": [...]}, or JSON Lines (the pipeline's events.jsonl). */
export function parseEvents(text: string): PipelineEvent[] {
  const trimmed = text.trim();
  if (!trimmed) return [];
  let raw: unknown[] = [];
  try {
    const body = JSON.parse(trimmed) as unknown;
    if (Array.isArray(body)) raw = body;
    else if (body && typeof body === "object" && Array.isArray((body as { events?: unknown[] }).events))
      raw = (body as { events: unknown[] }).events;
  } catch {
    raw = trimmed
      .split("\n")
      .map((line) => {
        try {
          return JSON.parse(line) as unknown;
        } catch {
          return null;
        }
      })
      .filter(Boolean);
  }
  return raw
    .filter((e): e is PipelineEvent => {
      const ev = e as Partial<PipelineEvent> | null;
      return Boolean(ev && typeof ev.type === "string");
    })
    .map((e) => ({ ...e, ts: typeof e.ts === "number" ? e.ts : 0, data: e.data ?? {} }));
}

export interface DemoBundle {
  id: string;
  report: JobReport | null;
  events: PipelineEvent[];
  hasReportMd: boolean;
  /** job.json attempts[].failure_frames: attempt n -> the frames sent to repair n + 1. */
  failureFrames: Record<number, string[]>;
}

export async function loadDemoJob(id: string): Promise<DemoBundle> {
  const base = `${encodeURIComponent(id)}/`;
  const [report, eventsText, md, job] = await Promise.all([
    fetchJson<JobReport>(demoUrl(`${base}report.json`)),
    fetchText(demoUrl(`${base}events.json`)).then(
      async (t) => t ?? (await fetchText(demoUrl(`${base}events.jsonl`))),
    ),
    fetchText(demoUrl(`${base}report.md`)),
    fetchJson<{ attempts?: Pick<ApiAttempt, "n" | "failure_frames">[] }>(demoUrl(`${base}job.json`)),
  ]);
  return {
    id,
    report,
    events: eventsText ? parseEvents(eventsText) : [],
    hasReportMd: md != null,
    failureFrames: failureFramesFromApi(Array.isArray(job?.attempts) ? job.attempts : []),
  };
}

export async function loadDemoReport(id: string): Promise<JobReport | null> {
  return fetchJson<JobReport>(demoUrl(`${encodeURIComponent(id)}/report.json`));
}

/** A recorded run: its index entry and its report (null when report.json is missing). */
export interface DemoRun {
  entry: DemoIndexEntry;
  report: JobReport | null;
}

export async function loadDemoRuns(): Promise<DemoRun[]> {
  const index = await loadDemoIndex();
  const reports = await Promise.all(index.map((e) => loadDemoReport(e.id)));
  return index.map((entry, i) => ({ entry, report: reports[i] }));
}

/** The run that shows the whole loop (some scenes failed, a repair fixed them), else the newest. */
export function featuredRun(runs: DemoRun[]): DemoRun | null {
  const repaired = runs.find(
    (r) =>
      (r.report?.attempts.length ?? r.entry.attempts ?? 0) > 1 &&
      normalizeStatus(r.report?.status ?? r.entry.status, "failed") === "verified",
  );
  return repaired ?? runs[0] ?? null;
}

export async function loadDemoBench(): Promise<BenchResults | null> {
  return normalizeBench(await fetchJson<unknown>(demoUrl("bench.json")));
}

export async function loadDemoTasks(): Promise<TaskPreset[]> {
  const body = await fetchJson<TaskPreset[] | { tasks?: TaskPreset[] }>(demoUrl("tasks.json"));
  return Array.isArray(body) ? body : (body?.tasks ?? []);
}

/** Percent-encode each segment of a slash-separated path (file names may hold "#", "?", "%"). */
function encodePath(path: string): string {
  return path.split("/").map(encodeURIComponent).join("/");
}

/**
 * Media for recorded runs. The exporter rewrites every media path to "media/<id>/<path>" (relative
 * to the bundle root) and copies the file to demo/media/<id>/<path>. Live API URLs
 * (/media/jobs/<id>/<path>) and bare job-folder paths (attempts/0/seed_3000/full.mp4) map to the
 * same place; other site-absolute paths and full URLs are used as they are.
 */
export function demoMediaUrl(path: string | null | undefined, jobId: string): string | undefined {
  if (!path) return undefined;
  if (/^(https?:|data:|blob:)/.test(path)) return path;
  const live = path.match(/^\/media\/jobs\/([^/]+)\/(.+)$/);
  if (live) return demoUrl(`media/${encodePath(live[1])}/${encodePath(live[2])}`);
  if (path.startsWith("/")) return path; // site-absolute, e.g. /demo/media/<id>/...
  const rel = path.replace(/^(\.\/)+/, "");
  if (rel.startsWith("media/")) return demoUrl(encodePath(rel));
  return demoUrl(`media/${encodePath(jobId)}/${encodePath(rel)}`);
}

// ------------------------------------------------------------------ replay timing
/** Longest pause between two events (seconds, at 1x). Real runs take minutes; the replay doesn't. */
export const MAX_GAP_S = 1.5;
/** Background events (SAM verdicts, stage ends, logs) never hold the replay for long. */
const QUICK_TYPES = new Set(["judge_done", "stage_done", "log", "track_summary"]);
const QUICK_GAP_S = 0.35;
/** Beats that let a viewer read what just happened. */
const HOLD_AFTER_S: Record<string, number> = {
  spec_ready: 1.2,
  awaiting_approval: 2.5,
  program_ready: 1.0,
  attempt_done: 2.0,
  repair_started: 0.8,
  repair_done: 2.2,
};
/** Stage starts that hold too: the repair (diagnosing the failures) is the moment to watch. */
const HOLD_AFTER_STAGE_S: Record<string, number> = { repair: 2.0 };

/** Milliseconds to wait before applying each event (index 0 = before the first event). */
export function replayDelays(events: PipelineEvent[]): number[] {
  return events.map((ev, i) => {
    if (i === 0) return 500;
    const prev = events[i - 1];
    const gap = Math.max(0, (ev.ts ?? 0) - (prev.ts ?? 0));
    let s = Math.min(gap, QUICK_TYPES.has(ev.type) ? QUICK_GAP_S : MAX_GAP_S);
    const hold =
      HOLD_AFTER_S[prev.type] ??
      (prev.type === "stage_started" ? HOLD_AFTER_STAGE_S[String(prev.data?.stage)] : undefined);
    if (hold) s = Math.max(s, hold);
    return Math.round(s * 1000);
  });
}

export const SPEEDS = [1, 2, 4] as const;
export type Speed = (typeof SPEEDS)[number];

/** Timer-driven cursor over the events. `onChange(cursor, playing)` after every step. */
export class Replayer {
  private timer: ReturnType<typeof setTimeout> | null = null;
  private readonly delays: number[];
  cursor = 0;
  playing = false;
  speed: Speed = 1;

  constructor(
    private readonly count: number,
    delays: number[],
    private readonly onChange: (cursor: number, playing: boolean) => void,
  ) {
    this.delays = delays;
  }

  play(): void {
    if (this.cursor >= this.count) return;
    this.playing = true;
    this.schedule();
    this.emit();
  }

  pause(): void {
    this.playing = false;
    this.clear();
    this.emit();
  }

  setSpeed(speed: Speed): void {
    this.speed = speed;
    if (this.playing) {
      this.clear();
      this.schedule();
    }
  }

  restart(): void {
    this.clear();
    this.cursor = 0;
    this.playing = false;
    if (this.count > 0) this.play();
    else this.emit();
  }

  skipToEnd(): void {
    this.clear();
    this.cursor = this.count;
    this.playing = false;
    this.emit();
  }

  dispose(): void {
    this.clear();
  }

  private emit(): void {
    this.onChange(this.cursor, this.playing);
  }

  private clear(): void {
    if (this.timer != null) clearTimeout(this.timer);
    this.timer = null;
  }

  private schedule(): void {
    this.clear();
    if (this.cursor >= this.count) {
      this.playing = false;
      return;
    }
    const delay = (this.delays[this.cursor] ?? 0) / this.speed;
    this.timer = setTimeout(() => {
      this.timer = null;
      this.cursor += 1;
      if (this.cursor >= this.count) this.playing = false;
      if (this.playing) this.schedule();
      this.emit();
    }, delay);
  }
}
