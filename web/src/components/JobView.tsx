/** The job page body, shared by live jobs and demo replays (both render a JobState). */

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import type { MediaResolver } from "../hooks";
import {
  formatDateTime,
  formatDuration,
  formatPct,
  formatUsd,
  kindLabel,
  objectLabel,
  shortId,
} from "../lib/format";
import {
  activityText,
  attemptAgreement,
  attemptCounts,
  isTerminal,
  isVerified,
  lastAttempt,
  pct,
  type JobState,
} from "../state";
import type { AgreementStats, TaskSpec } from "../types";
import { AttemptTimeline } from "./AttemptTimeline";
import { IconChevronLeft, IconClock, IconDownload, IconHand, Spinner } from "./icons";
import { ProgramPanel } from "./ProgramPanel";
import { SeedGrid } from "./SeedGrid";
import { SeedModal } from "./SeedModal";
import { SpecCard } from "./SpecCard";
import { StageStrip } from "./StageStrip";
import { Button, buttonClass, Card, CardHeader, Chip, cx, Stat, StatusChip } from "./ui";
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

// ------------------------------------------------------------------ side cards
function DemonstrationCard({ state, media }: { state: JobState; media: MediaResolver }) {
  const url = media(state.videoPath);
  const [failed, setFailed] = useState(false);
  const objects = [...(state.trackSummary?.objects ?? [])].sort((a, b) => {
    const ao = a.move_order ?? Number.POSITIVE_INFINITY;
    const bo = b.move_order ?? Number.POSITIVE_INFINITY;
    return ao - bo || a.concept.localeCompare(b.concept);
  });
  const tracking = ["running", "waiting"].includes(state.stages.track.status) || state.stages.prep.status === "running" || state.stages.discover.status === "running";

  return (
    <Card>
      <CardHeader
        title="Demonstration"
        icon={<IconHand size={16} />}
        subtitle={state.useVideo ? "What SAM 3.1 tracked in the human demo" : "Sentence only: no video was used"}
      />
      <div className="space-y-4 px-4 py-4 sm:px-5">
        {state.useVideo && url && !failed && (
          <video
            src={url}
            controls
            muted
            playsInline
            preload="metadata"
            onError={() => setFailed(true)}
            className="aspect-video w-full rounded-lg bg-zinc-900 object-contain"
          />
        )}
        {!state.useVideo ? (
          <p className="text-sm text-zinc-600">
            Muse Wonder wrote the spec from the rule sentence and the list of objects in the scene.
            This is the ablation the benchmark compares against.
          </p>
        ) : objects.length ? (
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
                <span className="min-w-0 flex-1 truncate text-zinc-800">{o.concept}</span>
                <span className="shrink-0 text-xs text-zinc-500">
                  {o.moved ? (o.end_bin ? `to ${o.end_bin.replace("_", " ")}` : "moved") : "not moved"}
                </span>
              </li>
            ))}
          </ol>
        ) : (
          <p className="flex items-center gap-2 text-sm text-zinc-500">
            {tracking && <Spinner size={14} />}
            {tracking ? "Tracking the hand and objects" : "No track summary for this job."}
          </p>
        )}
        {state.trackSummary?.notes?.length ? (
          <ul className="space-y-1 text-xs text-zinc-500">
            {state.trackSummary.notes.map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
        ) : null}
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
            label="API cost"
            value={cost ? formatUsd(cost.usd) : "—"}
            hint={cost ? `Wonder ${formatUsd(cost.wonder_usd)} · SAM ${formatUsd(cost.sam_usd)} · ${cost.calls} calls` : undefined}
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
                      <span className="w-20 shrink-0 text-right tabular-nums text-zinc-500">
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
  media,
  source,
  downloads,
  onApprove,
  banner,
  back,
  noVideoNote,
}: {
  state: JobState;
  media: MediaResolver;
  source: "live" | "replay";
  downloads: DownloadLink[];
  onApprove?: (spec: TaskSpec | null) => Promise<void>;
  banner?: ReactNode;
  back: { label: string; to: string };
  noVideoNote?: string;
}) {
  const last = lastAttempt(state);
  const [picked, setPicked] = useState<number | null>(null);
  const [modalSeed, setModalSeed] = useState<number | null>(null);

  // A new attempt takes focus (the replay story: fail -> diagnose -> rerun).
  const count = state.attempts.length;
  const prevCount = useRef(count);
  useEffect(() => {
    if (count > prevCount.current) setPicked(null);
    prevCount.current = count;
  }, [count]);

  const selected = useMemo(
    () => state.attempts.find((a) => a.n === picked) ?? last,
    [state.attempts, picked, last],
  );
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
              <span className="text-zinc-700">{activityText(state)}</span>
            )}
            <span className="hidden h-3 w-px bg-zinc-200 sm:block" />
            {state.taskId && <span className="font-mono text-[11px]">{state.taskId}</span>}
            <span>{state.useVideo ? "Video + sentence" : "Sentence only"}</span>
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
          {verified && finalCounts && <VerifiedBadge passed={finalCounts.passed} total={finalCounts.total} />}
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
            edited={state.specEdited}
            onApprove={onApprove}
            pendingText={
              state.status === "queued"
                ? "Waiting for the job to start"
                : state.useVideo && state.stages.spec.status === "pending"
                  ? "Watching the demonstration first"
                  : "Muse Wonder is writing the spec"
            }
            readOnlyNote="Recorded run: the spec was approved at this point."
          />
          <div className="lg:hidden">
            <AttemptTimeline state={state} selected={selected?.n ?? null} onSelect={setPicked} />
          </div>
          <SeedGrid
            state={state}
            attempt={selected}
            onSelectAttempt={setPicked}
            onOpen={setModalSeed}
            media={media}
          />
          <ProgramPanel attempts={state.attempts} selected={selected} onSelect={setPicked} />
        </div>
        <div className="min-w-0 space-y-6 lg:col-span-4">
          <div className="hidden lg:block">
            <AttemptTimeline state={state} selected={selected?.n ?? null} onSelect={setPicked} />
          </div>
          <DemonstrationCard state={state} media={media} />
          <DetailsCard state={state} />
        </div>
      </div>

      {modalSeed != null && selected && selected.results[modalSeed] && (
        <SeedModal
          attempt={selected}
          seed={modalSeed}
          spec={state.spec}
          media={media}
          onClose={() => setModalSeed(null)}
          onNavigate={setModalSeed}
          noVideoNote={noVideoNote}
        />
      )}
    </div>
  );
}
