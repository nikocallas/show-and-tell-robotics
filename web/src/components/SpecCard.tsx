/** The task spec: rules and checks as sentences, editable inline while the pipeline waits. */

import { useEffect, useMemo, useState } from "react";
import {
  binLabel,
  checkSentence,
  describeSelector,
  kindLabel,
  ruleSentence,
} from "../lib/format";
import type {
  BinName,
  Check,
  CheckKind,
  Color,
  Rule,
  Selector,
  Shape,
  Size,
  SpecSource,
  TaskSpec,
} from "../types";
import { BINS, CHECK_KINDS, COLORS, SHAPES, SIZES } from "../types";
import {
  IconArrowRight,
  IconCheck,
  IconPencil,
  IconPlus,
  IconQuestion,
  IconTrash,
  Spinner,
} from "./icons";
import { Banner, Button, Card, CardHeader, Chip, cx, Select, Skeleton } from "./ui";

const SOURCE_LABEL: Record<SpecSource, string> = {
  video: "From video + sentence",
  sentence: "From sentence only",
  given: "Spec supplied",
};

export interface SpecCardProps {
  spec: TaskSpec | null;
  source: SpecSource | null;
  mediaPath: string | null;
  clarifyingQuestion: string | null;
  awaiting: boolean;
  approved: boolean;
  edited: boolean;
  /** Live jobs only: approve as proposed (null) or send the edited spec. */
  onApprove?: (spec: TaskSpec | null) => Promise<void>;
  /** Shown while there is no spec yet. */
  pendingText: string;
  /** Replays: the approval is part of the recording. */
  readOnlyNote?: string;
}

// ------------------------------------------------------------------ normalize + validate
const ID_RE = /^[a-z0-9_]{1,40}$/;

function normSelector(s: Selector | null | undefined): Selector {
  return {
    size: s?.size ?? null,
    color: s?.color ?? null,
    shape: s?.shape ?? null,
    fragile: s?.fragile ?? null,
  };
}

/** Canonical form matching the pydantic models (used for comparison and for the POST body). */
export function normalizeSpec(spec: TaskSpec): TaskSpec {
  return {
    rules: spec.rules.map((r) => ({
      select: normSelector(r.select),
      place_in: r.leave ? null : (r.place_in ?? null),
      leave: Boolean(r.leave),
    })),
    checks: spec.checks.map((c) => ({
      id: c.id.trim(),
      kind: c.kind,
      select: normSelector(c.select),
      bin: c.kind === "in_bin" ? (c.bin ?? null) : null,
      text: c.text.trim(),
    })),
    clarifying_question: spec.clarifying_question ?? null,
    rationale: spec.rationale ?? "",
  };
}

export function validateSpec(spec: TaskSpec): string[] {
  const errors: string[] = [];
  if (spec.rules.length === 0) errors.push("Add at least one rule.");
  if (spec.checks.length === 0) errors.push("Add at least one check.");
  spec.rules.forEach((r, i) => {
    if (!r.leave && !r.place_in) errors.push(`Rule ${i + 1} needs a bin or "leave in place".`);
  });
  const seen = new Set<string>();
  for (const c of spec.checks) {
    const id = c.id.trim();
    if (!ID_RE.test(id)) errors.push(`Check id "${id || "(empty)"}" must be 1-40 lowercase letters, digits or underscores.`);
    else if (seen.has(id)) errors.push(`Check id "${id}" is used twice.`);
    seen.add(id);
    if (c.kind === "in_bin" && !c.bin) errors.push(`Check "${id}" needs a bin.`);
    if (!c.text.trim()) errors.push(`Check "${id}" needs a sentence a person can verify.`);
  }
  return errors;
}

function uniqueId(base: string, taken: string[]): string {
  let i = taken.length + 1;
  while (taken.includes(`${base}_${i}`)) i++;
  return `${base}_${i}`;
}

