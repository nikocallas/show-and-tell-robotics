/** Data hooks: live jobs (snapshot + SSE), demo replays, and small async loaders. */

import { useCallback, useEffect, useMemo, useRef, useState, type DependencyList } from "react";
import {
  ApiError,
  getBench,
  getJob,
  getTasks,
  liveMediaUrl,
  postSpec,
  subscribeEvents,
  type StreamStatus,
} from "./api";
import {
  demoMediaUrl,
  loadDemoBench,
  loadDemoJob,
  loadDemoTasks,
  replayDelays,
  Replayer,
  type DemoBundle,
  type Speed,
} from "./demo";
import {
  completeJob,
  eventKey,
  failureFramesFromApi,
  initialJobState,
  isTerminal,
  metaFromReport,
  metaFromSnapshot,
  normalizeStatus,
  reduceEvents,
  stateFromReport,
  stateFromSnapshot,
  type JobState,
} from "./state";
import type { ApiJobResponse, BenchRow, PipelineEvent, TaskPreset, TaskSpec } from "./types";

export type MediaResolver = (path: string | null | undefined) => string | undefined;

// ------------------------------------------------------------------ generic loader
export interface AsyncState<T> {
  data: T | undefined;
  error: unknown;
  loading: boolean;
  reload: () => void;
}

export function useAsync<T>(fn: () => Promise<T>, deps: DependencyList): AsyncState<T> {
  const [state, setState] = useState<{ data?: T; error?: unknown; loading: boolean }>({
    loading: true,
  });
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setState((prev) => ({ ...prev, loading: true }));
    fn().then(
      (data) => !cancelled && setState({ data, loading: false }),
      (error: unknown) => !cancelled && setState({ error, loading: false }),
    );
    return () => {
      cancelled = true;
    };
    // `fn` is recreated every render; the caller's deps decide when to reload.
  }, [...deps, nonce]);
  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return { data: state.data, error: state.error, loading: state.loading, reload };
}

// ------------------------------------------------------------------ live job
export interface LiveJob {
  state: JobState | null;
  loading: boolean;
  error: ApiError | null;
  stream: StreamStatus;
  approve: (spec: TaskSpec | null) => Promise<void>;
  retry: () => void;
  media: MediaResolver;
}

const FLUSH_MS = 60;
/** repair_done refreshes within this window share one request (a reconnect replays them all). */
const REFRESH_DEBOUNCE_MS = 300;

