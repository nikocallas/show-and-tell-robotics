/** App shell: header with navigation and the demo/live switch, footer. */

import type { ReactNode } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";
import { backendLabel, FIXTURE_LABEL, IS_FIXTURE, LIVE_AVAILABLE } from "../config";
import { useMode } from "../mode";
import { LogoMark } from "./icons";
import { Chip, cx, Segmented } from "./ui";

interface NavItem {
  to: string;
  short: string;
  label: string;
}

/** With a backend: start tasks. Without one (the hosted demo) the runs come first and /how explains. */
const NAV: NavItem[] = LIVE_AVAILABLE
  ? [
      { to: "/new", short: "New", label: "New task" },
      { to: "/runs", short: "Runs", label: "Runs" },
      { to: "/bench", short: "Bench", label: "Benchmark" },
    ]
  : [
      { to: "/runs", short: "Runs", label: "Runs" },
      { to: "/bench", short: "Bench", label: "Benchmark" },
      { to: "/how", short: "How", label: "How it works" },
    ];

function ModeSwitch() {
  const { mode, setMode } = useMode();
  if (!LIVE_AVAILABLE) {
    return (
      <span className="hidden min-[420px]:inline-flex">
        <Chip tone="muted" title="Replaying recorded runs; no backend needed" icon={<span className="h-1.5 w-1.5 rounded-full bg-accent-500" />}>
          Demo
        </Chip>
      </span>
    );
  }
  return (
    <Segmented
      label="Data source"
      size="xs"
      value={mode}
      onChange={setMode}
      options={[
        { value: "demo", label: "Demo", title: "Replay recorded runs (no backend)" },
        { value: "live", label: "Live", title: `Live backend: ${backendLabel()}` },
      ]}
    />
  );
}

export function Layout({ children }: { children: ReactNode }) {
  const { mode } = useMode();
  const { pathname } = useLocation();
  // "/" shows the runs in demo mode and the new-task form in live mode; without a backend /new
  // shows How it works.
  const alias: Record<string, string> = { "/": mode === "live" ? "/new" : "/runs" };
  if (!LIVE_AVAILABLE) alias["/new"] = "/how";

  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-30 border-b border-zinc-200/80 bg-white/85 backdrop-blur supports-[backdrop-filter]:bg-white/70">
        <div className="mx-auto flex h-14 max-w-7xl items-center gap-3 px-4 sm:gap-6 sm:px-6 lg:px-8">
          <Link to="/" className="flex shrink-0 items-center gap-2.5" aria-label="Show & Tell Robotics home">
            <LogoMark size={26} />
            <span className="text-sm font-semibold tracking-tight text-zinc-900">
              Show &amp; Tell
              <span className="ml-1 hidden font-normal text-zinc-400 min-[420px]:inline">Robotics</span>
            </span>
          </Link>
          <nav aria-label="Main" className="flex min-w-0 items-center gap-0.5">
            {NAV.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                className={({ isActive }) =>
                  cx(
                    "rounded-md px-2 py-1.5 text-sm font-medium transition-colors sm:px-2.5",
                    isActive || alias[pathname] === n.to
                      ? "bg-zinc-100 text-zinc-900"
                      : "text-zinc-500 hover:text-zinc-900",
                  )
                }
              >
                <span className="sm:hidden">{n.short}</span>
                <span className="hidden sm:inline">{n.label}</span>
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto shrink-0">
            <ModeSwitch />
          </div>
        </div>
      </header>
      {IS_FIXTURE && (
        <div className="border-b border-amber-200 bg-amber-100 px-4 py-1.5 text-center text-xs font-medium text-amber-900">
          {FIXTURE_LABEL}: dev fixtures from web/dev-fixtures (VITE_DEV_FIXTURE=1)
        </div>
      )}
      <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 sm:px-6 sm:py-8 lg:px-8">{children}</main>
      <footer className="border-t border-zinc-200 bg-white">
        <div className="mx-auto flex max-w-7xl flex-col gap-1 px-4 py-5 text-xs text-zinc-500 sm:flex-row sm:justify-between sm:px-6 lg:px-8">
          <span>Show &amp; Tell Robotics: test-driven robot programming</span>
          <span>Muse Wonder writes the spec and the program · SAM 3.1 watches and judges · MuJoCo, Franka Panda</span>
        </div>
      </footer>
    </div>
  );
}
