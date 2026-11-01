/** The pipeline as a stepper: prep, discover, track, spec, program, verify, judge, repair, report. */

import { useEffect, useRef } from "react";
import { formatDuration } from "../lib/format";
import {
  attemptCounts,
  isTerminal,
  judgeOffReason,
  lastAttempt,
  programSupplied,
  repairStopNote,
  stageCached,
  STAGES,
  type JobState,
  type StageKey,
  type StageState,
} from "../state";
import { IconAlert, IconCheck, IconMinus, Spinner } from "./icons";
import { cx } from "./ui";

const LABELS: Record<StageKey, { label: string; hint: string }> = {
  prep: { label: "Prep", hint: "Normalize the demo video and pick keyframes" },
  discover: { label: "Discover", hint: "Muse Wonder lists the objects in the video" },
  track: { label: "Track", hint: "SAM 3.1 tracks the hand and every object" },
  spec: { label: "Spec", hint: "Rules and pass/fail checks, approved before the program is written" },
  program: { label: "Program", hint: "Muse Wonder writes the robot program, unless the job supplies one" },
  verify: { label: "Verify", hint: "Run the program on randomized simulated scenes" },
  judge: { label: "Judge", hint: "SAM 3.1 checks the rendered scenes" },
  repair: { label: "Repair", hint: "Diagnose failures and patch the program" },
  report: { label: "Report", hint: "Test report and full attempt log" },
};

const CACHED_HINT =
  "Served from the local response cache: Muse Wonder had already answered this exact request, so the stage took no time and made no new API call.";

/** What the data says about the spec: where it came from and whether (and how) it was approved. */
function specHint(state: JobState): string {
  const given = state.specSource === "given";
  const base = given
    ? "Rules and pass/fail checks supplied with the job (Muse Wonder did not write them)"
    : "Rules and pass/fail checks";
  if (state.awaitingApproval) {
    return `${base}. Waiting for approval: the program is written only once the spec is approved`;
  }
  if (state.specApproved) {
    if (state.sawAwaitingApproval || state.specEdited) {
      const edits = state.specEdited ? " with edits" : "";
      return `${base}. The run paused for approval, and the spec was approved${edits} before the program was written`;
    }
    if (state.specAutoApproved && !given) {
      return `${base}. This run approved the spec automatically, without review (benchmark and non-interactive runs do)`;
    }
    return given ? `${base}. The run used them without pausing for approval` : base;
  }
  if (state.sawAwaitingApproval && isTerminal(state.status)) {
    return `${base}. The run stopped before the spec was approved`;
  }
  return given ? base : LABELS.spec.hint;
}

function hint(key: StageKey, state: JobState): string {
  const st = state.stages[key];
  if (key === "spec") return specHint(state);
  if (key === "program" && programSupplied(state)) {
    return "Program supplied with the job and checked by the sandbox: Muse Wonder did not write it";
  }
  if (key === "judge" && st.status === "skipped") {
    const why = judgeOffReason(state);
    if (why) return `${LABELS.judge.hint}. In this run ${why}`;
  }
  if (key === "repair" && st.status === "skipped" && state.status === "failed") {
    const why = repairStopNote(state);
    if (why) return `${LABELS.repair.hint}. ${why}`;
  }
  return LABELS[key].hint;
}

const BAR: Record<StageState["status"], string> = {
  pending: "bg-zinc-200",
  running: "bg-accent-500 animate-pulse-soft",
  waiting: "bg-amber-400 animate-pulse-soft",
  done: "bg-zinc-800",
  skipped: "bg-[repeating-linear-gradient(90deg,#d4d4d8_0_4px,transparent_4px_8px)]",
  error: "bg-rose-500",
};

