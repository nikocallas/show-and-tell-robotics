/** Recorded runs (demo bundle) and, in live mode, the jobs started from this browser. */

import { Link } from "react-router-dom";
import { recentJobs } from "../api";
import { FIXTURE_LABEL, IS_FIXTURE, LIVE_AVAILABLE } from "../config";
import { loadDemoIndex, loadDemoReport } from "../demo";
import { useAsync } from "../hooks";
import { formatDateTime, formatDuration, toEpochSeconds } from "../lib/format";
import { useMode } from "../mode";
import { normalizeStatus } from "../state";
import type { DemoIndexEntry, JobReport } from "../types";
import { IconArrowRight, IconPlay } from "../components/icons";
import { buttonClass, Card, Chip, EmptyState, PageLoading, StatusChip } from "../components/ui";
import { VerifiedBadge } from "../components/VerifiedBadge";

function RunCard({ entry }: { entry: DemoIndexEntry }) {
  // Index entries may be minimal ({id}); fill the gaps from the run's report. export_demo's entries
  // carry passed/total but not the first try or the wall time, so their report is read too.
  const needsReport = !entry.rules || entry.final_total == null || !entry.status;
  const report = useAsync<JobReport | null>(
    () => (needsReport ? loadDemoReport(entry.id) : Promise.resolve(null)),
    [entry.id, needsReport],
  );
  const r = report.data;
  const rules = entry.rules ?? r?.rules ?? "";
  const status = normalizeStatus(entry.status ?? r?.status, "failed");
  const passed = entry.final_passed ?? entry.passed ?? r?.final_passed;
  const total = entry.final_total ?? entry.total ?? r?.final_total;
  const attempts = entry.attempts ?? r?.attempts.length;
  const firstTry = r?.attempts[0];
  const created = toEpochSeconds(entry.created_at ?? r?.created_at ?? null);
  const wall = entry.wall_seconds ?? r?.wall_seconds;
  const taskId = entry.task_id ?? r?.task_id;
  const useVideo = entry.use_video ?? r?.use_video;

  return (
    <Link
      to={`/replay/${encodeURIComponent(entry.id)}`}
      className="group block rounded-xl border border-zinc-200 bg-white p-5 shadow-card transition hover:border-zinc-300 hover:shadow-pop focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500"
    >
      <div className="flex flex-wrap items-center gap-2">
        {status === "verified" && passed != null && total != null ? (
          <VerifiedBadge passed={passed} total={total} compact />
        ) : (
          <StatusChip status={status} />
        )}
        {IS_FIXTURE && <Chip tone="warning">{FIXTURE_LABEL}</Chip>}
        {taskId && <span className="font-mono text-[11px] text-zinc-400">{taskId}</span>}
      </div>
      <h2 className="mt-3 text-base font-semibold leading-snug text-zinc-900">
        {entry.title && !rules ? entry.title : rules ? `“${rules}”` : entry.id}
      </h2>
      {entry.title && rules && <p className="mt-1 text-sm text-zinc-500">{entry.title}</p>}
      <ul className="mt-4 flex flex-wrap gap-x-5 gap-y-1 text-xs text-zinc-500">
        {firstTry && (
          <li>
            First try{" "}
            <span className="font-medium tabular-nums text-zinc-800">
              {firstTry.passed}/{firstTry.total}
            </span>
          </li>
        )}
        {passed != null && total != null && (
          <li>
            Final <span className="font-medium tabular-nums text-zinc-800">{passed}/{total}</span>
          </li>
        )}
        {attempts != null && (
          <li>
            {attempts} attempt{attempts === 1 ? "" : "s"}
          </li>
        )}
        {useVideo != null && <li>{useVideo ? "Video + sentence" : "Sentence only"}</li>}
        {wall ? <li>{formatDuration(wall)}</li> : null}
        {created != null && <li>{formatDateTime(created)}</li>}
      </ul>
      <span className="mt-4 inline-flex items-center gap-1.5 text-sm font-medium text-accent-700">
        <IconPlay size={13} /> Replay
        <IconArrowRight size={14} className="transition-transform group-hover:translate-x-0.5" />
      </span>
    </Link>
  );
}

export function DemoIndexPage() {
  const { mode } = useMode();
  const index = useAsync(loadDemoIndex, []);
  const recent = mode === "live" ? recentJobs() : [];
  const runs = index.data ?? [];

  return (
    <div className="space-y-8">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-zinc-900 sm:text-2xl">Recorded runs</h1>
          <p className="mt-1.5 max-w-2xl text-sm text-zinc-600">
            Complete runs of the pipeline, replayed in the browser with their real events: the spec, every
            test scene, the failures, the diagnosis and the repair. No backend needed.
          </p>
        </div>
        {LIVE_AVAILABLE && mode === "live" && (
          <Link to="/new" className={buttonClass("primary", "md")}>
            New task <IconArrowRight size={15} />
          </Link>
        )}
      </div>

      {index.loading ? (
        <PageLoading label="Loading recorded runs" />
      ) : runs.length === 0 ? (
        <EmptyState icon={<IconPlay size={18} />} title="No recorded runs yet">
          Finish a job, then export it with{" "}
          <code className="rounded bg-zinc-100 px-1 py-0.5 font-mono text-xs">python -m showtell export-demo --job ID</code>.
          It lands in web/public/demo/ and shows up here.
        </EmptyState>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {runs.map((e) => (
            <RunCard key={e.id} entry={e} />
          ))}
        </div>
      )}

      {recent.length > 0 && (
        <Card>
          <div className="px-4 py-5 sm:px-6">
            <h2 className="text-sm font-semibold text-zinc-900">Your recent jobs</h2>
            <p className="mt-0.5 text-xs text-zinc-500">Started from this browser on the live backend</p>
            <ul className="mt-3 divide-y divide-zinc-100">
              {recent.map((j) => (
                <li key={j.id}>
                  <Link
                    to={`/jobs/${encodeURIComponent(j.id)}`}
                    className="flex items-center gap-3 py-2.5 text-sm hover:text-accent-700"
                  >
                    <span className="min-w-0 flex-1 truncate text-zinc-800">{j.rules}</span>
                    <span className="shrink-0 font-mono text-[11px] text-zinc-400">{j.id}</span>
                    <span className="hidden shrink-0 text-xs text-zinc-400 sm:inline">{formatDateTime(j.createdAt)}</span>
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        </Card>
      )}
    </div>
  );
}
