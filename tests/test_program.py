"""Phase 4: program generation with a fake Muse Wonder (offline; the real API is never called)."""

from typing import Any

import pytest

from showtell.agent import program
from showtell.agent.prompts import PROGRAM_INSTRUCTIONS
from showtell.agent.sandbox import SandboxError
from showtell.agent.schemas import Program, TaskSpec
from showtell.sim.scene import get_task
from showtell.sim.skills import skill_api_doc

GOOD = (
    "def run(scene):\n"
    '    for obj in scene.objects(size="large", fragile=False):\n'
    "        pick(obj)\n"
    '        place("left_bin")\n'
)
BAD_IMPORT = "def run(scene):\n    import os\n"
BAD_WHILE = "def run(scene):\n    while True:\n        pass\n"


class FakeWonder:
    """Stands in for wonder.call_json: returns scripted replies and records every call."""

    def __init__(self, *sources: str) -> None:
        self.sources = list(sources)
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self, model_cls, instructions, content, effort="high", max_output_tokens=8000, **kw: Any
    ):
        self.calls.append(
            {
                "model_cls": model_cls,
                "instructions": instructions,
                "content": list(content),
                "effort": effort,
                **kw,
            }
        )
        return model_cls.model_validate({"source": self.sources.pop(0), "notes": "fake notes"})


@pytest.fixture()
def task3() -> tuple[TaskSpec, list[str]]:
    task = get_task("task03_size_glass")
    spec = TaskSpec.model_validate({**task.reference, "rationale": "reference spec"})
    return spec, [o.id for o in task.objects]


def fake(monkeypatch, *sources: str) -> FakeWonder:
    wonder = FakeWonder(*sources)
    monkeypatch.setattr(program.wonder, "call_json", wonder)
    return wonder


def test_valid_program_on_the_first_try(monkeypatch, task3) -> None:
    spec, object_ids = task3
    wonder = fake(monkeypatch, f"Here it is:\n```python\n{GOOD}```")
    result = program.write_program(spec, object_ids, tag="job1:program")
    assert result == Program(source=GOOD, notes="fake notes")
    (call,) = wonder.calls
    assert call["model_cls"] is Program
    assert call["instructions"] == PROGRAM_INSTRUCTIONS.format(skill_api_doc=skill_api_doc())
    assert call["effort"] == "high" and call["tag"] == "job1:program"
    (part,) = call["content"]
    assert part["type"] == "input_text"
    assert '"id": "glass_untouched"' in part["text"] and '"kind": "upright"' in part["text"]
    for obj_id in object_ids:
        assert f"- {obj_id}: " in part["text"]
    assert (
        '- clear_glass_large: size="large", color="clear", shape="glass", fragile=True'
        in (part["text"])
    )


def test_prompt_carries_no_strategy_hints(monkeypatch, task3) -> None:
    """Whether the first program avoids the glass is the experiment: only spec + objects go in."""
    spec, object_ids = task3
    wonder = fake(monkeypatch, GOOD)
    program.write_program(spec, object_ids)
    text = wonder.calls[0]["content"][0]["text"].lower()
    assert "avoid" not in text and "height" not in text and "transit" not in text
    assert wonder.calls[0]["tag"] == "program"


def test_rejected_program_is_retried_once_with_the_reason(monkeypatch, task3) -> None:
    spec, object_ids = task3
    wonder = fake(monkeypatch, BAD_IMPORT, f"```\n{GOOD}```")
    result = program.write_program(spec, object_ids)
    assert result.source == GOOD
    assert len(wonder.calls) == 2
    first, retry = wonder.calls
    assert retry["content"][0] == first["content"][0]
    assert retry["instructions"] == first["instructions"] and retry["effort"] == "high"
    feedback = retry["content"][1]["text"]
    assert "line 2: imports are not allowed" in feedback
    assert BAD_IMPORT in feedback


def test_rejected_twice_raises(monkeypatch, task3) -> None:
    spec, object_ids = task3
    wonder = fake(monkeypatch, BAD_IMPORT, BAD_WHILE, GOOD)
    with pytest.raises(SandboxError) as info:
        program.write_program(spec, object_ids)
    assert len(wonder.calls) == 2  # exactly one retry
    assert "rejected twice" in str(info.value)
    assert info.value.problems[0].startswith("line 2: while loops are not allowed")
    assert info.value.source == BAD_WHILE


def test_empty_source_counts_as_a_rejection(monkeypatch, task3) -> None:
    spec, object_ids = task3
    wonder = fake(monkeypatch, "```python\n```", GOOD)
    assert program.write_program(spec, object_ids).source == GOOD
    assert "empty" in wonder.calls[1]["content"][1]["text"]
