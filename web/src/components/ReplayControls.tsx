/** Floating player for demo replays: play/pause, restart, skip, speed, progress. */

import { SPEEDS, type Speed } from "../demo";
import { formatClock } from "../lib/format";
import { IconPause, IconPlay, IconRestart, IconSkip } from "./icons";
import { cx } from "./ui";

export function ReplayControls({
  playing,
  cursor,
  total,
  speed,
  recordedElapsed,
  recordedTotal,
  onPlay,
  onPause,
  onRestart,
  onSkip,
  onSpeed,
}: {
  playing: boolean;
  cursor: number;
  total: number;
  speed: Speed;
  recordedElapsed: number;
  recordedTotal: number;
  onPlay: () => void;
  onPause: () => void;
  onRestart: () => void;
  onSkip: () => void;
  onSpeed: (s: Speed) => void;
}) {
  const finished = cursor >= total;
  const progress = total ? (100 * cursor) / total : 0;
  const iconBtn =
    "flex h-8 w-8 items-center justify-center rounded-full text-zinc-300 transition-colors hover:bg-white/10 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-400 disabled:opacity-40";

  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-3 z-40 flex justify-center px-3 sm:bottom-5">
      <div
        role="group"
        aria-label="Replay controls"
        className="pointer-events-auto flex w-full max-w-xl items-center gap-2 rounded-2xl bg-zinc-900/95 px-2.5 py-2 text-white shadow-pop ring-1 ring-black/10 backdrop-blur sm:gap-3 sm:px-3"
      >
        <button
          type="button"
          onClick={finished ? onRestart : playing ? onPause : onPlay}
          aria-label={finished ? "Replay again" : playing ? "Pause" : "Play"}
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-white text-zinc-900 transition-transform hover:scale-105 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-400"
        >
          {finished ? <IconRestart size={16} /> : playing ? <IconPause size={16} /> : <IconPlay size={16} />}
        </button>
        <button type="button" onClick={onRestart} aria-label="Restart" title="Restart" className={iconBtn}>
          <IconRestart size={15} />
        </button>
        <button type="button" onClick={onSkip} disabled={finished} aria-label="Skip to the end" title="Skip to the end" className={iconBtn}>
          <IconSkip size={15} />
        </button>

        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between text-[11px] text-zinc-400">
            <span className="truncate">{finished ? "Replay finished" : "Replaying a recorded run"}</span>
            <span className="ml-2 shrink-0 font-mono tabular-nums">
              {formatClock(recordedElapsed)} / {formatClock(recordedTotal)}
            </span>
          </div>
          <div className="mt-1 h-1 overflow-hidden rounded-full bg-white/15">
            <div className="h-full rounded-full bg-accent-400 transition-[width] duration-300" style={{ width: `${progress}%` }} />
          </div>
        </div>

        <div className="flex shrink-0 rounded-full bg-white/10 p-0.5" role="radiogroup" aria-label="Replay speed">
          {SPEEDS.map((s) => (
            <button
              key={s}
              type="button"
              role="radio"
              aria-checked={speed === s}
              onClick={() => onSpeed(s)}
              className={cx(
                "rounded-full px-2 py-0.5 text-[11px] font-semibold tabular-nums transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-400",
                speed === s ? "bg-white text-zinc-900" : "text-zinc-300 hover:text-white",
              )}
            >
              {s}x
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
