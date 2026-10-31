"""Rule sentence + demo summary -> TaskSpec (CLAUDE.md 5.6), plus deterministic spec checks.

`make_spec` asks Muse Wonder for the spec: with the SAM track summary and the demo video (or its
keyframes when the video part is rejected), or from the sentence alone (the --no-video ablation).
`validate_spec` lists problems a person should look at before approving the spec;
`ensure_checks` only adds checks that a rule is missing and never removes or edits anything. An
object a leave rule or an untouched/unmoved check keeps in place counts as one that no place rule
moves, so an added check never asks for it to end in a bin (a check that could never pass).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence, Set
from pathlib import Path
from typing import Any

from showtell.agent.prompts import SPEC_INSTRUCTIONS, SPEC_NO_VIDEO_NOTE
from showtell.agent.schemas import VOCABULARY, Check, Rule, Selector, TaskSpec, TrackSummary
from showtell.meta import wonder
from showtell.sim.scene import ObjSpec, parse_object_id

log = logging.getLogger(__name__)

SPEC_EFFORT = "high"
SPEC_MAX_TOKENS = 16000  # reasoning tokens count toward this limit
STAY_KINDS = ("untouched", "unmoved")  # check kinds that keep a "leave" rule's objects in place
CHECK_ID_MAX = 40  # Check.id pattern: ^[a-z0-9_]{1,40}$
NARROW_KEYS = ("fragile", "shape", "size", "color")  # tried in this order to exclude kept objects

Plan = dict[str, tuple[dict[str, list[int]], list[int]]]  # obj id -> ({bin: rule idx}, leave idx)


# ---------------------------------------------------------------- objects and selectors
def _objects(object_ids: Sequence[str]) -> list[ObjSpec]:
    if len(set(object_ids)) != len(object_ids):
        raise ValueError(f"duplicate object ids in {list(object_ids)}")
    return [parse_object_id(i) for i in object_ids]


def _select(objs: Sequence[ObjSpec], sel: Selector) -> list[ObjSpec]:
    """Objects matching every set field (the same rule as scene.objects and the checks)."""
    want = sel.as_dict()
    return [o for o in objs if all(o.attrs()[k] == v for k, v in want.items())]


def object_vocabulary(object_ids: Sequence[str]) -> dict[str, Any]:
    """{"objects_present": [{id, size, color, shape, fragile}], "allowed": VOCABULARY}."""
    return {
        "objects_present": [{"id": o.id, **o.attrs()} for o in _objects(object_ids)],
        "allowed": {k: list(v) for k, v in VOCABULARY.items()},
    }


# ---------------------------------------------------------------- Wonder call
def spec_instructions(*, sentence_only: bool) -> str:
    """SPEC_INSTRUCTIONS filled in; the no-video note is appended in sentence-only mode."""
    text = SPEC_INSTRUCTIONS.format(
        schema=json.dumps(TaskSpec.model_json_schema()), vocabulary=json.dumps(VOCABULARY)
    )
    return f"{text}\n\n{SPEC_NO_VIDEO_NOTE}" if sentence_only else text


def spec_request_text(
    rules_text: str,
    object_ids: Sequence[str],
    track_summary: TrackSummary | None,
    *,
    media: bool = False,
) -> str:
    """The one text part of a spec request: sentence, scene objects, and the track summary."""
    parts = [
        "Rule sentence (the user's own words; the sentence wins over the video):",
        rules_text.strip(),
        "",
        "Objects in the robot's scene (every selector must match some of them) and the allowed "
        "attribute values:",
        json.dumps(object_vocabulary(object_ids), indent=1),
    ]
    if track_summary is not None:
        parts += [
            "",
            "Demonstration summary from SAM 3.1 tracking of the video. Pixel coordinates; moved = "
            "displaced more than 5% of the frame width; end_bin = the bowl it ended in (bowls "
            "named left_bin, middle_bin, right_bin from left to right in the video); move_order "
            "0 = moved first. attrs describe the real objects, which can differ slightly from the "
            "robot's objects:",
            track_summary.model_dump_json(indent=1),
        ]
        if media:
            parts += ["", "The demonstration video (or keyframes from it, in time order) follows."]
    return "\n".join(parts)


def make_spec(
    rules_text: str,
    object_ids: Sequence[str],
    *,
    track_summary: TrackSummary | None,
    keyframes: Sequence[Path] = (),
    video_file_id: str | None = None,
    tag: str = "",
) -> tuple[TaskSpec, str]:
    """Ask Wonder (effort high) for a TaskSpec. Returns (spec, media_path).

    With a track summary, Wonder also sees the video (`video_file_id`) or, if the video part is
    rejected or absent, the keyframes: media_path is "video" or "keyframes" ("none" when neither
    is given). Without a track summary (sentence-only ablation) no media is sent, the
    SPEC_NO_VIDEO_NOTE is appended to the instructions and media_path is "none". `tag` is the
    usage-log tag as is (default "spec").
    """
    if not rules_text.strip():
        raise ValueError("make_spec needs a rule sentence")
    if not object_ids:
        raise ValueError("make_spec needs the scene's object ids")
    sentence_only = track_summary is None
    if sentence_only and (keyframes or video_file_id):
        log.info("sentence-only spec: not sending the video or keyframes")
    media = not sentence_only and bool(video_file_id or keyframes)
    instructions = spec_instructions(sentence_only=sentence_only)
    text = spec_request_text(rules_text, object_ids, track_summary, media=media)
    parts = [wonder.text_part(text)]
    sub = tag or "spec"

    def call(content: list[dict[str, Any]]) -> TaskSpec:
        return wonder.call_json(
            TaskSpec,
            instructions,
            content,
            effort=SPEC_EFFORT,
            max_output_tokens=SPEC_MAX_TOKENS,
            tag=sub,
        )

    if not media:
        return call(parts), "none"
    video = wonder.video_file_part(video_file_id) if video_file_id else None
    spec, path = wonder.send_media(call, parts, video=video, keyframes=list(keyframes), tag=sub)
    return spec, path


# ---------------------------------------------------------------- deterministic checks
def _rule_label(i: int, rule: Rule) -> str:
    return f"rule {i + 1} ({rule.select.describe()} -> {rule.place_in or 'leave'})"


def _rule_list(idx: Sequence[int]) -> str:
    return ", ".join(str(i + 1) for i in idx)


def _kept(spec: TaskSpec, objs: Sequence[ObjSpec]) -> set[str]:
    """Ids of objects that must stay where they are: a leave rule or an untouched/unmoved check
    selects them. The sentence's constraints win over a broader place rule."""
    ids = {o.id for r in spec.rules if r.leave for o in _select(objs, r.select)}
    return ids | {
        o.id for c in spec.checks if c.kind in STAY_KINDS for o in _select(objs, c.select)
    }


