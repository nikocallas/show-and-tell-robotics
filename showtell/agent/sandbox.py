"""AST whitelist for generated robot programs (CLAUDE.md 5.6).

A program is exactly one `def run(scene):` written in a small subset of Python: `for`/`if`, plain
assignment, calls to the skill API (scene.objects, pick, place, move_to, avoid) and to len, sorted,
range, min and max, reads of object fields (obj.color, objs[0].position) and comprehensions without
side effects. `validate_program` rejects everything else with one "line N: reason" per problem. The
reasons are fed back to the model, so each one says what to write instead.

This is the static half of the sandbox. The other half runs the program in a spawned subprocess
with no builtins except that whitelist, range() capped at 1000 items and a wall-clock timeout
(showtell.sim.episode.exec_program via showtell.verify.runner).
"""

from __future__ import annotations

import ast
import inspect
import re
import textwrap

from showtell.sim.skills import OBJ_FIELDS, SELECT_KEYS, SKILL_NAMES, SceneView, SkillAPI

MAX_SOURCE_CHARS = 6000
MAX_NESTING = 8  # nested for/if blocks inside run
MAX_EXPR_DEPTH = 20  # also keeps compile() clear of its recursion limit
MAX_REPORTED = 8  # problems listed in the error message

BUILTIN_NAMES = ("len", "sorted", "range", "min", "max")  # = showtell.sim.episode.SAFE_BUILTINS
CALLABLE_NAMES = (*SKILL_NAMES, *BUILTIN_NAMES)  # plus scene.objects
COMPREHENSION_CALLS = (
    "len",
    "min",
    "max",
    "sorted",
)  # allowed in a comprehension's value/condition
PROTECTED_NAMES = frozenset({"scene", *CALLABLE_NAMES})  # cannot be assigned
KEYWORDS = ("avoid", "margin_m", *SELECT_KEYS)  # the only keyword arguments a program may pass
CONSTANT_TYPES = (str, int, float, bool, type(None))


def _signature(fn) -> inspect.Signature:
    sig = inspect.signature(fn)
    return sig.replace(parameters=[p for p in sig.parameters.values() if p.name != "self"])


# Taken from showtell.sim.skills so the checks cannot drift from the real API.
SIGNATURES: dict[str, inspect.Signature] = {
    "scene.objects": _signature(SceneView.objects),
    **{name: _signature(getattr(SkillAPI, name)) for name in SKILL_NAMES},
}
CALL_KEYWORDS: dict[str, tuple[str, ...]] = {
    **{name: tuple(p for p in sig.parameters if p in KEYWORDS) for name, sig in SIGNATURES.items()},
    **{name: () for name in BUILTIN_NAMES},
}
BUILTIN_ARITY: dict[str, tuple[int, int | None]] = {
    "len": (1, 1),
    "sorted": (1, 1),
    "range": (1, 3),
    "min": (1, None),
    "max": (1, None),
}

