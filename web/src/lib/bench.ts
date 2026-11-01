/**
 * bench/results.json (written by bench/run_bench.py) -> benchmark table rows.
 *
 * The file is {"tasks": {<task_id>: {"video": Entry, "sentence": Entry}}, "updated_at":
 * "YYYY-MM-DD HH:MM:SS", "n_seeds": 20}. Entry is {job_id, status, error, repairs, attempts:
 * [{n, passed, total, diagnosis}], cost_usd, wall_s, agreement_pct (0-100 or null), spec,
 * final_program, ref_first: [k, n], ref_final: [k, n], ...}, or {status: "missing_video", video}
 * when the demo video was not recorded. The flat cells follow run_bench.table_rows, so the page
 * shows what bench/results.md shows; `video` and `sentence` keep each condition's details.
 */

import type { BenchCondition, BenchResults, BenchRow, PassValue, Rule, Selector, TaskSpec } from "../types";

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

/** An attempt's own pass count ({n, passed, total}), scored on the run's own spec. */
function ownRate(attempt: unknown): PassValue {
  if (!isObj(attempt)) return null;
  const passed = finite(attempt.passed);
  const total = finite(attempt.total);
  return passed != null && total != null ? { passed, total } : null;
}

function condition(entry: Json | null): BenchCondition | null {
  if (!entry) return null;
  const attempts = Array.isArray(entry.attempts) ? entry.attempts : [];
  return {
    status: typeof entry.status === "string" ? entry.status : null,
    job_id: typeof entry.job_id === "string" ? entry.job_id : null,
    ref_first: rate(entry, "ref_first"),
    ref_final: rate(entry, "ref_final"),
    own_first: ownRate(attempts[0]),
    own_final: ownRate(attempts[attempts.length - 1]),
    repairs: finite(entry.repairs),
    cost_usd: finite(entry.cost_usd),
    wall_s: finite(entry.wall_s),
    agreement_pct: finite(entry.agreement_pct),
    spec: isObj(entry.spec) ? (entry.spec as unknown as TaskSpec) : null,
    final_program: typeof entry.final_program === "string" ? entry.final_program : null,
  };
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
    video: condition(video),
    sentence: condition(sentence),
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

/** A condition that actually ran (it has a result, not "no video"). */
export function conditionRan(c: BenchCondition | null | undefined): c is BenchCondition {
  return c != null && c.status !== "missing_video";
}

// ------------------------------------------------------------------ comparing runs
function selectorKey(s: Selector | null | undefined): string {
  return JSON.stringify([s?.size ?? null, s?.color ?? null, s?.shape ?? null, s?.fragile ?? null]);
}

function ruleKey(r: Rule): string {
  return JSON.stringify([selectorKey(r.select), r.leave ? null : (r.place_in ?? null), Boolean(r.leave)]);
}

/** Same rules and checks (selectors, bins, kinds, ids); wording and rationale are ignored. */
export function sameSpec(a: TaskSpec | null | undefined, b: TaskSpec | null | undefined): boolean {
  if (!a || !b) return false;
  const checks = (s: TaskSpec) =>
    s.checks.map((c) => JSON.stringify([c.id, c.kind, selectorKey(c.select), c.kind === "in_bin" ? (c.bin ?? null) : null]));
  return (
    JSON.stringify(a.rules.map(ruleKey)) === JSON.stringify(b.rules.map(ruleKey)) &&
    JSON.stringify(checks(a)) === JSON.stringify(checks(b))
  );
}

/** Same program text, ignoring trailing whitespace. */
export function sameProgram(a: string | null | undefined, b: string | null | undefined): boolean {
  if (!a || !b) return false;
  const norm = (s: string) => s.replace(/[ \t]+$/gm, "").trim();
  return norm(a) === norm(b);
}
