/**
 * Dev-only Vite plugin, active with VITE_DEV_FIXTURE=1 under `vite` (never in `vite build`).
 *
 * SAMPLE DATA - NOT A REAL RUN. It serves:
 * - /__dev-fixtures/*  this folder (the demo-mode replay source in fixture mode)
 * - /api/*             a mock of the FastAPI backend that replays sample-job/events.json over SSE,
 *                      pausing at awaiting_approval until POST /api/jobs/{id}/spec, so the live
 *                      path (upload, approval, edits, live grid, downloads) can be clicked through
 *                      without the backend or an API key. Response shapes follow showtell/api
 *                      (jobs.py job_view / attempt_view), and POST /api/jobs checks use_video the way
 *                      app.py does.
 * - /media/*           404 (the sample has no videos)
 */

import fs from "node:fs";
import type { IncomingMessage, ServerResponse } from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { Plugin } from "vite";

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const SAMPLE_ID = "sample-job";
const MAX_GAP_S = 0.35; // mock runs take ~30 s instead of ~8 min

interface Ev {
  type: string;
  ts: number;
  job_id: string | null;
  data: Record<string, unknown>;
}

interface MockJob {
  id: string;
  createdAt: number;
  rules: string;
  useVideo: boolean;
  autoApprove: boolean;
  taskId: string | null;
  video: string | null; // job-relative, like Job.video_path
  timeline: Ev[];
  emitted: Ev[];
  cursor: number;
  waiting: boolean;
  approvedSpec: unknown;
  edited: boolean;
  status: string;
  clients: Set<ServerResponse>;
  timer: ReturnType<typeof setTimeout> | null;
}

function readJson<T>(rel: string): T {
  return JSON.parse(fs.readFileSync(path.join(ROOT, rel), "utf8")) as T;
}

function sampleEvents(): Ev[] {
  const body = readJson<{ events: Ev[] } | Ev[]>(`${SAMPLE_ID}/events.json`);
  return Array.isArray(body) ? body : body.events;
}

function sampleReport(id: string): Record<string, unknown> {
  return { ...readJson<Record<string, unknown>>(`${SAMPLE_ID}/report.json`), job_id: id };
}

function sendJson(res: ServerResponse, status: number, body: unknown): void {
  res.statusCode = status;
  res.setHeader("Content-Type", "application/json");
  res.end(JSON.stringify(body));
}

function sendText(res: ServerResponse, text: string, type: string, filename: string): void {
  res.statusCode = 200;
  res.setHeader("Content-Type", type);
  res.setHeader("Content-Disposition", `attachment; filename="${filename}"`);
  res.end(text);
}

function readBody(req: IncomingMessage): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    req.on("data", (c: Buffer) => chunks.push(c));
    req.on("end", () => resolve(Buffer.concat(chunks)));
    req.on("error", reject);
  });
}

/** A text field of a multipart/form-data body (good enough for a mock). */
function formField(body: string, name: string): string | null {
  const m = body.match(new RegExp(`name="${name}"\\r\\n(?:[^\\r\\n]+\\r\\n)*\\r\\n([\\s\\S]*?)\\r\\n--`));
  return m ? m[1] : null;
}

function writeEvent(res: ServerResponse, ev: Ev): void {
  res.write(`event: ${ev.type}\ndata: ${JSON.stringify(ev)}\n\n`);
}

const VIDEO_STAGES = new Set(["prep", "discover", "track", "summary"]);

/** The sample (a video run) as a sentence-only run: no perception stages, spec from the sentence. */
function sentenceOnly(timeline: Ev[]): Ev[] {
  return timeline
    .filter(
      (e) =>
        e.type !== "track_summary" &&
        !((e.type === "stage_started" || e.type === "stage_done") && VIDEO_STAGES.has(String(e.data.stage))),
    )
    .map((e) => {
      if (e.type === "job_started") return { ...e, data: { ...e.data, use_video: false } };
      if (e.type === "spec_ready") return { ...e, data: { ...e.data, source: "sentence", media_path: "none" } };
      if (e.type === "job_done" && e.data.report && typeof e.data.report === "object") {
        const report = { ...(e.data.report as Record<string, unknown>) };
        Object.assign(report, { use_video: false, video: null, track_summary: null, spec_source: "sentence", media_path: "none" });
        return { ...e, data: { ...e.data, report } };
      }
      return e;
    });
}

