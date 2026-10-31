"""Phase 4: the sandbox accepts the skill-API subset of Python and rejects everything else."""

import ast
import inspect
import textwrap
from pathlib import Path

import pytest

from showtell.agent.sandbox import (
    BUILTIN_NAMES,
    CALL_KEYWORDS,
    MAX_NESTING,
    MAX_REPORTED,
    MAX_SOURCE_CHARS,
    SandboxError,
    strip_fences,
    validate_program,
)
from showtell.sim.episode import SAFE_BUILTINS, exec_program
from showtell.sim.skills import SKILL_NAMES, SceneView, SkillAPI

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = sorted((ROOT / "examples").glob("*.py")) + sorted(
    (ROOT / "examples" / "reference").glob("*.py")
)


def prog(body: str) -> str:
    """`body` as the body of def run(scene)."""
    return "def run(scene):\n" + textwrap.indent(textwrap.dedent(body).strip("\n"), "    ") + "\n"


def nested_ifs(levels: int) -> str:
    lines = ["def run(scene):"] + ["    " * (k + 1) + "if True:" for k in range(levels)]
    return "\n".join([*lines, "    " * (levels + 1) + "pass"]) + "\n"


def rejection(source: str) -> str:
    with pytest.raises(SandboxError) as info:
        validate_program(source)
    return str(info.value)


# ---------------------------------------------------------------------- accepted programs
def test_examples_exist() -> None:
    assert len(EXAMPLES) >= 9


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_example_and_reference_programs_pass(path: Path) -> None:
    assert isinstance(validate_program(path.read_text()), ast.Module)


VALID = {
    "comprehension_filter": prog(
        """
        objs = [o for o in scene.objects() if o.color != "blue" and not o.fragile]
        for o in objs:
            pick(o)
            place("left_bin")
        """
    ),
    "generator_in_builtins": prog(
        """
        cubes = scene.objects(shape="cube")
        n = max(len(cubes), min(len([o for o in cubes if o.size == "large"]), 3))
        first = sorted(o for o in cubes if o.color in ("red", "green"))
        """
    ),
    "index_range_and_negative_index": prog(
        """
        objs = sorted(scene.objects(size="large"))
        glass = scene.objects(shape="glass")
        for i in range(len(objs)):
            pick(objs[i], avoid=glass)
            place("left_bin", avoid=glass)
        last = objs[-1]
        """
    ),
    "control_flow": prog(
        """
        glass = scene.objects(shape="glass")
        if not glass:
            return
        elif len(glass) > 1:
            pass
        else:
            avoid(glass, margin_m=0.1)
        for o in scene.objects(fragile=False):
            if o.fragile is True or o.shape == "glass":
                continue
            if o.color in ["red", "green"]:
                pick(o)
                place("right_bin")
            else:
                break
        """
    ),
    "docstring_fields_and_none_checks": prog(
        '''
        """Sort the cubes."""
        first = scene.objects(shape="cube")[0]
        target = None
        if first.position[2] > 0.4 and first.id != "x" and target is None:
            pick(first)
            place("middle_bin")
        '''
    ),
    "tuple_unpacking": prog(
        """
        big, small = scene.objects(size="large"), scene.objects(size="small")
        for group, bin_name in [(big, "left_bin"), (small, "right_bin")]:
            for o in group:
                pick(o)
                place(bin_name)
        """
    ),
    "move_to_with_a_negative_literal": prog("move_to((0.45, -0.1, 0.6, 0.0))"),
    "constant_annotations_and_module_docstring": '"""Program."""\n\n'
    'def run(scene: "SceneView") -> None:\n    pass\n',
    "positional_selector_and_comparisons": prog(
        """
        objs = scene.objects("large", "red")
        if len(objs) >= 1 and len(objs) <= 2 and objs[0] not in scene.objects(color="blue"):
            pick(objs[0])
            place("left_bin")
        """
    ),
    "eight_nested_blocks": nested_ifs(MAX_NESTING),
    "long_elif_chain": prog(
        "c = scene.objects()[0].color\n"
        + "\n".join(f'{"if" if k == 0 else "elif"} c == "{k}":\n    pass' for k in range(12))
    ),
    "huge_range_literal_passes_the_ast_check": prog("for i in range(1000000000):\n    pass"),
}


@pytest.mark.parametrize("source", VALID.values(), ids=VALID.keys())
def test_valid_programs_pass(source: str) -> None:
    validate_program(source)


