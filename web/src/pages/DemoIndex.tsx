/** Recorded runs (demo bundle) and, in live mode, the jobs started from this browser. */

import { Link } from "react-router-dom";
import { recentJobs } from "../api";
import { FIXTURE_LABEL, IS_FIXTURE, LIVE_AVAILABLE } from "../config";
import { featuredRun, loadDemoBench, loadDemoRuns, type DemoRun } from "../demo";
import { useAsync } from "../hooks";
import { conditionRan } from "../lib/bench";
import { formatDateTime, formatDuration, toEpochSeconds } from "../lib/format";
import { isStandInVideo, runStory } from "../lib/provenance";
import { useMode } from "../mode";
import { normalizeStatus } from "../state";
import { PITCH, StepsStrip } from "../components/HowSteps";
import { IconArrowRight, IconPlay } from "../components/icons";
import { buttonClass, Card, Chip, EmptyState, PageLoading, StatusChip } from "../components/ui";
import { VerifiedBadge } from "../components/VerifiedBadge";

function RunCard({ run }: { run: DemoRun }) {
  const { entry, report: r } = run;
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
  const standIn = Boolean(useVideo && isStandInVideo(r?.video));
  const story = runStory(r);
  const agreement = r?.agreement;

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
        {useVideo != null &&
          (standIn ? (
            <Chip tone="warning" title="The demonstration is a stand-in clip, not a recording of a person">
              Stand-in video
            </Chip>
          ) : (
            <Chip tone="muted">{useVideo ? "Video + sentence" : "Sentence only"}</Chip>
          ))}
        {IS_FIXTURE && <Chip tone="warning">{FIXTURE_LABEL}</Chip>}
        {taskId && <span className="font-mono text-[11px] text-zinc-400">{taskId}</span>}
      </div>
      <h2 className="mt-3 text-base font-semibold leading-snug text-zinc-900">
        {entry.title && !rules ? entry.title : rules ? `“${rules}”` : entry.id}
      </h2>
      {entry.title && rules && <p className="mt-1 text-sm text-zinc-500">{entry.title}</p>}
      {entry.note && <p className="mt-1 text-xs text-zinc-500">{entry.note}</p>}
      {story && <p className="mt-2 text-sm text-zinc-700">{story}</p>}
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
        {agreement && agreement.n > 0 && (
          <li title="SAM 3.1 verdicts on the rendered clips vs simulator ground truth">
            SAM agrees{" "}
            <span className="font-medium tabular-nums text-zinc-800">
              {agreement.agree}/{agreement.n}
            </span>
          </li>
        )}
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

/** Demo mode's landing: the pitch, the four steps, and the run that shows the whole loop. */
function Hero({ featured }: { featured: DemoRun | null }) {
  const attempts = featured?.report?.attempts ?? [];
  const first = attempts[0];
  const last = attempts[attempts.length - 1];
  const repaired = attempts.length > 1 && first && last;
  return (
    <section>
      <p className="text-xs font-medium text-accent-700">Show &amp; Tell Robotics · test-driven robot programming</p>
      <h1 className="mt-2 max-w-3xl text-xl font-semibold leading-snug tracking-tight text-zinc-900 sm:text-2xl">
        {PITCH}
      </h1>
      <div className="mt-5 flex flex-wrap gap-2">
        {featured && (
          // A primary button that may wrap on phones (buttonClass never wraps).
          <Link
            to={`/replay/${encodeURIComponent(featured.entry.id)}`}
            className="inline-flex min-h-9 items-center gap-2 rounded-lg bg-accent-600 px-3.5 py-2 text-left text-sm font-medium text-white shadow-sm transition-colors hover:bg-accent-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 focus-visible:ring-offset-2"
          >
            <IconPlay size={13} className="shrink-0" />
            {repaired
              ? `Watch it fail, diagnose and fix itself (${first.passed}/${first.total} → ${last.passed}/${last.total})`
              : "Watch a recorded run"}
          </Link>
        )}
        <Link to="/bench" className={buttonClass("secondary", "md")}>
          Benchmark <IconArrowRight size={15} />
        </Link>
      </div>
      <StepsStrip className="mt-6" />
    </section>
  );
}

export function DemoIndexPage() {
  const { mode } = useMode();
  const runsData = useAsync(loadDemoRuns, []);
  const bench = useAsync(() => loadDemoBench().catch(() => null), []);
  const recent = mode === "live" ? recentJobs() : [];
  const runs = runsData.data ?? [];
  const demo = mode === "demo";
  // bench/results.json says "missing_video" for every task until the human demos are recorded.
  const benchRows = bench.data?.tasks ?? [];
  const videoPending = benchRows.length > 0 && benchRows.every((row) => !conditionRan(row.video));

  return (
    <div className="space-y-8">
      {demo && !runsData.loading && <Hero featured={featuredRun(runs)} />}

      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          {demo ? (
            <h2 className="text-base font-semibold tracking-tight text-zinc-900 sm:text-lg">Recorded runs</h2>
          ) : (
            <h1 className="text-xl font-semibold tracking-tight text-zinc-900 sm:text-2xl">Recorded runs</h1>
          )}
          <p className="mt-1.5 max-w-2xl text-sm text-zinc-600">
            Complete runs of the pipeline, replayed in the browser from their recorded events: the spec,
            every test scene and, when a scene failed, the diagnosis and the repair. No backend needed.
          </p>
          {videoPending && (
            <p className="mt-1.5 max-w-2xl text-xs text-zinc-500">
              The human demo videos are not recorded yet, so the benchmark's video + sentence condition
              has not run. Each run below says what it was made from.
            </p>
          )}
        </div>
        {LIVE_AVAILABLE && mode === "live" && (
          <Link to="/new" className={buttonClass("primary", "md")}>
            New task <IconArrowRight size={15} />
          </Link>
        )}
      </div>

      {runsData.loading ? (
        <PageLoading label="Loading recorded runs" />
      ) : runs.length === 0 ? (
        <EmptyState icon={<IconPlay size={18} />} title="No recorded runs yet">
          Finish a job, then export it with{" "}
          <code className="rounded bg-zinc-100 px-1 py-0.5 font-mono text-xs">python -m showtell export-demo --job ID</code>.
          It lands in web/public/demo/ and shows up here.
        </EmptyState>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {runs.map((run) => (
            <RunCard key={run.entry.id} run={run} />
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