interface JobForm {
  rules: string;
  useVideo: boolean;
  autoApprove: boolean;
  taskId: string | null;
  video: string | null;
}

function makeJob(id: string, form: JobForm): MockJob {
  let timeline: Ev[] = sampleEvents().map((e) => ({ ...e, job_id: id }));
  if (form.autoApprove) {
    timeline = timeline.filter(
      (e) =>
        e.type !== "awaiting_approval" &&
        !((e.type === "stage_started" || e.type === "stage_done") && e.data.stage === "approval"),
    );
  }
  if (!form.useVideo) timeline = sentenceOnly(timeline);
  if (form.rules) {
    // the sample's events carry its own sentence; show the one that was submitted
    timeline = timeline.map((e) => {
      if (e.type === "job_started") return { ...e, data: { ...e.data, rules: form.rules } };
      if (e.type === "job_done" && e.data.report && typeof e.data.report === "object") {
        return { ...e, data: { ...e.data, report: { ...(e.data.report as object), rules: form.rules } } };
      }
      return e;
    });
  }
  return {
    id,
    createdAt: Date.now() / 1000,
    rules: form.rules,
    useVideo: form.useVideo,
    autoApprove: form.autoApprove,
    taskId: form.taskId,
    video: form.video,
    timeline,
    emitted: [],
    cursor: 0,
    waiting: false,
    approvedSpec: null,
    edited: false,
    status: "queued",
    clients: new Set(),
    timer: null,
  };
}

function step(job: MockJob): void {
  job.timer = null;
  if (job.waiting || job.cursor >= job.timeline.length) return;
  const src = job.timeline[job.cursor];
  let ev: Ev = { ...src, ts: Date.now() / 1000 };
  if (src.type === "spec_approved" && job.approvedSpec) {
    ev = { ...ev, data: { ...src.data, spec: job.approvedSpec, edited: job.edited } };
  }
  job.cursor += 1;
  job.emitted.push(ev);
  for (const c of job.clients) writeEvent(c, ev);
  if (src.type === "job_started" || src.type === "spec_approved") job.status = "running";
  if (src.type === "awaiting_approval") {
    job.status = "awaiting_approval";
    job.waiting = true;
    return;
  }
  if (src.type === "job_done") {
    job.status = String(src.data.status ?? "verified");
    return;
  }
  schedule(job, src);
}

function schedule(job: MockJob, prev: Ev | null): void {
  const next = job.timeline[job.cursor];
  if (!next) return;
  const gap = prev ? Math.min(Math.max(next.ts - prev.ts, 0), MAX_GAP_S) : 0.4;
  job.timer = setTimeout(() => step(job), gap * 1000);
}

function finishedJob(id: string): MockJob {
  const job = makeJob(id, { rules: "", useVideo: true, autoApprove: false, taskId: null, video: null });
  job.emitted = job.timeline.map((e) => ({ ...e }));
  job.cursor = job.timeline.length;
  const last = job.timeline[job.timeline.length - 1];
  job.status = String(last?.data.status ?? "verified");
  return job;
}

/** attempt_view rows rebuilt from the events emitted so far (what the API's DB holds). */
function attemptViews(job: MockJob): Record<string, unknown>[] {
  const rows = new Map<number, Record<string, unknown>>();
  const row = (n: number) => {
    let r = rows.get(n);
    if (!r) {
      r = { n, kind: n === 0 ? "initial" : "repair", program: "", notes: "", diagnosis: null, passed: 0, total: 0, done: false, agreement: null, wall_seconds: 0, seeds: [] as unknown[], failure_frames: [] };
      rows.set(n, r);
    }
    return r;
  };
  for (const e of job.emitted) {
    const n = e.data.attempt;
    if (typeof n !== "number") continue;
    const r = row(n);
    if (e.type === "program_ready") Object.assign(r, { program: e.data.source ?? "", notes: e.data.notes ?? "", kind: e.data.kind ?? r.kind });
    if (e.type === "repair_started" || e.type === "repair_done") r.kind = "repair";
    if (e.type === "attempt_started") r.total = Array.isArray(e.data.seeds) ? e.data.seeds.length : 0;
    if (e.type === "seed_done") (r.seeds as unknown[]).push(e.data.seed);
    if (e.type === "attempt_done") Object.assign(r, { passed: e.data.passed, total: e.data.total, done: true, agreement: e.data.agreement ?? null });
    if (typeof e.data.diagnosis === "string" && e.data.diagnosis) r.diagnosis = e.data.diagnosis;
  }
  return [...rows.values()].map((r) => {
    const seeds = r.seeds as { passed?: boolean }[];
    return r.done ? r : { ...r, passed: seeds.filter((s) => s.passed).length, total: r.total || seeds.length };
  });
}