# ---------------------------------------------------------------------- rejected programs
REJECTED = {
    # imports and the dangerous builtins
    "import_in_body": (prog("import os\nos.system('ls')"), "imports are not allowed"),
    "import_top_level": ("import os\n\ndef run(scene):\n    pass\n", "imports are not allowed"),
    "from_import": ("from os import system\n\ndef run(scene):\n    pass\n", "imports"),
    "dunder_import": (prog("__import__('os').system('ls')"), "double underscores"),
    "dunder_attribute_chain": (
        prog("objs = scene.objects()\nx = objs[0].__class__.__mro__"),
        "double underscores",
    ),
    "dunder_on_scene": (prog("d = scene.__dict__"), "double underscores"),
    "dunder_name": (prog("x = __builtins__"), "double underscores"),
    "getattr": (prog("x = getattr(scene, 'objects')"), "calling `getattr` is not allowed"),
    "eval": (prog("eval('1 + 1')"), "calling `eval` is not allowed"),
    "exec": (prog("exec('x = 1')"), "calling `exec` is not allowed"),
    "open": (prog("f = open('/etc/passwd')"), "calling `open` is not allowed"),
    "print": (prog("print('hi')"), "calling `print` is not allowed"),
    "globals": (prog("g = globals()"), "calling `globals` is not allowed"),
    # forbidden statements
    "while_loop": (prog("while True:\n    pass"), "while loops are not allowed"),
    "lambda": (prog("f = lambda o: o.color"), "lambda is not allowed"),
    "class_in_body": (prog("class A:\n    pass"), "classes are not allowed"),
    "class_top_level": ("class A:\n    pass\n\ndef run(scene):\n    pass\n", "classes"),
    "try_except": (prog("try:\n    pass\nexcept Exception:\n    pass"), "try/except"),
    "with_block": (prog("with x:\n    pass"), "`with` blocks are not allowed"),
    "global": (prog("global x\nx = 1"), "global/nonlocal"),
    "nonlocal": (prog("nonlocal x"), "global/nonlocal"),
    "raise": (prog("raise ValueError('x')"), "raise is not allowed"),
    "assert": (prog("assert True"), "assert is not allowed"),
    "delete": (prog("x = 1\ndel x"), "del is not allowed"),
    "augmented_assignment": (prog("x = 1\nx += 1"), "augmented assignment"),
    "annotated_assignment": (prog("x: int = 1"), "annotated assignment"),
    "match": (prog("match 1:\n    case 1:\n        pass"), "match statements"),
    "nested_def": (prog("def helper(o):\n    pick(o)"), "nested functions are not allowed"),
    # forbidden expressions
    "walrus": (prog("if (n := len(scene.objects())) > 0:\n    pass"), "walrus"),
    "f_string": (prog("x = f'{scene}'"), "f-strings are not allowed"),
    "star_args": (prog("objs = scene.objects()\npick(*objs)"), "star-arguments"),
    "double_star_kwargs": (prog("sel = scene.objects()\nx = scene.objects(**sel)"), "**kwargs"),
    "star_unpacking": (prog("objs = scene.objects()\nx = [*objs]"), "star-unpacking"),
    "starred_target": (prog("a, *b = scene.objects()"), "starred assignment"),
    "dict_literal": (prog("x = {'a': 1}"), "dicts are not allowed"),
    "set_literal": (prog("x = {1, 2}"), "sets are not allowed"),
    "dict_comprehension": (prog("x = {o: 1 for o in scene.objects()}"), "dicts"),
    "set_comprehension": (prog("x = {o for o in scene.objects()}"), "sets"),
    "yield": (prog("yield 1"), "yield is not allowed"),
    "arithmetic_in_range": (prog("for i in range(10**9):\n    pass"), "arithmetic (**)"),
    "list_concatenation": (
        prog('for o in scene.objects(color="red") + scene.objects(color="blue"):\n    pass'),
        "arithmetic (+) is not allowed; write one for loop per scene.objects(...) list",
    ),
    "dict_as_a_bin_mapping": (
        prog('bins = {"large": "left_bin", "small": "right_bin"}'),
        "dicts are not allowed; write one for loop per group",
    ),
    "arithmetic_on_coordinates": (
        prog("o = scene.objects()[0]\nx = o.position[0] + 0.1"),
        "arithmetic (+)",
    ),
    "conditional_expression": (prog('b = "left_bin" if True else "right_bin"'), "use an if"),
    "slice": (prog("x = scene.objects()[1:]"), "slices"),
    "index_is_a_call": (prog("objs = scene.objects()\nx = objs[len(objs)]"), "an index must"),
    "is_with_a_value": (
        prog('o = scene.objects()[0]\nif o.color is "red":\n    pass'),
        "`is` / `is not` only compare with None",
    ),
    "bytes_constant": (prog("x = b'abc'"), "constant `b'abc'` is not allowed"),
    "complex_constant": (prog("x = 1j"), "constant `1j` is not allowed"),
    "ellipsis_body": ("def run(scene):\n    ...\n", "constant `...` is not allowed"),
    "unary_invert": (prog("x = ~1"), "unary operators"),
    "negated_name": (prog("o = 1\nx = -o"), "unary operators"),
    # signature and top level
    "decorator": ("@staticmethod\ndef run(scene):\n    pass\n", "decorators are not allowed"),
    "async_def": ("async def run(scene):\n    await scene\n", "not `async def`"),
    "wrong_function_name": ("def main(scene):\n    pass\n", "must define `def run(scene):`"),
    "no_function": ('"""Nothing here."""\n', "must define `def run(scene):`"),
    "two_functions": ("def helper():\n    pass\n\ndef run(scene):\n    pass\n", "one function"),
    "run_defined_twice": ("def run(scene):\n    pass\n\ndef run(scene):\n    pass\n", "twice"),
    "wrong_argument_name": ("def run(robot):\n    pass\n", "exactly `def run(scene):`"),
    "default_argument": ("def run(scene=None):\n    pass\n", "exactly `def run(scene):`"),
    "var_args": ("def run(scene, *args):\n    pass\n", "exactly `def run(scene):`"),
    "kw_args": ("def run(scene, **kw):\n    pass\n", "exactly `def run(scene):`"),
    "annotation_that_calls": ("def run(scene: print('x')):\n    pass\n", "annotations"),
    "return_annotation_that_calls": ("def run(scene) -> open('x'):\n    pass\n", "annotations"),
    "top_level_assignment": ("x = 1\n\ndef run(scene):\n    pass\n", "at the top level"),
    "top_level_call": ("def run(scene):\n    pass\n\nrun(None)\n", "top-level calls"),
    "main_guard": (
        'def run(scene):\n    pass\n\nif __name__ == "__main__":\n    run(None)\n',
        "at the top level",
    ),
    # comprehensions
    "comprehension_calling_pick": (
        prog("[pick(o) for o in scene.objects()]"),
        "pick() cannot be called inside a comprehension",
    ),
    "comprehension_condition_calling_place": (
        prog('x = [o for o in scene.objects() if place("left_bin")]'),
        "place() cannot be called inside a comprehension",
    ),
    "comprehension_iterator_calling_pick": (
        prog("x = [o for o in pick(scene.objects()[0])]"),
        "pick() cannot be called inside a comprehension",
    ),
    "generator_calling_avoid": (
        prog("x = max(avoid(o) for o in scene.objects())"),
        "avoid() cannot be called inside a comprehension",
    ),
    "comprehension_value_calling_scene_objects": (
        prog('x = [o for o in scene.objects() if o in scene.objects(color="red")]'),
        "only len, min, max and sorted can",
    ),
    "nested_comprehension": (
        prog("objs = scene.objects()\nx = [[p for p in objs] for o in objs]"),
        "cannot be nested in another comprehension",
    ),
    "two_fors_in_a_comprehension": (
        prog("objs = scene.objects()\nx = [o for o in objs for p in objs]"),
        "one `for` per comprehension",
    ),
    # scene and object access
    "subscript_scene": (prog("x = scene[0]"), "scene cannot be indexed"),
    "scene_other_attribute": (prog("r = scene.robot"), "scene has no attribute `robot`"),
    "scene_private_attribute": (prog("r = scene._robot"), "scene has no attribute `_robot`"),
    "scene_other_method": (prog("scene.reset()"), "scene has no method `reset`"),
    "uncalled_scene_objects": (prog("f = scene.objects"), "call it: scene.objects(...)"),
    "object_private_attribute": (
        prog("r = scene.objects()[0]._robot"),
        "`._robot` is not allowed; objects only have the fields",
    ),
    "method_call_on_call_result": (prog("x = scene.objects().pop()"), "method calls like `.pop"),
    "attribute_on_call_result": (prog("x = len(scene.objects()).real"), "not from a call result"),
    "attribute_chain": (prog("o = scene.objects()[0]\nx = o.position.x"), "no chains"),
    "method_on_a_field": (
        prog('o = scene.objects()[0]\nif o.color.startswith("r"):\n    pass'),
        "method calls like `.startswith",
    ),
    "field_on_a_function": (prog("x = pick.id"), "`pick` is a function"),
    "call_of_a_call": (prog("x = scene.objects()[0]()"), "only named functions can be called"),
    "assign_to_a_field": (prog("o = scene.objects()[0]\no.color = 'red'"), "read-only"),
    "assign_into_a_list": (prog("objs = scene.objects()\nobjs[0] = 1"), "assigning into a list"),
    # names, keywords and arity
    "assign_builtin_name": (prog("len = 3"), "`len` is a built-in name and cannot be assigned"),
    "assign_skill_name": (prog("pick = place"), "`pick` is a built-in name"),
    "assign_scene": (prog("scene = 1"), "`scene` is a built-in name"),
    "loop_variable_shadows_builtin": (
        prog("for range in scene.objects():\n    pass"),
        "`range` is a built-in name",
    ),
    "undefined_name": (prog("for o in objects:\n    pick(o)"), "name `objects` is not defined"),
    "keyword_not_allowed": (
        prog("x = sorted(scene.objects(), reverse=True)"),
        "sorted() does not take `reverse=`",
    ),
    "keyword_of_another_skill": (
        prog("pick(scene.objects()[0], margin_m=0.1)"),
        "pick() does not take `margin_m=`; its keywords are avoid=",
    ),
    "unknown_selector_keyword": (
        prog('x = scene.objects(name="red")'),
        "scene.objects() does not take `name=`",
    ),
    "missing_argument": (prog("pick()"), "pick(): missing a required argument: 'obj'"),
    "too_many_arguments": (prog('place("left_bin", None, 1)'), "too many positional arguments"),
    "builtin_arity": (prog("n = len()"), "len() takes 1 argument(s), got 0"),
    # syntax, size and depth
    "syntax_error": ("def run(scene):\n    pick(\n", "syntax error"),
    "break_outside_loop": (prog("break"), "line 2: syntax error: 'break' outside loop"),
    "empty": ("  \n", "empty"),
    "too_long": ("def run(scene):\n" + "    pass\n" * 800, "the limit is 6,000"),
    "too_deeply_nested_blocks": (nested_ifs(MAX_NESTING + 1), "nested more than 8 levels"),
    "too_deeply_nested_expression": (prog("x = " + "not " * 50 + "True"), "nested too deeply"),
    "pathological_expression": (prog("x = " + "not " * 1400 + "True"), "nested too deeply"),
}


