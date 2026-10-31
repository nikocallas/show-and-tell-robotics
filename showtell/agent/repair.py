"""Repair (CLAUDE.md 5.6, 5.8): failed test scenes -> failure report + frames -> patched program.

Muse Wonder gets the spec, the failing program, a compact report of the failed checks (grouped by
failure pattern, with the contact log that says what touched what during which skill) and frames
from one failing scene. It answers with a one-sentence diagnosis and a corrected program, which must
pass the sandbox (and differ from the failing program) before the pipeline runs it.
"""

from __future__ import annotations

import ast
import logging
import re
import textwrap
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from showtell.agent.prompts import REPAIR_INSTRUCTIONS
from showtell.agent.schemas import Check, RepairResult, TaskSpec
from showtell.meta import wonder
from showtell.perception.video_prep import keyframe_indices
from showtell.sim.scene import BIN_NAMES
from showtell.sim.skills import skill_api_doc
from showtell.verify.runner import SeedResult

log = logging.getLogger(__name__)

REPORT_MAX_CHARS = 4000
TOUCH_CHECK_KINDS = ("untouched", "upright", "unmoved")
DIAGNOSIS_MAX_CHARS = 200
MAX_OUTPUT_TOKENS = 16000  # reasoning tokens count too; only what is generated is billed
FRAME_WIDTH = 640
FRAME_STEP_S = 0.4  # spacing of failure frames around the focus time
FULL_FPS = 30.0  # full.mp4 frame rate (hero camera); the clip starts at sim time 0

_FRAME_NAME = "seed{seed}_f{i:02d}_t{t:.2f}s.jpg"
_FRAME_RE = re.compile(r"seed(-?\d+)_f(\d+)_t(\d+(?:\.\d+)?)s\.jpg$")
_OBJ_ID = re.compile(r"\b[a-z]+_[a-z]+_[a-z]+(?:_\d+)?\b")
_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?")
_FENCE = re.compile(r"```[ \t]*[A-Za-z0-9_+-]*[ \t]*\r?\n(.*?)(?:```|\Z)", re.DOTALL)
_SENTENCE_END = re.compile(r"[.!?](?=\s+[A-Z0-9\"'(\[]|\s*$)")
_LABEL = re.compile(r"^(?:diagnosis|root cause)\s*[:\-]\s*", re.IGNORECASE)
_LIST_MARK = re.compile(r"^(?:[-*•#]+|\d+[.)])\s+")
_WHO = {
    "robot": "the robot or the object it carried",
    "bin": "a bin",
    "object": "another object",
    "table": "the table",
    "world": "the scene",
}


class RepairError(ValueError):
    """The repair call returned no usable program (rejected twice)."""


# ------------------------------------------------------------------------- contact log helpers
def _mentions(text: str, obj_id: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(obj_id)}(?!\w)", text or "") is not None


def _watched_objects(result: SeedResult) -> list[str]:
    """Objects of failed untouched/upright/unmoved checks (the ones the detail names, if any)."""
    out: list[str] = []
    for c in result.failed_checks:
        if c.kind not in TOUCH_CHECK_KINDS:
            continue
        named = [o for o in c.objects if _mentions(c.detail, o)]
        out += [o for o in (named or c.objects) if o not in out]
    return out


def _touches_of(result: SeedResult, obj_id: str) -> list[dict[str, Any]]:
    events = [e for e in result.touch_events if e.get("obj") == obj_id]
    return sorted(events, key=lambda e: float(e.get("t", 0.0)))


def _first_touch(result: SeedResult) -> dict[str, Any] | None:
    events = [e for o in _watched_objects(result) for e in _touches_of(result, o)]
    return min(events, key=lambda e: float(e.get("t", 0.0)), default=None)


def _carried(during: str) -> str | None:
    """Object in the gripper during a skill phase, read from the phase text."""
    m = re.search(r"carrying (\w+)", during)
    m = m or re.match(r"pick\((\w+)\): (?:grasping|lifting)", during)
    return m.group(1) if m else None


