/** Benchmark: bench/results.json as a table (live: GET /api/bench; demo: demo/bench.json). */

import { useMemo } from "react";
import { ApiError, getBench, getTasks } from "../api";
import { FIXTURE_LABEL, IS_FIXTURE } from "../config";
import { loadDemoBench, loadDemoTasks } from "../demo";
import { useAsync } from "../hooks";
import {
  agreementPct,
  formatDateTime,
  formatDuration,
  formatPct,
  formatUsd,
  passParts,
  passText,
  toEpochSeconds,
} from "../lib/format";
import { useMode } from "../mode";
import type { BenchResults, BenchRow, PassValue, TaskPreset } from "../types";
import { IconBeaker } from "../components/icons";
import { Banner, Button, cx, EmptyState, PageLoading } from "../components/ui";

const COLUMNS: { key: keyof BenchRow | "task"; label: string; hint: string }[] = [
  { key: "task", label: "Task", hint: "Benchmark task and its rule sentence" },
  { key: "first_try_video", label: "First try (video)", hint: "Scenes passed by the first program, video + sentence" },
  { key: "final_video", label: "Final (video)", hint: "Scenes passed after repairs, video + sentence" },
  { key: "repairs", label: "Repairs", hint: "Repair rounds used (max 3)" },
  { key: "final_sentence_only", label: "Final (sentence only)", hint: "Scenes passed after repairs, sentence only (no video)" },
  { key: "judge_agreement", label: "Judge agreement", hint: "SAM 3.1 verdicts vs simulator ground truth" },
  { key: "cost_usd", label: "Cost $", hint: "Meta Model API spend, both conditions, at list price" },
  { key: "time_s", label: "Time", hint: "Wall-clock time of the video + sentence run" },
];

function PassCell({ value }: { value: PassValue }) {
  const p = passParts(value);
  const full = p != null && p.total > 0 && p.passed === p.total;
  // Words instead of a rate: "no video" (not recorded) or "error" (the run crashed).
  const word = p == null && typeof value === "string" && value !== "";
  return (
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
  );
}

function num(v: number | string | null | undefined): number | null {
  if (v == null || v === "") return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

function BenchTable({ data }: { data: BenchResults }) {
  const rows = data.tasks ?? [];
  const costs = rows.map((r) => num(r.cost_usd));
  const times = rows.map((r) => num(r.time_s));
  const totalCost = costs.every((c) => c != null) ? costs.reduce<number>((a, c) => a + (c ?? 0), 0) : null;
  const totalTime = times.every((t) => t != null) ? times.reduce<number>((a, t) => a + (t ?? 0), 0) : null;

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
            return (
              <tr key={r.task} className="align-top hover:bg-zinc-50/60">
                <th scope="row" className="sticky left-0 z-10 max-w-[260px] bg-white px-4 py-3 font-normal">
                  <span className="block font-mono text-xs font-medium text-zinc-900">{r.task}</span>
                  {r.rules && (
                    <span className="mt-0.5 hidden text-xs leading-snug text-zinc-500 sm:block">“{r.rules}”</span>
                  )}
                </th>
                <td className="px-4 py-3">
                  <PassCell value={r.first_try_video} />
                </td>
                <td className="px-4 py-3">
                  <PassCell value={r.final_video} />
                </td>
                <td className="px-4 py-3 tabular-nums text-zinc-900">{repairs ?? (r.repairs || "—")}</td>
                <td className="px-4 py-3">
                  <PassCell value={r.final_sentence_only} />
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
  const data = useMemo<BenchResults | null | undefined>(() => {
    const raw = bench.data;
    if (!raw) return raw;
    const rules = new Map((presets.data ?? []).map((p) => [p.id, p.rules]));
    return { ...raw, tasks: raw.tasks.map((r) => ({ ...r, rules: r.rules ?? rules.get(r.task) })) };
  }, [bench.data, presets.data]);
  const generated = toEpochSeconds(data?.generated_at ?? null);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-zinc-900 sm:text-2xl">Benchmark</h1>
        <p className="mt-1.5 max-w-3xl text-sm text-zinc-600">
          Six tasks, each run twice on the same fixed seeds: once from the demo video and the sentence,
          once from the sentence alone. A scene passes only if every ground-truth check passes.
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
      ) : !data || !data.tasks?.length ? (
        <EmptyState icon={<IconBeaker size={20} />} title="No benchmark results yet">
          Run <code className="rounded bg-zinc-100 px-1 py-0.5 font-mono text-xs">python -m showtell bench</code> to
          write bench/results.json
          {mode === "demo" ? "; export-demo then copies it to web/public/demo/bench.json" : ""}.
        </EmptyState>
      ) : (
        <>
          <BenchTable data={data} />
          <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs text-zinc-500">
            {data.n_seeds ? <span>{data.n_seeds} seeds per attempt</span> : null}
            {generated != null && <span>Updated {formatDateTime(generated)}</span>}
            <span>
              Pass rates are scenes passed, scored against each task's reference checks (bench/tasks.yaml).
              First try is the first generated program; Final is after up to 3 repairs.
            </span>
          </div>
        </>
      )}
    </div>
  );
}