/** The line under a stage label; `wide` is extra text shown from the sm breakpoint up. */
function subText(key: StageKey, st: StageState, state: JobState): { text: string; wide?: string } {
  const last = lastAttempt(state);
  switch (st.status) {
    case "pending":
      return { text: "" };
    case "skipped":
      return { text: "Skipped" };
    case "error":
      return { text: "Failed" };
    case "waiting":
      return { text: "Needs approval" };
    case "running": {
      if (key === "verify" && last) {
        const c = attemptCounts(last, state.nSeeds);
        return { text: `${c.done}/${c.total} scenes` };
      }
      if (key === "judge" && last) {
        const judged = Object.keys(last.sam).length;
        return { text: `${judged}/${last.seeds.length || state.nSeeds} judged` };
      }
      if (key === "repair" && st.attempt != null) return { text: `Round ${st.attempt}` };
      return { text: "Running" };
    }
    case "done": {
      const rounds = state.attempts.filter((a) => a.kind === "repair").length;
      const cached = stageCached(state, key);
      if (key === "repair" && rounds > 0) {
        return { text: `${rounds} round${rounds === 1 ? "" : "s"}`, wide: cached ? ", cached" : undefined };
      }
      if (cached) return { text: "Cached" };
      if ((key === "spec" && state.specSource === "given") || (key === "program" && programSupplied(state))) {
        return { text: "Supplied" };
      }
      const t = st.seconds > 0 ? formatDuration(st.seconds) : "Done";
      if ((key === "verify" || key === "judge") && state.attempts.length > 1) return { text: t, wide: " total" };
      return { text: t };
    }
  }
}

function StageIcon({ status }: { status: StageState["status"] }) {
  switch (status) {
    case "done":
      return <IconCheck size={13} strokeWidth={2.6} className="text-zinc-800" />;
    case "running":
      return <Spinner size={12} className="text-accent-600" />;
    case "waiting":
      return <span className="h-2 w-2 rounded-full bg-amber-500" />;
    case "error":
      return <IconAlert size={13} className="text-rose-600" />;
    case "skipped":
      return <IconMinus size={13} className="text-zinc-400" />;
    default:
      return <span className="h-2 w-2 rounded-full border border-zinc-300" />;
  }
}

/** The stage to keep in view on narrow screens: the active one, else the last one reached. */
function focusStage(state: JobState): StageKey | null {
  const active = STAGES.find((k) => ["running", "waiting"].includes(state.stages[k].status));
  if (active) return active;
  const reached = STAGES.filter((k) => ["done", "error"].includes(state.stages[k].status));
  return reached[reached.length - 1] ?? null;
}

export function StageStrip({ state }: { state: JobState }) {
  const navRef = useRef<HTMLElement>(null);
  const focus = focusStage(state);

  // Narrow screens scroll the strip sideways: keep the current stage visible (horizontally only,
  // so the page itself never jumps).
  useEffect(() => {
    const nav = navRef.current;
    if (!nav || !focus || nav.scrollWidth <= nav.clientWidth) return;
    const item = nav.querySelector<HTMLElement>(`[data-stage="${focus}"]`);
    if (!item) return;
    const left = item.offsetLeft - (nav.clientWidth - item.offsetWidth) / 2;
    nav.scrollTo({ left: Math.max(0, left), behavior: "smooth" });
  }, [focus]);

  return (
    <nav
      ref={navRef}
      aria-label="Pipeline stages"
      className="relative -mx-4 overflow-x-auto px-4 pb-1 sm:mx-0 sm:px-0 sm:pb-0"
    >
      <ol className="grid min-w-[720px] grid-cols-9 gap-2">
        {STAGES.map((key) => {
          const st = state.stages[key];
          const active = st.status === "running" || st.status === "waiting";
          const cached = stageCached(state, key);
          const sub = subText(key, st, state);
          return (
            <li
              key={key}
              data-stage={key}
              title={cached ? `${hint(key, state)}. ${CACHED_HINT}` : hint(key, state)}
              className="min-w-0"
            >
              <div className={cx("h-1 rounded-full", BAR[st.status])} />
              <div className="mt-2 flex items-center gap-1.5">
                <span className="flex h-4 w-4 items-center justify-center">
                  <StageIcon status={st.status} />
                </span>
                <span
                  className={cx(
                    "truncate text-xs font-medium",
                    active ? "text-zinc-900" : st.status === "pending" || st.status === "skipped" ? "text-zinc-400" : "text-zinc-700",
                  )}
                >
                  {LABELS[key].label}
                </span>
              </div>
              <div className="mt-0.5 h-4 truncate pl-[22px] text-[11px] tabular-nums text-zinc-500">
                {sub.text}
                {sub.wide && <span className="hidden sm:inline">{sub.wide}</span>}
              </div>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