_NAMES = ", ".join(CALLABLE_NAMES)
_CALLABLES = f"scene.objects, {_NAMES}"
_FIELDS = ", ".join(OBJ_FIELDS)
_BODY, _COMP, _ITER = "body", "comprehension value", "comprehension iterator"
_DUNDER = "names containing double underscores are not allowed"
_IMPORT = (
    "imports are not allowed; scene, pick, place, move_to, avoid, len, sorted, range, min and max "
    "are already available"
)
_TRY = "try/except is not allowed; a failing skill stops the program and is reported"
_DICT = (
    "dicts are not allowed; write one for loop per group, e.g. "
    'for obj in scene.objects(size="large"): ...'
)
_STMT_REASONS: dict[type, str] = {
    ast.Import: _IMPORT,
    ast.ImportFrom: _IMPORT,
    ast.While: "while loops are not allowed; loop over a list with for, e.g. "
    "for obj in scene.objects(...)",
    ast.With: "`with` blocks are not allowed",
    ast.AsyncWith: "`with` blocks are not allowed",
    ast.Try: _TRY,
    ast.TryStar: _TRY,
    ast.Raise: "raise is not allowed",
    ast.Global: "global/nonlocal declarations are not allowed",
    ast.Nonlocal: "global/nonlocal declarations are not allowed",
    ast.ClassDef: "classes are not allowed",
    ast.FunctionDef: "nested functions are not allowed; write the code directly inside run",
    ast.AsyncFunctionDef: "nested functions are not allowed; write the code directly inside run",
    ast.AugAssign: "augmented assignment (x += ...) is not allowed; programs need no arithmetic",
    ast.AnnAssign: "annotated assignment is not allowed; write name = value",
    ast.Delete: "del is not allowed",
    ast.Assert: "assert is not allowed",
    ast.Match: "match statements are not allowed; use if/elif",
    ast.AsyncFor: "async for is not allowed; use a plain for loop",
}
_EXPR_REASONS: dict[type, str] = {
    ast.Lambda: "lambda is not allowed; write the code inline",
    ast.NamedExpr: "the walrus operator (:=) is not allowed; assign on its own line",
    ast.JoinedStr: "f-strings are not allowed",
    ast.FormattedValue: "f-strings are not allowed",
    ast.Starred: "star-unpacking (*x) is not allowed",
    ast.Dict: _DICT,
    ast.DictComp: _DICT,
    ast.Set: "sets are not allowed; use lists",
    ast.SetComp: "sets are not allowed; use lists",
    ast.Await: "await is not allowed",
    ast.Yield: "yield is not allowed",
    ast.YieldFrom: "yield is not allowed",
    ast.IfExp: "conditional expressions (a if c else b) are not allowed; use an if statement",
    ast.Slice: "slices (a[i:j]) are not allowed; index one item at a time, e.g. objs[0]",
}
_OPERATORS = {
    ast.Add: "+",
    ast.Sub: "-",
    ast.Mult: "*",
    ast.MatMult: "@",
    ast.Div: "/",
    ast.FloorDiv: "//",
    ast.Mod: "%",
    ast.Pow: "**",
    ast.LShift: "<<",
    ast.RShift: ">>",
    ast.BitOr: "|",
    ast.BitXor: "^",
    ast.BitAnd: "&",
}
_KINDS = {
    ast.Assign: "assignment",
    ast.Expr: "expression",
    ast.If: "if statement",
    ast.For: "for loop",
    ast.Return: "return",
}


class SandboxError(ValueError):
    """The program is outside the whitelist. `str(err)` has one "line N: reason" per problem."""

    def __init__(
        self, message: str, problems: list[str] | None = None, source: str | None = None
    ) -> None:
        super().__init__(message)
        self.problems = problems if problems is not None else [message]
        self.source = source


# ---------------------------------------------------------------------- fences
_FENCE = re.compile(r"```[ \t]*[\w+.-]*[ \t]*\n(.*?)(?:```|\Z)", re.DOTALL)
_FENCE_LINE = re.compile(r"^\s*```[\w+.-]*\s*$")


