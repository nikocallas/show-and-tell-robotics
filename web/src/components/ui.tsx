/** UI primitives: cards, buttons, chips, banners, toggles, segmented controls. */

import type { ButtonHTMLAttributes, ReactNode, SelectHTMLAttributes } from "react";
import type { JobStatus } from "../types";
import {
  IconAlert,
  IconCheck,
  IconChevronDown,
  IconClock,
  IconInfo,
  IconX,
  Spinner,
} from "./icons";

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

// ------------------------------------------------------------------ layout
export function Card({
  children,
  className,
  id,
}: {
  children: ReactNode;
  className?: string;
  id?: string;
}) {
  return (
    <section
      id={id}
      className={cx("rounded-xl border border-zinc-200 bg-white shadow-card", className)}
    >
      {children}
    </section>
  );
}

export function CardHeader({
  title,
  subtitle,
  actions,
  icon,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 border-b border-zinc-100 px-4 py-3 sm:px-5">
      <div className="flex min-w-0 items-center gap-2.5">
        {icon && <span className="text-zinc-400">{icon}</span>}
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-zinc-900">{title}</h2>
          {subtitle && <p className="mt-0.5 text-xs text-zinc-500">{subtitle}</p>}
        </div>
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

// ------------------------------------------------------------------ buttons
export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type ButtonSize = "xs" | "sm" | "md";

const BTN_BASE =
  "inline-flex select-none items-center justify-center gap-1.5 whitespace-nowrap rounded-lg font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50";
const BTN_VARIANT: Record<ButtonVariant, string> = {
  primary: "bg-accent-600 text-white shadow-sm hover:bg-accent-700 disabled:hover:bg-accent-600",
  secondary:
    "border border-zinc-200 bg-white text-zinc-800 shadow-sm hover:border-zinc-300 hover:bg-zinc-50",
  ghost: "text-zinc-600 hover:bg-zinc-100 hover:text-zinc-900",
  danger: "text-rose-600 hover:bg-rose-50",
};
const BTN_SIZE: Record<ButtonSize, string> = {
  xs: "h-7 px-2 text-xs",
  sm: "h-8 px-2.5 text-xs",
  md: "h-9 px-3.5 text-sm",
};

export function buttonClass(variant: ButtonVariant = "secondary", size: ButtonSize = "md"): string {
  return cx(BTN_BASE, BTN_VARIANT[variant], BTN_SIZE[size]);
}

export function Button({
  variant = "secondary",
  size = "md",
  className,
  type = "button",
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant; size?: ButtonSize }) {
  return <button type={type} className={cx(buttonClass(variant, size), className)} {...rest} />;
}

// ------------------------------------------------------------------ chips
export type Tone = "neutral" | "accent" | "success" | "danger" | "warning" | "muted";

const CHIP_TONE: Record<Tone, string> = {
  neutral: "bg-zinc-100 text-zinc-700 ring-zinc-200",
  accent: "bg-accent-50 text-accent-700 ring-accent-200",
  success: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  danger: "bg-rose-50 text-rose-700 ring-rose-200",
  warning: "bg-amber-50 text-amber-800 ring-amber-200",
  muted: "bg-white text-zinc-500 ring-zinc-200",
};

export function Chip({
  tone = "neutral",
  children,
  icon,
  className,
  title,
}: {
  tone?: Tone;
  children: ReactNode;
  icon?: ReactNode;
  className?: string;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={cx(
        "inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset",
        CHIP_TONE[tone],
        className,
      )}
    >
      {icon}
      {children}
    </span>
  );
}

const STATUS: Record<JobStatus, { tone: Tone; label: string; icon: ReactNode }> = {
  queued: { tone: "neutral", label: "Queued", icon: <IconClock size={12} /> },
  running: { tone: "accent", label: "Running", icon: <Spinner size={11} /> },
  awaiting_approval: {
    tone: "warning",
    label: "Needs approval",
    icon: <span className="h-1.5 w-1.5 rounded-full bg-amber-500" />,
  },
  verified: { tone: "success", label: "Verified", icon: <IconCheck size={12} strokeWidth={2.6} /> },
  failed: { tone: "danger", label: "Not verified", icon: <IconX size={12} strokeWidth={2.6} /> },
  error: { tone: "danger", label: "Error", icon: <IconAlert size={12} /> },
};

export function StatusChip({ status }: { status: JobStatus }) {
  const s = STATUS[status] ?? STATUS.running;
  return (
    <Chip tone={s.tone} icon={s.icon}>
      {s.label}
    </Chip>
  );
}

/** Pass/fail marker used in tables and lists. */
export function PassMark({ passed, label }: { passed: boolean | null | undefined; label?: string }) {
  if (passed == null) {
    return (
      <Chip tone="muted" icon={<span className="h-1.5 w-1.5 rounded-full bg-zinc-300" />}>
        {label ?? "Undecided"}
      </Chip>
    );
  }
  return passed ? (
    <Chip tone="success" icon={<IconCheck size={12} strokeWidth={2.6} />}>
      {label ?? "Pass"}
    </Chip>
  ) : (
    <Chip tone="danger" icon={<IconX size={12} strokeWidth={2.6} />}>
      {label ?? "Fail"}
    </Chip>
  );
}

// ------------------------------------------------------------------ banners and empty states
const BANNER_TONE = {
  info: "border-accent-200 bg-accent-50/70 text-accent-900",
  warning: "border-amber-200 bg-amber-50 text-amber-900",
  danger: "border-rose-200 bg-rose-50 text-rose-900",
  neutral: "border-zinc-200 bg-white text-zinc-800",
} as const;

export function Banner({
  tone = "info",
  title,
  children,
  action,
  icon,
  className,
}: {
  tone?: keyof typeof BANNER_TONE;
  title?: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
  icon?: ReactNode;
  className?: string;
}) {
  const fallbackIcon = tone === "danger" || tone === "warning" ? <IconAlert size={16} /> : <IconInfo size={16} />;
  return (
    <div
      role={tone === "danger" ? "alert" : "status"}
      className={cx(
        "flex flex-col gap-3 rounded-xl border px-4 py-3 text-sm sm:flex-row sm:items-center",
        BANNER_TONE[tone],
        className,
      )}
    >
      <div className="flex min-w-0 flex-1 items-start gap-2.5">
        <span className="mt-0.5 shrink-0 opacity-80">{icon ?? fallbackIcon}</span>
        <div className="min-w-0">
          {title && <p className="font-medium">{title}</p>}
          {children && <div className={cx(title ? "mt-0.5 opacity-90" : "")}>{children}</div>}
        </div>
      </div>
      {action && <div className="flex shrink-0 flex-wrap gap-2 sm:justify-end">{action}</div>}
    </div>
  );
}

export function EmptyState({
  icon,
  title,
  children,
  action,
}: {
  icon?: ReactNode;
  title: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-zinc-300 bg-white px-6 py-14 text-center">
      {icon && (
        <div className="mb-4 flex h-11 w-11 items-center justify-center rounded-xl bg-zinc-100 text-zinc-500">
          {icon}
        </div>
      )}
      <h3 className="text-base font-semibold text-zinc-900">{title}</h3>
      {children && <div className="mt-1.5 max-w-md text-sm text-zinc-500">{children}</div>}
      {action && <div className="mt-5 flex flex-wrap justify-center gap-2">{action}</div>}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cx("animate-pulse rounded-md bg-zinc-100", className)} />;
}

export function PageLoading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-24 text-sm text-zinc-500">
      <Spinner size={16} /> {label}
    </div>
  );
}

