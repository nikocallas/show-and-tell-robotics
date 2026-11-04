/** The job page body, shared by live jobs and demo replays (both render a JobState). */

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { useTaskContext, type MediaResolver } from "../hooks";
import { conditionRan, sameProgram, sameSpec } from "../lib/bench";
import {
  binLabel,
  binWithColor,
  describeSelector,
  formatDateTime,
  formatDuration,
  formatPct,
  formatUsd,
  kindLabel,
  objectLabel,
  passParts,
  shortId,
} from "../lib/format";
import { fileName, isStandInVideo } from "../lib/provenance";
import {
  activityText,
  attemptAgreement,
  attemptCounts,
  cachedStages,
  isTerminal,
  isVerified,
  judgeOffReason,
  lastAttempt,
  pct,
  programSupplied,
  type JobState,
  type StageKey,
} from "../state";
import type {
  AgreementStats,
  BenchCondition,
  BenchRow,
  Rule,
  SpecSource,
  TaskPreset,
  TaskSpec,
} from "../types";
import { AttemptTimeline } from "./AttemptTimeline";
import { IconArrowRight, IconChevronLeft, IconClock, IconDownload, IconHand, Spinner } from "./icons";
import { ProgramPanel } from "./ProgramPanel";
import { SeedGrid } from "./SeedGrid";
import { SeedModal } from "./SeedModal";
import { SpecCard } from "./SpecCard";
import { StageStrip } from "./StageStrip";
import {
  AspectVideo,
  Banner,
  Button,
  buttonClass,
  Card,
  CardHeader,
  Chip,
  cx,
  Stat,
  StatusChip,
} from "./ui";
import { VerifiedBadge } from "./VerifiedBadge";

export interface DownloadLink {
  label: string;
  href?: string;
  onClick?: () => void;
  disabled?: boolean;
  title?: string;
}

function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(t);
  }, [active]);
  return now;
}

function mergeAgreement(list: (AgreementStats | null)[]): AgreementStats | null {
  const out: AgreementStats = { agree: 0, n: 0, undecided: 0, by_kind: {} };
  let any = false;
  for (const a of list) {
    if (!a) continue;
    any = true;
    out.agree += a.agree;
    out.n += a.n;
    out.undecided += a.undecided;
    for (const [k, v] of Object.entries(a.by_kind ?? {})) {
      const t = (out.by_kind[k] ??= { agree: 0, n: 0 });
      t.agree += v.agree;
      t.n += v.n;
    }
  }
  return any ? out : null;
}

// ------------------------------------------------------------------ benchmark reference
interface Reference {
  condition: "sentence-only" | "video + sentence" | "stand-in video + sentence";
  run: BenchCondition;
  passed: number;
  total: number;
}

/**
 * The benchmark's score for this job's spec and program: the task's benchmark run in the same
 * condition, when it has exactly this spec and final program (so its reference score applies).
 */
function benchReference(run: JobState, row: BenchRow | null): Reference | null {
  const cond = run.useVideo ? row?.video : row?.sentence;
  if (!row || !conditionRan(cond)) return null;
  const score = passParts(cond.ref_final);
  if (!score) return null;
  const program = run.report?.final_program || lastAttempt(run)?.program;
  if (!sameSpec(run.spec, cond.spec) || !sameProgram(program, cond.final_program)) return null;
  return {
    condition: !run.useVideo ? "sentence-only" : row.video_is_standin ? "stand-in video + sentence" : "video + sentence",
    run: cond,
    passed: score.passed,
    total: score.total,
  };
}

function ruleShort(rule: Rule): string {
  const what = describeSelector(rule.select);
  return rule.place_in && !rule.leave ? `${what} → ${binWithColor(rule.place_in)}` : `${what} stay put`;
}

const REFERENCE_ID = "benchmark-reference";