/** The job's final report: the job_done payload of its timeline (the sample report as fallback). */
function finalReport(job: MockJob): Record<string, unknown> {
  const done = job.timeline.find((e) => e.type === "job_done")?.data.report;
  const report = done && typeof done === "object" ? (done as Record<string, unknown>) : sampleReport(job.id);
  return { ...report, job_id: job.id, rules: job.rules || report.rules };
}

/** GET /api/jobs/{id}: {job, attempts, report, media} like jobs.py JobManager.detail. */
function snapshot(job: MockJob): unknown {
  const done = job.cursor >= job.timeline.length;
  const specEv = [...job.emitted].reverse().find((e) => e.data.spec);
  const started = job.timeline.find((e) => e.type === "job_started");
  const sample = sampleReport(job.id);
  const spec = (specEv?.data.spec ?? null) as { clarifying_question?: string | null } | null;
  const base = `/api/jobs/${job.id}`;
  return {
    job: {
      id: job.id,
      created_at: job.createdAt,
      status: job.status,
      stage: null,
      rules: job.rules || sample.rules,
      use_video: job.useVideo,
      auto_approve: job.autoApprove,
      use_judge: true,
      n_seeds: started?.data.n_seeds ?? 20,
      task_id: job.taskId ?? sample.task_id,
      object_ids: sample.object_ids,
      video: job.video ? `/media/jobs/${job.id}/${job.video}` : null,
      spec,
      clarifying_question: spec?.clarifying_question ?? null,
      approved: job.emitted.some((e) => e.type === "spec_approved"),
      awaiting_approval: job.status === "awaiting_approval",
      error: null,
      finished_at: null,
      links: { self: base, events: `${base}/events`, report_md: `${base}/report.md`, report_json: `${base}/report.json`, program: `${base}/program.py` },
    },
    attempts: attemptViews(job),
    report: done ? finalReport(job) : null,
    media: { input_video: job.video, normalized_video: null, keyframes: [] },
  };
}

interface FixtureTask {
  id: string;
  video?: string;
  has_video?: boolean;
}

/** POST /api/jobs validation, as in showtell/api/app.py create_job. */
function jobForm(body: string): JobForm | { status: number; detail: string } {
  const rules = (formField(body, "rules") ?? "").trim();
  if (!rules) return { status: 422, detail: "rules must not be empty" };
  const flag = (name: string, dflt: boolean) => {
    const v = formField(body, name);
    return v == null ? dflt : ["true", "1", "yes", "on"].includes(v.trim().toLowerCase());
  };
  const useVideo = flag("use_video", true);
  const taskId = formField(body, "task_id")?.trim() || null;
  const task = taskId ? readJson<FixtureTask[]>("tasks.json").find((t) => t.id === taskId) : null;
  if (taskId && !task) return { status: 400, detail: `unknown task_id '${taskId}'` };
  const upload = body.match(/name="video"; filename="([^"]+)"/)?.[1] ?? null;
  const recorded = !upload && useVideo && task?.has_video ? (task.video ?? `${task.id}.mp4`) : null;
  if (useVideo && !upload && !recorded) {
    return {
      status: 400,
      detail:
        "use_video is on but there is no video: upload an .mp4/.mov, pick a task preset with a recorded video, or set use_video=false",
    };
  }
  if (!useVideo && !task) return { status: 400, detail: "no sim objects: use a demo video, a task preset (task_id) or objects=..." };
  const file = upload ? upload.replace(/[^A-Za-z0-9_.-]+/g, "_") : recorded;
  return {
    rules,
    useVideo: Boolean(useVideo && file),
    autoApprove: flag("auto_approve", false),
    taskId,
    video: useVideo && file ? `input/${file}` : null,
  };
}