def _relevant(rule: Rule, checks: Sequence[Check]) -> list[Check]:
    if rule.place_in:
        return [c for c in checks if c.kind == "in_bin" and c.bin == rule.place_in]
    return [c for c in checks if c.kind in STAY_KINDS]


def _covered(
    rule: Rule, checks: Sequence[Check], objs: Sequence[ObjSpec], kept: Set[str] = frozenset()
) -> bool:
    """A place rule needs in_bin checks (same bin) over all its objects except `kept` ones; a
    leave rule needs untouched/unmoved checks. A rule matching no object needs a check with the
    same selector."""
    relevant = _relevant(rule, checks)
    targets = _select(objs, rule.select)
    if not targets:
        return any(c.select.as_dict() == rule.select.as_dict() for c in relevant)
    if rule.place_in:
        targets = [o for o in targets if o.id not in kept]
    covered = {o.id for c in relevant for o in _select(objs, c.select)}
    return all(o.id in covered for o in targets)


def _missing_selectors(
    rule: Rule, checks: Sequence[Check], objs: Sequence[ObjSpec], kept: Set[str]
) -> list[Selector]:
    """Selectors for the checks an uncovered rule needs. For a place rule they never select a
    kept object: the rule's selector when it selects none, else the selector plus one attribute
    that leaves them out (e.g. fragile: false), else one full selector per uncovered kind."""
    matched = _select(objs, rule.select)
    if not rule.place_in or not any(o.id in kept for o in matched):
        return [rule.select]
    movable = [o for o in matched if o.id not in kept]
    base = rule.select.as_dict()
    for key in NARROW_KEYS:
        values = {o.attrs()[key] for o in movable}
        if key in base or len(values) != 1:
            continue
        sel = Selector.model_validate({**base, key: values.pop()})
        if not any(o.id in kept for o in _select(objs, sel)):
            return [sel]
    # Every attribute set: matches only objects identical to `o`, which are movable too (kept
    # objects are chosen by attribute selectors, so identical objects share their fate).
    covered = {o.id for c in _relevant(rule, checks) for o in _select(objs, c.select)}
    out: list[Selector] = []
    for o in movable:
        sel = Selector.model_validate(o.attrs())
        if o.id not in covered and sel not in out:
            out.append(sel)
    return out


def _plan(spec: TaskSpec, objs: Sequence[ObjSpec]) -> Plan:
    plan: Plan = {o.id: ({}, []) for o in objs}
    for i, rule in enumerate(spec.rules):
        for o in _select(objs, rule.select):
            dests, leave = plan[o.id]
            if rule.place_in:
                dests.setdefault(rule.place_in, []).append(i)
            else:
                leave.append(i)
    return plan