def _by_kind(event: dict[str, Any]) -> str:
    by, during = str(event.get("by", "")), str(event.get("during", ""))
    if by == "robot" or by == _carried(during):
        return "robot"
    if by in BIN_NAMES:
        return "bin"
    return by if by in ("table", "world") else "object"


def _who(event: dict[str, Any]) -> str:
    by = str(event.get("by", "?"))
    if by == "robot":
        return "the robot"
    return f"{by} (carried)" if by == _carried(str(event.get("during", ""))) else by


def _step(during: str) -> str:
    """Skill phase without object ids and bins, e.g. 'place: moving above the bin'."""
    s = re.sub(r"\([^)]*\)", "", during or "")
    s = re.sub(r"\s+carrying\s+\w+", "", s)
    s = _OBJ_ID.sub("an object", s)
    for b in BIN_NAMES:
        s = s.replace(b, "a bin")
    return " ".join(s.split()) or "unknown step"


def _error_event(result: SeedResult) -> dict[str, Any] | None:
    return next((e for e in result.trace if e.get("event") == "error"), None)


def _error_kind(error: str | None) -> str | None:
    if not error or not error.strip():
        return None
    return _NUMBER.sub("#", error.strip().splitlines()[0])[:80]


def _pattern_key(result: SeedResult) -> tuple[Any, ...]:
    """Failed check ids + who touched first and in which skill step + the kind of program error."""
    failed = tuple(sorted(c.check_id for c in result.failed_checks))
    ev = _first_touch(result)
    touch = (_by_kind(ev), _step(str(ev.get("during", "")))) if ev else None
    return failed, touch, _error_kind(result.error)


def failure_focus(result: SeedResult) -> tuple[float, str] | None:
    """(sim time, reason) to look at: the first touch of an object in a failed untouched/upright/
    unmoved check, else the program error; None when neither is known."""
    ev = _first_touch(result)
    if ev is not None:
        return float(ev.get("t", 0.0)), (
            f'{ev.get("obj")} is first touched, by {_who(ev)} during "{ev.get("during")}"'
        )
    err = _error_event(result)
    if err is not None:
        return float(err.get("t", 0.0)), "the program error happens"
    return None