export function useLiveJob(id: string): LiveJob {
  const [snapshot, setSnapshot] = useState<ApiJobResponse | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [events, setEvents] = useState<PipelineEvent[]>([]);
  const [stream, setStream] = useState<StreamStatus>("connecting");
  const [nonce, setNonce] = useState(0);
  const seen = useRef<Set<string>>(new Set());
  // Snapshot requests can finish out of order: an answer older than the one shown is dropped.
  const issued = useRef(0);
  const applied = useRef(0);

  const refresh = useCallback(async () => {
    const req = ++issued.current;
    try {
      const snap = await getJob(id);
      if (req <= applied.current) return;
      applied.current = req;
      setSnapshot(snap);
      setError(null);
    } catch (e) {
      if (req <= applied.current) return;
      setError(e instanceof ApiError ? e : new ApiError(0, String(e)));
    }
  }, [id]);

  useEffect(() => {
    setSnapshot(null);
    setError(null);
    setEvents([]);
    seen.current = new Set();
    applied.current = issued.current; // answers to requests made before this (another job) are dropped
    void refresh();

    // Batch bursts (the server replays every past event on connect) into few renders.
    let buffer: PipelineEvent[] = [];
    let timer: ReturnType<typeof setTimeout> | null = null;
    let refreshTimer: ReturnType<typeof setTimeout> | null = null;
    const flush = () => {
      timer = null;
      const batch = buffer;
      buffer = [];
      if (batch.length) setEvents((prev) => [...prev, ...batch]);
    };
    const cancelRefresh = () => {
      if (refreshTimer != null) clearTimeout(refreshTimer);
      refreshTimer = null;
    };
    let stop: () => void = () => undefined;
    stop = subscribeEvents(
      id,
      (ev) => {
        const key = eventKey(ev);
        if (seen.current.has(key)) return;
        seen.current.add(key);
        buffer.push(ev);
        if (timer == null) timer = setTimeout(flush, FLUSH_MS);
        if (ev.type === "job_done") {
          stop();
          cancelRefresh();
          void refresh(); // final snapshot carries the report and media URLs
        } else if (ev.type === "repair_done" && refreshTimer == null) {
          // The snapshot lists the frames the repair call was sent.
          refreshTimer = setTimeout(() => {
            refreshTimer = null;
            void refresh();
          }, REFRESH_DEBOUNCE_MS);
        }
      },
      setStream,
    );
    return () => {
      stop();
      if (timer != null) clearTimeout(timer);
      cancelRefresh();
      buffer = [];
    };
  }, [id, nonce, refresh]);

  // Without the event stream, poll the snapshot until the job ends.
  const snapshotDone = snapshot ? isTerminal(normalizeStatus(snapshot.job.status)) : false;
  useEffect(() => {
    if (stream !== "failed" || snapshotDone) return;
    const t = setInterval(() => void refresh(), 3000);
    return () => clearInterval(t);
  }, [stream, snapshotDone, refresh]);

  const state = useMemo<JobState | null>(() => {
    if (events.length === 0) return snapshot ? stateFromSnapshot(snapshot) : null;
    let s = reduceEvents(events, initialJobState(id, snapshot ? metaFromSnapshot(snapshot) : {}));
    if (snapshot) {
      const snapStatus = normalizeStatus(snapshot.job.status, s.status);
      if (isTerminal(snapStatus) && !isTerminal(s.status)) {
        s = completeJob(s, snapStatus, snapshot.report); // the stream missed the end
      }
      if (snapshot.job.video) s = { ...s, videoPath: snapshot.job.video };
      s = { ...s, failureFrames: failureFramesFromApi(snapshot.attempts) };
    }
    return s;
  }, [id, snapshot, events]);

  const approve = useCallback(
    async (spec: TaskSpec | null) => {
      await postSpec(id, spec ?? { approve: true });
      void refresh();
    },
    [id, refresh],
  );
  const media = useCallback<MediaResolver>((p) => liveMediaUrl(p, id), [id]);
  const retry = useCallback(() => setNonce((n) => n + 1), []);

  return {
    state,
    loading: state == null && error == null,
    error: state == null ? error : null,
    stream,
    approve,
    retry,
    media,
  };
}

// ------------------------------------------------------------------ demo replay
export interface ReplayJob {
  state: JobState | null;
  /** The whole recording, for facts that do not depend on how far the replay has got. */
  recording: JobState | null;
  bundle: DemoBundle | null;
  loading: boolean;
  notFound: boolean;
  cursor: number;
  total: number;
  playing: boolean;
  speed: Speed;
  recordedElapsed: number;
  recordedTotal: number;
  play: () => void;
  pause: () => void;
  restart: () => void;
  skipToEnd: () => void;
  setSpeed: (s: Speed) => void;
  media: MediaResolver;
}

