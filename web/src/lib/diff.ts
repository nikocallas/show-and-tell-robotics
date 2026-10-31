/** Line diff (longest common subsequence). Programs are a few dozen lines, so O(n*m) is fine. */

export type DiffKind = "equal" | "add" | "del";

export interface DiffLine {
  kind: DiffKind;
  text: string;
  oldNo: number | null;
  newNo: number | null;
}

export function splitLines(text: string): string[] {
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  if (lines.length > 0 && lines[lines.length - 1] === "") lines.pop();
  return lines;
}

const MAX_CELLS = 4_000_000;

export function diffLines(oldText: string, newText: string): DiffLine[] {
  const a = splitLines(oldText);
  const b = splitLines(newText);
  const out: DiffLine[] = [];
  let oldNo = 1;
  let newNo = 1;
  const equal = (text: string) => out.push({ kind: "equal", text, oldNo: oldNo++, newNo: newNo++ });
  const del = (text: string) => out.push({ kind: "del", text, oldNo: oldNo++, newNo: null });
  const add = (text: string) => out.push({ kind: "add", text, oldNo: null, newNo: newNo++ });

  // Common prefix and suffix need no table.
  let start = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start++;
  let endA = a.length;
  let endB = b.length;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) {
    endA--;
    endB--;
  }
  for (let i = 0; i < start; i++) equal(a[i]);

  const A = a.slice(start, endA);
  const B = b.slice(start, endB);
  if (A.length * B.length > MAX_CELLS) {
    A.forEach(del);
    B.forEach(add);
  } else {
    const cols = B.length + 1;
    const dp = new Uint32Array((A.length + 1) * cols);
    for (let i = A.length - 1; i >= 0; i--) {
      for (let j = B.length - 1; j >= 0; j--) {
        dp[i * cols + j] =
          A[i] === B[j]
            ? dp[(i + 1) * cols + j + 1] + 1
            : Math.max(dp[(i + 1) * cols + j], dp[i * cols + j + 1]);
      }
    }
    let i = 0;
    let j = 0;
    while (i < A.length && j < B.length) {
      if (A[i] === B[j]) {
        equal(A[i]);
        i++;
        j++;
      } else if (dp[(i + 1) * cols + j] >= dp[i * cols + j + 1]) {
        del(A[i]);
        i++;
      } else {
        add(B[j]);
        j++;
      }
    }
    while (i < A.length) del(A[i++]);
    while (j < B.length) add(B[j++]);
  }
  for (let i = endA; i < a.length; i++) equal(a[i]);
  return out;
}

export function diffStats(lines: DiffLine[]): { added: number; removed: number } {
  let added = 0;
  let removed = 0;
  for (const l of lines) {
    if (l.kind === "add") added++;
    else if (l.kind === "del") removed++;
  }
  return { added, removed };
}