// ------------------------------------------------------------------ small pieces
/** Three bins left to right; the target is filled. */
function BinTag({ rule }: { rule: Rule }) {
  if (rule.leave || !rule.place_in) {
    return (
      <span className="inline-flex items-center rounded-md border border-dashed border-zinc-300 px-1.5 py-0.5 text-[11px] font-medium text-zinc-500">
        Stays put
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5" title={binLabel(rule.place_in)}>
      <span className="flex gap-0.5" aria-hidden="true">
        {BINS.map((b) => (
          <span
            key={b}
            className={cx(
              "h-3 w-3.5 rounded-[3px] border",
              b === rule.place_in ? "border-accent-600 bg-accent-500" : "border-zinc-300 bg-white",
            )}
          />
        ))}
      </span>
      <span className="text-[11px] font-medium text-zinc-500">{binLabel(rule.place_in).replace(" bin", "")}</span>
    </span>
  );
}

function SelectorFields({ value, onChange }: { value: Selector; onChange: (s: Selector) => void }) {
  return (
    <>
      <Select
        aria-label="Size"
        value={value.size ?? ""}
        onChange={(e) => onChange({ ...value, size: (e.target.value || null) as Size | null })}
      >
        <option value="">Any size</option>
        {SIZES.map((s) => (
          <option key={s} value={s}>
            {s}
          </option>
        ))}
      </Select>
      <Select
        aria-label="Color"
        value={value.color ?? ""}
        onChange={(e) => onChange({ ...value, color: (e.target.value || null) as Color | null })}
      >
        <option value="">Any color</option>
        {COLORS.map((c) => (
          <option key={c} value={c}>
            {c}
          </option>
        ))}
      </Select>
      <Select
        aria-label="Shape"
        value={value.shape ?? ""}
        onChange={(e) => onChange({ ...value, shape: (e.target.value || null) as Shape | null })}
      >
        <option value="">Any shape</option>
        {SHAPES.map((s) => (
          <option key={s} value={s}>
            {s}
          </option>
        ))}
      </Select>
      <Select
        aria-label="Fragile"
        value={value.fragile == null ? "" : value.fragile ? "true" : "false"}
        onChange={(e) =>
          onChange({ ...value, fragile: e.target.value === "" ? null : e.target.value === "true" })
        }
      >
        <option value="">Fragile or not</option>
        <option value="true">Fragile</option>
        <option value="false">Not fragile</option>
      </Select>
    </>
  );
}

function RuleEditor({
  rule,
  index,
  onChange,
  onRemove,
}: {
  rule: Rule;
  index: number;
  onChange: (r: Rule) => void;
  onRemove: () => void;
}) {
  const action = rule.leave ? "leave" : (rule.place_in ?? "left_bin");
  return (
    <li className="rounded-lg border border-zinc-200 bg-zinc-50/60 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="mr-1 text-xs font-medium text-zinc-500">Rule {index + 1}</span>
        <SelectorFields value={rule.select} onChange={(select) => onChange({ ...rule, select })} />
        <IconArrowRight size={14} className="text-zinc-400" />
        <Select
          aria-label="Action"
          value={action}
          onChange={(e) => {
            const v = e.target.value;
            onChange(
              v === "leave"
                ? { ...rule, leave: true, place_in: null }
                : { ...rule, leave: false, place_in: v as BinName },
            );
          }}
        >
          {BINS.map((b) => (
            <option key={b} value={b}>
              Put in {binLabel(b)}
            </option>
          ))}
          <option value="leave">Leave in place</option>
        </Select>
        <Button variant="ghost" size="xs" onClick={onRemove} aria-label={`Remove rule ${index + 1}`} className="ml-auto">
          <IconTrash size={14} />
        </Button>
      </div>
      <p className="mt-2 text-xs text-zinc-500">{ruleSentence(rule)}</p>
    </li>
  );
}

function CheckEditor({
  check,
  onChange,
  onRemove,
}: {
  check: Check;
  onChange: (c: Check) => void;
  onRemove: () => void;
}) {
  return (
    <li className="rounded-lg border border-zinc-200 bg-zinc-50/60 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Select
          aria-label="Check kind"
          value={check.kind}
          onChange={(e) => {
            const kind = e.target.value as CheckKind;
            onChange({ ...check, kind, bin: kind === "in_bin" ? (check.bin ?? "left_bin") : null });
          }}
        >
          {CHECK_KINDS.map((k) => (
            <option key={k} value={k}>
              {kindLabel(k)}
            </option>
          ))}
        </Select>
        <SelectorFields value={check.select} onChange={(select) => onChange({ ...check, select })} />
        {check.kind === "in_bin" && (
          <Select
            aria-label="Bin"
            value={check.bin ?? ""}
            onChange={(e) => onChange({ ...check, bin: (e.target.value || null) as BinName | null })}
          >
            {BINS.map((b) => (
              <option key={b} value={b}>
                {binLabel(b)}
              </option>
            ))}
          </Select>
        )}
        <Button variant="ghost" size="xs" onClick={onRemove} aria-label={`Remove check ${check.id}`} className="ml-auto">
          <IconTrash size={14} />
        </Button>
      </div>
      <div className="mt-2 flex flex-col gap-2 sm:flex-row">
        <input
          aria-label="Check sentence"
          value={check.text}
          placeholder={checkSentence(check)}
          onChange={(e) => onChange({ ...check, text: e.target.value })}
          className="h-8 min-w-0 flex-1 rounded-lg border border-zinc-200 bg-white px-2.5 text-xs text-zinc-900 shadow-sm placeholder:text-zinc-400 focus:border-accent-500 focus:outline-none focus:ring-2 focus:ring-accent-500/30"
        />
        <input
          aria-label="Check id"
          value={check.id}
          onChange={(e) => onChange({ ...check, id: e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_") })}
          className="h-8 w-full rounded-lg border border-zinc-200 bg-white px-2.5 font-mono text-[11px] text-zinc-700 shadow-sm focus:border-accent-500 focus:outline-none focus:ring-2 focus:ring-accent-500/30 sm:w-40"
        />
      </div>
    </li>
  );
}

// ------------------------------------------------------------------ the card
export function SpecCard({
  spec,
  source,
  mediaPath,
  clarifyingQuestion,
  awaiting,
  approved,
  edited,
  onApprove,
  pendingText,
  readOnlyNote,
}: SpecCardProps) {
  const [draft, setDraft] = useState<TaskSpec | null>(null);
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!awaiting) {
      setDraft(null);
      setSent(false);
    }
  }, [awaiting]);

  const canEdit = Boolean(onApprove) && awaiting && !approved && !sent;
  const errors = useMemo(() => (draft ? validateSpec(draft) : []), [draft]);
  const changed = useMemo(
    () =>
      draft != null &&
      spec != null &&
      JSON.stringify(normalizeSpec(draft)) !== JSON.stringify(normalizeSpec(spec)),
    [draft, spec],
  );

  async function approve() {
    if (!onApprove) return;
    setBusy(true);
    setError(null);
    try {
      await onApprove(changed && draft ? normalizeSpec(draft) : null);
      setSent(true);
      setDraft(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const question = clarifyingQuestion ?? spec?.clarifying_question ?? null;
  const statusChip = approved ? (
    <Chip tone="success" icon={<IconCheck size={12} strokeWidth={2.6} />}>
      {edited ? "Approved with edits" : "Approved"}
    </Chip>
  ) : sent ? (
    <Chip tone="accent" icon={<Spinner size={11} />}>
      Approving
    </Chip>
  ) : awaiting ? (
    <Chip tone="warning" icon={<span className="h-1.5 w-1.5 rounded-full bg-amber-500" />}>
      Needs your approval
    </Chip>
  ) : null;
  const sourceChip = source ? (
    <Chip
      tone="muted"
      title={
        source === "given"
          ? "The job came with this spec; Muse Wonder did not write it"
          : mediaPath && mediaPath !== "none"
            ? `Muse Wonder saw the ${mediaPath}`
            : undefined
      }
    >
      {SOURCE_LABEL[source]}
    </Chip>
  ) : null;

  const highlight = awaiting && !approved && !sent;

  return (
    <Card className={cx(highlight && "ring-2 ring-amber-300/70")}>
      <CardHeader
        title="Task spec"
        subtitle="What the program must achieve, as rules and pass/fail checks"
        actions={
          <>
            {sourceChip}
            {statusChip}
          </>
        }
      />
      {!spec ? (
        <div className="space-y-3 px-4 py-5 sm:px-5">
          <p className="flex items-center gap-2 text-sm text-zinc-500">
            <Spinner size={14} /> {pendingText}
          </p>
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-4 w-2/3" />
          <Skeleton className="h-4 w-1/2" />
        </div>
      ) : draft ? (
        <div className="space-y-5 px-4 py-4 sm:px-5">
          <section>
            <h3 className="text-xs font-medium uppercase tracking-wide text-zinc-500">Rules</h3>
            <ul className="mt-2 space-y-2">
              {draft.rules.map((r, i) => (
                <RuleEditor
                  key={i}
                  rule={r}
                  index={i}
                  onChange={(nr) => setDraft({ ...draft, rules: draft.rules.map((x, j) => (j === i ? nr : x)) })}
                  onRemove={() => setDraft({ ...draft, rules: draft.rules.filter((_, j) => j !== i) })}
                />
              ))}
            </ul>
            <Button
              size="sm"
              variant="ghost"
              className="mt-2"
              onClick={() =>
                setDraft({ ...draft, rules: [...draft.rules, { select: {}, place_in: "left_bin", leave: false }] })
              }
            >
              <IconPlus size={14} /> Add rule
            </Button>
          </section>
          <section>
            <h3 className="text-xs font-medium uppercase tracking-wide text-zinc-500">Checks</h3>
            <ul className="mt-2 space-y-2">
              {draft.checks.map((c, i) => (
                <CheckEditor
                  key={i}
                  check={c}
                  onChange={(nc) => setDraft({ ...draft, checks: draft.checks.map((x, j) => (j === i ? nc : x)) })}
                  onRemove={() => setDraft({ ...draft, checks: draft.checks.filter((_, j) => j !== i) })}
                />
              ))}
            </ul>
            <Button
              size="sm"
              variant="ghost"
              className="mt-2"
              onClick={() =>
                setDraft({
                  ...draft,
                  checks: [
                    ...draft.checks,
                    {
                      id: uniqueId("check", draft.checks.map((c) => c.id)),
                      kind: "untouched",
                      select: {},
                      bin: null,
                      text: "",
                    },
                  ],
                })
              }
            >
              <IconPlus size={14} /> Add check
            </Button>
          </section>
        </div>
      ) : (
        <div className="divide-y divide-zinc-100">
          <section className="px-4 py-4 sm:px-5">
            <h3 className="text-xs font-medium uppercase tracking-wide text-zinc-500">Rules</h3>
            <ul className="mt-2.5 space-y-2.5">
              {spec.rules.map((r, i) => (
                <li key={i} className="flex items-start gap-3">
                  <span className="mt-px flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-zinc-100 text-[11px] font-semibold text-zinc-500">
                    {i + 1}
                  </span>
                  <span className="min-w-0 flex-1 text-sm text-zinc-900">{ruleSentence(r)}</span>
                  <span className="mt-0.5 shrink-0">
                    <BinTag rule={r} />
                  </span>
                </li>
              ))}
            </ul>
          </section>
          <section className="px-4 py-4 sm:px-5">
            <h3 className="text-xs font-medium uppercase tracking-wide text-zinc-500">
              Checks <span className="font-normal normal-case tracking-normal text-zinc-400">· every test scene must pass all of them</span>
            </h3>
            <ul className="mt-2.5 space-y-2.5">
              {spec.checks.map((c) => (
                <li key={c.id} className="flex items-start gap-3">
                  <span className="mt-px w-[78px] shrink-0">
                    <Chip tone="neutral">{kindLabel(c.kind)}</Chip>
                  </span>
                  <span className="min-w-0 flex-1 text-sm text-zinc-800">
                    {c.text || checkSentence(c)}
                    <span className="block text-xs text-zinc-400">
                      {describeSelector(c.select)}
                      {c.kind === "in_bin" && c.bin ? ` · ${binLabel(c.bin)}` : ""}
                    </span>
                  </span>
                  <code className="hidden shrink-0 pt-0.5 font-mono text-[11px] text-zinc-400 sm:block">{c.id}</code>
                </li>
              ))}
            </ul>
          </section>
          {spec.rationale && (
            <details className="group px-4 py-3 sm:px-5">
              <summary className="cursor-pointer list-none text-xs font-medium text-zinc-500 hover:text-zinc-800">
                <span className="inline-block transition-transform group-open:rotate-90">›</span> Why these rules
              </summary>
              <p className="mt-2 text-sm leading-relaxed text-zinc-600">{spec.rationale}</p>
            </details>
          )}
        </div>
      )}

      {spec && question && (
        <div className="px-4 pb-4 sm:px-5">
          <Banner tone="warning" icon={<IconQuestion size={16} />} title="Question from the planner">
            {question}
            {canEdit && <span className="block text-xs opacity-80">Answer it by editing the spec, then approve.</span>}
          </Banner>
        </div>
      )}

      {spec && (canEdit || (draft && onApprove)) && (
        <div className="flex flex-col gap-3 border-t border-zinc-100 bg-zinc-50/60 px-4 py-3 sm:px-5">
          {draft && errors.length > 0 && (
            <ul className="list-disc space-y-0.5 pl-5 text-xs text-rose-700">
              {errors.map((e) => (
                <li key={e}>{e}</li>
              ))}
            </ul>
          )}
          {error && <p className="text-xs text-rose-700">Could not send the approval: {error}</p>}
          <div className="flex flex-wrap items-center justify-end gap-2">
            <p className="mr-auto text-xs text-zinc-500">
              {draft
                ? changed
                  ? "Your edits replace the proposed spec."
                  : "No changes yet."
                : "The program is written and tested only after you approve."}
            </p>
            {draft ? (
              <Button size="sm" variant="ghost" onClick={() => setDraft(null)} disabled={busy}>
                Cancel
              </Button>
            ) : (
              <Button size="sm" onClick={() => setDraft(normalizeSpec(spec))} disabled={busy}>
                <IconPencil size={14} /> Edit
              </Button>
            )}
            <Button
              size="sm"
              variant="primary"
              onClick={approve}
              disabled={busy || (draft != null && errors.length > 0)}
            >
              {busy ? <Spinner size={13} /> : <IconCheck size={14} strokeWidth={2.4} />}
              {draft && changed ? "Approve edited spec" : "Approve and run"}
            </Button>
          </div>
        </div>
      )}

      {spec && awaiting && !onApprove && readOnlyNote && (
        <div className="border-t border-zinc-100 bg-zinc-50/60 px-4 py-3 text-xs text-zinc-500 sm:px-5">
          {readOnlyNote}
        </div>
      )}
    </Card>
  );
}