# ------------------------------------------------------------------------- failure report
def _clip(text: Any, n: int) -> str:
    s = " ".join(str(text).split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _seed_list(seeds: Sequence[int]) -> str:
    return ", ".join(str(s) for s in seeds)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _legend(spec: TaskSpec, failed: list[SeedResult]) -> str:
    """One line naming every failed check with its kind and sentence, in spec order."""
    texts: dict[str, Check] = {c.id: c for c in spec.checks}
    order = {cid: i for i, cid in enumerate(texts)}
    seen: dict[str, str] = {}
    for r in failed:
        for c in r.failed_checks:
            if c.check_id not in seen:
                spec_check = texts.get(c.check_id)
                text = f' "{_clip(spec_check.text, 110)}"' if spec_check else ""
                seen[c.check_id] = f"{c.check_id} ({c.kind}{text})"
    ids = sorted(seen, key=lambda cid: order.get(cid, len(order)))
    return "Failed checks: " + ("; ".join(seen[i] for i in ids) if ids else "none (program error)")


def _error_line(result: SeedResult) -> str:
    err = _error_event(result)
    where = ""
    if err is not None:
        t = float(err.get("t", 0.0))
        done = [
            e
            for e in result.trace
            if e.get("event") in ("pick", "place", "move_to") and float(e.get("t", 0.0)) <= t
        ]
        last = done[-1] if done else None
        after = ""
        if last is not None:
            target = last.get("obj") or ""
            if last.get("event") == "place":
                target = f"{target} -> {last.get('bin')}"
            after = f", after {last.get('event')}({target})"
        where = f" at {t:.2f} s{after}"
    first_line = (result.error or "").strip().splitlines()[0] if result.error else ""
    return f"program error{where}: {_clip(first_line, 240)}"


def _touch_line(result: SeedResult, obj: str, limit: int) -> str:
    events = _touches_of(result, obj)
    if not events:
        return f"{obj}: no contact recorded"
    parts: list[str] = []
    prev = None
    for e in events[:limit]:
        during = str(e.get("during", ""))
        where = "same step" if during == prev else f'during "{_clip(during, 100)}"'
        parts.append(f"{float(e.get('t', 0.0)):.2f} s by {_who(e)}, {where}")
        prev = during
    more = len(events) - limit
    tail = f"; +{_plural(more, 'later contact')}" if more > 0 else ""
    return f"contacts of {obj}: " + "; ".join(parts) + tail


def _misplaced_line(result: SeedResult) -> str | None:
    """What happened to objects of failed in_bin checks (picked? placed where?)."""
    notes: list[str] = []
    for c in result.failed_checks:
        if c.kind != "in_bin":
            continue
        for obj in c.objects:
            if not _mentions(c.detail, obj) or any(obj in n for n in notes):
                continue
            picks = [e for e in result.trace if e.get("event") == "pick" and e.get("obj") == obj]
            places = [e for e in result.trace if e.get("event") == "place" and e.get("obj") == obj]
            if places:
                notes.append(f"{obj} was placed in {places[-1].get('bin')}")
            elif picks:
                notes.append(f"{obj} was picked but never placed")
            else:
                notes.append(f"{obj} was never picked")
    return "history: " + "; ".join(notes) if notes else None


def _seed_block(result: SeedResult, touches: int) -> list[str]:
    checks = "; ".join(f"{c.check_id}: {_clip(c.detail, 200)}" for c in result.failed_checks)
    lines = [f"  - seed {result.seed}: " + (checks or "all checks passed")]
    if result.error:
        lines.append("    " + _error_line(result))
    lines += [f"    {_touch_line(result, o, touches)}" for o in _watched_objects(result)]
    history = _misplaced_line(result)
    if history:
        lines.append("    " + _clip(history, 300))
    return lines


def _pattern_block(i: int, group: list[SeedResult], per: int, touches: int) -> str:
    first = group[0]
    failed = [c.check_id for c in first.failed_checks]
    head = [f"failed {', '.join(failed)}" if failed else "no failed checks"]
    if first.error:
        head.append("program error")
    ev = _first_touch(first)
    if ev is not None:
        objs: list[str] = []
        for r in group:
            e = _first_touch(r)
            if e is not None and e.get("obj") not in objs:
                objs.append(str(e.get("obj")))
        head.append(
            f"first contact: {', '.join(objs)} touched by {_WHO[_by_kind(ev)]} "
            f'during "{_step(str(ev.get("during", "")))}"'
        )
    lines = [f"Pattern {i} ({_plural(len(group), 'scene')}): " + "; ".join(head) + "."]
    for r in group[:per]:
        lines += _seed_block(r, touches)
    rest = [r.seed for r in group[per:]]
    if rest:
        lines.append(f"  Same pattern in {_plural(len(rest), 'more seed')}: {_seed_list(rest)}.")
    return "\n".join(lines)


def _levels(max_seeds: int) -> list[tuple[int, int]]:
    """(example seeds per pattern, contacts per object), most detailed first."""
    out: list[tuple[int, int]] = []
    for per, touches in ((max_seeds, 3), (4, 2), (2, 2), (1, 1)):
        level = (max(1, min(per, max_seeds)), touches)
        if level not in out:
            out.append(level)
    return out


def failure_report(
    spec: TaskSpec,
    results: list[SeedResult],
    *,
    max_seeds: int = 6,
    max_chars: int = REPORT_MAX_CHARS,
) -> str:
    """Compact, model-friendly text: "k of N test scenes failed.", the passing seeds in one line,
    then one block per failure pattern with up to `max_seeds` example seeds (failed checks, program
    error, contact log of objects in failed untouched/upright/unmoved checks). Detail shrinks until
    the text fits in `max_chars`."""
    if not results:
        return "No test scenes were run."
    ordered = sorted(results, key=lambda r: r.seed)
    failed = [r for r in ordered if not r.passed]
    passing = [r.seed for r in ordered if r.passed]
    passing_line = f"Passing scenes ({len(passing)}): {_seed_list(passing)}."
    head = [
        f"{len(failed)} of {len(ordered)} test scenes failed.",
        passing_line if passing else "No scene passed.",
    ]
    if not failed:
        return "\n".join(head)
    head.append(_legend(spec, failed))
    groups: dict[tuple[Any, ...], list[SeedResult]] = {}
    for r in failed:
        groups.setdefault(_pattern_key(r), []).append(r)
    patterns = sorted(groups.values(), key=lambda g: (-len(g), g[0].seed))

    def render(blocks: list[str]) -> str:
        return "\n".join(head) + "\n\n" + "\n\n".join(blocks)

    blocks: list[str] = []
    text = ""
    for per, touches in _levels(max_seeds):
        blocks = [_pattern_block(i + 1, g, per, touches) for i, g in enumerate(patterns)]
        text = render(blocks)
        if len(text) <= max_chars:
            return text
    while len(blocks) > 1 and len(text) > max_chars:  # drop the rarest patterns
        blocks.pop()
        hidden = [r.seed for g in patterns[len(blocks) :] for r in g]
        note = f"{_plural(len(patterns) - len(blocks), 'more failure pattern')} not shown " + (
            f"(seeds {_seed_list(sorted(hidden))})."
        )
        text = render([*blocks, note])
    if len(text) > max_chars:
        text = text[: max_chars - 20].rstrip() + "\n[report truncated]"
    return text


# ------------------------------------------------------------------------- failure frames
def _full_video(seed_dir: Path, result: SeedResult) -> Path | None:
    candidates = [Path(seed_dir) / "full.mp4"]
    if result.videos.get("full"):
        candidates.append(Path(result.videos["full"]))
    return next((p for p in candidates if p.is_file()), None)


def _frame_count(cap: cv2.VideoCapture, video: Path) -> int:
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total > 0:
        return total
    counter = cv2.VideoCapture(str(video))
    try:
        while counter.grab():
            total += 1
    finally:
        counter.release()
    return total


def _frame_indices(total: int, fps: float, n: int, center_t: float | None) -> list[int]:
    """n frame indices FRAME_STEP_S apart around center_t (3 before it, the rest after), or
    evenly spaced over the whole clip when there is no focus or the clip is too short."""
    step = max(1, round(FRAME_STEP_S * fps))
    span = step * (n - 1)
    if center_t is None or span >= total:
        return keyframe_indices(total, n)
    start = round(center_t * fps) - step * ((n - 1) // 2)
    start = max(0, min(start, total - 1 - span))
    return [start + i * step for i in range(n)]


def _read_frames(cap: cv2.VideoCapture, indices: list[int]) -> list[tuple[int, np.ndarray]]:
    want = set(indices)
    got: list[tuple[int, np.ndarray]] = []
    i = 0
    while want and cap.grab():
        if i in want:
            ok, frame = cap.retrieve()
            if ok:
                got.append((i, frame))
            want.discard(i)
        i += 1
    return got


def _fit_width(frame: np.ndarray, width: int) -> np.ndarray:
    h, w = frame.shape[:2]
    if w == width:
        return frame
    interp = cv2.INTER_AREA if w > width else cv2.INTER_LINEAR
    return cv2.resize(frame, (width, max(1, round(h * width / w))), interpolation=interp)


def _stamp(frame: np.ndarray, text: str) -> None:
    font, scale, thick = cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1
    (w, h), base = cv2.getTextSize(text, font, scale, thick)
    cv2.rectangle(frame, (6, 6), (6 + w + 8, 6 + h + base + 6), (0, 0, 0), -1)
    cv2.putText(frame, text, (10, 9 + h), font, scale, (255, 255, 255), thick, cv2.LINE_AA)


def failure_frames(
    seed_dir: Path, result: SeedResult, n: int = 8, out_dir: Path | None = None
) -> list[Path]:
    """Write n JPEG frames (640 px wide, time-stamped) from seed_dir/full.mp4 around the first
    relevant failure time (see `failure_focus`), else evenly spaced. Missing video -> []."""
    video = _full_video(Path(seed_dir), result)
    if video is None or n <= 0:
        return []
    cap = cv2.VideoCapture(str(video))
    try:
        if not cap.isOpened():
            log.warning("cannot open %s", video)
            return []
        fps = float(cap.get(cv2.CAP_PROP_FPS)) or FULL_FPS
        total = _frame_count(cap, video)
        focus = failure_focus(result)
        frames = _read_frames(cap, _frame_indices(total, fps, n, focus[0] if focus else None))
    finally:
        cap.release()
    out = Path(out_dir) if out_dir is not None else Path(seed_dir) / "repair_frames"
    out.mkdir(parents=True, exist_ok=True)
    for stale in out.glob(f"seed{result.seed}_f*_t*s.jpg"):
        stale.unlink()
    paths: list[Path] = []
    for i, (k, frame) in enumerate(frames):
        t = k / fps
        img = _fit_width(frame, FRAME_WIDTH).copy()
        _stamp(img, f"seed {result.seed}  t = {t:.2f} s")
        path = out / _FRAME_NAME.format(seed=result.seed, i=i, t=t)
        cv2.imwrite(str(path), img, [cv2.IMWRITE_JPEG_QUALITY, 88])
        paths.append(path)
    return paths


# ------------------------------------------------------------------------- repair call
def strip_code_fences(text: str) -> str:
    """Program text without Markdown fences (the block with `def run` if there are several)."""
    s = (text or "").replace("\r\n", "\n")
    blocks = _FENCE.findall(s)
    if blocks:
        s = next((b for b in blocks if "def run" in b), blocks[0])
    lines = [ln.rstrip() for ln in s.split("\n") if not ln.strip().startswith("```")]
    return textwrap.dedent("\n".join(lines)).strip("\n") + "\n"


def first_sentence(text: str, limit: int = DIAGNOSIS_MAX_CHARS) -> str:
    """First sentence of the first non-empty line, without a 'Diagnosis:' label, <= limit chars."""
    s = ""
    for line in (text or "").splitlines():
        s = _LIST_MARK.sub("", line.strip()).strip("*_` ")
        s = _LABEL.sub("", s).strip("*_` ")
        if s and not re.fullmatch(r"(?i)(diagnosis|root cause)\W*", s):
            break
        s = ""
    s = " ".join(s.split())
    if not s:
        return "No diagnosis given."
    m = _SENTENCE_END.search(s)
    if m:
        s = s[: m.end()]
    if len(s) > limit:
        cut = s[: limit - 1]
        cut = cut.rsplit(" ", 1)[0] if " " in cut else cut
        return cut.rstrip(",;:- ") + "…"
    return s + "." if s[-1].isalnum() else s


def _same_program(a: str, b: str) -> bool:
    try:
        return ast.dump(ast.parse(a)) == ast.dump(ast.parse(b))
    except SyntaxError:
        return a.strip() == b.strip()


def _rejection(source: str, previous: str) -> str | None:
    """Why `source` cannot be run as the repaired program, or None."""
    from showtell.agent.sandbox import validate_program  # lazy: written alongside this module

    if not source.strip():
        return "the program is empty"
    try:
        validate_program(source)
    except (SyntaxError, ValueError) as err:  # SandboxError is a ValueError
        return f"{type(err).__name__}: {err}"
    if _same_program(source, previous):
        return "it is the same program that failed; change it so the failed checks pass"
    return None


def _frames_caption(frames: Sequence[Path], results: Sequence[SeedResult]) -> str:
    if not frames:
        return "FRAMES: none available for this round."
    found = [_FRAME_RE.search(Path(f).name) for f in frames]
    matches = [m for m in found if m is not None]
    if len(matches) != len(found) or len({m.group(1) for m in matches}) != 1:
        return f"FRAMES: the {len(frames)} images below come from one failing scene, in time order."
    seed = int(matches[0].group(1))
    times = ", ".join(f"{float(m.group(3)):.1f}" for m in matches)
    text = (
        f"FRAMES: the {len(frames)} images below come from failing seed {seed} (hero camera), "
        f"in time order, at t = {times} s."
    )
    result = next((r for r in results if r.seed == seed), None)
    focus = failure_focus(result) if result is not None else None
    if focus is not None:
        text += f" They are centered on t = {focus[0]:.2f} s, when {focus[1]}."
    return text


def _repair_text(
    spec: TaskSpec,
    program_source: str,
    results: list[SeedResult],
    frames: Sequence[Path],
    attempt: int,
) -> str:
    return "\n\n".join(
        [
            f"Repair round {attempt}.",
            "TASK SPEC (JSON):\n" + spec.model_dump_json(exclude_none=True),
            "CURRENT PROGRAM:\n```python\n" + program_source.strip() + "\n```",
            "TEST RESULTS (simulator ground truth):\n" + failure_report(spec, results),
            _frames_caption(frames, results),
        ]
    )


def _retry_text(problem: str, source: str) -> str:
    return (
        f"Your corrected program cannot be run: {_clip(problem, 600)}\n"
        f"Rejected program:\n```python\n{source.strip()}\n```\n"
        "Reply again with the JSON {diagnosis, source}, following the API rules above."
    )


def repair(
    spec: TaskSpec,
    program_source: str,
    results: list[SeedResult],
    frames: list[Path],
    *,
    attempt: int,
    tag: str = "",
) -> RepairResult:
    """Ask Muse Wonder to diagnose the failures and patch the program.

    The new source is fence-stripped and must pass `showtell.agent.sandbox.validate_program` and
    differ from `program_source`; otherwise the call is retried ONCE with the reason appended, then
    `RepairError` is raised. The diagnosis is cut to its first sentence (<= 200 chars).
    """
    instructions = REPAIR_INSTRUCTIONS.format(skill_api_doc=skill_api_doc())
    images = [Path(f) for f in frames if Path(f).is_file()]
    if len(images) != len(frames):
        log.warning("repair %d: %d frame files are missing", attempt, len(frames) - len(images))
    content = [
        wonder.text_part(_repair_text(spec, program_source, results, images, attempt)),
        *(wonder.image_part(p, detail="low") for p in images),
    ]
    tag = tag or f"repair{attempt}"

    def ask(parts: list[dict[str, Any]]) -> RepairResult:
        return wonder.call_json(
            RepairResult,
            instructions,
            parts,
            effort="high",
            max_output_tokens=MAX_OUTPUT_TOKENS,
            tag=tag,
        )

    reply = ask(content)
    source = strip_code_fences(reply.source)
    problem = _rejection(source, program_source)
    if problem is not None:
        log.warning("repair %d: program rejected (%s); retrying once", attempt, problem)
        reply = ask([*content, wonder.text_part(_retry_text(problem, source))])
        source = strip_code_fences(reply.source)
        problem = _rejection(source, program_source)
        if problem is not None:
            raise RepairError(f"repair {attempt}: the program was rejected twice: {problem}")
    return RepairResult(diagnosis=first_sentence(reply.diagnosis), source=source)
