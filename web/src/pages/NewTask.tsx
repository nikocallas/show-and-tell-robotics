/** New task: optional demo video, the rule sentence, scene preset and options, then Start. */

import { useEffect, useRef, useState, type DragEvent, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { createJob, getTasks, MAX_UPLOAD_MB, recentJobs, rememberJob } from "../api";
import { LIVE_AVAILABLE } from "../config";
import { loadDemoTasks } from "../demo";
import { useAsync } from "../hooks";
import { formatDateTime, objectLabel } from "../lib/format";
import { useMode } from "../mode";
import { JUDGING_NOTE, StepsList } from "../components/HowSteps";
import { IconArrowRight, IconUpload, IconX, Spinner } from "../components/icons";
import {
  Banner,
  Button,
  buttonClass,
  Card,
  Chip,
  cx,
  Segmented,
  Select,
  Toggle,
} from "../components/ui";

const MAX_MB = MAX_UPLOAD_MB;

function formatBytes(n: number): string {
  if (n < 1024 * 1024) return `${Math.max(1, Math.round(n / 1024))} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export function NewTaskPage() {
  const { mode, setMode } = useMode();
  const navigate = useNavigate();
  const presets = useAsync(() => (mode === "live" ? getTasks() : loadDemoTasks()), [mode]);

  const [video, setVideo] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [rules, setRules] = useState("");
  const [useVideo, setUseVideo] = useState(true);
  const [review, setReview] = useState(true);
  const [taskId, setTaskId] = useState("");
  const [seeds, setSeeds] = useState(20);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!video) {
      setPreviewUrl(null);
      return;
    }
    const url = URL.createObjectURL(video);
    setPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [video]);

  function acceptFile(f: File | null | undefined) {
    setFileError(null);
    if (!f) return;
    const okType = /\.(mp4|mov)$/i.test(f.name) || ["video/mp4", "video/quicktime"].includes(f.type);
    if (!okType) {
      setFileError("Use an MP4 or MOV video.");
      return;
    }
    if (f.size > MAX_MB * 1024 * 1024) {
      setFileError(`That file is ${formatBytes(f.size)}; keep demo videos under ${MAX_MB} MB (30 s is plenty).`);
      return;
    }
    setVideo(f);
    setUseVideo(true);
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDragOver(false);
    acceptFile(e.dataTransfer.files?.[0]);
  }

  const presetList = presets.data ?? [];
  const preset = presetList.find((p) => p.id === taskId) ?? null;
  // Without an upload, the backend runs on the preset's recorded demo (bench/videos/) if it has one.
  const recorded = video == null && preset?.has_video === true ? preset : null;
  const videoAvailable = video != null || recorded != null;
  const effectiveUseVideo = videoAvailable && useVideo;
  const needsScene = !effectiveUseVideo && !taskId;
  const live = mode === "live";
  const canSubmit = live && rules.trim().length > 0 && !needsScene && !submitting;
  const recent = live ? recentJobs() : [];

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      const { job_id } = await createJob({
        video,
        rules: rules.trim(),
        useVideo: effectiveUseVideo,
        autoApprove: !review,
        taskId: taskId || null,
        seeds,
      });
      rememberJob({ id: job_id, rules: rules.trim(), createdAt: Date.now() / 1000 });
      navigate(`/jobs/${encodeURIComponent(job_id)}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setSubmitting(false);
    }
  }

  return (
    <div className="grid gap-8 lg:grid-cols-12">
      <div className="min-w-0 lg:col-span-7">
        <h1 className="text-xl font-semibold tracking-tight text-zinc-900 sm:text-2xl">New task</h1>
        <p className="mt-1.5 max-w-xl text-sm text-zinc-600">
          Show the robot a task once, state the rules in one sentence, and get back a program that is
          tested on {seeds} simulated scenes before you trust it.
        </p>

        {!live && (
          <Banner
            className="mt-5"
            tone="info"
            title="This is the hosted demo"
            action={
              <>
                <Link to="/runs" className={buttonClass("secondary", "sm")}>
                  Watch a recorded run
                </Link>
                {LIVE_AVAILABLE && (
                  <Button size="sm" variant="primary" onClick={() => setMode("live")}>
                    Use the live backend
                  </Button>
                )}
              </>
            }
          >
            Starting a task needs the live backend. Recorded runs replay real pipeline output.
          </Banner>
        )}

        {live && presets.error != null && (
          <Banner
            className="mt-5"
            tone="warning"
            title="The backend is not answering"
            action={
              <>
                <Button size="sm" onClick={presets.reload}>
                  Retry
                </Button>
                <Button size="sm" onClick={() => setMode("demo")}>
                  Switch to demo
                </Button>
              </>
            }
          >
            {presets.error instanceof Error ? presets.error.message : String(presets.error)} Start it with{" "}
            <code className="font-mono text-xs">uvicorn showtell.api.app:app</code>.
          </Banner>
        )}

        <form onSubmit={submit} className="mt-6">
          <Card>
            {/* video */}
            <section className="border-b border-zinc-100 px-4 py-5 sm:px-6">
              <div className="flex items-baseline justify-between gap-2">
                <h2 className="text-sm font-semibold text-zinc-900">Demonstration video</h2>
                <span className="text-xs text-zinc-400">Optional</span>
              </div>
              <p className="mt-0.5 text-xs text-zinc-500">
                Phone top-down about 80 cm above the table, good light, one take under 30 s. MP4 or MOV.
              </p>
              <input
                ref={inputRef}
                type="file"
                accept="video/mp4,video/quicktime,.mp4,.mov"
                className="sr-only"
                tabIndex={-1}
                onChange={(e) => {
                  acceptFile(e.target.files?.[0]);
                  e.target.value = "";
                }}
              />
              {video && previewUrl ? (
                <div className="mt-3 overflow-hidden rounded-xl border border-zinc-200">
                  <video
                    src={previewUrl}
                    controls
                    muted
                    playsInline
                    className="aspect-video max-h-72 w-full bg-zinc-900 object-contain"
                  />
                  <div className="flex flex-wrap items-center gap-3 px-3 py-2.5">
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium text-zinc-900">{video.name}</p>
                      <p className="text-xs text-zinc-500">{formatBytes(video.size)}</p>
                    </div>
                    <Button size="sm" onClick={() => inputRef.current?.click()}>
                      Replace
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => setVideo(null)} aria-label="Remove video">
                      <IconX size={14} /> Remove
                    </Button>
                  </div>
                </div>
              ) : (
                <button
                  type="button"
                  onClick={() => inputRef.current?.click()}
                  onDragOver={(e) => {
                    e.preventDefault();
                    setDragOver(true);
                  }}
                  onDragLeave={() => setDragOver(false)}
                  onDrop={onDrop}
                  className={cx(
                    "mt-3 flex w-full flex-col items-center justify-center rounded-xl border-2 border-dashed px-4 py-9 text-center transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500",
                    dragOver ? "border-accent-500 bg-accent-50/60" : "border-zinc-300 bg-zinc-50/50 hover:border-zinc-400 hover:bg-zinc-50",
                  )}
                >
                  <span className="flex h-10 w-10 items-center justify-center rounded-full bg-white text-zinc-500 shadow-sm ring-1 ring-zinc-200">
                    <IconUpload size={18} />
                  </span>
                  <span className="mt-3 text-sm font-medium text-zinc-900">
                    Drop a demo video here, or <span className="text-accent-700">browse</span>
                  </span>
                  <span className="mt-1 text-xs text-zinc-500">
                    {recorded
                      ? `Or leave it empty to use the recorded demo of ${recorded.id}.`
                      : "Without a video, the spec comes from the sentence alone."}
                  </span>
                </button>
              )}
              {fileError && <p className="mt-2 text-xs text-rose-700">{fileError}</p>}
            </section>

            {/* rules */}
            <section className="border-b border-zinc-100 px-4 py-5 sm:px-6">
              <label htmlFor="rules" className="text-sm font-semibold text-zinc-900">
                Rules
              </label>
              <p className="mt-0.5 text-xs text-zinc-500">One sentence. Constraints like "don't touch the glass" become checks.</p>
              <textarea
                id="rules"
                value={rules}
                onChange={(e) => setRules(e.target.value)}
                rows={3}
                maxLength={500}
                placeholder="Sort by size, and don't touch the glass."
                className="mt-3 block w-full resize-y rounded-lg border border-zinc-200 bg-white px-3 py-2.5 text-sm text-zinc-900 shadow-sm placeholder:text-zinc-400 focus:border-accent-500 focus:outline-none focus:ring-2 focus:ring-accent-500/30"
              />
              {presetList.length > 0 && (
                <div className="mt-3">
                  <p className="text-xs font-medium text-zinc-500">Try a benchmark task</p>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {presetList.map((p) => (
                      <button
                        key={p.id}
                        type="button"
                        title={p.demo ?? p.id}
                        onClick={() => {
                          setRules(p.rules);
                          setTaskId(p.id);
                        }}
                        className={cx(
                          "max-w-full truncate rounded-full border px-2.5 py-1 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500",
                          taskId === p.id && rules === p.rules
                            ? "border-accent-300 bg-accent-50 text-accent-800"
                            : "border-zinc-200 bg-white text-zinc-700 hover:border-zinc-300 hover:bg-zinc-50",
                        )}
                      >
                        {p.rules}
                      </button>
                    ))}
                  </div>
                </div>
              )}
              {presets.loading && live && (
                <p className="mt-3 flex items-center gap-2 text-xs text-zinc-400">
                  <Spinner size={12} /> Loading presets
                </p>
              )}
            </section>

            {/* scene */}
            <section className="border-b border-zinc-100 px-4 py-5 sm:px-6">
              <label htmlFor="scene" className="text-sm font-semibold text-zinc-900">
                Scene
              </label>
              <p className="mt-0.5 text-xs text-zinc-500">
                Which objects the simulator places on the table. From the video, they are detected automatically.
              </p>
              <Select
                id="scene"
                value={taskId}
                onChange={(e) => setTaskId(e.target.value)}
                className="mt-3 w-full sm:w-96"
              >
                <option value="">From the video (detect objects)</option>
                {presetList.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.id} · {p.objects.length} objects{p.has_video ? " · recorded demo" : ""}
                  </option>
                ))}
              </Select>
              {preset && (
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {preset.objects.map((o, i) => (
                    <Chip key={`${o}-${i}`} tone="muted" title={o}>
                      {objectLabel(o)}
                    </Chip>
                  ))}
                </div>
              )}
              {needsScene && rules.trim() !== "" && (
                <p className="mt-2 text-xs text-amber-700">
                  Without the video the simulator can't tell which objects to place: pick a scene preset.
                </p>
              )}
            </section>

            {/* options */}
            <section className="space-y-4 px-4 py-5 sm:px-6">
              <Toggle
                checked={effectiveUseVideo}
                onChange={setUseVideo}
                disabled={!videoAvailable}
                label="Use the video"
                description={
                  video
                    ? "Off builds the spec from the sentence only (the benchmark's ablation)."
                    : recorded
                      ? `Uses the recorded demo of ${recorded.id}${recorded.video ? ` (${recorded.video})` : ""}. Off builds the spec from the sentence only.`
                      : "Add a video, or pick a benchmark task with a recorded demo, to use it."
                }
              />
              <Toggle
                checked={review}
                onChange={setReview}
                label="Review the spec before running"
                description="Pause after the spec is written so you can edit and approve it."
              />
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p className="text-sm font-medium text-zinc-900">Test scenes per attempt</p>
                  <p className="mt-0.5 text-xs text-zinc-500">Randomized layouts with fixed seeds.</p>
                </div>
                <Segmented
                  label="Test scenes"
                  value={seeds}
                  onChange={setSeeds}
                  options={[5, 10, 20].map((n) => ({ value: n, label: String(n) }))}
                />
              </div>
            </section>

            <div className="flex flex-col gap-3 rounded-b-xl border-t border-zinc-100 bg-zinc-50/70 px-4 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-6">
              <p className="text-xs text-zinc-500">
                {error ? (
                  <span className="text-rose-700">{error}</span>
                ) : !live ? (
                  LIVE_AVAILABLE ? (
                    "Switch to the live backend to start a task."
                  ) : (
                    "This hosted demo has no backend: run the app locally (see the README) to start a task."
                  )
                ) : !rules.trim() ? (
                  "Write the rules to start."
                ) : (
                  `${effectiveUseVideo ? (recorded ? "Recorded video + sentence" : "Video + sentence") : "Sentence only"} · ${seeds} scenes · ${review ? "you approve the spec" : "auto-approve"}`
                )}
              </p>
              <Button type="submit" variant="primary" disabled={!canSubmit}>
                {submitting ? <Spinner size={14} /> : null}
                Start
                {!submitting && <IconArrowRight size={15} />}
              </Button>
            </div>
          </Card>
        </form>
      </div>

      <aside className="min-w-0 space-y-6 lg:col-span-5 lg:pt-[4.25rem]">
        <Card>
          <div className="px-4 py-5 sm:px-6">
            <h2 className="text-sm font-semibold text-zinc-900">How it works</h2>
            <div className="mt-4">
              <StepsList />
            </div>
            <p className="mt-5 border-t border-zinc-100 pt-4 text-xs leading-relaxed text-zinc-500">
              {JUDGING_NOTE}
            </p>
          </div>
        </Card>

        {recent.length > 0 && (
          <Card>
            <div className="px-4 py-5 sm:px-6">
              <h2 className="text-sm font-semibold text-zinc-900">Recent jobs</h2>
              <p className="mt-0.5 text-xs text-zinc-500">Started from this browser</p>
              <ul className="mt-3 divide-y divide-zinc-100">
                {recent.map((j) => (
                  <li key={j.id}>
                    <Link
                      to={`/jobs/${encodeURIComponent(j.id)}`}
                      className="flex items-center gap-3 py-2.5 text-sm hover:text-accent-700"
                    >
                      <span className="min-w-0 flex-1 truncate text-zinc-800">{j.rules}</span>
                      <span className="shrink-0 text-xs text-zinc-400">{formatDateTime(j.createdAt)}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          </Card>
        )}
      </aside>
    </div>
  );
}