@pytest.mark.parametrize("source,reason", REJECTED.values(), ids=REJECTED.keys())
def test_rejected_programs(source: str, reason: str) -> None:
    assert reason in rejection(source)


def test_error_names_the_line() -> None:
    message = rejection(prog("x = scene.objects()\nwhile True:\n    pass"))
    assert message.startswith("line 3: while loops are not allowed")


def test_all_problems_are_reported_in_line_order() -> None:
    with pytest.raises(SandboxError) as info:
        validate_program(prog("for o in objects:\n    pass\nimport os\nprint(1)\nx = lambda: 1"))
    problems = info.value.problems
    assert [p.split(":")[0] for p in problems] == ["line 2", "line 4", "line 5", "line 6"]
    assert "name `objects` is not defined" in problems[0]
    assert "imports are not allowed" in problems[1]
    assert problems[2] == (
        "line 5: calling `print` is not allowed; you can call scene.objects, pick, place, "
        "move_to, avoid, len, sorted, range, min, max"
    )
    assert problems[3] == "line 6: lambda is not allowed; write the code inline"
    assert str(info.value).splitlines() == problems


def test_long_problem_lists_are_truncated() -> None:
    body = "\n".join(f"f{k}()" for k in range(MAX_REPORTED + 4))
    with pytest.raises(SandboxError) as info:
        validate_program(prog(body))
    lines = str(info.value).splitlines()
    assert len(info.value.problems) == MAX_REPORTED + 4
    assert len(lines) == MAX_REPORTED + 1 and lines[-1] == "... and 4 more problems"


