/** Live backend client (FastAPI, showtell/api/app.py) and the SSE event stream. */

import { API_URL, backendLabel } from "./config";
import { normalizeBench } from "./lib/bench";
import type {
  ApiJobResponse,
  BenchResults,
  PipelineEvent,
  TaskPreset,
  TaskSpec,
} from "./types";
import { EVENT_TYPES } from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
  get unreachable(): boolean {
    return this.status === 0;
  }
  get notFound(): boolean {
    return this.status === 404;
  }
}

export function apiUrl(path: string): string {
  return `${API_URL}${path}`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(apiUrl(path), {
      ...init,
      headers: { Accept: "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError(0, `Can't reach the backend (${backendLabel()}).`);
  }
  const type = res.headers.get("content-type") ?? "";
  if (!res.ok) {
    let detail = "";
    try {
      const text = await res.text();
      if (type.includes("json")) {
        const body = JSON.parse(text) as { detail?: unknown };
        detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
      } else {
        detail = text.trim().slice(0, 200);
      }
    } catch {
      // keep the status text
    }
    // An empty 5xx is a proxy or gateway with nothing behind it (e.g. `npm run dev` without uvicorn).
    if (!detail && (res.status >= 502 || res.status === 500)) {
      throw new ApiError(0, `Can't reach the backend (${backendLabel()}).`);
    }
    throw new ApiError(res.status, detail || `${res.status} ${res.statusText}`.trim());
  }
  if (!type.includes("json")) {
    // e.g. the dev server answering index.html because no backend is behind the proxy
    throw new ApiError(0, `Can't reach the backend (${backendLabel()}): got ${type || "no JSON"}.`);
  }
  return (await res.json()) as T;
}

/** Largest upload the backend accepts (showtell/api/app.py MAX_UPLOAD_BYTES); bigger gets a 413. */
export const MAX_UPLOAD_MB = 300;

export interface NewJobInput {
  video: File | null;
  rules: string;
  /** With no `video`, the API uses the task preset's recorded video (TaskPreset.has_video). */
  useVideo: boolean;
  autoApprove: boolean;
  taskId: string | null;
  seeds: number;
}

/** POST /api/jobs (multipart) -> {job_id}. */
export function createJob(input: NewJobInput): Promise<{ job_id: string }> {
  const fd = new FormData();
  if (input.video) fd.append("video", input.video, input.video.name);
  fd.append("rules", input.rules);
  fd.append("use_video", String(input.useVideo));
  fd.append("auto_approve", String(input.autoApprove));
  if (input.taskId) fd.append("task_id", input.taskId);
  fd.append("seeds", String(input.seeds));
  return request("/api/jobs", { method: "POST", body: fd });
}

export function getJob(id: string): Promise<ApiJobResponse> {
  return request(`/api/jobs/${encodeURIComponent(id)}`);
}

/** Approve the spec as proposed ({approve: true}) or send the edited TaskSpec. */
export function postSpec(id: string, body: TaskSpec | { approve: true }): Promise<unknown> {
  return request(`/api/jobs/${encodeURIComponent(id)}/spec`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/** GET /api/bench serves bench/results.json as written; null when it is not a results file. */
export async function getBench(): Promise<BenchResults | null> {
  return normalizeBench(await request<unknown>("/api/bench"));
}

export async function getTasks(): Promise<TaskPreset[]> {
  const body = await request<TaskPreset[] | { tasks: TaskPreset[] }>("/api/tasks");
  return Array.isArray(body) ? body : (body.tasks ?? []);
}

export type JobFile = "report.md" | "report.json" | "program.py";

export function downloadUrl(id: string, file: JobFile): string {
  return apiUrl(`/api/jobs/${encodeURIComponent(id)}/${file}`);
}

/**
 * Media for live jobs. Snapshot seeds carry /media/... URLs; events and reports carry paths
 * relative to the job folder, which the backend serves at /media/jobs/<id>/<path>.
 */
export function liveMediaUrl(path: string | null | undefined, jobId: string): string | undefined {
  if (!path) return undefined;
  if (/^(https?:|data:|blob:)/.test(path)) return path;
  if (path.startsWith("/")) return apiUrl(path);
  return apiUrl(`/media/jobs/${encodeURIComponent(jobId)}/${path.replace(/^\.?\/+/, "")}`);
}

export type StreamStatus = "connecting" | "open" | "reconnecting" | "closed" | "failed";

/**
 * Subscribe to GET /api/jobs/{id}/events. The server sends named events ("event: <type>") whose
 * data is the Event JSON, and replays all past events on connect. Returns an unsubscribe function.
 */
export function subscribeEvents(
  id: string,
  onEvent: (ev: PipelineEvent) => void,
  onStatus: (status: StreamStatus) => void,
): () => void {
  if (typeof EventSource === "undefined") {
    onStatus("failed");
    return () => undefined;
  }
  const es = new EventSource(apiUrl(`/api/jobs/${encodeURIComponent(id)}/events`));
  let closed = false;
  onStatus("connecting");

  const handle = (msg: MessageEvent<string>) => {
    if (closed) return;
    try {
      const parsed = JSON.parse(msg.data) as Partial<PipelineEvent>;
      const type = parsed.type ?? (msg.type !== "message" ? msg.type : undefined);
      if (!type) return;
      onEvent({
        type,
        ts: typeof parsed.ts === "number" ? parsed.ts : Date.now() / 1000,
        job_id: parsed.job_id ?? id,
        data: parsed.data ?? {},
      });
    } catch {
      // ignore keep-alives and malformed lines
    }
  };
  for (const t of EVENT_TYPES) es.addEventListener(t, handle as EventListener);
  es.addEventListener("message", handle as EventListener);
  es.onopen = () => !closed && onStatus("open");
  es.onerror = () => {
    if (closed) return;
    onStatus(es.readyState === EventSource.CLOSED ? "failed" : "reconnecting");
  };
  return () => {
    closed = true;
    es.close();
    onStatus("closed");
  };
}

// ------------------------------------------------------------------ recent jobs (this browser)
export interface RecentJob {
  id: string;
  rules: string;
  createdAt: number;
}

const RECENT_KEY = "showtell.recentJobs";

export function recentJobs(): RecentJob[] {
  try {
    const raw = window.localStorage.getItem(RECENT_KEY);
    const list = raw ? (JSON.parse(raw) as RecentJob[]) : [];
    return Array.isArray(list) ? list.filter((j) => j && typeof j.id === "string") : [];
  } catch {
    return [];
  }
}

export function rememberJob(job: RecentJob): void {
  try {
    const list = [job, ...recentJobs().filter((j) => j.id !== job.id)].slice(0, 12);
    window.localStorage.setItem(RECENT_KEY, JSON.stringify(list));
  } catch {
    // storage unavailable: the list is a convenience only
  }
}