function ReferenceBox({
  reference,
  spec,
  preset,
  taskId,
}: {
  reference: Reference;
  spec: TaskSpec | null;
  preset: TaskPreset | null;
  taskId: string | null;
}) {
  const full = reference.passed === reference.total;
  return (
    <div id={REFERENCE_ID} className="scroll-mt-24 rounded-lg border border-zinc-200 bg-zinc-50/70 p-3">
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-sm font-medium text-zinc-900">Against the benchmark reference</h3>
        <span className={cx("text-sm font-semibold tabular-nums", full ? "text-emerald-700" : "text-rose-700")}>
          {reference.passed}/{reference.total}
        </span>
      </div>
      <p className="mt-1 text-xs leading-relaxed text-zinc-600">
        The benchmark's {reference.condition} run of {taskId ?? "this task"}
        {reference.run.job_id ? (
          <>
            {" "}
            (<span className="break-all font-mono text-[11px]">{reference.run.job_id}</span>)
          </>
        ) : null}{" "}
        has this spec and final program. Scored against the reference checks, which encode what the human
        demo is to show, it passes {reference.passed} of {reference.total} scenes.
      </p>
      <dl className="mt-2.5 space-y-2 text-xs">
        {spec && spec.rules.length > 0 && (
          <div>
            <dt className="font-medium text-zinc-500">This spec</dt>
            <dd className="mt-0.5 text-zinc-800">{spec.rules.map(ruleShort).join(" · ")}</dd>
          </div>
        )}
        {preset?.demo && (
          <div>
            <dt className="font-medium text-zinc-500">What the human demo is to show (the reference)</dt>
            <dd className="mt-0.5 text-zinc-800">{preset.demo}</dd>
          </div>
        )}
      </dl>
      <Link
        to="/bench"
        className="mt-2.5 inline-flex items-center gap-1 text-xs font-medium text-accent-700 hover:text-accent-800"
      >
        Benchmark <IconArrowRight size={12} />
      </Link>
    </div>
  );
}

// ------------------------------------------------------------------ how a recording was made
const CACHED_NOUN: Partial<Record<StageKey, string>> = {
  discover: "the object discovery",
  spec: "the spec",
  program: "the program",
  repair: "the repair",
};