def test_source_limit_is_exact() -> None:
    head = "def run(scene):\n    "
    ok = head + "x = '" + "a" * (MAX_SOURCE_CHARS - len(head) - 7) + "'\n"
    assert len(ok) == MAX_SOURCE_CHARS
    validate_program(ok)
    with pytest.raises(SandboxError, match="limit"):
        validate_program(ok + " ")


def test_non_string_source_is_rejected() -> None:
    with pytest.raises(SandboxError, match="must be a string"):
        validate_program(b"def run(scene):\n    pass\n")  # type: ignore[arg-type]


# ---------------------------------------------------------------------- runtime half
def test_huge_range_literal_is_capped_at_runtime() -> None:
    source = VALID["huge_range_literal_passes_the_ast_check"]
    validate_program(source)
    with pytest.raises(ValueError, match="range"):
        exec_program(source, None, {})


class _FakeObj:
    def __init__(self, obj_id: str, color: str, size: str) -> None:
        self.id, self.color, self.size, self.shape, self.fragile = (
            obj_id,
            color,
            size,
            "cube",
            False,
        )
        self.position = (0.3, 0.0, 0.43)

    def __lt__(self, other) -> bool:
        return self.id < other.id


class _FakeScene:
    def __init__(self) -> None:
        self.all = [_FakeObj("b", "blue", "large"), _FakeObj("a", "red", "small")]

    def objects(self, size=None, color=None, shape=None, fragile=None) -> list:
        want = {"size": size, "color": color, "shape": shape, "fragile": fragile}
        return [
            o for o in self.all if all(v is None or getattr(o, k) == v for k, v in want.items())
        ]


