/**
 * bench/results.json (written by bench/run_bench.py) -> benchmark table rows.
 *
 * The file is {"tasks": {<task_id>: {"video": Entry, "sentence": Entry}}, "updated_at":
 * "YYYY-MM-DD HH:MM:SS", "n_seeds": 20}. Entry is {status, error, repairs, cost_usd, wall_s,
 * agreement_pct (0-100 or null), ref_first: [k, n], ref_final: [k, n], ...}, or
 * {status: "missing_video", video} when the demo video was not recorded. The cells follow
 * run_bench.table_rows, so the page shows what bench/results.md shows.
 */

import type { BenchResults, BenchRow, PassValue } from "../types";

type Json = Record<string, unknown>;

const isObj = (v: unknown): v is Json => v !== null && typeof v === "object" && !Array.isArray(v);
const finite = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;

/** run_bench._rate: "no video" / "error" from the status, else the [passed, total] pair. */
function rate(entry: Json | null, key: "ref_first" | "ref_final"): PassValue {
  if (!entry) return null;
  if (entry.status === "missing_video") return "no video";
  if (entry.status === "error") return "error";
  const pair = entry[key];
  if (!Array.isArray(pair) || pair.length !== 2) return null;
  const passed = finite(pair[0]);
  const total = finite(pair[1]);
  return passed != null && total != null ? { passed, total } : null;
}

/** One table row from a task's two conditions (run_bench.table_rows). */
export function benchRow(task: string, conditions: Json): BenchRow {
  const video = isObj(conditions.video) ? conditions.video : null;
  const sentence = isObj(conditions.sentence) ? conditions.sentence : null;
  const present = [video, sentence].filter((c): c is Json => c != null);
  const agreement = video ? finite(video.agreement_pct) : null;
  return {
    task,
    first_try_video: rate(video, "ref_first"),
    final_video: rate(video, "ref_final"),
    repairs: video ? finite(video.repairs) : null,
    final_sentence_only: rate(sentence, "ref_final"),
    // agreement_pct is already 0-100; the "%" keeps 1 (= 1%) from reading as a fraction.
    judge_agreement: agreement != null ? `${agreement}%` : null,
    cost_usd: present.length
      ? present.reduce((sum, c) => sum + (finite(c.cost_usd) ?? 0), 0)
      : null,
    time_s: video ? finite(video.wall_s) || null : null,
  };
}

/** The table, or null when `raw` is not a results file. Rows keep the file's task order. */
export function normalizeBench(raw: unknown): BenchResults | null {
  if (!isObj(raw) || !isObj(raw.tasks)) return null;
  const updated = raw.updated_at;
  return {
    tasks: Object.entries(raw.tasks)
      .filter((entry): entry is [string, Json] => isObj(entry[1]))
      .map(([task, conditions]) => benchRow(task, conditions)),
    generated_at: typeof updated === "string" || typeof updated === "number" ? updated : null,
    n_seeds: finite(raw.n_seeds),
  };
}