def strip_fences(text: str) -> str:
    """The program inside markdown code fences (```python ... ```), or the text itself.

    With several fenced blocks the first one containing `def run` wins. Line endings are
    normalized, stray fence lines and surrounding blank lines dropped, common indentation removed.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    blocks = _FENCE.findall(text)
    if blocks:
        text = next((b for b in blocks if "def run" in b), blocks[0])
    lines = textwrap.dedent(text).split("\n")
    while lines and (not lines[0].strip() or _FENCE_LINE.match(lines[0])):
        lines.pop(0)
    while lines and (not lines[-1].strip() or _FENCE_LINE.match(lines[-1])):
        lines.pop()
    return textwrap.dedent("\n".join(lines)) + "\n" if lines else ""


# ---------------------------------------------------------------------- AST helpers
def _is_scene(node: ast.AST) -> bool:
    return isinstance(node, ast.Name) and node.id == "scene"


def _is_docstring(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _is_number(node: ast.AST, types: tuple[type, ...] = (int, float)) -> bool:
    """A number literal, including a negative one (-1 is UnaryOp(USub, 1) in the AST)."""
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        node = node.operand
    return isinstance(node, ast.Constant) and type(node.value) in types


def _is_singleton(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and (node.value is None or type(node.value) is bool)


def _describe(node: ast.AST) -> str:
    if isinstance(node, ast.Call):
        return "a call result (index it or assign it to a variable first)"
    if isinstance(node, ast.Attribute):
        return "another attribute (no chains like obj.position.x)"
    if isinstance(node, ast.Constant):
        return "a constant"
    return "this expression"


def _syntax_reason(exc: SyntaxError) -> str:
    where = f"line {exc.lineno}: " if exc.lineno else ""
    return f"{where}syntax error: {exc.msg}"


class _Checker:
    """Walks `def run(scene)` and collects (line, reason) problems."""

    def __init__(self) -> None:
        self.problems: list[tuple[int, str]] = []
        self._reasons: set[str] = set()
        self.assigned: set[str] = set()
        self.loads: list[ast.Name] = []

    def bad(self, node: ast.AST | None, reason: str) -> None:
        if reason not in self._reasons:  # one report per distinct reason
            self._reasons.add(reason)
            self.problems.append((getattr(node, "lineno", 0) or 0, reason))

    def messages(self) -> list[str]:
        ordered = sorted(self.problems, key=lambda p: p[0])
        return [f"line {line}: {reason}" if line else reason for line, reason in ordered]

    # ------------------------------------------------------------------ module and signature
    def module(self, tree: ast.Module) -> None:
        funcs = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for node in tree.body:
            if node in funcs or _is_docstring(node):
                continue
            if type(node) in _STMT_REASONS:
                self.bad(node, _STMT_REASONS[type(node)])
            elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                self.bad(node, "top-level calls are not allowed; the test harness calls run(scene)")
            else:
                kind = _KINDS.get(type(node), type(node).__name__)
                self.bad(
                    node,
                    f"only `def run(scene):` may appear at the top level; move this "
                    f"{kind} into run",
                )
        runs = [f for f in funcs if f.name == "run"]
        if not runs:
            found = f" (found def {', '.join(f.name for f in funcs)})" if funcs else ""
            self.bad(None, f"the program must define `def run(scene):`{found}")
            return
        for f in funcs:
            if f.name != "run":
                self.bad(
                    f,
                    f"only one function is allowed, def run(scene); put the code of "
                    f"`{f.name}` inside run",
                )
        for dup in runs[1:]:
            self.bad(dup, "run is defined twice; keep a single def run(scene)")
        run = runs[0]
        if isinstance(run, ast.AsyncFunctionDef):
            self.bad(run, "run must be a plain `def`, not `async def`")
            return
        self.function(run)

    def function(self, fn: ast.FunctionDef) -> None:
        if fn.decorator_list:
            self.bad(
                fn.decorator_list[0], "decorators are not allowed; write a plain `def run(scene):`"
            )
        if getattr(fn, "type_params", None):
            self.bad(fn, "type parameters are not allowed; write `def run(scene):`")
        a = fn.args
        extra = a.posonlyargs or a.kwonlyargs or a.vararg or a.kwarg or a.defaults
        if extra or [arg.arg for arg in a.args] != ["scene"]:
            self.bad(
                fn,
                "the signature must be exactly `def run(scene):` (one argument named "
                "scene; no defaults, *args or **kwargs)",
            )
        params = [*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg]
        annotations = [p.annotation for p in params if p is not None] + [fn.returns]
        for ann in annotations:
            if ann is not None and not (
                isinstance(ann, ast.Constant) and (ann.value is None or isinstance(ann.value, str))
            ):
                self.bad(ann, "type annotations are not allowed; write `def run(scene):`")
        self.block(fn.body, 0)
        known = {"scene", "run", *CALLABLE_NAMES} | self.assigned
        for name in self.loads:
            if name.id not in known:
                self.bad(
                    name,
                    f"name `{name.id}` is not defined; only scene, {_NAMES} and "
                    "variables you assign exist",
                )

    # ------------------------------------------------------------------ statements
    def block(self, body: list[ast.stmt], level: int) -> None:
        for node in body:
            self.stmt(node, level)

    def stmt(self, node: ast.stmt, level: int) -> None:
        if level > MAX_NESTING:
            self.bad(
                node, f"blocks are nested more than {MAX_NESTING} levels deep; flatten the program"
            )
            return
        if isinstance(node, ast.For):
            self.target(node.target)
            self.expr(node.iter, 0, _BODY)
            self.block(node.body, level + 1)
            self.block(node.orelse, level + 1)
        elif isinstance(node, ast.If):
            self.expr(node.test, 0, _BODY)
            self.block(node.body, level + 1)
            if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
                self.stmt(node.orelse[0], level)  # elif stays on the same level
            else:
                self.block(node.orelse, level + 1)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                self.target(target)
            self.expr(node.value, 0, _BODY)
        elif isinstance(node, ast.Expr):
            self.expr(node.value, 0, _BODY)
        elif isinstance(node, ast.Return):
            if node.value is not None:
                self.expr(node.value, 0, _BODY)
        elif not isinstance(node, (ast.Pass, ast.Break, ast.Continue)):
            reason = _STMT_REASONS.get(type(node), f"{type(node).__name__} is not allowed")
            self.bad(node, reason)

    def target(self, node: ast.expr) -> None:
        """Assignment / loop targets: plain names or tuples of them."""
        if isinstance(node, ast.Name):
            if "__" in node.id:
                self.bad(node, f"`{node.id}`: {_DUNDER}")
            elif node.id in PROTECTED_NAMES:
                self.bad(
                    node,
                    f"`{node.id}` is a built-in name and cannot be assigned; use "
                    "another variable name",
                )
            else:
                self.assigned.add(node.id)
        elif isinstance(node, (ast.Tuple, ast.List)):
            for elt in node.elts:
                self.target(elt)
        elif isinstance(node, ast.Attribute):
            self.bad(node, "object fields are read-only; assign to a plain variable name")
        elif isinstance(node, ast.Subscript):
            self.bad(
                node,
                "assigning into a list (x[i] = ...) is not allowed; assign to a plain "
                "variable name",
            )
        elif isinstance(node, ast.Starred):
            self.bad(node, "starred assignment (*x) is not allowed")
        else:
            self.bad(node, "you can only assign to plain variable names")

    # ------------------------------------------------------------------ expressions
    def expr(self, node: ast.expr, depth: int, where: str) -> None:
        if depth > MAX_EXPR_DEPTH:
            self.bad(node, "expression is nested too deeply; split it over several assignments")
            return
        d = depth + 1
        if isinstance(node, ast.Constant):
            if type(node.value) not in CONSTANT_TYPES:
                self.bad(
                    node,
                    f"constant `{ast.unparse(node)[:30]}` is not allowed; use strings, "
                    "numbers, True, False or None",
                )
        elif isinstance(node, ast.Name):
            if "__" in node.id:
                self.bad(node, f"`{node.id}`: {_DUNDER}")
            else:
                self.loads.append(node)
        elif isinstance(node, ast.Attribute):
            self.attribute(node, d, where)
        elif isinstance(node, ast.Call):
            self.call(node, d, where)
        elif isinstance(node, ast.Subscript):
            self.subscript(node, d, where)
        elif isinstance(node, (ast.List, ast.Tuple)):
            for elt in node.elts:
                self.expr(elt, d, where)
        elif isinstance(node, ast.Compare):
            self.expr(node.left, d, where)
            for op, right in zip(node.ops, node.comparators, strict=True):
                if isinstance(op, (ast.Is, ast.IsNot)) and not _is_singleton(right):
                    self.bad(
                        node,
                        "`is` / `is not` only compare with None, True or False; use == "
                        "or != for values",
                    )
                self.expr(right, d, where)
        elif isinstance(node, ast.BoolOp):
            for value in node.values:
                self.expr(value, d, where)
        elif isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.Not):
                self.expr(node.operand, d, where)
            elif not _is_number(node):
                self.bad(
                    node, "only `not` and negative number literals are allowed as unary operators"
                )
        elif isinstance(node, (ast.ListComp, ast.GeneratorExp)):
            self.comprehension(node, d, where)
        elif isinstance(node, ast.BinOp):
            op = _OPERATORS.get(type(node.op), type(node.op).__name__)
            hint = (
                "write one for loop per scene.objects(...) list instead of joining lists"
                if isinstance(node.op, ast.Add)
                else "select objects by attribute instead of computing values"
            )
            self.bad(node, f"arithmetic ({op}) is not allowed; {hint}")
        else:
            reason = _EXPR_REASONS.get(type(node), f"{type(node).__name__} is not allowed")
            self.bad(node, reason)

    def attribute(self, node: ast.Attribute, depth: int, where: str) -> None:
        """Reads of Obj fields on a variable or an indexed item (scene.objects must be called)."""
        recv, attr = node.value, node.attr
        if "__" in attr:
            self.bad(node, f"`.{attr}`: {_DUNDER}")
        elif _is_scene(recv):
            if attr == "objects":
                self.bad(node, "scene.objects is a function; call it: scene.objects(...)")
            else:
                self.bad(
                    node,
                    f"scene has no attribute `{attr}`; the scene only offers scene.objects(...)",
                )
        elif not isinstance(recv, (ast.Name, ast.Subscript)):
            self.bad(
                node,
                f"`{ast.unparse(node)[:40]}`: attributes can only be read from a "
                f"variable or an indexed item (e.g. obj.color, objs[0].position[2]), not from "
                f"{_describe(recv)}",
            )
        elif attr not in OBJ_FIELDS:
            self.bad(node, f"`.{attr}` is not allowed; objects only have the fields {_FIELDS}")
        elif isinstance(recv, ast.Name) and recv.id in CALLABLE_NAMES:
            self.bad(node, f"`{recv.id}` is a function and has no fields")
        else:
            self.expr(recv, depth, where)

    def callee(self, func: ast.expr, depth: int, where: str) -> str | None:
        """Name of a whitelisted callee, or None after reporting why it cannot be called."""
        if isinstance(func, ast.Name):
            if "__" in func.id:
                self.bad(func, f"`{func.id}`: {_DUNDER}")
            elif func.id in CALLABLE_NAMES:
                return func.id
            else:
                self.bad(func, f"calling `{func.id}` is not allowed; you can call {_CALLABLES}")
        elif isinstance(func, ast.Attribute):
            if "__" in func.attr:
                self.bad(func, f"`.{func.attr}`: {_DUNDER}")
            elif _is_scene(func.value) and func.attr == "objects":
                return "scene.objects"
            elif _is_scene(func.value):
                self.bad(
                    func,
                    f"scene has no method `{func.attr}`; the scene only offers scene.objects(...)",
                )
            else:
                self.bad(
                    func,
                    f"method calls like `.{func.attr}(...)` are not allowed; you can "
                    f"call {_CALLABLES}",
                )
                self.expr(func.value, depth, where)  # e.g. __import__("os").system(...)
        else:
            self.bad(func, f"only named functions can be called; you can call {_CALLABLES}")
            self.expr(func, depth, where)
        return None

    def call(self, node: ast.Call, depth: int, where: str) -> None:
        name = self.callee(node.func, depth, where)
        if name is not None and where != _BODY:
            if name in SKILL_NAMES:
                self.bad(
                    node,
                    f"{name}() cannot be called inside a comprehension; call skills "
                    "from a for loop",
                )
            elif where == _COMP and name not in COMPREHENSION_CALLS:
                self.bad(
                    node,
                    f"{name}() cannot be called in a comprehension's value or "
                    "condition (only len, min, max and sorted can); call it in the "
                    "`for ... in` part or before the comprehension",
                )
        args_ok = True
        for arg in node.args:
            if isinstance(arg, ast.Starred):
                self.bad(arg, "star-arguments (*x) are not allowed; pass each argument explicitly")
                args_ok = False
            else:
                self.expr(arg, depth, where)
        for kw in node.keywords:
            if kw.arg is None:
                self.bad(
                    kw,
                    "**kwargs is not allowed; pass keywords one by one, e.g. "
                    'scene.objects(color="red")',
                )
                args_ok = False
                continue
            if name is not None and kw.arg not in CALL_KEYWORDS[name]:
                allowed = ", ".join(f"{k}=" for k in CALL_KEYWORDS[name])
                takes = f"its keywords are {allowed}" if allowed else "it takes no keywords"
                self.bad(kw, f"{name}() does not take `{kw.arg}=`; {takes}")
                args_ok = False
            self.expr(kw.value, depth, where)
        if name is not None and args_ok:
            self.arity(node, name)

    def arity(self, node: ast.Call, name: str) -> None:
        if name in BUILTIN_ARITY:
            lo, hi = BUILTIN_ARITY[name]
            n = len(node.args)
            if n < lo or (hi is not None and n > hi):
                want = str(lo) if lo == hi else (f"{lo} to {hi}" if hi else f"at least {lo}")
                self.bad(node, f"{name}() takes {want} argument(s), got {n}")
            return
        try:
            SIGNATURES[name].bind(*node.args, **{k.arg: k.value for k in node.keywords if k.arg})
        except TypeError as exc:
            self.bad(node, f"{name}(): {exc}")

    def subscript(self, node: ast.Subscript, depth: int, where: str) -> None:
        recv, index = node.value, node.slice
        if _is_scene(recv):
            self.bad(node, "scene cannot be indexed; use scene.objects(...) to get a list")
            return
        if isinstance(recv, ast.Name) and recv.id in CALLABLE_NAMES:
            self.bad(node, f"`{recv.id}` is a function and cannot be indexed")
            return
        self.expr(recv, depth, where)
        if isinstance(index, ast.Slice):
            self.bad(node, _EXPR_REASONS[ast.Slice])
        elif isinstance(index, ast.Name):
            self.expr(index, depth, where)
        elif not _is_number(index, (int,)):
            self.bad(node, "an index must be a whole number or a variable, e.g. objs[0] or objs[i]")

    def comprehension(self, node: ast.ListComp | ast.GeneratorExp, depth: int, where: str) -> None:
        """One `for`, no skills; value/conditions may only call len, min, max and sorted."""
        if where == _COMP:
            self.bad(
                node,
                "a comprehension cannot be nested in another comprehension's value or "
                "condition; use a for loop",
            )
            return
        if len(node.generators) != 1:
            self.bad(node, "use one `for` per comprehension; write nested for loops instead")
            return
        gen = node.generators[0]
        if gen.is_async:
            self.bad(node, "async comprehensions are not allowed")
            return
        self.target(gen.target)
        self.expr(gen.iter, depth, _ITER)
        for cond in gen.ifs:
            self.expr(cond, depth, _COMP)
        self.expr(node.elt, depth, _COMP)


# ---------------------------------------------------------------------- entry point
def validate_program(source: str) -> ast.Module:
    """Parse and check a program; return its AST or raise SandboxError("line N: reason ...").

    Every problem found is listed (one per line, at most MAX_REPORTED) so a single retry can fix
    them all; `err.problems` has the full list.
    """
    if not isinstance(source, str):
        raise SandboxError("the program source must be a string")
    if len(source) > MAX_SOURCE_CHARS:
        raise SandboxError(
            f"the program is {len(source):,} characters; the limit is {MAX_SOURCE_CHARS:,}. "
            "Write a shorter program"
        )
    if not source.strip():
        raise SandboxError("the program is empty; write `def run(scene):` and its body")
    try:
        tree = ast.parse(source, filename="<program>")
    except SyntaxError as exc:
        raise SandboxError(_syntax_reason(exc)) from None
    except (ValueError, RecursionError, MemoryError) as exc:
        raise SandboxError(f"the program could not be parsed: {exc}") from None
    checker = _Checker()
    checker.module(tree)
    problems = checker.messages()
    if problems:
        shown = problems[:MAX_REPORTED]
        if len(problems) > len(shown):
            shown.append(f"... and {len(problems) - len(shown)} more problems")
        raise SandboxError("\n".join(shown), problems=problems, source=source)
    try:
        compile(tree, "<program>", "exec")  # compile-time errors such as `break` outside a loop
    except SyntaxError as exc:
        raise SandboxError(_syntax_reason(exc), source=source) from None
    except (ValueError, RecursionError, MemoryError) as exc:
        raise SandboxError(f"the program could not be compiled: {exc}", source=source) from None
    return tree
