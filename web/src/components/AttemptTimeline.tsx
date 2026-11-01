/** Attempt 0 -> Repair 1 -> ... with k/N passed, a per-scene bar and the one-line diagnosis. */

import type { MediaResolver } from "../hooks";
import { formatDuration, formatPct } from "../lib/format";
import { fileName } from "../lib/provenance";
import {
  attemptAgreement,
  attemptCounts,
  isVerified,
  pct,
  repairStopNote,
  type AttemptState,
  type JobState,
} from "../state";
import { IconCheck, IconClock, IconWrench, IconX, Spinner } from "./icons";
import { Card, CardHeader, cx } from "./ui";
import { VerifiedBadge } from "./VerifiedBadge";

/** repair.failure_frames names files seed<seed>_f<i>_t<seconds>s.jpg. */
function frameInfo(path: string): { seed: number | null; t: number | null } {
  const m = fileName(path).match(/seed(\d+)_f\d+_t([\d.]+)s\./);
  return m ? { seed: Number(m[1]), t: Number(m[2]) } : { seed: null, t: null };
}

/** The frames of the failing scene that Muse Wonder was sent with the failed checks. */
function RepairFrames({
  frames,
  from,
  fallbackSeed,
  media,
}: {
  frames: string[];
  from: AttemptState | undefined;
  fallbackSeed: number | null;
  media: MediaResolver;
}) {
  const seed = frameInfo(frames[0]).seed ?? fallbackSeed;
  return (
    <div className="mt-2 rounded-lg border border-zinc-200 bg-zinc-50/70 p-2.5">
      <p className="text-[11px] font-medium text-zinc-600">
        What Muse Wonder saw: {frames.length} frame{frames.length === 1 ? "" : "s"}
        {seed != null ? ` of scene ${seed}` : ""}
        {from ? ` (${attemptTitle(from)})` : ""}, sent with the failed checks
      </p>
      <ol className="mt-2 grid grid-cols-4 gap-1">
        {frames.map((f, i) => {
          const url = media(f);
          const { t } = frameInfo(f);
          const label = `Frame ${i + 1}${t != null ? ` at ${t.toFixed(2)} s` : ""}`;
          return (
            <li key={f}>
              <a
                href={url}
                target="_blank"
                rel="noreferrer"
                title={`${label} (opens the full frame)`}
                className="block overflow-hidden rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500"
              >
                <img
                  src={url}
                  alt={label}
                  loading="lazy"
                  className="aspect-[4/3] w-full bg-zinc-200 object-cover transition-opacity hover:opacity-90"
                />
              </a>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

export function attemptTitle(a: Pick<AttemptState, "n" | "kind">): string {
  return a.kind === "initial" || a.n === 0 ? `Attempt ${a.n}` : `Repair ${a.n}`;
}

/** One cell per scene: green passed, red failed, grey pending. */
export function SceneBar({ attempt, nSeeds }: { attempt: AttemptState; nSeeds: number }) {
  const seeds = attempt.seeds.length ? attempt.seeds : Array.from({ length: nSeeds }, (_, i) => -1 - i);
  const running = attempt.status === "running";
  return (
    <div className="flex gap-[3px]" aria-hidden="true">
      {seeds.map((seed) => {
        const r = attempt.results[seed];
        return (
          <span
            key={seed}
            className={cx(
              "h-1.5 min-w-0 flex-1 rounded-full",
              r ? (r.passed ? "bg-emerald-500" : "bg-rose-500") : running ? "bg-zinc-200 animate-pulse-soft" : "bg-zinc-200",
            )}
          />
        );
      })}
    </div>
  );
}

function NodeIcon({ attempt, nSeeds }: { attempt: AttemptState; nSeeds: number }) {
  const c = attemptCounts(attempt, nSeeds);
  if (attempt.status === "done") {
    const ok = c.total > 0 && c.passed === c.total;
    return (
      <span
        className={cx(
          "flex h-6 w-6 items-center justify-center rounded-full ring-4 ring-white",
          ok ? "bg-emerald-600 text-white" : "bg-rose-600 text-white",
        )}
      >
        {ok ? <IconCheck size={13} strokeWidth={2.8} /> : <IconX size={13} strokeWidth={2.8} />}
      </span>
    );
  }
  if (attempt.status === "running") {
    return (
      <span className="flex h-6 w-6 items-center justify-center rounded-full bg-accent-50 text-accent-600 ring-4 ring-white">
        <Spinner size={14} />
      </span>
    );
  }
  if (attempt.status === "writing") {
    return (
      <span className="flex h-6 w-6 items-center justify-center rounded-full bg-amber-50 text-amber-600 ring-4 ring-white">
        <IconWrench size={13} />
      </span>
    );
  }
  return (
    <span className="flex h-6 w-6 items-center justify-center rounded-full bg-zinc-100 text-zinc-500 ring-4 ring-white">
      <IconClock size={13} />
    </span>
  );
}

function statusLine(a: AttemptState, nSeeds: number): string {
  const c = attemptCounts(a, nSeeds);
  switch (a.status) {
    case "writing":
      return a.kind === "repair" ? "Diagnosing failures and patching the program" : "Writing the program";
    case "ready":
      return "Program ready, starting the test scenes";
    case "running":
      return `Running: ${c.done}/${c.total} scenes done`;
    case "done": {
      const parts: string[] = [];
      if (a.wallSeconds) parts.push(formatDuration(a.wallSeconds));
      const ag = pct(attemptAgreement(a));
      if (ag != null) parts.push(`SAM agrees ${formatPct(ag)}`);
      return parts.join(" · ");
    }
  }
}

export function AttemptTimeline({
  state,
  selected,
  onSelect,
  media,
}: {
  state: JobState;
  selected: number | null;
  onSelect: (n: number) => void;
  media: MediaResolver;
}) {
  const verified = isVerified(state);
  const last = state.attempts[state.attempts.length - 1];
  const repairs = state.attempts.filter((a) => a.kind === "repair").length;
  // Why fewer repairs ran than could have (the pipeline only logs it).
  const repairNote = state.status === "failed" ? repairStopNote(state) : null;

  return (
    <Card>
      <CardHeader
        title="Attempts"
        subtitle={
          state.attempts.length
            ? `${state.attempts.length} attempt${state.attempts.length === 1 ? "" : "s"}, up to 3 repairs`
            : "Every attempt runs on the same scenes"
        }
      />
      <div className="px-4 py-4 sm:px-5">
        {state.attempts.length === 0 ? (
          <p className="text-sm text-zinc-500">
            The first program is tested on {state.nSeeds} randomized scenes as soon as it is written.
            Failures go back to Muse Wonder with the failed checks and frames from a failing scene.
          </p>
        ) : (
          <ol className="relative">
            {state.attempts.map((a, i) => {
              const c = attemptCounts(a, state.nSeeds);
              const isSel = selected === a.n;
              const ok = a.status === "done" && c.total > 0 && c.passed === c.total;
              const fedFrom = a.kind === "repair" ? state.attempts.find((x) => x.n === a.n - 1) : undefined;
              const frames = a.kind === "repair" ? (state.failureFrames[a.n - 1] ?? []) : [];
              return (
                <li key={a.n} className="relative pb-4 pl-9 last:pb-0">
                  {i < state.attempts.length - 1 && (
                    <span className="absolute left-[11px] top-7 h-[calc(100%-1.75rem)] w-px bg-zinc-200" aria-hidden="true" />
                  )}
                  <span className="absolute left-0 top-2">
                    <NodeIcon attempt={a} nSeeds={state.nSeeds} />
                  </span>
                  <button
                    type="button"
                    onClick={() => onSelect(a.n)}
                    aria-pressed={isSel}
                    className={cx(
                      "w-full rounded-lg border px-3 py-2.5 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500",
                      isSel ? "border-accent-300 bg-accent-50/40" : "border-transparent hover:border-zinc-200 hover:bg-zinc-50",
                    )}
                  >
                    <div className="flex items-baseline justify-between gap-3">
                      <span className="text-sm font-semibold text-zinc-900">
                        {attemptTitle(a)}
                        <span className="ml-1.5 text-xs font-normal text-zinc-500">
                          {a.kind === "initial" ? "initial program" : "patched program"}
                        </span>
                      </span>
                      <span
                        className={cx(
                          "shrink-0 text-sm font-semibold tabular-nums",
                          a.status !== "done" ? "text-zinc-500" : ok ? "text-emerald-700" : "text-rose-700",
                        )}
                      >
                        {c.passed}/{c.total}
                        <span className="ml-1 text-xs font-normal text-zinc-500">passed</span>
                      </span>
                    </div>
                    <div className="mt-2">
                      <SceneBar attempt={a} nSeeds={state.nSeeds} />
                    </div>
                    {a.diagnosis && (
                      <p className="mt-2.5 border-l-2 border-amber-300 pl-2.5 text-[13px] leading-snug text-zinc-700">
                        <span className="block text-[11px] font-medium uppercase tracking-wide text-amber-700">
                          Diagnosis
                        </span>
                        {a.diagnosis}
                      </p>
                    )}
                    <p className="mt-2 text-xs text-zinc-500">{statusLine(a, state.nSeeds)}</p>
                  </button>
                  {frames.length > 0 && (
                    <RepairFrames
                      frames={frames}
                      from={fedFrom}
                      fallbackSeed={fedFrom?.failedSeeds[0] ?? null}
                      media={media}
                    />
                  )}
                </li>
              );
            })}
          </ol>
        )}

        {last && (verified || state.status === "failed" || state.status === "error") && (
          <div className="mt-4 border-t border-zinc-100 pt-4">
            {verified ? (
              <div className="flex flex-wrap items-center gap-3">
                <VerifiedBadge passed={attemptCounts(last, state.nSeeds).passed} total={attemptCounts(last, state.nSeeds).total} compact />
                <span className="text-xs text-zinc-500">
                  {repairs === 0 ? "First try." : `After ${repairs} repair${repairs === 1 ? "" : "s"}.`}
                </span>
              </div>
            ) : state.status === "failed" ? (
              <>
                <p className="text-sm text-rose-700">
                  Not verified: {attemptCounts(last, state.nSeeds).passed}/{attemptCounts(last, state.nSeeds).total} scenes
                  pass after {repairs} repair{repairs === 1 ? "" : "s"}.
                </p>
                {repairNote && <p className="mt-1 text-xs text-zinc-500">{repairNote}</p>}
              </>
            ) : (
              <p className="text-sm text-rose-700">The job stopped with an error.</p>
            )}
          </div>
        )}
      </div>
    </Card>
  );
}
