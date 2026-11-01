/** Job pages: a live job (snapshot + SSE) and a recorded run replayed from the demo bundle. */

import { useRef } from "react";
import { Link, useParams } from "react-router-dom";
import { downloadUrl } from "../api";
import { backendLabel, FIXTURE_LABEL, IS_FIXTURE, LIVE_AVAILABLE } from "../config";
import { demoUrl } from "../demo";
import { useLiveJob, useReplayJob } from "../hooks";
import { saveText } from "../lib/download";
import { useMode } from "../mode";
import { isTerminal, lastAttempt } from "../state";
import { JobView, type DownloadLink } from "../components/JobView";
import { ReplayControls } from "../components/ReplayControls";
import { IconAlert, IconVideo } from "../components/icons";
import { Banner, Button, buttonClass, EmptyState, PageLoading } from "../components/ui";

export function JobPage() {
  const { id = "" } = useParams();
  const { setMode } = useMode();
  const live = useLiveJob(id);

  if (live.loading) return <PageLoading label="Loading job" />;
  if (!live.state) {
    const err = live.error;
    if (err?.notFound) {
      return (
        <EmptyState
          icon={<IconAlert size={20} />}
          title="Job not found"
          action={
            <Link to="/new" className={buttonClass("primary", "sm")}>
              Start a new task
            </Link>
          }
        >
          The backend has no job <span className="font-mono">{id}</span>.
        </EmptyState>
      );
    }
    return (
      <EmptyState
        icon={<IconAlert size={20} />}
        title="Can't load this job"
        action={
          <>
            <Button size="sm" onClick={live.retry}>
              Try again
            </Button>
            <Link to="/runs" onClick={() => setMode("demo")} className={buttonClass("primary", "sm")}>
              Watch a recorded run
            </Link>
          </>
        }
      >
        {err?.message ?? `The backend (${backendLabel()}) did not answer.`}
        {!LIVE_AVAILABLE && " This deployment has no live backend configured."}
      </EmptyState>
    );
  }

  const s = live.state;
  const terminal = isTerminal(s.status);
  const hasProgram = s.attempts.some((a) => a.program);
  const downloads: DownloadLink[] = [
    { label: "report.md", href: downloadUrl(id, "report.md"), disabled: !terminal },
    { label: "report.json", href: downloadUrl(id, "report.json"), disabled: !terminal },
    {
      label: "program.py",
      href: downloadUrl(id, "program.py"),
      disabled: !hasProgram,
      title: terminal ? "Final program" : "Latest program",
    },
  ];
  const banner =
    !terminal && live.stream === "reconnecting" ? (
      <Banner tone="neutral">Reconnecting to the live event stream…</Banner>
    ) : !terminal && live.stream === "failed" ? (
      <Banner tone="warning" title="Live updates unavailable">
        The event stream could not connect; this page refreshes every few seconds instead.
      </Banner>
    ) : s.awaitingApproval ? (
      <Banner tone="warning" title="Your turn">
        Review the task spec below. The program is written and tested only after you approve it.
      </Banner>
    ) : null;

  return (
    <JobView
      state={s}
      media={live.media}
      source="live"
      downloads={downloads}
      onApprove={live.approve}
      banner={banner}
      back={{ label: "New task", to: "/new" }}
    />
  );
}

export function ReplayPage() {
  const { id = "" } = useParams();
  const r = useReplayJob(id);
  // The replay waits while a scene is open (its controls sit under the dialog), then carries on.
  const playingRef = useRef(r.playing);
  playingRef.current = r.playing;
  const resumeOnClose = useRef(false);
  const onModalChange = (open: boolean) => {
    if (open) {
      resumeOnClose.current = playingRef.current;
      if (playingRef.current) r.pause();
    } else if (resumeOnClose.current) {
      resumeOnClose.current = false;
      r.play();
    }
  };

  if (r.loading) return <PageLoading label="Loading recorded run" />;
  if (r.notFound || !r.state) {
    return (
      <EmptyState
        icon={<IconVideo size={20} />}
        title="Recorded run not found"
        action={
          <Link to="/runs" className={buttonClass("primary", "sm")}>
            All recorded runs
          </Link>
        }
      >
        There is no <span className="font-mono">{id}</span> in the demo bundle.
      </EmptyState>
    );
  }

  const s = r.state;
  const report = r.bundle?.report ?? null;
  const reached = isTerminal(s.status);
  const finalProgram = report?.final_program || lastAttempt(s)?.program || "";
  const downloads: DownloadLink[] = [
    ...(r.bundle?.hasReportMd
      ? [{ label: "report.md", href: demoUrl(`${encodeURIComponent(id)}/report.md`), disabled: !reached }]
      : []),
    {
      label: "report.json",
      disabled: !reached || !report,
      onClick: () => report && saveText(`${id}_report.json`, JSON.stringify(report, null, 2), "application/json"),
    },
    {
      label: "program.py",
      disabled: !reached || !finalProgram,
      onClick: () => saveText(`${id}_program.py`, finalProgram, "text/x-python"),
    },
  ];
  const banner = IS_FIXTURE ? (
    <Banner tone="warning" title={FIXTURE_LABEL}>
      This page replays a hand-made fixture from web/dev-fixtures for UI development. Its numbers are
      not results.
    </Banner>
  ) : null;

  return (
    <div className="pb-24">
      <JobView
        state={s}
        recording={r.recording}
        media={r.media}
        source="replay"
        downloads={downloads}
        banner={banner}
        back={{ label: "Recorded runs", to: "/runs" }}
        noVideoNote={IS_FIXTURE ? "Sample data has no videos." : undefined}
        onModalChange={onModalChange}
      />
      {r.total > 0 && (
        <ReplayControls
          playing={r.playing}
          cursor={r.cursor}
          total={r.total}
          speed={r.speed}
          recordedElapsed={r.recordedElapsed}
          recordedTotal={r.recordedTotal}
          onPlay={r.play}
          onPause={r.pause}
          onRestart={r.restart}
          onSkip={r.skipToEnd}
          onSpeed={r.setSpeed}
        />
      )}
    </div>
  );
}
