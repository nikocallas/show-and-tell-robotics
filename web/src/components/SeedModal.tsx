/** One test scene: video, ground-truth checks next to the SAM verdicts, and contact events. */

import { useEffect, useMemo, useRef, useState, type RefObject } from "react";
import type { MediaResolver } from "../hooks";
import { kindLabel, objectLabel } from "../lib/format";
import { seedAgreement, type AttemptState } from "../state";
import type { TaskSpec } from "../types";
import { attemptTitle } from "./AttemptTimeline";
import { IconChevronLeft, IconChevronRight, IconVideo, IconX } from "./icons";
import { AspectVideo, Button, Chip, cx, PassMark, Segmented } from "./ui";

type Camera = "full" | "overhead" | "judge_angle";

const CAMERAS: { key: Camera; label: string; title: string }[] = [
  { key: "full", label: "Hero", title: "Cinematic camera, looking back at the robot from across the table" },
  { key: "overhead", label: "Overhead", title: "Judge clip (overhead): first 25 + last 25 frames" },
  { key: "judge_angle", label: "45°", title: "Judge clip (45°): first 25 + last 25 frames" },
];

/** Checks a contact can break; in_bin failures are about where objects ended, not touches. */
const CONTACT_KINDS = new Set(["untouched", "upright", "unmoved"]);

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), video[controls], [tabindex]:not([tabindex="-1"])';

/** The dialog's keyboard-reachable elements in document order (visible ones, guards excluded). */
function focusables(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (el) => !el.hasAttribute("data-focus-guard") && el.getClientRects().length > 0,
  );
}

/**
 * Invisible, empty tab stops at both ends of the dialog: tabbing onto one wraps focus to the other
 * end, so the page behind the dialog (the replay controls included) stays out of keyboard reach.
 */
function FocusGuard({ panel, to }: { panel: RefObject<HTMLDivElement>; to: "first" | "last" }) {
  return (
    <span
      tabIndex={0}
      data-focus-guard=""
      className="sr-only"
      onFocus={() => {
        const root = panel.current;
        if (!root) return;
        const items = focusables(root);
        const target = to === "first" ? items[0] : items[items.length - 1];
        if (target) target.focus();
        else root.focus();
      }}
    />
  );
}