export function devFixtures(): Plugin {
  const jobs = new Map<string, MockJob>();

  async function api(req: IncomingMessage, res: ServerResponse): Promise<void> {
    const url = new URL(req.url ?? "/", "http://localhost");
    const parts = url.pathname.split("/").filter(Boolean).map(decodeURIComponent);
    const method = req.method ?? "GET";

    if (method === "GET" && parts[0] === "tasks") return sendJson(res, 200, readJson("tasks.json"));
    if (method === "GET" && parts[0] === "bench") return sendJson(res, 200, readJson("bench.json"));

    if (parts[0] === "jobs" && parts.length === 1 && method === "POST") {
      const body = (await readBody(req)).toString("utf8"); // the text fields; video bytes are ignored
      const form = jobForm(body);
      if ("detail" in form) return sendJson(res, form.status, { detail: form.detail });
      const id = `mock-${Date.now().toString(36)}`;
      const job = makeJob(id, form);
      jobs.set(id, job);
      schedule(job, null);
      return sendJson(res, 200, { job_id: id });
    }

    if (parts[0] === "jobs" && parts[1]) {
      const id = parts[1];
      let job = jobs.get(id);
      if (!job && id === SAMPLE_ID) {
        job = finishedJob(id);
        jobs.set(id, job);
      }
      if (!job) return sendJson(res, 404, { detail: `job ${id} not found (mock API)` });
      const sub = parts[2];

      if (!sub && method === "GET") return sendJson(res, 200, snapshot(job));
      if (sub === "events" && method === "GET") {
        res.writeHead(200, {
          "Content-Type": "text/event-stream",
          "Cache-Control": "no-cache",
          Connection: "keep-alive",
        });
        for (const ev of job.emitted) writeEvent(res, ev);
        job.clients.add(res);
        const ping = setInterval(() => res.write(": ping\n\n"), 15000);
        req.on("close", () => {
          clearInterval(ping);
          job?.clients.delete(res);
        });
        return;
      }
      if (sub === "spec" && method === "POST") {
        if (!job.waiting) return sendJson(res, 409, { detail: "job is not waiting for approval" });
        const body = JSON.parse((await readBody(req)).toString("utf8") || "{}") as Record<string, unknown>;
        job.approvedSpec = body.approve === true ? null : body;
        job.edited = body.approve !== true;
        job.waiting = false;
        schedule(job, null);
        return sendJson(res, 200, { ok: true });
      }
      const report = finalReport(job);
      if (sub === "report.json") return sendText(res, JSON.stringify(report, null, 2), "application/json", `${id}_report.json`);
      if (sub === "program.py") return sendText(res, String(report.final_program ?? ""), "text/x-python", `${id}_program.py`);
      if (sub === "report.md") {
        const md = `# Sample data - not a real run\n\nMock report for job \`${id}\` from web/dev-fixtures.\n`;
        return sendText(res, md, "text/markdown", `${id}_report.md`);
      }
    }
    return sendJson(res, 404, { detail: "not found (mock API)" });
  }

  return {
    name: "showtell-dev-fixtures",
    apply: "serve",
    configureServer(server) {
      server.middlewares.use("/__dev-fixtures", (req, res) => {
        const rel = decodeURIComponent((req.url ?? "/").split("?")[0]).replace(/^\/+/, "");
        const file = path.resolve(ROOT, rel);
        if (!file.startsWith(ROOT + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
          res.statusCode = 404;
          res.setHeader("Content-Type", "text/plain");
          res.end("not found");
          return;
        }
        res.setHeader("Content-Type", file.endsWith(".json") ? "application/json" : "application/octet-stream");
        fs.createReadStream(file).pipe(res);
      });
      server.middlewares.use("/api", (req, res) => {
        api(req, res).catch((e: unknown) => sendJson(res, 500, { detail: String(e) }));
      });
      server.middlewares.use("/media", (_req, res) => sendJson(res, 404, { detail: "the sample has no media" }));
    },
  };
}