def test_accepted_programs_run_with_only_the_whitelisted_builtins() -> None:
    calls: list[tuple] = []
    funcs = {
        "pick": lambda obj, avoid=None: calls.append(("pick", obj.id, avoid)),
        "place": lambda bin_name, avoid=None: calls.append(("place", bin_name, avoid)),
        "move_to": lambda pose: calls.append(("move_to", pose)),
        "avoid": lambda obj, margin_m=0.08: calls.append(("avoid", margin_m)),
    }
    source = prog(
        """
        objs = sorted(o for o in scene.objects() if o.color != "green" and not o.fragile)
        for i in range(len(objs)):
            if objs[i].size == "large":
                pick(objs[i], avoid=None)
                place("left_bin")
            elif objs[-1].position[2] > 0.4:
                pick(objs[i])
                place("right_bin")
        avoid(scene.objects(color="blue"), margin_m=0.1)
        move_to((0.45, -0.1, 0.6, 0.0))
        """
    )
    validate_program(source)
    exec_program(source, _FakeScene(), funcs)
    assert calls == [
        ("pick", "a", None),
        ("place", "right_bin", None),
        ("pick", "b", None),
        ("place", "left_bin", None),
        ("avoid", 0.1),
        ("move_to", (0.45, -0.1, 0.6, 0.0)),
    ]


# ---------------------------------------------------------------------- no drift from the API
def test_builtin_whitelist_matches_the_executor() -> None:
    assert set(BUILTIN_NAMES) == set(SAFE_BUILTINS)


def test_keyword_whitelist_matches_the_skill_signatures() -> None:
    assert CALL_KEYWORDS["scene.objects"] == ("size", "color", "shape", "fragile")
    assert CALL_KEYWORDS["pick"] == CALL_KEYWORDS["place"] == ("avoid",)
    assert CALL_KEYWORDS["avoid"] == ("margin_m",)
    assert CALL_KEYWORDS["move_to"] == ()
    assert set(inspect.signature(SceneView.objects).parameters) >= set(
        CALL_KEYWORDS["scene.objects"]
    )
    for name in SKILL_NAMES:
        assert set(CALL_KEYWORDS[name]) <= set(
            inspect.signature(getattr(SkillAPI, name)).parameters
        )


# ---------------------------------------------------------------------- fences
PROGRAM = "def run(scene):\n    pass\n"
FENCED = {
    "python_fence": "```python\ndef run(scene):\n    pass\n```",
    "bare_fence": "```\ndef run(scene):\n    pass\n```\n",
    "prose_around": "Here is the program:\n```py\ndef run(scene):\n    pass\n```\nIt does it.",
    "first_block_with_def_run": '```json\n{"a": 1}\n```\n```python\ndef run(scene):\n    pass\n```',
    "unterminated_fence": "```python\ndef run(scene):\n    pass\n",
    "stray_closing_fence": "def run(scene):\n    pass\n```",
    "crlf": "```python\r\ndef run(scene):\r\n    pass\r\n```",
    "indented": "\n\n    def run(scene):\n        pass\n\n",
    "no_fence": "def run(scene):\n    pass",
    "bom": "\ufeffdef run(scene):\n    pass\n",
}


@pytest.mark.parametrize("text", FENCED.values(), ids=FENCED.keys())
def test_strip_fences(text: str) -> None:
    assert strip_fences(text) == PROGRAM
    validate_program(strip_fences(text))


def test_strip_fences_of_nothing() -> None:
    assert strip_fences("") == "" and strip_fences("```\n```") == ""
