/** How the pipeline works. Without a backend (the hosted demo) it also stands in for New task. */

import { Link } from "react-router-dom";
import { LIVE_AVAILABLE, REPO_URL } from "../config";
import { featuredRun, loadDemoRuns } from "../demo";
import { useAsync } from "../hooks";
import { JUDGING_NOTE, PITCH, StepsList } from "../components/HowSteps";
import { IconArrowRight, IconPlay } from "../components/icons";
import { buttonClass, Card } from "../components/ui";

export function HowItWorksPage() {
  const runs = useAsync(loadDemoRuns, []);
  const featured = featuredRun(runs.data ?? []);

  return (
    <div className="grid gap-8 lg:grid-cols-12">
      <div className="min-w-0 lg:col-span-7">
        <h1 className="text-xl font-semibold tracking-tight text-zinc-900 sm:text-2xl">How it works</h1>
        <p className="mt-1.5 max-w-xl text-sm text-zinc-600">{PITCH}</p>
        <Card className="mt-6">
          <div className="px-4 py-5 sm:px-6">
            <StepsList />
            <p className="mt-5 border-t border-zinc-100 pt-4 text-xs leading-relaxed text-zinc-500">
              {JUDGING_NOTE}
            </p>
          </div>
        </Card>
      </div>

      <aside className="min-w-0 lg:col-span-5 lg:pt-[4.25rem]">
        <Card>
          <div className="px-4 py-5 sm:px-6">
            <h2 className="text-sm font-semibold text-zinc-900">See it run</h2>
            <p className="mt-1 text-sm text-zinc-600">
              {LIVE_AVAILABLE ? (
                "Start a task on the live backend, or replay a recorded run."
              ) : (
                <>
                  This hosted demo has no backend: run the app locally (see the{" "}
                  <a className="underline underline-offset-2 hover:text-zinc-900" href={REPO_URL}>
                    README on GitHub
                  </a>
                  ) to start a task. The recorded runs replay real pipeline output in the browser.
                </>
              )}
            </p>
            <div className="mt-4 flex flex-wrap gap-2">
              <Link
                to={featured ? `/replay/${encodeURIComponent(featured.entry.id)}` : "/runs"}
                className={buttonClass("primary", "md")}
              >
                <IconPlay size={13} /> Watch a recorded run
              </Link>
              <Link to="/runs" className={buttonClass("secondary", "md")}>
                All runs <IconArrowRight size={15} />
              </Link>
            </div>
          </div>
        </Card>
      </aside>
    </div>
  );
}