function joinAnd(items: string[]): string {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

/**
 * Facts about how a recorded run was made, all read from the recording itself (the export note,
 * report.demo_note, is shown on its own).
 */
function recordingFacts(run: JobState, preset: TaskPreset | null): ReactNode[] {
  const facts: ReactNode[] = [];
  const noHumanVideo =
    preset && preset.has_video === false ? ` No human demo video is recorded for ${preset.id} yet.` : "";
  const givenSpec = run.specSource === "given";
  const givenProgram = programSupplied(run);
  if (givenSpec || givenProgram) {
    facts.push(
      givenSpec && givenProgram
        ? "The spec and the first program were supplied with the job: Muse Wonder wrote neither."
        : givenSpec
          ? "The spec was supplied with the job: Muse Wonder did not write it."
          : "The first program was supplied with the job: Muse Wonder did not write it.",
    );
  }
  if (!run.useVideo) {
    facts.push(
      run.specSource === "sentence"
        ? `Sentence only: Muse Wonder wrote the spec from the sentence, without a demonstration video.${noHumanVideo}`
        : `No demonstration video was used.${noHumanVideo}`,
    );
  } else if (isStandInVideo(run.videoPath)) {
    facts.push(
      <>
        The demonstration file is named as a stand-in clip (
        <span className="break-all font-mono text-xs">{fileName(run.videoPath)}</span>), not as a recording of
        a person.{noHumanVideo}
      </>,
    );
  }
  if (run.specApproved && run.specAutoApproved && !run.specEdited && !givenSpec) {
    facts.push("Auto-approved spec: nobody reviewed it before the program was written.");
  }
  const cached = cachedStages(run)
    .map((k) => CACHED_NOUN[k])
    .filter((n): n is string => Boolean(n));
  if (cached.length) {
    const simulated = run.stages.verify.status === "done" && run.stages.verify.seconds > 0;
    const judged = run.stages.judge.status === "done" && run.stages.judge.seconds > 0;
    const one = cached.length === 1;
    facts.push(
      `Muse Wonder's answer${one ? "" : "s"} for ${joinAnd(cached)} came from the local response cache: ${one ? "an identical request had" : "identical requests had"} been answered before, so ${one ? "that stage takes" : "those stages take"} no time here.` +
        (simulated
          ? ` The simulator ran every test scene in this run${judged ? " and SAM 3.1 judged the rendered clips" : ""}.`
          : ""),
    );
  }
  return facts;
}

// ------------------------------------------------------------------ side cards
const THIRD: Record<string, string> = { left_bin: "left", middle_bin: "middle", right_bin: "right" };

/** The card's subtitle: what the demonstration contributed to this job's spec. */
function demonstrationSubtitle(useVideo: boolean, specSource: SpecSource | null): string {
  if (specSource === "given") {
    return useVideo ? "Not analysed: the spec was supplied with the job" : "No video: the spec was supplied with the job";
  }
  return useVideo ? "What SAM 3.1 tracked in the demonstration video" : "Sentence only: no video was used";
}

function DemonstrationCard({
  state,
  specSource,
  media,
  reference,
  preset,
  replay,
}: {
  state: JobState;
  /** Where the spec came from (for replays, the whole recording's answer, known from the start). */
  specSource: SpecSource | null;
  media: MediaResolver;
  reference: Reference | null;
  preset: TaskPreset | null;
  /** Recorded runs only: their demo bundle names stand-in clips "standin_..."; uploads keep any name. */
  replay: boolean;
}) {
  const url = media(state.videoPath);
  const [failed, setFailed] = useState(false);
  const standIn = replay && state.useVideo && isStandInVideo(state.videoPath);
  const given = specSource === "given";
  const notes = state.trackSummary?.notes ?? [];
  // track_summary.py: when it finds fewer than 3 bowls, bins are named by thirds of the frame.
  const thirdsNote = notes.find((n) => /frame thirds/i.test(n));
  const bowlsFound = thirdsNote?.match(/found (\d+) bowl/i);
  const objects = [...(state.trackSummary?.objects ?? [])].sort((a, b) => {
    const ao = a.move_order ?? Number.POSITIVE_INFINITY;
    const bo = b.move_order ?? Number.POSITIVE_INFINITY;
    return ao - bo || a.concept.localeCompare(b.concept);
  });
  const tracking = ["running", "waiting"].includes(state.stages.track.status) || state.stages.prep.status === "running" || state.stages.discover.status === "running";
  const referenceBox =
    reference && state.spec ? (
      <ReferenceBox reference={reference} spec={state.spec} preset={preset} taskId={state.taskId} />
    ) : null;

  return (
    <Card>
      <CardHeader
        title="Demonstration"
        icon={<IconHand size={16} />}
        subtitle={demonstrationSubtitle(state.useVideo, specSource)}
        actions={
          standIn ? (
            <Chip tone="warning" title="The file name marks this clip as a stand-in (standin_…), not as a recording of a person">
              Stand-in video
            </Chip>
          ) : undefined
        }
      />
      <div className="space-y-4 px-4 py-4 sm:px-5">
        {state.useVideo && url && !failed && (
          <AspectVideo
            src={url}
            controls
            muted
            playsInline
            preload="metadata"
            fallbackAspect={16 / 9}
            onError={() => setFailed(true)}
            className="w-full rounded-lg bg-zinc-900 object-contain"
          />
        )}
        {standIn && (
          <p className="-mt-1 text-xs text-amber-800">
            Named as a stand-in clip, not as a human demonstration:{" "}
            <span className="break-all font-mono text-[11px]">{fileName(state.videoPath)}</span>
          </p>
        )}
        {given ? (
          <p className="text-sm leading-relaxed text-zinc-600">
            {state.useVideo
              ? "The spec was supplied with the job, so this video was not analysed: SAM 3.1 tracked nothing in it and Muse Wonder did not write the spec."
              : "The spec was supplied with the job: Muse Wonder did not write it, and no video was used."}{" "}
            Pass and fail on this page use the supplied spec's checks.
          </p>
        ) : !state.useVideo ? (
          specSource === "sentence" ? (
            <p className="text-sm leading-relaxed text-zinc-600">
              Muse Wonder wrote the spec from the rule sentence and the list of objects in the scene,
              with no video: the benchmark's sentence-only ablation. Pass and fail on this page use this
              spec's own checks. The Benchmark page scores the same kind of run against reference checks
              that encode what the human demo is to show, where a spec written without the video can score lower.
            </p>
          ) : (
            <p className="text-sm text-zinc-500">No video is used for this job.</p>
          )
        ) : objects.length ? (
          <div>
            <ol className="space-y-1.5">
              {objects.map((o) => (
                <li key={o.track_id} className="flex items-center gap-2.5 text-sm">
                  <span
                    className={cx(
                      "flex h-5 w-5 shrink-0 items-center justify-center rounded-md text-[11px] font-semibold",
                      o.moved ? "bg-zinc-900 text-white" : "bg-zinc-100 text-zinc-400",
                    )}
                  >
                    {o.move_order != null ? o.move_order + 1 : "–"}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-zinc-800" title={o.track_id}>
                    {o.attrs?.size ? `${o.attrs.size} ${o.concept}` : o.concept}
                  </span>
                  <span className="shrink-0 text-xs text-zinc-500">
                    {!o.moved
                      ? "not moved"
                      : !o.end_bin
                        ? "moved"
                        : thirdsNote
                          ? `ends in the frame's ${THIRD[o.end_bin] ?? binLabel(o.end_bin)} third`
                          : `to ${binLabel(o.end_bin)}`}
                  </span>
                </li>
              ))}
            </ol>
            {thirdsNote && (
              <p className="mt-2.5 text-xs text-zinc-500">
                {bowlsFound && Number(bowlsFound[1]) > 0
                  ? `Only ${bowlsFound[1]} of the 3 bowls ${Number(bowlsFound[1]) === 1 ? "was" : "were"} found in the video`
                  : "No bowls were found in the video"}
                , so end positions are thirds of the frame (left, middle, right), not bowls.
              </p>
            )}
          </div>
        ) : (
          <p className="flex items-center gap-2 text-sm text-zinc-500">
            {tracking && <Spinner size={14} />}
            {tracking
              ? "Tracking the hand and objects"
              : !isTerminal(state.status) && state.stages.track.status === "pending"
                ? "Not tracked yet."
                : "No track summary for this job."}
          </p>
        )}
        {referenceBox}
        {notes.length > 0 && (
          <details className="group">
            <summary className="cursor-pointer list-none text-xs font-medium text-zinc-500 hover:text-zinc-800">
              <span className="inline-block transition-transform group-open:rotate-90">›</span> Perception
              notes ({notes.length})
            </summary>
            <ul className="mt-2 space-y-1 rounded-lg bg-zinc-50 p-2.5 text-[11px] leading-relaxed text-zinc-600">
              {notes.map((n, i) => (
                <li key={i}>{n}</li>
              ))}
            </ul>
          </details>
        )}
      </div>
    </Card>
  );
}

function DetailsCard({ state }: { state: JobState }) {
  const report = state.report;
  const agreement = report?.agreement ?? mergeAgreement(state.attempts.map(attemptAgreement));
  const seeds = state.seeds;
  const wall = report?.wall_seconds || (state.endedAt && state.startedAt ? state.endedAt - state.startedAt : null);
  const cost = report?.cost;
  const logs = state.logs;

  return (
    <Card>
      <CardHeader title="Run details" />
      <div className="space-y-5 px-4 py-4 sm:px-5">
        <dl className="grid grid-cols-2 gap-x-4 gap-y-4">
          <Stat
            label="Scenes"
            value={seeds.length ? `${seeds.length} seeds` : `${state.nSeeds} seeds`}
            hint={seeds.length ? `${seeds[0]}–${seeds[seeds.length - 1]}, fixed per task` : undefined}
          />
          <Stat label="Wall time" value={formatDuration(wall)} />
          <Stat
            label="API cost at list price"
            value={cost ? formatUsd(cost.usd) : "—"}
            hint={
              cost
                ? `Wonder ${formatUsd(cost.wonder_usd)} · SAM ${formatUsd(cost.sam_usd)} · ${cost.calls} calls, counted at list price even when served from the local cache${cost.paid_usd != null ? ` · billed ${formatUsd(cost.paid_usd)}` : ""}`
                : undefined
            }
          />
          <Stat
            label="Judge agreement"
            value={formatPct(pct(agreement))}
            hint={agreement ? `${agreement.agree}/${agreement.n} checks${agreement.undecided ? `, ${agreement.undecided} undecided` : ""}` : "SAM 3.1 vs ground truth"}
          />
        </dl>

        {agreement && Object.keys(agreement.by_kind).length > 0 && (
          <div>
            <h3 className="text-xs font-medium text-zinc-500">Agreement by check</h3>
            <ul className="mt-2 space-y-1.5">
              {Object.entries(agreement.by_kind)
                .filter(([, v]) => v.n > 0)
                .map(([k, v]) => {
                  const p = pct(v) ?? 0;
                  return (
                    <li key={k} className="flex items-center gap-2 text-xs">
                      <span className="w-20 shrink-0 text-zinc-600">{kindLabel(k)}</span>
                      <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-zinc-100">
                        <span className="block h-full rounded-full bg-zinc-700" style={{ width: `${p}%` }} />
                      </span>
                      <span className="shrink-0 whitespace-nowrap text-right tabular-nums text-zinc-500">
                        {formatPct(p)} ({v.agree}/{v.n})
                      </span>
                    </li>
                  );
                })}
            </ul>
          </div>
        )}

        {state.objectIds.length > 0 && (
          <div>
            <h3 className="text-xs font-medium text-zinc-500">Objects in the scene</h3>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {state.objectIds.map((o) => (
                <Chip key={o} tone="muted" title={o}>
                  {objectLabel(o)}
                </Chip>
              ))}
            </div>
          </div>
        )}

        {logs.length > 0 && (
          <details className="group">
            <summary className="cursor-pointer list-none text-xs font-medium text-zinc-500 hover:text-zinc-800">
              <span className="inline-block transition-transform group-open:rotate-90">›</span> Pipeline log ({logs.length})
            </summary>
            <ol className="mt-2 max-h-56 space-y-1 overflow-y-auto rounded-lg bg-zinc-50 p-2.5 font-mono text-[11px] leading-relaxed">
              {logs.map((l, i) => (
                <li key={i} className={l.level === "error" ? "text-rose-700" : "text-zinc-600"}>
                  {l.message}
                </li>
              ))}
            </ol>
          </details>
        )}
      </div>
    </Card>
  );
}

// ------------------------------------------------------------------ the view
export function JobView({
  state,
  recording,
  media,
  source,
  downloads,
  onApprove,
  banner,
  back,
  noVideoNote,
  onModalChange,
}: {
  state: JobState;
  /** Replays: the whole recording, so facts about the run show from the start of the replay. */
  recording?: JobState | null;
  media: MediaResolver;
  source: "live" | "replay";
  downloads: DownloadLink[];
  onApprove?: (spec: TaskSpec | null) => Promise<void>;
  banner?: ReactNode;
  back: { label: string; to: string };
  noVideoNote?: string;
  /** A scene dialog opened (true) or closed (false); replays pause while one is open. */
  onModalChange?: (open: boolean) => void;
}) {
  const last = lastAttempt(state);
  const [picked, setPicked] = useState<number | null>(null);
  // The dialog stays on the attempt it was opened from, whatever the replay or live run adds.
  const [modal, setModal] = useState<{ attempt: number; seed: number } | null>(null);
  const replay = source === "replay";
  const whole = recording ?? state;
  const context = useTaskContext(source, whole.taskId);
  const reference = useMemo(() => benchReference(whole, context.bench), [whole, context.bench]);
  const facts = useMemo(() => (replay ? recordingFacts(whole, context.preset) : []), [replay, whole, context.preset]);
  // How the run was made, in the words of whoever exported it (export-demo --note).
  const note = replay ? whole.report?.demo_note?.trim() || null : null;

  // A new attempt takes focus (the replay story: fail -> diagnose -> rerun).
  const count = state.attempts.length;
  const prevCount = useRef(count);
  useEffect(() => {
    if (count > prevCount.current) setPicked(null);
    prevCount.current = count;
  }, [count]);

  const modalOpen = modal != null;
  const onModalChangeRef = useRef(onModalChange);
  onModalChangeRef.current = onModalChange;
  const mounted = useRef(false);
  useEffect(() => {
    if (!mounted.current) {
      mounted.current = true;
      return;
    }
    onModalChangeRef.current?.(modalOpen);
  }, [modalOpen]);

  const selected = useMemo(
    () => state.attempts.find((a) => a.n === picked) ?? last,
    [state.attempts, picked, last],
  );
  const modalAttempt = modal ? state.attempts.find((a) => a.n === modal.attempt) : undefined;
  const modalResult = modal && modalAttempt ? modalAttempt.results[modal.seed] : undefined;
  // A restarted replay can drop the attempt the dialog shows: close it then.
  useEffect(() => {
    if (modal && !modalResult) setModal(null);
  }, [modal, modalResult]);

  const verified = isVerified(state);
  const terminal = isTerminal(state.status);
  const now = useNow(source === "live" && !terminal && state.startedAt != null);
  const elapsed =
    state.startedAt != null
      ? (state.endedAt ?? (source === "live" ? now : (state.lastTs ?? state.startedAt))) - state.startedAt
      : null;
  const finalCounts = last ? attemptCounts(last, state.nSeeds) : null;

  return (
    <div className="space-y-6">
      {/* header */}
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <Link
            to={back.to}
            className="inline-flex items-center gap-1 text-xs font-medium text-zinc-500 hover:text-zinc-900"
          >
            <IconChevronLeft size={14} /> {back.label}
          </Link>
          <h1 className="mt-2 text-xl font-semibold leading-snug tracking-tight text-zinc-900 sm:text-2xl">
            {state.rules ? `“${state.rules}”` : "Untitled job"}
          </h1>
          <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-2 text-xs text-zinc-500">
            <StatusChip status={state.status} />
            {(state.status === "running" || state.status === "awaiting_approval") && (
              <span className="text-zinc-700">{activityText(state, replay)}</span>
            )}
            <span className="hidden h-3 w-px bg-zinc-200 sm:block" />
            {state.taskId && <span className="font-mono text-[11px]">{state.taskId}</span>}
            <span>
              {whole.specSource === "given" ? "Spec supplied" : state.useVideo ? "Video + sentence" : "Sentence only"}
            </span>
            <span>{state.nSeeds} scenes</span>
            {elapsed != null && (
              <span className="inline-flex items-center gap-1 tabular-nums">
                <IconClock size={12} /> {formatDuration(elapsed)}
              </span>
            )}
            {state.createdAt != null && <span>{formatDateTime(state.createdAt)}</span>}
            <span className="font-mono text-[11px] text-zinc-400" title={state.jobId}>
              {shortId(state.jobId)}
            </span>
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-start gap-3 lg:items-end">
          {verified && finalCounts && (
            <div className="flex flex-col items-start gap-1.5 lg:items-end">
              <VerifiedBadge passed={finalCounts.passed} total={finalCounts.total} />
              <span className="text-xs text-zinc-500">
                Every scene passes this spec's checks
                {reference && reference.passed < reference.total && (
                  <>
                    {" · "}
                    <button
                      type="button"
                      onClick={() =>
                        document.getElementById(REFERENCE_ID)?.scrollIntoView({ behavior: "smooth", block: "center" })
                      }
                      className="font-medium text-zinc-700 underline decoration-zinc-300 underline-offset-2 hover:text-zinc-900"
                    >
                      benchmark reference{" "}
                      <span className="tabular-nums text-rose-700">
                        {reference.passed}/{reference.total}
                      </span>
                    </button>
                  </>
                )}
              </span>
            </div>
          )}
          <div className="flex flex-wrap gap-2">
            {downloads.map((d) =>
              d.href && !d.disabled ? (
                <a key={d.label} href={d.href} download className={buttonClass("secondary", "sm")} title={d.title}>
                  <IconDownload size={14} /> {d.label}
                </a>
              ) : (
                <Button
                  key={d.label}
                  size="sm"
                  disabled={d.disabled || !d.onClick}
                  onClick={d.onClick}
                  title={d.title ?? (d.disabled ? "Available when the job finishes" : undefined)}
                >
                  <IconDownload size={14} /> {d.label}
                </Button>
              ),
            )}
          </div>
        </div>
      </div>

      {banner}

      {(note || facts.length > 0) && (
        <Banner tone="neutral" title="About this recording">
          {note && <p className="mt-1 text-xs leading-relaxed text-zinc-600 sm:text-[13px]">{note}</p>}
          {facts.length > 0 &&
            (note ? (
              // The note covers how the run was made; what the events themselves show stays one click away.
              <details className="group mt-2">
                <summary className="cursor-pointer list-none text-xs font-medium text-zinc-500 hover:text-zinc-800">
                  <span className="inline-block transition-transform group-open:rotate-90">›</span> From the
                  recorded events ({facts.length})
                </summary>
                <ul className="mt-1.5 list-disc space-y-1 pl-4 text-xs leading-relaxed text-zinc-600 sm:text-[13px]">
                  {facts.map((f, i) => (
                    <li key={i}>{f}</li>
                  ))}
                </ul>
              </details>
            ) : (
              <ul className="mt-1 list-disc space-y-1 pl-4 text-xs leading-relaxed text-zinc-600 sm:text-[13px]">
                {facts.map((f, i) => (
                  <li key={i}>{f}</li>
                ))}
              </ul>
            ))}
        </Banner>
      )}

      <Card className="px-4 py-3 sm:px-5">
        <StageStrip state={state} />
      </Card>

      {state.error && state.status === "error" && (
        <div className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900">
          <p className="font-medium">The pipeline stopped</p>
          <p className="mt-0.5 break-words font-mono text-xs">{state.error}</p>
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-12">
        <div className="min-w-0 space-y-6 lg:col-span-8">
          <SpecCard
            spec={state.spec}
            source={state.specSource}
            mediaPath={state.mediaPath}
            clarifyingQuestion={state.clarifyingQuestion}
            awaiting={state.awaitingApproval}
            approved={state.specApproved}
            autoApproved={state.specAutoApproved}
            paused={state.sawAwaitingApproval}
            edited={state.specEdited}
            replay={replay}
            onApprove={onApprove}
            pendingText={
              terminal
                ? "The job stopped before a spec was written."
                : state.status === "queued"
                  ? "Waiting for the job to start"
                  : whole.specSource === "given"
                    ? "Reading the spec supplied with the job"
                    : state.useVideo && state.stages.spec.status === "pending"
                      ? "Watching the demonstration first"
                      : "Muse Wonder is writing the spec"
            }
            pendingStopped={terminal}
            readOnlyNote="Recorded run: the spec was approved at this point."
          />
          <div className="lg:hidden">
            <AttemptTimeline state={state} selected={selected?.n ?? null} onSelect={setPicked} media={media} />
          </div>
          <SeedGrid
            state={state}
            attempt={selected}
            onSelectAttempt={setPicked}
            onOpen={(seed) => selected && setModal({ attempt: selected.n, seed })}
            media={media}
          />
          <ProgramPanel attempts={state.attempts} selected={selected} onSelect={setPicked} />
        </div>
        <div className="min-w-0 space-y-6 lg:col-span-4">
          <div className="hidden lg:block">
            <AttemptTimeline state={state} selected={selected?.n ?? null} onSelect={setPicked} media={media} />
          </div>
          <DemonstrationCard
            state={state}
            specSource={whole.specSource}
            media={media}
            reference={reference}
            preset={context.preset}
            replay={replay}
          />
          <DetailsCard state={state} />
        </div>
      </div>

      {modal && modalAttempt && modalResult && (
        <SeedModal
          attempt={modalAttempt}
          seed={modal.seed}
          spec={state.spec}
          media={media}
          onClose={() => setModal(null)}
          onNavigate={(seed) => setModal({ attempt: modal.attempt, seed })}
          noVideoNote={noVideoNote}
          judgeOff={judgeOffReason(state)}
        />
      )}
    </div>
  );
}