export function SeedModal({
  attempt,
  seed,
  spec,
  media,
  onClose,
  onNavigate,
  noVideoNote,
  judgeOff,
}: {
  attempt: AttemptState;
  seed: number;
  spec: TaskSpec | null;
  media: MediaResolver;
  onClose: () => void;
  onNavigate: (seed: number) => void;
  noVideoNote?: string;
  /** Why SAM 3.1 is not judging this job (state.judgeOffReason), when the data says. */
  judgeOff?: string | null;
}) {
  const result = attempt.results[seed];
  const panelRef = useRef<HTMLDivElement>(null);
  const available = useMemo(
    () => CAMERAS.filter((c) => result?.videos?.[c.key]),
    [result],
  );
  const [camera, setCamera] = useState<Camera>("full");
  const [videoFailed, setVideoFailed] = useState(false);
  const cam = available.find((c) => c.key === camera) ?? available[0];
  const videoUrl = cam ? media(result?.videos?.[cam.key]) : undefined;
  const poster = media(result?.videos?.thumb);

  const done = attempt.seeds.filter((s) => attempt.results[s]);
  const idx = done.indexOf(seed);
  const prev = idx > 0 ? done[idx - 1] : null;
  const next = idx >= 0 && idx < done.length - 1 ? done[idx + 1] : null;

  useEffect(() => setVideoFailed(false), [videoUrl]);

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    panelRef.current?.focus();
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    // Focus that lands behind the dialog (tabbing in from the browser's address bar, say) comes back.
    const onFocusIn = (e: FocusEvent) => {
      const panel = panelRef.current;
      if (panel && e.target instanceof Node && !panel.contains(e.target)) panel.focus();
    };
    document.addEventListener("focusin", onFocusIn);
    return () => {
      document.removeEventListener("focusin", onFocusIn);
      document.body.style.overflow = prevOverflow;
      opener?.focus?.();
    };
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Tab") {
        // Inside the dialog the focus guards wrap the tab order. From the panel itself (focused on
        // open) or from anywhere outside it, Tab goes to the dialog's first or last element instead
        // of the page behind it.
        const panel = panelRef.current;
        const active = document.activeElement;
        if (!panel || (active instanceof Node && panel.contains(active) && active !== panel)) return;
        e.preventDefault();
        const items = focusables(panel);
        const target = e.shiftKey ? items[items.length - 1] : items[0];
        if (target) target.focus();
        else panel.focus();
        return;
      }
      if (e.key === "Escape") onClose();
      else if (e.key === "ArrowLeft" && prev != null) onNavigate(prev);
      else if (e.key === "ArrowRight" && next != null) onNavigate(next);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, onNavigate, prev, next]);

  if (!result) return null;
  const ag = seedAgreement(result);
  const failingObjects = new Set(
    result.checks
      .filter((c) => !c.passed && CONTACT_KINDS.has(c.kind))
      .flatMap((c) => c.objects ?? []),
  );
  const specCheck = (id: string) => spec?.checks.find((c) => c.id === id);

  return (
    <div
      className="fixed inset-0 z-50 !mt-0 flex items-end justify-center bg-zinc-950/50 backdrop-blur-[2px] sm:items-center sm:p-6"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={`Scene ${seed}`}
        tabIndex={-1}
        className="flex max-h-[94vh] w-full flex-col overflow-hidden rounded-t-2xl bg-white shadow-pop focus:outline-none sm:max-w-5xl sm:rounded-2xl"
      >
        <FocusGuard panel={panelRef} to="last" />
        {/* header */}
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-zinc-100 px-4 py-3 sm:px-5">
          <div className="flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1">
            <h2 className="text-base font-semibold text-zinc-900">Scene {seed}</h2>
            <PassMark passed={result.passed} label={result.passed ? "Passed" : "Failed"} />
            <Chip tone="muted">{attemptTitle(attempt)}</Chip>
          </div>
          <div className="ml-auto flex items-center gap-1">
            <Button size="sm" variant="ghost" disabled={prev == null} onClick={() => prev != null && onNavigate(prev)} aria-label="Previous scene">
              <IconChevronLeft size={16} />
            </Button>
            <span className="min-w-[3.5rem] text-center text-xs tabular-nums text-zinc-500">
              {idx + 1} / {done.length}
            </span>
            <Button size="sm" variant="ghost" disabled={next == null} onClick={() => next != null && onNavigate(next)} aria-label="Next scene">
              <IconChevronRight size={16} />
            </Button>
            <Button size="sm" variant="ghost" onClick={onClose} aria-label="Close">
              <IconX size={16} />
            </Button>
          </div>
        </div>

        <div className="grid min-h-0 flex-1 gap-0 overflow-y-auto lg:grid-cols-5">
          {/* video */}
          <div className="border-b border-zinc-100 p-4 sm:p-5 lg:sticky lg:top-0 lg:col-span-3 lg:self-start lg:border-b-0">
            {available.length > 1 && (
              <div className="mb-3">
                <Segmented
                  label="Camera"
                  value={cam?.key ?? "full"}
                  onChange={setCamera}
                  options={available.map((c) => ({ value: c.key, label: c.label, title: c.title }))}
                />
              </div>
            )}
            {videoUrl && !videoFailed ? (
              <AspectVideo
                key={videoUrl}
                src={videoUrl}
                poster={poster}
                controls
                autoPlay
                muted
                loop
                playsInline
                onError={() => setVideoFailed(true)}
                className="w-full rounded-lg bg-zinc-900 object-contain"
              />
            ) : (
              <div className="flex aspect-[4/3] w-full flex-col items-center justify-center gap-2 rounded-lg bg-zinc-100 text-center text-sm text-zinc-500">
                <IconVideo size={22} className="text-zinc-400" />
                {videoFailed ? "The video could not be loaded." : (noVideoNote ?? "No video was recorded for this scene.")}
              </div>
            )}
            {cam?.key === "full" && videoUrl && !videoFailed && (
              <p className="mt-2 text-xs text-zinc-500">
                The hero camera looks back at the robot from across the table, so the robot's left bin
                (slate) appears on the right of the frame; the overhead clip keeps left on the left.
              </p>
            )}
            <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-zinc-500">
              {result.sim_seconds != null && <span>Simulated time {result.sim_seconds.toFixed(1)} s</span>}
              <span>Seed {seed}</span>
              {cam && cam.key !== "full" && <span>Judge clip: first 25 + last 25 frames</span>}
            </div>
            {result.error && (
              <div className="mt-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2">
                <p className="text-xs font-medium text-rose-800">The program stopped with an error</p>
                <pre className="mt-1 whitespace-pre-wrap break-words font-mono text-[11px] text-rose-700">{result.error}</pre>
              </div>
            )}
          </div>

          {/* checks + contacts */}
          <div className="space-y-5 p-4 sm:p-5 lg:col-span-2 lg:border-l lg:border-zinc-100">
            <section>
              <div className="flex items-baseline justify-between gap-2">
                <h3 className="text-sm font-semibold text-zinc-900">Checks</h3>
                <span className="text-[11px] text-zinc-500">Ground truth decides</span>
              </div>
              <div className="mt-2 overflow-hidden rounded-lg border border-zinc-200">
                <table className="w-full table-fixed text-left text-xs">
                  <thead className="bg-zinc-50 text-[11px] uppercase tracking-wide text-zinc-500">
                    <tr>
                      <th className="px-2.5 py-2 font-medium">Check</th>
                      <th className="w-[84px] px-2 py-2 font-medium">Simulator</th>
                      <th className="w-[84px] px-2 py-2 font-medium">SAM 3.1</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-zinc-100">
                    {result.checks.map((c) => {
                      const v = result.sam?.find((x) => x.check_id === c.check_id);
                      const differs = v != null && v.passed != null && v.passed !== c.passed;
                      const sc = specCheck(c.check_id);
                      return (
                        <tr key={c.check_id} className={cx("align-top", differs && "bg-amber-50/70")}>
                          <td className="px-2.5 py-2">
                            <p className="text-zinc-900">{sc?.text ?? c.check_id}</p>
                            <p className="mt-0.5 text-[11px] text-zinc-500">
                              {kindLabel(c.kind)} · <span className="font-mono">{c.check_id}</span>
                            </p>
                            {c.detail && <p className="mt-1 text-[11px] text-zinc-600">Sim: {c.detail}</p>}
                            {v?.detail && <p className="mt-0.5 text-[11px] text-zinc-600">SAM: {v.detail}</p>}
                          </td>
                          <td className="px-2 py-2">
                            <PassMark passed={c.passed} />
                          </td>
                          <td className="px-2 py-2">
                            {v ? <PassMark passed={v.passed} label={v.passed == null ? "Unsure" : undefined} /> : <span className="text-zinc-400">—</span>}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <p className="mt-2 text-xs text-zinc-500">
                {ag
                  ? `SAM agrees on ${ag.agree} of ${ag.n} check${ag.n === 1 ? "" : "s"}${ag.undecided ? `; ${ag.undecided} undecided` : ""}.`
                  : judgeOff
                    ? `No SAM 3.1 verdict for this scene: ${judgeOff}.`
                    : attempt.status === "done" // attempt_done comes after the judge's last verdict
                      ? "No SAM 3.1 verdict for this scene."
                      : "No SAM verdict for this scene yet."}
              </p>
            </section>

            <section>
              <h3 className="text-sm font-semibold text-zinc-900">Contacts</h3>
              <p className="mt-0.5 text-xs text-zinc-500">First contact of each object with anything but the table.</p>
              {result.touch_events?.length ? (
                <ol className="mt-2 space-y-1.5">
                  {result.touch_events.map((t, i) => {
                    const bad = failingObjects.has(t.obj);
                    return (
                      <li key={i} className="flex gap-2.5 text-xs">
                        <span className="w-12 shrink-0 pt-px text-right font-mono tabular-nums text-zinc-400">{t.t.toFixed(2)}s</span>
                        <span className={cx("min-w-0", bad ? "text-rose-700" : "text-zinc-700")}>
                          <span className="font-medium">{objectLabel(t.obj)}</span> touched by{" "}
                          <span className="font-medium">{objectLabel(t.by)}</span>
                          <span className="block text-[11px] text-zinc-500">during {t.during}</span>
                        </span>
                        {bad && (
                          <Chip
                            tone="danger"
                            className="ml-auto self-start"
                            title="This object is part of a failed untouched/upright/unmoved check"
                          >
                            failed check
                          </Chip>
                        )}
                      </li>
                    );
                  })}
                </ol>
              ) : (
                <p className="mt-2 text-xs text-zinc-500">No contacts recorded.</p>
              )}
            </section>
          </div>
        </div>
        <FocusGuard panel={panelRef} to="first" />
      </div>
    </div>
  );
}
