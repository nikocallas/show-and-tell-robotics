/** A small Python tokenizer for syntax highlighting generated programs (no full grammar needed). */

export type TokenType =
  | "kw"
  | "const"
  | "builtin"
  | "skill"
  | "scene"
  | "def"
  | "kwarg"
  | "attr"
  | "string"
  | "number"
  | "comment"
  | "text";

export interface Token {
  type: TokenType;
  text: string;
}

const KEYWORDS = new Set([
  "and", "as", "assert", "break", "class", "continue", "def", "del", "elif", "else", "except",
  "finally", "for", "from", "global", "if", "import", "in", "is", "lambda", "nonlocal", "not",
  "or", "pass", "raise", "return", "try", "while", "with", "yield",
]);
const CONSTS = new Set(["None", "True", "False"]);
const BUILTINS = new Set(["len", "sorted", "range", "min", "max"]);
/** Skill functions from showtell/sim/skills.py (SKILL_NAMES) plus scene.objects. */
const SKILLS = new Set(["pick", "place", "move_to", "avoid"]);

const TOKEN_RE =
  /(#[^\n]*)|("""[\s\S]*?(?:"""|$)|'''[\s\S]*?(?:'''|$))|([rRbBfFuU]{0,2}"(?:[^"\\\n]|\\.)*"?|[rRbBfFuU]{0,2}'(?:[^'\\\n]|\\.)*'?)|(\b\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?\b)|([A-Za-z_][A-Za-z0-9_]*)|(\s+)|([^\sA-Za-z0-9_#'"]+)/g;

export function tokenize(source: string): Token[] {
  const tokens: Token[] = [];
  let prevWord = "";
  let prevSignificant = "";
  let depth = 0; // bracket depth: `name=` is a keyword argument only inside a call
  TOKEN_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = TOKEN_RE.exec(source)) !== null) {
    if (m[0] === "") {
      TOKEN_RE.lastIndex++;
      continue;
    }
    const text = m[0];
    let type: TokenType = "text";
    if (m[1]) type = "comment";
    else if (m[2] || m[3]) type = "string";
    else if (m[4]) type = "number";
    else if (m[5]) {
      const after = source.slice(TOKEN_RE.lastIndex, TOKEN_RE.lastIndex + 8);
      if (prevWord === "def") type = "def";
      else if (KEYWORDS.has(text)) type = "kw";
      else if (CONSTS.has(text)) type = "const";
      else if (prevSignificant === ".") type = text === "objects" ? "skill" : "attr";
      else if (depth > 0 && /^\s*=(?!=)/.test(after)) type = "kwarg";
      else if (SKILLS.has(text)) type = "skill";
      else if (BUILTINS.has(text)) type = "builtin";
      else if (text === "scene") type = "scene";
      prevWord = text;
    }
    if (m[7]) {
      for (const ch of text) {
        if (ch === "(" || ch === "[" || ch === "{") depth++;
        else if (ch === ")" || ch === "]" || ch === "}") depth = Math.max(0, depth - 1);
      }
    }
    if (!m[6]) {
      prevSignificant = m[5] ? text : text.trim().slice(-1);
      if (!m[5]) prevWord = "";
    }
    tokens.push({ type, text });
  }
  return tokens;
}

/** Tokens grouped into lines (multi-line strings are split across lines). */
export function tokenizeLines(source: string): Token[][] {
  const lines: Token[][] = [[]];
  for (const tok of tokenize(source.replace(/\r\n?/g, "\n"))) {
    const parts = tok.text.split("\n");
    parts.forEach((part, i) => {
      if (i > 0) lines.push([]);
      if (part) lines[lines.length - 1].push({ type: tok.type, text: part });
    });
  }
  if (lines.length > 1 && lines[lines.length - 1].length === 0) lines.pop();
  return lines;
}

export const TOKEN_CLASS: Record<TokenType, string> = {
  kw: "text-rose-700",
  const: "text-blue-700",
  builtin: "text-sky-700",
  skill: "text-accent-700 font-medium",
  scene: "text-amber-700",
  def: "text-violet-700 font-medium",
  kwarg: "text-orange-700",
  attr: "text-zinc-800",
  string: "text-emerald-700",
  number: "text-blue-700",
  comment: "text-zinc-400 italic",
  text: "text-zinc-800",
};
