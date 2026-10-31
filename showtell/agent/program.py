"""TaskSpec + skill API doc -> a sandbox-valid `def run(scene)` by Muse Wonder (CLAUDE.md 5.6).

The model gets the approved spec and the objects present, nothing else: no strategy hints beyond
the API doc, because whether the first program protects the glass is part of the honest
experiment. The reply is stripped of markdown fences and checked by the sandbox; a rejected
program gets ONE retry with the sandbox's reasons and the rejected source appended, then the
SandboxError is raised.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence

from showtell.agent import sandbox
from showtell.agent.prompts import PROGRAM_INSTRUCTIONS
from showtell.agent.schemas import Program, TaskSpec
from showtell.meta import wonder
from showtell.sim.scene import parse_object_id
from showtell.sim.skills import skill_api_doc

log = logging.getLogger(__name__)

MAX_ECHO_CHARS = sandbox.MAX_SOURCE_CHARS + 2000  # rejected source echoed back on the retry


def program_instructions() -> str:
    return PROGRAM_INSTRUCTIONS.format(skill_api_doc=skill_api_doc())


def objects_text(object_ids: Sequence[str]) -> str:
    """One line per object with the attributes scene.objects(...) selects on."""
    lines = []
    for obj_id in object_ids:
        spec = parse_object_id(obj_id)
        attrs = ", ".join(
            f'{k}="{v}"' if isinstance(v, str) else f"{k}={v}" for k, v in spec.attrs().items()
        )
        lines.append(f"- {spec.id}: {attrs}")
    return "\n".join(lines)


def task_text(spec: TaskSpec, object_ids: Sequence[str]) -> str:
    """The user message: the approved spec as JSON and the objects present."""
    spec_json = json.dumps(spec.model_dump(mode="json", exclude_none=True), indent=2)
    return (
        f"Task spec (JSON):\n{spec_json}\n\n"
        f"Objects in the scene:\n{objects_text(object_ids)}\n\n"
        "Write run(scene) for this task."
    )


def rejection_text(source: str, err: sandbox.SandboxError) -> str:
    """Retry message: why the sandbox rejected the program, plus the program itself."""
    shown = source if len(source) <= MAX_ECHO_CHARS else source[:MAX_ECHO_CHARS] + "\n[...]"
    return (
        "The sandbox rejected your previous program before running it:\n"
        f"{err}\n\n"
        f"Rejected program:\n{shown}\n\n"
        "Fix every problem listed and return the complete corrected program as JSON "
        "{source, notes}."
    )


def write_program(spec: TaskSpec, object_ids: list[str], *, tag: str = "") -> Program:
    """Ask Wonder for `def run(scene)` and validate it, with one retry on a sandbox rejection.

    Returns the Program with fences stripped. Raises SandboxError if the retry is rejected too
    (`err.source` is the last rejected program).
    """
    tag = tag or "program"
    instructions = program_instructions()
    content = [wonder.text_part(task_text(spec, object_ids))]
    reply = wonder.call_json(Program, instructions, content, effort="high", tag=tag)
    source = sandbox.strip_fences(reply.source)
    try:
        sandbox.validate_program(source)
    except sandbox.SandboxError as err:
        log.warning("[%s] sandbox rejected the program, retrying once: %s", tag, err)
        retry = [*content, wonder.text_part(rejection_text(source, err))]
        reply = wonder.call_json(Program, instructions, retry, effort="high", tag=tag)
        source = sandbox.strip_fences(reply.source)
        try:
            sandbox.validate_program(source)
        except sandbox.SandboxError as err2:
            raise sandbox.SandboxError(
                f"the generated program was rejected twice by the sandbox:\n{err2}",
                problems=err2.problems,
                source=source,
            ) from err2
    return Program(source=source, notes=reply.notes)
