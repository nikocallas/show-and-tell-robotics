/** The pipeline in four steps, shared by the new-task page, "How it works" and the landing page. */

import { IconCamera, IconCode, IconCube, IconShieldCheck } from "./icons";
import { cx } from "./ui";

export const PITCH =
  "Show a robot a task once, state the rules in one sentence, and get back a robot program that passed 20 randomized simulated scenes.";

export const STEPS = [
  {
    icon: <IconCamera size={16} />,
    title: "Watch",
    body: "SAM 3.1 tracks the hand and every object in your demo video.",
  },
  {
    icon: <IconShieldCheck size={16} />,
    title: "Write the checks",
    body: "Muse Wonder turns the video and your sentence into rules and pass/fail checks. You approve or edit them.",
  },
  {
    icon: <IconCode size={16} />,
    title: "Write the program",
    body: "A short Python program composes tested robot skills and picks objects by attribute, never by coordinates.",
  },
  {
    icon: <IconCube size={16} />,
    title: "Test and repair",
    body: "The program runs on 20 randomized MuJoCo scenes. Failures go back to Muse Wonder with the failed checks and frames, up to 3 repairs.",
  },
];

/**
 * use_judge defaults on for app jobs (api/app.py) and `showtell run`; bench/run_bench.py turns the
 * SAM judge off for the sentence-only condition.
 */
export const JUDGING_NOTE =
  "Every scene is graded by the simulator's ground truth, which decides pass or fail. Runs started from the app or the CLI are also judged by SAM 3.1 on the rendered camera clips, and the report shows how often the two agree. The benchmark's sentence-only rows are graded by ground truth only.";

export function StepsList() {
  return (
    <ol className="space-y-4">
      {STEPS.map((s, i) => (
        <li key={s.title} className="flex gap-3">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-zinc-100 text-zinc-600">
            {s.icon}
          </span>
          <div className="min-w-0">
            <p className="text-sm font-medium text-zinc-900">
              <span className="mr-1.5 text-zinc-400">{i + 1}</span>
              {s.title}
            </p>
            <p className="mt-0.5 text-xs leading-relaxed text-zinc-500">{s.body}</p>
          </div>
        </li>
      ))}
    </ol>
  );
}

/** The steps side by side: 4 across on desktops, 2 x 2 below; phones show the titles only. */
export function StepsStrip({ className }: { className?: string }) {
  return (
    <ol className={cx("grid grid-cols-2 gap-2 sm:gap-3 lg:grid-cols-4", className)}>
      {STEPS.map((s, i) => (
        <li key={s.title} className="rounded-xl border border-zinc-200 bg-white p-3 shadow-card sm:p-4">
          <div className="flex items-center gap-2 sm:gap-2.5">
            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-zinc-100 text-zinc-600">
              {s.icon}
            </span>
            <p className="text-[13px] font-medium leading-snug text-zinc-900 sm:text-sm">
              <span className="mr-1 text-zinc-400 sm:mr-1.5">{i + 1}</span>
              {s.title}
            </p>
          </div>
          <p className="mt-2 hidden text-xs leading-relaxed text-zinc-500 sm:block">{s.body}</p>
        </li>
      ))}
    </ol>
  );
}