export function useReplayJob(id: string): ReplayJob {
  const [bundle, setBundle] = useState<DemoBundle | null>(null);
  const [loading, setLoading] = useState(true);
  const [cursor, setCursor] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeedState] = useState<Speed>(1);
  const speedRef = useRef<Speed>(1);
  const replayer = useRef<Replayer | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setBundle(null);
    setCursor(0);
    loadDemoJob(id).then((b) => {
      if (cancelled) return;
      setBundle(b);
      setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [id]);

  useEffect(() => {
    if (!bundle || bundle.events.length === 0) return;
    const r = new Replayer(bundle.events.length, replayDelays(bundle.events), (c, p) => {
      setCursor(c);
      setPlaying(p);
    });
    r.speed = speedRef.current;
    replayer.current = r;
    r.play();
    return () => {
      r.dispose();
      replayer.current = null;
    };
  }, [bundle]);

  const base = useMemo(
    () =>
      bundle ? initialJobState(id, bundle.report ? metaFromReport(bundle.report) : {}) : null,
    [bundle, id],
  );

  /** The run after `upTo` events (the whole recording when upTo >= the event count). */
  const stateAt = useCallback(
    (upTo: number): JobState | null => {
      if (!bundle || !base) return null;
      let s: JobState;
      if (bundle.events.length === 0) {
        if (!bundle.report) return null;
        s = stateFromReport(id, bundle.report);
      } else {
        s = reduceEvents(bundle.events.slice(0, upTo), base);
        if (upTo >= bundle.events.length && bundle.report && !s.report) {
          s = completeJob(s, normalizeStatus(bundle.report.status, "failed"), bundle.report);
        }
      }
      // export-demo writes the provenance note into report.json only: the report that job_done
      // carries predates the export.
      const note = bundle.report?.demo_note;
      if (note && s.report && !s.report.demo_note) s = { ...s, report: { ...s.report, demo_note: note } };
      // Shown under the repair they fed, so they appear when the replay reaches that repair.
      return { ...s, failureFrames: bundle.failureFrames };
    },
    [bundle, base, id],
  );
  const state = useMemo(() => stateAt(cursor), [stateAt, cursor]);
  const recording = useMemo(() => stateAt(Number.POSITIVE_INFINITY), [stateAt]);

  const events = bundle?.events ?? [];
  const t0 = events.length ? events[0].ts : 0;
  const recordedTotal = events.length ? Math.max(0, events[events.length - 1].ts - t0) : 0;
  const recordedElapsed = cursor > 0 ? Math.max(0, events[Math.min(cursor, events.length) - 1].ts - t0) : 0;

  const setSpeed = useCallback((s: Speed) => {
    speedRef.current = s;
    setSpeedState(s);
    replayer.current?.setSpeed(s);
  }, []);
  const media = useCallback<MediaResolver>((p) => demoMediaUrl(p, id), [id]);

  return {
    state,
    recording,
    bundle,
    loading,
    notFound: !loading && (!bundle || (!bundle.report && bundle.events.length === 0)),
    cursor,
    total: events.length,
    playing,
    speed,
    recordedElapsed,
    recordedTotal,
    play: () => replayer.current?.play(),
    pause: () => replayer.current?.pause(),
    restart: () => replayer.current?.restart(),
    skipToEnd: () => replayer.current?.skipToEnd(),
    setSpeed,
    media,
  };
}

// ------------------------------------------------------------------ task context
export interface TaskContext {
  /** The benchmark task preset (bench/tasks.yaml via /api/tasks or demo/tasks.json). */
  preset: TaskPreset | null;
  /** The task's row of bench/results.json. */
  bench: BenchRow | null;
}

const NO_CONTEXT: TaskContext = { preset: null, bench: null };

/** The preset and benchmark results for a job's task: the demo bundle for replays, else the API. */
export function useTaskContext(source: "live" | "replay", taskId: string | null): TaskContext {
  const res = useAsync<TaskContext>(async () => {
    if (!taskId) return NO_CONTEXT;
    const [presets, bench] = await Promise.all([
      (source === "replay" ? loadDemoTasks() : getTasks()).catch((): TaskPreset[] => []),
      (source === "replay" ? loadDemoBench() : getBench()).catch(() => null),
    ]);
    return {
      preset: presets.find((p) => p.id === taskId) ?? null,
      bench: bench?.tasks.find((r) => r.task === taskId) ?? null,
    };
  }, [source, taskId]);
  return res.data ?? NO_CONTEXT;
}
