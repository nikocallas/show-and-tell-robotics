/** The test scenes of one attempt as thumbnails with pass/fail borders, filling in live. */

import { useState } from "react";
import type { MediaResolver } from "../hooks";
import { formatPct } from "../lib/format";
import {
  attemptAgreement,
  attemptCounts,
  pct,
  seedAgreement,
  type AttemptState,
  type JobState,
} from "../state";
import type { SeedSummary } from "../types";
import { attemptTitle } from "./AttemptTimeline";
import { IconCheck, IconX } from "./icons";
import { Card, CardHeader, cx, Segmented } from "./ui";

function SeedTile({
  seed,
  result,
  running,
  media,
  onOpen,
}: {
  seed: number | null;
  result: SeedSummary | undefined;
  running: boolean;
  media: MediaResolver;
  onOpen: () => void;
}) {
  const [imgFailed, setImgFailed] = useState(false);
  const thumb = result ? media(result.videos?.thumb) : undefined;
  const showImg = Boolean(thumb) && !imgFailed;
  const failedChecks = result ? result.checks.filter((c) => !c.passed).map((c) => c.check_id) : [];
  const ag = result ? seedAgreement(result) : null;
  const disagree = ag ? ag.disagree.length : 0;
  const label = seed == null ? "Scene" : `Scene ${seed}`;
  const status = result ? (result.passed ? "passed" : "failed") : running ? "running" : "waiting";

  return (
    <button
      type="button"
      disabled={!result}
      onClick={onOpen}
      aria-label={`${label}: ${status}${failedChecks.length ? ` (${failedChecks.join(", ")})` : ""}`}
      className={cx(
        "group relative aspect-[4/3] overflow-hidden rounded-lg border-2 text-left transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 focus-visible:ring-offset-2",
        result
          ? result.passed
            ? "border-emerald-500 hover:shadow-pop"
            : "border-rose-500 hover:shadow-pop"
          : "cursor-default border-dashed border-zinc-300",
        result && "animate-fade-in",
      )}
    >
      {showImg ? (
        <img
          src={thumb}
          alt=""
          loading="lazy"
          onError={() => setImgFailed(true)}
          className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-[1.03]"
        />
      ) : (
        <div
          className={cx(
            "flex h-full w-full flex-col items-center justify-center gap-0.5 px-1 text-center",
            result ? (result.passed ? "bg-emerald-50" : "bg-rose-50") : running ? "animate-pulse-soft bg-zinc-100" : "bg-zinc-50",
          )}
        >
          {result && !result.passed && (
            <span className="flex max-w-full flex-col font-mono text-[10px] leading-tight text-rose-700/80">
              {(result.error ? ["program error"] : failedChecks.slice(0, 2)).map((c) => (
                <span key={c} className="truncate">
                  {c}
                </span>
              ))}
              {!result.error && failedChecks.length > 2 && <span>+{failedChecks.length - 2} more</span>}
            </span>
          )}
          {!result && <span className="text-[11px] text-zinc-400">{running ? "Running" : "Waiting"}</span>}
        </div>
      )}
      {seed != null && (
        <span className="absolute bottom-1 left-1 rounded bg-zinc-950/70 px-1.5 py-px font-mono text-[10px] font-medium text-white">
          {seed}
        </span>
      )}
      {result && (
        <span
          className={cx(
            "absolute right-1 top-1 flex h-[18px] w-[18px] items-center justify-center rounded-full text-white shadow",
            result.passed ? "bg-emerald-600" : "bg-rose-600",
          )}
        >
          {result.passed ? <IconCheck size={11} strokeWidth={3} /> : <IconX size={11} strokeWidth={3} />}
        </span>
      )}
      {disagree > 0 && (
        <span
          title={`SAM disagrees with the simulator on ${disagree} check${disagree === 1 ? "" : "s"}`}
          className="absolute left-1 top-1 rounded bg-amber-400 px-1 py-px text-[9px] font-bold uppercase tracking-wide text-amber-950"
        >
          SAM
        </span>
      )}
    </button>
  );
}

export function SeedGrid({
  state,
  attempt,
  onSelectAttempt,
  onOpen,
  media,
}: {
  state: JobState;
  attempt: AttemptState | null;
  onSelectAttempt: (n: number) => void;
  onOpen: (seed: number) => void;
  media: MediaResolver;
}) {
  const counts = attempt ? attemptCounts(attempt, state.nSeeds) : null;
  const known: (number | null)[] = attempt?.seeds.length
    ? attempt.seeds
    : state.seeds.length
      ? state.seeds
      : [];
  // A snapshot of a running attempt lists only the finished seeds: pad with pending tiles.
  const size = Math.max(known.length, counts?.total ?? 0, known.length ? 0 : state.nSeeds);
  const seeds = [...known, ...Array.from({ length: size - known.length }, () => null)];
  const running = attempt?.status === "running";
  const ag = attempt ? attemptAgreement(attempt) : null;

  return (
    <Card>
      <CardHeader
        title="Test scenes"
        subtitle={
          attempt
            ? `${attemptTitle(attempt)} on ${counts?.total ?? seeds.length} randomized layouts`
            : `${seeds.length} randomized layouts, the same for every attempt`
        }
        actions={
          state.attempts.length > 1 && attempt ? (
            <Segmented
              label="Attempt"
              value={attempt.n}
              onChange={onSelectAttempt}
              options={state.attempts.map((a) => {
                const c = attemptCounts(a, state.nSeeds);
                return { value: a.n, label: `${a.n === 0 ? "A0" : `R${a.n}`} · ${c.passed}/${c.total}`, title: attemptTitle(a) };
              })}
            />
          ) : undefined
        }
      />
      <div className="px-4 py-4 sm:px-5">
        <div className="grid grid-cols-4 gap-2 sm:grid-cols-5 sm:gap-3">
          {seeds.map((seed, i) => (
            <SeedTile
              key={`${attempt?.n ?? "x"}-${seed ?? i}`}
              seed={seed}
              result={seed != null && attempt ? attempt.results[seed] : undefined}
              running={Boolean(running)}
              media={media}
              onOpen={() => seed != null && onOpen(seed)}
            />
          ))}
        </div>
        <div className="mt-4 flex flex-wrap items-center justify-between gap-x-4 gap-y-2 text-xs text-zinc-500">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <span className="inline-flex items-center gap-1.5">
              <span className="h-2.5 w-2.5 rounded-sm bg-emerald-500" /> {counts?.passed ?? 0} passed
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="h-2.5 w-2.5 rounded-sm bg-rose-500" /> {counts?.failed ?? 0} failed
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="h-2.5 w-2.5 rounded-sm border border-dashed border-zinc-400" />{" "}
              {counts ? counts.total - counts.done : seeds.length} pending
            </span>
          </div>
          {ag && ag.n > 0 ? (
            <span title="SAM 3.1 verdicts on the rendered clips vs simulator ground truth (ground truth decides)">
              SAM agrees with ground truth on {formatPct(pct(ag))} of checks ({ag.agree}/{ag.n}
              {ag.undecided ? `, ${ag.undecided} undecided` : ""})
            </span>
          ) : (
            <span>Click a scene for its video, checks and contacts</span>
          )}
        </div>
      </div>
    </Card>
  );
}