def validate_spec(spec: TaskSpec, object_ids: Sequence[str]) -> list[str]:
    """Warnings for a person reviewing the spec (empty = nothing found).

    Flags selectors that match no object in the scene, rules without a matching check, move rules
    that select a fragile object, objects that rules send to different bins (or both move and
    leave), and checks that contradict the rules.
    """
    objs = _objects(object_ids)
    kept = _kept(spec, objs)
    out: list[str] = []
    for i, rule in enumerate(spec.rules):
        if not _select(objs, rule.select):
            out.append(f"{_rule_label(i, rule)} selects no object in the scene")
    for c in spec.checks:
        if not _select(objs, c.select):
            out.append(f"check {c.id} selects no object in the scene ({c.select.describe()})")
    for i, rule in enumerate(spec.rules):
        if not _covered(rule, spec.checks, objs, kept):
            if rule.place_in:
                need = f"an in_bin check for {rule.place_in}"
            else:
                need = "an untouched or unmoved check"
            out.append(f"{_rule_label(i, rule)} has no matching check (needs {need})")
        if rule.place_in:
            fragile = [o.id for o in _select(objs, rule.select) if o.fragile]
            if fragile:
                out.append(
                    f"{_rule_label(i, rule)} moves fragile {', '.join(fragile)}; add "
                    "fragile: false unless the sentence asks to move it"
                )
    plan = _plan(spec, objs)
    for o in objs:
        dests, leave = plan[o.id]
        if len(dests) > 1:
            sent = "; ".join(f"{b} by rule {_rule_list(r)}" for b, r in sorted(dests.items()))
            out.append(f"{o.id} is sent to different bins: {sent}")
        if dests and leave:
            movers = sorted(i for r in dests.values() for i in r)
            out.append(
                f"{o.id} is both moved (rule {_rule_list(movers)}) and left in place "
                f"(rule {_rule_list(leave)})"
            )
    for c in spec.checks:
        for o in _select(objs, c.select):
            dests, leave = plan[o.id]
            if c.kind == "in_bin" and c.bin not in dests:
                if dests:
                    why = f"the rules send it to {', '.join(sorted(dests))}"
                elif leave:
                    why = f"rule {_rule_list(leave)} leaves it in place"
                else:
                    why = "no rule moves it"
                out.append(f"check {c.id} expects {o.id} in {c.bin}, but {why}")
            elif c.kind in STAY_KINDS and dests:
                movers = sorted(i for r in dests.values() for i in r)
                out.append(
                    f"check {c.id} expects {o.id} to stay {c.kind}, but rule "
                    f"{_rule_list(movers)} moves it"
                )
    return out


def _slug(sel: Selector) -> str:
    d = sel.as_dict()
    parts = [str(d[k]) for k in ("size", "color", "shape") if k in d]
    if "fragile" in d:
        parts.append("fragile" if d["fragile"] else "nonfragile")
    return "_".join(parts) or "all"


def _unique_id(base: str, taken: set[str]) -> str:
    base = base[:CHECK_ID_MAX]
    n = 1
    cid = base
    while cid in taken:
        n += 1
        suffix = f"_{n}"
        cid = base[: CHECK_ID_MAX - len(suffix)] + suffix
    return cid


def _check_for(rule: Rule, taken: set[str], select: Selector | None = None) -> Check:
    """The check a rule is missing: in_bin for a place rule, unmoved for a leave rule, over
    `select` (default: the rule's selector)."""
    select = rule.select if select is None else select
    desc = select.describe()
    every = desc == "every object"
    subject = "Every object" if every else f"All {desc}"
    if rule.place_in:
        where = rule.place_in.replace("_", " ")
        text = f"{subject} {'ends' if every else 'end'} in the {where}."
        cid = _unique_id(f"{_slug(select)}_in_{rule.place_in.removesuffix('_bin')}", taken)
        return Check(id=cid, kind="in_bin", select=select, bin=rule.place_in, text=text)
    text = f"{subject} {'stays where it' if every else 'stay where they'} started."
    cid = _unique_id(f"{_slug(select)}_unmoved", taken)
    return Check(id=cid, kind="unmoved", select=select, text=text)


def ensure_checks(spec: TaskSpec, object_ids: Sequence[str]) -> tuple[TaskSpec, list[str]]:
    """Add in_bin checks for every place rule lacking them and an unmoved check for every leave
    rule lacking an untouched/unmoved one. Objects a leave rule or an untouched/unmoved check
    keeps in place are never put in an added in_bin check. Never removes or edits. Returns
    (new spec, notes)."""
    objs = _objects(object_ids)
    checks = list(spec.checks)
    kept = _kept(spec, objs)
    notes: list[str] = []
    for i, rule in enumerate(spec.rules):
        if _covered(rule, checks, objs, kept):
            continue
        for select in _missing_selectors(rule, checks, objs, kept):
            check = _check_for(rule, {c.id for c in checks}, select)
            checks.append(check)
            notes.append(f"added check {check.id} ({check.text}) for {_rule_label(i, rule)}")
    data = spec.model_dump(mode="json")
    data["checks"] = [c.model_dump(mode="json") for c in checks]
    return TaskSpec.model_validate(data), notes
