/** The robot program of the selected attempt, highlighted, with a diff against the previous one. */

import { useMemo, useState } from "react";
import { diffLines, diffStats, type DiffLine } from "../lib/diff";
import { saveText } from "../lib/download";
import { tokenizeLines, TOKEN_CLASS, type Token } from "../lib/python";
import type { AttemptState } from "../state";
import { attemptTitle } from "./AttemptTimeline";
import { IconCheck, IconCode, IconCopy, IconDownload } from "./icons";
import { Button, Card, CardHeader, cx, Segmented, Skeleton } from "./ui";

function Tokens({ tokens }: { tokens: Token[] }) {
  return (
    <>
      {tokens.map((t, i) => (
        <span key={i} className={TOKEN_CLASS[t.type]}>
          {t.text}
        </span>
      ))}
    </>
  );
}

function CodeView({ source }: { source: string }) {
  const lines = useMemo(() => tokenizeLines(source), [source]);
  return (
    <pre className="overflow-x-auto py-3 font-mono text-[12.5px] leading-6">
      <code className="block min-w-max">
        {lines.map((toks, i) => (
          <div key={i} className="flex pr-4">
            <span className="w-11 shrink-0 select-none pr-4 text-right text-zinc-300">{i + 1}</span>
            <span className="whitespace-pre">
              <Tokens tokens={toks} />
            </span>
          </div>
        ))}
      </code>
    </pre>
  );
}

function highlightLine(text: string): Token[] {
  return tokenizeLines(text)[0] ?? [];
}

function DiffView({ lines }: { lines: DiffLine[] }) {
  return (
    <pre className="overflow-x-auto py-3 font-mono text-[12.5px] leading-6">
      <code className="block min-w-max">
        {lines.map((l, i) => (
          <div
            key={i}
            className={cx(
              "flex pr-4",
              l.kind === "add" && "bg-emerald-50",
              l.kind === "del" && "bg-rose-50",
            )}
          >
            <span className="w-9 shrink-0 select-none pr-2 text-right text-zinc-300">{l.oldNo ?? ""}</span>
            <span className="w-9 shrink-0 select-none pr-2 text-right text-zinc-300">{l.newNo ?? ""}</span>
            <span
              className={cx(
                "w-5 shrink-0 select-none text-center",
                l.kind === "add" ? "text-emerald-600" : l.kind === "del" ? "text-rose-600" : "text-zinc-300",
              )}
            >
              {l.kind === "add" ? "+" : l.kind === "del" ? "−" : ""}
            </span>
            <span className={cx("whitespace-pre", l.kind === "del" && "opacity-70")}>
              <Tokens tokens={highlightLine(l.text)} />
            </span>
          </div>
        ))}
      </code>
    </pre>
  );
}

export function ProgramPanel({
  attempts,
  selected,
  onSelect,
}: {
  attempts: AttemptState[];
  selected: AttemptState | null;
  onSelect: (n: number) => void;
}) {
  const [view, setView] = useState<"code" | "diff">("code");
  const [copied, setCopied] = useState(false);
  const prev = selected ? attempts.filter((a) => a.n < selected.n && a.program).pop() ?? null : null;
  const diff = useMemo(
    () => (selected?.program && prev?.program ? diffLines(prev.program, selected.program) : null),
    [selected?.program, prev?.program],
  );
  const stats = diff ? diffStats(diff) : null;
  const showDiff = view === "diff" && diff != null;
  const withPrograms = attempts.filter((a) => a.program);

  async function copy() {
    if (!selected?.program) return;
    try {
      await navigator.clipboard.writeText(selected.program);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // clipboard blocked: nothing to do
    }
  }

  return (
    <Card>
      <CardHeader
        title="Program"
        icon={<IconCode size={16} />}
        subtitle={
          selected?.program
            ? `${attemptTitle(selected)}: composes tested skills, selects objects by attribute`
            : "Written by Muse Wonder from the approved spec"
        }
        actions={
          selected?.program ? (
            <>
              {withPrograms.length > 1 && (
                <Segmented
                  label="Attempt"
                  value={selected.n}
                  onChange={onSelect}
                  options={withPrograms.map((a) => ({
                    value: a.n,
                    label: a.n === 0 ? "A0" : `R${a.n}`,
                    title: attemptTitle(a),
                  }))}
                />
              )}
              {diff && (
                <Segmented
                  label="View"
                  value={view}
                  onChange={setView}
                  options={[
                    { value: "code", label: "Code" },
                    {
                      value: "diff",
                      label: `Changes${stats ? ` +${stats.added} −${stats.removed}` : ""}`,
                      title: `Diff against ${prev ? attemptTitle(prev) : "the previous attempt"}`,
                    },
                  ]}
                />
              )}
              <Button size="sm" variant="ghost" onClick={copy} aria-label="Copy program">
                {copied ? <IconCheck size={14} /> : <IconCopy size={14} />}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() =>
                  saveText(`program_attempt${selected.n}.py`, selected.program ?? "", "text/x-python")
                }
                aria-label="Download this attempt's program"
              >
                <IconDownload size={14} />
              </Button>
            </>
          ) : undefined
        }
      />
      {selected?.program ? (
        <div>
          {showDiff && prev && (
            <p className="border-b border-zinc-100 px-4 py-2 text-xs text-zinc-500 sm:px-5">
              {attemptTitle(selected)} vs {attemptTitle(prev)}
            </p>
          )}
          <div className="bg-zinc-50/50">{showDiff && diff ? <DiffView lines={diff} /> : <CodeView source={selected.program} />}</div>
          {selected.notes && (
            <p className="border-t border-zinc-100 px-4 py-3 text-xs leading-relaxed text-zinc-600 sm:px-5">
              <span className="font-medium text-zinc-800">Notes: </span>
              {selected.notes}
            </p>
          )}
        </div>
      ) : (
        <div className="space-y-2 px-4 py-5 sm:px-5">
          <p className="text-sm text-zinc-500">
            {selected?.status === "writing" && selected.kind === "repair"
              ? "Muse Wonder is reading the failed checks and frames from a failing scene."
              : "The program appears here once it is written."}
          </p>
          <Skeleton className="h-3.5 w-2/5" />
          <Skeleton className="h-3.5 w-3/5" />
          <Skeleton className="h-3.5 w-1/2" />
        </div>
      )}
    </Card>
  );
}
