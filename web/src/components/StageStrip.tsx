/** The pipeline as a stepper: prep, discover, track, spec, program, verify, judge, repair, report. */

import { formatDuration } from "../lib/format";
import { attemptCounts, lastAttempt, STAGES, type JobState, type StageKey, type StageState } from "../state";
import { IconAlert, IconCheck, IconMinus, Spinner } from "./icons";
import { cx } from "./ui";

const LABELS: Record<StageKey, { label: string; hint: string }> = {
  prep: { label: "Prep", hint: "Normalize the demo video and pick keyframes" },
  discover: { label: "Discover", hint: "Muse Wonder lists the objects in the video" },
  track: { label: "Track", hint: "SAM 3.1 tracks the hand and every object" },
  spec: { label: "Spec", hint: "Rules and pass/fail checks, approved by you" },
  program: { label: "Program", hint: "Muse Wonder writes the robot program" },
  verify: { label: "Verify", hint: "Run the program on randomized simulated scenes" },
  judge: { label: "Judge", hint: "SAM 3.1 checks the rendered scenes" },
  repair: { label: "Repair", hint: "Diagnose failures and patch the program" },
  report: { label: "Report", hint: "Test report and full attempt log" },
};

const BAR: Record<StageState["status"], string> = {
  pending: "bg-zinc-200",
  running: "bg-accent-500 animate-pulse-soft",
  waiting: "bg-amber-400 animate-pulse-soft",
  done: "bg-zinc-800",
  skipped: "bg-[repeating-linear-gradient(90deg,#d4d4d8_0_4px,transparent_4px_8px)]",
  error: "bg-rose-500",
};

function subText(key: StageKey, st: StageState, state: JobState): string {
  const last = lastAttempt(state);
  switch (st.status) {
    case "pending":
      return "";
    case "skipped":
      return "Skipped";
    case "error":
      return "Failed";
    case "waiting":
      return "Needs approval";
    case "running": {
      if (key === "verify" && last) {
        const c = attemptCounts(last, state.nSeeds);
        return `${c.done}/${c.total} scenes`;
      }
      if (key === "judge" && last) {
        const judged = Object.keys(last.sam).length;
        return `${judged}/${last.seeds.length || state.nSeeds} judged`;
      }
      if (key === "repair" && st.attempt != null) return `Round ${st.attempt}`;
      return "Running";
    }
    case "done": {
      const t = st.seconds > 0 ? formatDuration(st.seconds) : "Done";
      const rounds = state.attempts.filter((a) => a.kind === "repair").length;
      if (key === "repair" && rounds > 0) return `${rounds} round${rounds === 1 ? "" : "s"}`;
      if ((key === "verify" || key === "judge") && state.attempts.length > 1)
        return `${t} total`;
      return t;
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

export function StageStrip({ state }: { state: JobState }) {
  return (
    <nav aria-label="Pipeline stages" className="-mx-4 overflow-x-auto px-4 pb-1 sm:mx-0 sm:px-0 sm:pb-0">
      <ol className="grid min-w-[720px] grid-cols-9 gap-2">
        {STAGES.map((key) => {
          const st = state.stages[key];
          const active = st.status === "running" || st.status === "waiting";
          return (
            <li key={key} title={LABELS[key].hint} className="min-w-0">
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
                {subText(key, st, state)}
              </div>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
