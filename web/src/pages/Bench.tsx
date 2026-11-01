/** Benchmark: bench/results.json as a table (live: GET /api/bench; demo: demo/bench.json). */

import { useMemo, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { ApiError, getBench, getTasks } from "../api";
import { FIXTURE_LABEL, IS_FIXTURE } from "../config";
import { loadDemoBench, loadDemoIndex, loadDemoTasks } from "../demo";
import { useAsync } from "../hooks";
import { conditionRan } from "../lib/bench";
import {
  agreementPct,
  binWithColor,
  describeSelector,
  formatDateTime,
  formatDuration,
  formatPct,
  formatUsd,
  passParts,
  passText,
  toEpochSeconds,
} from "../lib/format";
import { useMode } from "../mode";
import type {
  BenchCondition,
  BenchResults,
  BenchRow,
  DemoIndexEntry,
  PassValue,
  Rule,
  TaskPreset,
} from "../types";
import { IconArrowRight, IconBeaker } from "../components/icons";
import { Banner, Button, cx, EmptyState, PageLoading } from "../components/ui";

const COST_HINT =
  "List price of the Meta API calls the runs used, counting responses served from the local cache at list price";

/** bench/results.md's columns, used once the video + sentence condition has results. */
const COLUMNS: { key: string; label: string; hint: string }[] = [
  { key: "task", label: "Task", hint: "Benchmark task and its rule sentence" },
  { key: "first_try_video", label: "First try (video)", hint: "Scenes passed by the first program, video + sentence" },
  { key: "final_video", label: "Final (video)", hint: "Scenes passed after repairs, video + sentence" },
  { key: "repairs", label: "Repairs", hint: "Repair rounds used by the video + sentence run (max 3)" },
  { key: "final_sentence_only", label: "Final (sentence only)", hint: "Scenes passed after repairs, sentence only (no video)" },
  { key: "judge_agreement", label: "Judge agreement", hint: "SAM 3.1 verdicts vs simulator ground truth" },
  { key: "cost_usd", label: "Cost $", hint: `${COST_HINT}; sum of the conditions that ran` },
  { key: "time_s", label: "Time", hint: "Wall-clock time of the video + sentence run" },
];

/** While only the sentence-only condition has run: its columns, nothing empty. */
const SENTENCE_COLUMNS: { key: string; label: string; hint: string }[] = [
  { key: "task", label: "Task", hint: "Benchmark task and its rule sentence" },
  { key: "first", label: "First try", hint: "Scenes passed by the first program, scored against the reference checks" },
  { key: "final", label: "Final", hint: "Scenes passed after repairs, scored against the reference checks" },
  { key: "own", label: "Own spec", hint: "Scenes passed after repairs on the run's own spec, as its run page shows them" },
  { key: "repairs", label: "Repairs", hint: "Repair rounds used (max 3)" },
  { key: "cost", label: "Cost $", hint: COST_HINT },
  { key: "time", label: "Time", hint: "Wall-clock time of the sentence-only run" },
];

function PassCell({ value, children }: { value: PassValue; children?: ReactNode }) {
  const p = passParts(value);
  const full = p != null && p.total > 0 && p.passed === p.total;
  // Words instead of a rate: "no video" (not recorded) or "error" (the run crashed).
  const word = p == null && typeof value === "string" && value !== "";
  return (
    <div>
      <div className="flex items-center gap-2.5">
        <span
          className={cx(
            "tabular-nums",
            word ? (value === "error" ? "text-rose-700" : "text-zinc-400") : "font-medium",
            !word && (full ? "text-emerald-700" : "text-zinc-900"),
          )}
        >
          {passText(value)}
        </span>
        {p && p.total > 0 && (
          <span className="h-1.5 w-14 overflow-hidden rounded-full bg-zinc-100" aria-hidden="true">
            <span
              className={cx("block h-full rounded-full", full ? "bg-emerald-500" : "bg-zinc-500")}
              style={{ width: `${(100 * p.passed) / p.total}%` }}
            />
          </span>
        )}
      </div>
      {children}
    </div>
  );
}

function num(v: number | string | null | undefined): number | null {
  if (v == null || v === "") return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

function repairsText(n: number | null): string {
  return n == null ? "" : n === 0 ? "no repair" : `${n} repair${n === 1 ? "" : "s"}`;
}

/** The run passed more scenes on its own checks than against the reference. */
function scoresLower(c: BenchCondition | null | undefined): c is BenchCondition {
  if (!conditionRan(c)) return false;
  const ref = passParts(c.ref_final);
  const own = passParts(c.own_final);
  return ref != null && own != null && ref.passed < own.passed;
}

const noteId = (task: string, condition: string) => `note-${task}-${condition}`;

function WhyLink({ target }: { target: string }) {
  return (
    <button
      type="button"
      onClick={() => document.getElementById(target)?.scrollIntoView({ behavior: "smooth", block: "center" })}
      className="mt-0.5 text-[11px] font-medium text-zinc-500 underline decoration-zinc-300 underline-offset-2 hover:text-zinc-800"
    >
      why lower?
    </button>
  );
}

/** Column totals, or null when a row has no value (a partial sum would mislead). */
function totals(
  rows: BenchRow[],
  cost: (r: BenchRow) => number | null,
  time: (r: BenchRow) => number | null,
): { totalCost: number | null; totalTime: number | null } {
  const costs = rows.map(cost);
  const times = rows.map(time);
  const totalCost = costs.every((c) => c != null) ? costs.reduce<number>((a, c) => a + (c ?? 0), 0) : null;
  const totalTime = times.every((t) => t != null) ? times.reduce<number>((a, t) => a + (t ?? 0), 0) : null;
  return { totalCost, totalTime };
}

function SentenceTable({ rows }: { rows: BenchRow[] }) {
  const { totalCost, totalTime } = totals(
    rows,
    (r) => r.sentence?.cost_usd ?? null,
    (r) => r.sentence?.wall_s ?? null,
  );
  return (
    <div className="overflow-x-auto rounded-xl border border-zinc-200 bg-white shadow-card">
      <table className="w-full min-w-[760px] text-left text-sm">
        <thead className="border-b border-zinc-200 bg-zinc-50 text-xs text-zinc-500">
          <tr>
            {SENTENCE_COLUMNS.map((c, i) => (
              <th
                key={c.key}
                title={c.hint}
                scope="col"
                className={cx("px-4 py-3 font-medium", i === 0 && "sticky left-0 z-10 bg-zinc-50")}
              >
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-100">
          {rows.map((r) => {
            const s = r.sentence;
            return (
              <tr key={r.task} className="align-top hover:bg-zinc-50/60">
                <TaskCell row={r} />
                <td className="px-4 py-3">
                  <PassCell value={s ? s.ref_first : null} />
                </td>
                <td className="px-4 py-3">
                  <PassCell value={s ? s.ref_final : null}>
                    {scoresLower(s) && <WhyLink target={noteId(r.task, "sentence")} />}
                  </PassCell>
                </td>
                <td className="px-4 py-3">
                  <PassCell value={conditionRan(s) ? s.own_final : null} />
                </td>
                <td className="px-4 py-3 tabular-nums text-zinc-900">{s?.repairs ?? "—"}</td>
                <td className="px-4 py-3 tabular-nums text-zinc-900">{formatUsd(s?.cost_usd)}</td>
                <td className="px-4 py-3 tabular-nums text-zinc-900">{formatDuration(s?.wall_s)}</td>
              </tr>
            );
          })}
        </tbody>
        {rows.length > 1 && (totalCost != null || totalTime != null) && (
          <tfoot className="border-t border-zinc-200 bg-zinc-50/70 text-xs text-zinc-600">
            <tr>
              <th scope="row" className="sticky left-0 z-10 bg-zinc-50 px-4 py-2.5 text-left font-medium">
                Total
              </th>
              <td colSpan={4} />
              <td className="px-4 py-2.5 font-medium tabular-nums">{totalCost != null ? formatUsd(totalCost) : ""}</td>
              <td className="px-4 py-2.5 font-medium tabular-nums">{totalTime != null ? formatDuration(totalTime) : ""}</td>
            </tr>
          </tfoot>
        )}
      </table>
    </div>
  );
}

function TaskCell({ row }: { row: BenchRow }) {
  return (
    <th scope="row" className="sticky left-0 z-10 max-w-[260px] bg-white px-4 py-3 font-normal">
      <span className="block font-mono text-xs font-medium text-zinc-900">{row.task}</span>
      {row.rules && <span className="mt-0.5 hidden text-xs leading-snug text-zinc-500 sm:block">“{row.rules}”</span>}
    </th>
  );
}

function BenchTable({ rows }: { rows: BenchRow[] }) {
  const { totalCost, totalTime } = totals(
    rows,
    (r) => num(r.cost_usd),
    (r) => num(r.time_s),
  );

  return (
    <div className="overflow-x-auto rounded-xl border border-zinc-200 bg-white shadow-card">
      <table className="w-full min-w-[920px] text-left text-sm">
        <thead className="border-b border-zinc-200 bg-zinc-50 text-xs text-zinc-500">
          <tr>
            {COLUMNS.map((c, i) => (
              <th
                key={c.key}
                title={c.hint}
                scope="col"
                className={cx("px-4 py-3 font-medium", i === 0 && "sticky left-0 z-10 bg-zinc-50")}
              >
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-100">
          {rows.map((r) => {
            const repairs = num(r.repairs);
            const s = r.sentence;
            return (
              <tr key={r.task} className="align-top hover:bg-zinc-50/60">
                <TaskCell row={r} />
                <td className="px-4 py-3">
                  <PassCell value={r.first_try_video} />
                </td>
                <td className="px-4 py-3">
                  <PassCell value={r.final_video}>
                    {scoresLower(r.video) && <WhyLink target={noteId(r.task, "video")} />}
                  </PassCell>
                </td>
                <td className="px-4 py-3 tabular-nums text-zinc-900">{repairs ?? (r.repairs || "—")}</td>
                <td className="px-4 py-3">
                  <PassCell value={r.final_sentence_only}>
                    {conditionRan(s) && passParts(s.own_final) && (
                      <span className="mt-0.5 block text-[11px] text-zinc-500">
                        own spec {passText(s.own_final)}
                        {s.repairs != null ? ` · ${repairsText(s.repairs)}` : ""}
                      </span>
                    )}
                    {scoresLower(s) && <WhyLink target={noteId(r.task, "sentence")} />}
                  </PassCell>
                </td>
                <td className="px-4 py-3 tabular-nums text-zinc-900">
                  {formatPct(agreementPct(r.judge_agreement))}
                </td>
                <td className="px-4 py-3 tabular-nums text-zinc-900">{formatUsd(r.cost_usd)}</td>
                <td className="px-4 py-3 tabular-nums text-zinc-900">{formatDuration(num(r.time_s))}</td>
              </tr>
            );
          })}
        </tbody>
        {rows.length > 1 && (totalCost != null || totalTime != null) && (
          <tfoot className="border-t border-zinc-200 bg-zinc-50/70 text-xs text-zinc-600">
            <tr>
              <th scope="row" className="sticky left-0 z-10 bg-zinc-50 px-4 py-2.5 text-left font-medium">
                Total
              </th>
              <td colSpan={5} />
              <td className="px-4 py-2.5 font-medium tabular-nums">{totalCost != null ? formatUsd(totalCost) : ""}</td>
              <td className="px-4 py-2.5 font-medium tabular-nums">{totalTime != null ? formatDuration(totalTime) : ""}</td>
            </tr>
          </tfoot>
        )}
      </table>
    </div>
  );
}

function ruleShort(rule: Rule): string {
  const what = describeSelector(rule.select);
  return rule.place_in && !rule.leave ? `${what} → ${binWithColor(rule.place_in)}` : `${what} stay put`;
}

/** Rows whose run passes its own checks but fewer reference checks: the two specs side by side. */
function LowerNotes({
  rows,
  presets,
  runs,
}: {
  rows: BenchRow[];
  presets: TaskPreset[];
  runs: DemoIndexEntry[];
}) {
  const notes = rows.flatMap((r) =>
    (["video", "sentence"] as const)
      .filter((k) => scoresLower(r[k]))
      .map((k) => ({ row: r, key: k, run: r[k] as BenchCondition })),
  );
  if (!notes.length) return null;
  return (
    <section className="space-y-3">
      <div>
        <h2 className="text-sm font-semibold text-zinc-900">Why some runs score lower here than on their own page</h2>
        <p className="mt-0.5 max-w-3xl text-xs text-zinc-500">
          Each run's own spec decides pass and fail on its run page, where it can be VERIFIED. This table
          scores the same programs against the reference checks, which encode the human demo.
        </p>
      </div>
      <ul className="grid gap-3 lg:grid-cols-2">
        {notes.map(({ row, key, run }) => {
          const preset = presets.find((p) => p.id === row.task);
          const recorded = runs.find((e) => e.task_id === row.task && e.use_video === (key === "video"));
          return (
            <li
              key={`${row.task}-${key}`}
              id={noteId(row.task, key)}
              className="scroll-mt-24 rounded-xl border border-zinc-200 bg-white p-4 text-sm shadow-card"
            >
              <p className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                <span className="font-mono text-xs font-medium text-zinc-900">{row.task}</span>
                <span className="text-xs text-zinc-500">{key === "video" ? "video + sentence" : "sentence only"}</span>
              </p>
              <p className="mt-1.5 text-zinc-800">
                Own spec <span className="font-medium tabular-nums text-emerald-700">{passText(run.own_final)}</span>
                {run.repairs ? ` after ${repairsText(run.repairs)}` : ""}; against the reference{" "}
                <span className="font-medium tabular-nums text-rose-700">{passText(run.ref_final)}</span>.
              </p>
              <dl className="mt-2.5 space-y-2 text-xs">
                {run.spec && run.spec.rules.length > 0 && (
                  <div>
                    <dt className="font-medium text-zinc-500">
                      {key === "video" ? "Spec written from the video and the sentence" : "Spec written from the sentence alone"}
                    </dt>
                    <dd className="mt-0.5 text-zinc-800">{run.spec.rules.map(ruleShort).join(" · ")}</dd>
                  </div>
                )}
                {preset?.demo && (
                  <div>
                    <dt className="font-medium text-zinc-500">What the human demo shows (the reference)</dt>
                    <dd className="mt-0.5 text-zinc-800">{preset.demo}</dd>
                  </div>
                )}
              </dl>
              {recorded && (
                <Link
                  to={`/replay/${encodeURIComponent(recorded.id)}`}
                  className="mt-3 inline-flex items-center gap-1 text-xs font-medium text-accent-700 hover:text-accent-800"
                >
                  Replay a recorded run of this task <IconArrowRight size={12} />
                </Link>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

export function BenchPage() {
  const { mode } = useMode();
  const bench = useAsync<BenchResults | null>(
    () =>
      mode === "live"
        ? getBench().catch((e: unknown) => {
            if (e instanceof ApiError && e.notFound) return null;
            throw e;
          })
        : loadDemoBench(),
    [mode],
  );
  // results.json has no rule sentences: take them from the task presets when available.
  const presets = useAsync<TaskPreset[]>(
    () => (mode === "live" ? getTasks() : loadDemoTasks()).catch(() => []),
    [mode],
  );
  // Recorded runs ship with the build in both modes.
  const runs = useAsync<DemoIndexEntry[]>(() => loadDemoIndex().catch(() => []), []);
  const data = useMemo<BenchResults | null | undefined>(() => {
    const raw = bench.data;
    if (!raw) return raw;
    const rules = new Map((presets.data ?? []).map((p) => [p.id, p.rules]));
    return { ...raw, tasks: raw.tasks.map((r) => ({ ...r, rules: r.rules ?? rules.get(r.task) })) };
  }, [bench.data, presets.data]);
  const generated = toEpochSeconds(data?.generated_at ?? null);
  const rows = data?.tasks ?? [];
  const videoRan = rows.some((r) => conditionRan(r.video));
  const videoMissing = rows.length > 0 && rows.every((r) => r.video?.status === "missing_video");

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-zinc-900 sm:text-2xl">Benchmark</h1>
        <p className="mt-1.5 max-w-3xl text-sm text-zinc-600">
          Each task runs on the same fixed seeds in two conditions: the demo video plus the sentence, and
          the sentence alone. A scene passes only if every reference check for the task
          (bench/tasks.yaml) passes in the simulator.
        </p>
      </div>

      {IS_FIXTURE && (
        <Banner tone="warning" title={FIXTURE_LABEL}>
          These rows come from web/dev-fixtures/bench.json for UI development; they are not results.
        </Banner>
      )}

      {bench.loading ? (
        <PageLoading label="Loading results" />
      ) : bench.error ? (
        <EmptyState
          icon={<IconBeaker size={20} />}
          title="Can't load the benchmark"
          action={
            <Button size="sm" onClick={bench.reload}>
              Try again
            </Button>
          }
        >
          {bench.error instanceof Error ? bench.error.message : String(bench.error)}
        </EmptyState>
      ) : !data || !rows.length ? (
        <EmptyState icon={<IconBeaker size={20} />} title="No benchmark results yet">
          Run <code className="rounded bg-zinc-100 px-1 py-0.5 font-mono text-xs">python -m showtell bench</code> to
          write bench/results.json
          {mode === "demo" ? "; export-demo then copies it to web/public/demo/bench.json" : ""}.
        </EmptyState>
      ) : (
        <>
          {!videoRan && (
            <Banner tone="info" title="Only the sentence-only condition has run">
              {videoMissing
                ? "The video + sentence condition is waiting for the human demo videos, which are not recorded yet. "
                : "The video + sentence condition has no results yet. "}
              The table shows the sentence-only ablation: Muse Wonder writes the spec from the sentence
              alone, without any video.
            </Banner>
          )}
          {videoRan ? <BenchTable rows={rows} /> : <SentenceTable rows={rows} />}
          <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs text-zinc-500">
            {data.n_seeds ? <span>{data.n_seeds} seeds per attempt</span> : null}
            {generated != null && <span>Updated {formatDateTime(generated)}</span>}
            <span className="max-w-4xl">
              Pass rates are scenes passed, scored against each task's reference checks (bench/tasks.yaml),
              which encode what the human demo shows. First try is the first generated program; Final is
              after up to 3 repairs. The sentence-only condition runs without the SAM judge. A run can pass
              every check of its own spec, and show VERIFIED on its run page, while scoring lower here:
              written without the video, its spec can put objects in different bins than the demo. The
              own-spec score is each run's result on its own checks.
            </span>
          </div>
          <LowerNotes rows={rows} presets={presets.data ?? []} runs={runs.data ?? []} />
        </>
      )}
    </div>
  );
}
