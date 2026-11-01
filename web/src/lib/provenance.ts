/** How a run was made, derived from its own data: stand-in videos, one-line stories. */

import type { JobReport } from "../types";

/** Last path segment ("input/standin_task03_overhead.mp4" -> "standin_task03_overhead.mp4"). */
export function fileName(path: string | null | undefined): string {
  if (!path) return "";
  const clean = path.split(/[?#]/)[0];
  return decodeURIComponent(clean.slice(clean.lastIndexOf("/") + 1));
}

/**
 * A demonstration video named as a stand-in ("standin_...", e.g. a simulator render used to test
 * the video path before the human demos are filmed), not a recording of a person.
 */
export function isStandInVideo(path: string | null | undefined): boolean {
  return /^stand[-_]?in[-_.]/i.test(fileName(path));
}

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

/** "1 scene failed → Muse Wonder diagnosed it → repaired → 20/20", from the report's attempts. */
export function runStory(report: JobReport | null | undefined): string | null {
  const attempts = report?.attempts ?? [];
  if (!report || attempts.length === 0) return null;
  const first = attempts[0];
  const last = attempts[attempts.length - 1];
  const verified = report.status === "verified";
  if (attempts.length === 1) {
    if (verified) return `Passed ${first.passed}/${first.total} scenes on the first try, no repair needed`;
    return `First try ${first.passed}/${first.total} scenes passed`;
  }
  const failed = first.total - first.passed;
  const repairs = attempts.length - 1;
  const diagnosed = failed === 1 ? "Muse Wonder diagnosed it" : "Muse Wonder diagnosed them";
  const repaired = repairs === 1 ? "repaired" : `${repairs} repairs`;
  return `${plural(failed, "scene")} failed → ${diagnosed} → ${repaired} → ${last.passed}/${last.total}`;
}
