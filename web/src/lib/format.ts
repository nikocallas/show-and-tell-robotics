/** Text formatting: spec sentences (mirrors Selector.describe in schemas.py), numbers, times. */

import type { BinName, Check, CheckKind, PassValue, Rule, Selector } from "../types";

const NOUNS: Record<string, string> = { cube: "cubes", cylinder: "cylinders", glass: "glasses" };

export function selectorDict(sel: Selector | null | undefined): Selector {
  const out: Selector = {};
  if (!sel) return out;
  if (sel.size != null) out.size = sel.size;
  if (sel.color != null) out.color = sel.color;
  if (sel.shape != null) out.shape = sel.shape;
  if (sel.fragile != null) out.fragile = sel.fragile;
  return out;
}

/** Same phrasing as Selector.describe(): "non-fragile large objects", "glasses", "every object". */
export function describeSelector(sel: Selector | null | undefined): string {
  const d = selectorDict(sel);
  if (Object.keys(d).length === 0) return "every object";
  const words: string[] = [];
  if (d.size) words.push(d.size);
  if (d.color) words.push(d.color);
  if (d.fragile === true) words.unshift("fragile");
  else if (d.fragile === false) words.unshift("non-fragile");
  words.push(NOUNS[d.shape ?? ""] ?? "objects");
  return words.join(" ");
}

export function capitalize(s: string): string {
  return s ? s[0].toUpperCase() + s.slice(1) : s;
}

export function binLabel(bin: BinName | string | null | undefined): string {
  if (!bin) return "a bin";
  return bin.replace(/_/g, " ");
}

/**
 * The bins' colours in the renders (showtell/sim/scene.py BIN_RGBA: slate, sand, sage). Left and
 * right are the robot's: the overhead camera shows them as image left and right; the hero camera
 * looks back at the robot from across the table, so there the left bin is on the right.
 */
export const BIN_COLORS: Record<BinName, { name: string; css: string }> = {
  left_bin: { name: "slate", css: "#788ca8" },
  middle_bin: { name: "sand", css: "#b3a180" },
  right_bin: { name: "sage", css: "#85a185" },
};

export function binColor(bin: BinName | string | null | undefined): { name: string; css: string } | null {
  return bin && bin in BIN_COLORS ? BIN_COLORS[bin as BinName] : null;
}

/** "left bin (slate)": the bin and its colour in the videos. */
export function binWithColor(bin: BinName | string | null | undefined): string {
  const c = binColor(bin);
  return c ? `${binLabel(bin)} (${c.name})` : binLabel(bin);
}

export function ruleSentence(rule: Rule): string {
  const what = describeSelector(rule.select);
  if (rule.place_in) return `Put ${what} in the ${binLabel(rule.place_in)}.`;
  return `Leave ${what} where they are.`;
}

export const KIND_LABEL: Record<CheckKind, string> = {
  in_bin: "In bin",
  untouched: "Untouched",
  upright: "Upright",
  unmoved: "Unmoved",
};

export function kindLabel(kind: string): string {
  return KIND_LABEL[kind as CheckKind] ?? kind;
}

/** A sentence built from the check's structure (the model-written `text` is shown first). */
export function checkSentence(check: Pick<Check, "kind" | "select" | "bin">): string {
  const what = describeSelector(check.select);
  switch (check.kind) {
    case "in_bin":
      return `${capitalize(what)} end in the ${binLabel(check.bin)}.`;
    case "untouched":
      return `Nothing touches ${what}.`;
    case "upright":
      return `${capitalize(what)} stay upright.`;
    case "unmoved":
      return `${capitalize(what)} stay where they started.`;
    default:
      return capitalize(what);
  }
}

/** Sim object ids are <color>_<shape>_<size>[_n]; owners also include robot, bins, world. */
export function objectLabel(id: string): string {
  const special: Record<string, string> = {
    robot: "robot arm",
    world: "scene",
    table: "table",
    left_bin: "left bin",
    middle_bin: "middle bin",
    right_bin: "right bin",
  };
  if (special[id]) return special[id];
  const parts = id.split("_");
  if (parts.length >= 3) {
    const [color, shape, size, n] = parts;
    const base = shape === "glass" ? "glass" : `${size} ${color} ${shape}`;
    return n ? `${base} #${n}` : base;
  }
  return id.replace(/_/g, " ");
}

// ------------------------------------------------------------------ numbers and time
export function toEpochSeconds(x: string | number | null | undefined): number | null {
  if (x == null || x === "") return null;
  if (typeof x === "number") return x > 1e12 ? x / 1000 : x;
  const n = Number(x);
  if (Number.isFinite(n)) return n > 1e12 ? n / 1000 : n;
  // "2026-10-31 14:05:00" (time.strftime in run_bench) is not ISO; with a "T" every browser reads
  // it as local time.
  const iso = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}/.test(x) ? x.replace(" ", "T") : x;
  const t = Date.parse(iso);
  return Number.isFinite(t) ? t / 1000 : null;
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "—";
  const s = Math.max(0, seconds);
  if (s < 10) return `${s.toFixed(1)} s`;
  if (s < 60) return `${Math.round(s)} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(Math.round(s % 60)).padStart(2, "0")}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
}

/** m:ss for replay clocks. */
export function formatClock(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function formatDateTime(epochSeconds: number | null | undefined): string {
  if (epochSeconds == null) return "—";
  const d = new Date(epochSeconds * 1000);
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatUsd(value: number | string | null | undefined): string {
  const n = typeof value === "string" ? Number(value) : value;
  if (n == null || !Number.isFinite(n)) return "—";
  if (n > 0 && n < 0.01) return "<$0.01";
  return `$${n.toFixed(2)}`;
}

/** Never rounds a miss up to 100% (99.6% -> "99%"), like agreement.format_pct. */
export function formatPct(pct: number | null | undefined): string {
  if (pct == null || !Number.isFinite(pct)) return "—";
  return `${Math.min(Math.round(pct), pct < 100 ? 99 : 100)}%`;
}

/** Agreement may arrive as a fraction (0.93) or a percentage (93). */
export function agreementPct(value: number | string | null | undefined): number | null {
  if (value == null || value === "") return null;
  if (typeof value === "string") {
    const n = Number(value.replace("%", "").trim());
    if (!Number.isFinite(n)) return null;
    return value.includes("%") || n > 1 ? n : n * 100;
  }
  if (!Number.isFinite(value)) return null;
  return value <= 1 ? value * 100 : value;
}

/** Parse a pass value into {passed, total} when possible. */
export function passParts(v: PassValue): { passed: number; total: number } | null {
  if (v == null) return null;
  if (typeof v === "object") {
    return Number.isFinite(v.passed) && Number.isFinite(v.total) ? v : null;
  }
  if (typeof v === "string") {
    const m = v.match(/^\s*(\d+)\s*\/\s*(\d+)\s*$/);
    return m ? { passed: Number(m[1]), total: Number(m[2]) } : null;
  }
  return null;
}

export function passText(v: PassValue): string {
  if (v == null || v === "") return "—";
  const p = passParts(v);
  if (p) return `${p.passed}/${p.total}`;
  return String(v);
}

export function plural(n: number, word: string, many?: string): string {
  return `${n} ${n === 1 ? word : (many ?? `${word}s`)}`;
}

export function shortId(id: string): string {
  return id.length > 18 ? `${id.slice(0, 8)}…${id.slice(-6)}` : id;
}