// ------------------------------------------------------------------ form controls
export function Toggle({
  checked,
  onChange,
  label,
  description,
  disabled,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: ReactNode;
  description?: ReactNode;
  disabled?: boolean;
}) {
  return (
    <label
      className={cx(
        "flex items-start justify-between gap-4",
        disabled ? "cursor-not-allowed opacity-60" : "cursor-pointer",
      )}
    >
      <span className="min-w-0">
        <span className="block text-sm font-medium text-zinc-900">{label}</span>
        {description && <span className="mt-0.5 block text-xs text-zinc-500">{description}</span>}
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cx(
          "relative mt-0.5 inline-flex h-5 w-9 shrink-0 rounded-full transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 focus-visible:ring-offset-2",
          checked ? "bg-accent-600" : "bg-zinc-300",
        )}
      >
        <span
          className={cx(
            "absolute top-0.5 h-4 w-4 rounded-full bg-white shadow-sm transition-transform",
            checked ? "translate-x-[18px]" : "translate-x-0.5",
          )}
        />
      </button>
    </label>
  );
}

export function Segmented<T extends string | number>({
  value,
  options,
  onChange,
  label,
  size = "sm",
}: {
  value: T;
  options: { value: T; label: ReactNode; title?: string }[];
  onChange: (v: T) => void;
  label: string;
  size?: "xs" | "sm";
}) {
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className="inline-flex rounded-lg border border-zinc-200 bg-zinc-100/70 p-0.5"
    >
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={String(o.value)}
            type="button"
            role="radio"
            aria-checked={active}
            title={o.title}
            onClick={() => onChange(o.value)}
            className={cx(
              "rounded-md font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500",
              size === "xs" ? "px-2 py-0.5 text-xs" : "px-2.5 py-1 text-xs",
              active
                ? "bg-white text-zinc-900 shadow-sm ring-1 ring-zinc-200"
                : "text-zinc-500 hover:text-zinc-800",
            )}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

export function Select({
  className,
  children,
  ...rest
}: SelectHTMLAttributes<HTMLSelectElement> & { children: ReactNode }) {
  return (
    <span className={cx("relative inline-flex", className)}>
      <select
        className="h-8 w-full appearance-none rounded-lg border border-zinc-200 bg-white py-1 pl-2.5 pr-7 text-xs text-zinc-800 shadow-sm hover:border-zinc-300 focus:border-accent-500 focus:outline-none focus:ring-2 focus:ring-accent-500/30 disabled:bg-zinc-50 disabled:text-zinc-400"
        {...rest}
      >
        {children}
      </select>
      <IconChevronDown
        size={14}
        className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-zinc-400"
      />
    </span>
  );
}

export function Stat({ label, value, hint }: { label: ReactNode; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-zinc-500">{label}</dt>
      <dd className="mt-0.5 truncate text-sm font-medium text-zinc-900">{value}</dd>
      {hint && <dd className="text-xs text-zinc-400">{hint}</dd>}
    </div>
  );
}
