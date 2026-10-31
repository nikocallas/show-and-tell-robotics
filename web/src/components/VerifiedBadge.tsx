import { IconShieldCheck } from "./icons";
import { cx } from "./ui";

/** Shown when the final attempt passes every test scene. */
export function VerifiedBadge({
  passed,
  total,
  compact = false,
  className,
}: {
  passed: number;
  total: number;
  compact?: boolean;
  className?: string;
}) {
  return (
    <div
      role="status"
      aria-label={`Verified: ${passed} of ${total} test scenes pass`}
      className={cx(
        "inline-flex animate-fade-in items-center overflow-hidden rounded-lg bg-emerald-600 text-white shadow-sm ring-1 ring-emerald-700/20",
        className,
      )}
    >
      <span className={cx("flex items-center gap-1.5 font-semibold tracking-[0.08em]", compact ? "px-2 py-1 text-[11px]" : "px-3 py-1.5 text-xs")}>
        <IconShieldCheck size={compact ? 14 : 16} strokeWidth={2.2} />
        VERIFIED
      </span>
      <span
        className={cx(
          "border-l border-white/25 bg-emerald-700/40 font-medium tabular-nums",
          compact ? "px-2 py-1 text-[11px]" : "px-3 py-1.5 text-xs",
        )}
      >
        {passed}/{total} scenes
      </span>
    </div>
  );
}
