"""Phase 5: the SAM judge against ground truth on real simulator states (no API calls).

Episodes run real physics (task 3 with the reference and the naive program) while a pose recorder
captures every object's pose at the exact sim times the judge clips are rendered. Projecting each
object's true outline into the overhead and judge_angle cameras gives the boxes SAM would return;
SAM ids change at the clip's cut, as a real tracker's may. The judge must then reach the
ground-truth verdict for every check. Static scenes cover identity, visibility and error paths.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import mujoco
import numpy as np
import pytest

from showtell.agent.schemas import Check
from showtell.contracts import SamVerdict
from showtell.meta import sam
from showtell.meta.sam import FrameBox, Tracks
from showtell.sim import scene as S
from showtell.sim.checks import evaluate_all
from showtell.sim.episode import POST_ROLL_S, PRE_ROLL_S, exec_program
from showtell.sim.randomizer import layout
from showtell.sim.render import JUDGE_FPS, JUDGE_HALF
from showtell.sim.skills import Robot, program_namespace
from showtell.verify import judge_sam as J
from showtell.verify.agreement import agreement, pairs
from showtell.verify.runner import CheckResult, task_seeds

ROOT = Path(__file__).resolve().parent.parent
TASK3 = "task03_size_glass"
REFERENCE = "examples/reference/task03_size_glass.py"
NAIVE = "examples/size_sort_naive.py"
N = J.N_FRAMES

Pose = tuple[np.ndarray, np.ndarray]  # (xyz, wxyz quaternion)
Frames = list[dict[str, Pose]]  # judge-clip frame -> object id -> pose


# ---------------------------------------------------------------- synthetic SAM output
def hull(spec: S.ObjSpec, pose: Pose) -> np.ndarray:
    """World points on the object's outline: cube corners, or both cylinder rims."""
    if spec.shape == "cube":
        r, hz = spec.half_xy, spec.height / 2
        local = np.array([(x, y, z) for x in (-r, r) for y in (-r, r) for z in (-hz, hz)])
    else:
        a = np.linspace(0, 2 * math.pi, 64, endpoint=False)
        rim = np.c_[spec.half_xy * np.cos(a), spec.half_xy * np.sin(a)]
        local = np.vstack([np.c_[rim, np.full(64, z)] for z in (-spec.height / 2, spec.height / 2)])
    rot = np.zeros(9)
    mujoco.mju_quat2Mat(rot, pose[1])
    return pose[0] + local @ rot.reshape(3, 3).T


def frame_box(spec: S.ObjSpec, pose: Pose, cam: dict, frame: int) -> FrameBox | None:
    """The box SAM would report: the projected outline clipped to the frame (None if out)."""
    uv = S.project(hull(spec, pose), cam)
    w, h = cam["width"], cam["height"]
    x1, y1 = max(0, math.floor(uv[:, 0].min())), max(0, math.floor(uv[:, 1].min()))
    x2, y2 = min(w - 1, math.ceil(uv[:, 0].max()) - 1), min(h - 1, math.ceil(uv[:, 1].max()) - 1)
    if x2 < x1 or y2 < y1:
        return None
    return FrameBox(frame=frame, x1=x1, y1=y1, x2=x2, y2=y2, w=w, h=h, conf=0.9)


def synth_tracks(objects: tuple[S.ObjSpec, ...], frames: Frames, cam: dict) -> dict[str, Tracks]:
    """{concept: {sam id: [FrameBox]}} on frames 0..2 and N-3..N-1. After the cut the ids are
    new and in reverse order, so the judge cannot lean on SAM's identities."""
    groups: dict[str, list[S.ObjSpec]] = {}
    for o in objects:
        groups.setdefault(J.concept_for(o), []).append(o)
    out: dict[str, Tracks] = {}
    for concept, group in groups.items():
        tracks: Tracks = {}
        for i, o in enumerate(group):
            for sam_id, span in ((i, range(3)), (100 + len(group) - 1 - i, range(N - 3, N))):
                boxes = [b for f in span if (b := frame_box(o, frames[f][o.id], cam, f))]
                if boxes:
                    tracks[sam_id] = boxes
        out[concept] = tracks
    return out


# ---------------------------------------------------------------- real episodes
class PoseRecorder:
    """Stands in for render.Recorder: captures object poses, not images, at judge-clip times."""

    def __init__(self, meta: S.SceneMeta) -> None:
        self.meta = meta
        self.capture = False
        self.next_t = 0.0
        self.segments: list[Frames] = []

    def start_judge_segment(self) -> None:
        self.capture, self.next_t = True, -1.0
        self.segments.append([])

    def stop_judge_segment(self) -> None:
        self.capture = False

    def tick(self, data: mujoco.MjData) -> None:
        t = float(data.time)
        if not self.capture or t < self.next_t:
            return
        if len(self.segments[-1]) < JUDGE_HALF:
            poses = {}
            for o in self.meta.objects:
                b = self.meta.obj_body[o.id]
                poses[o.id] = (data.xpos[b].copy(), data.xquat[b].copy())
            self.segments[-1].append(poses)
        self.next_t = (self.next_t if self.next_t > 0 else t) + 1.0 / JUDGE_FPS

    def frames(self) -> Frames:
        """Like Recorder.judge_frames: the first 25 frames of the run + the last 25."""
        first, last = self.segments[0][:JUDGE_HALF], self.segments[-1][-JUDGE_HALF:]
        pad = JUDGE_HALF - len(first), JUDGE_HALF - len(last)
        return [*first, *[first[-1]] * pad[0], *last, *[last[-1]] * pad[1]]


@dataclass
class Episode:
    objects: tuple[S.ObjSpec, ...]
    checks: list[Check]
    frames: Frames
    truth: list[CheckResult]
    meta: dict

    def tracks(self, view: str) -> dict[str, Tracks]:
        return synth_tracks(self.objects, self.frames, self.meta["cameras"][J.VIEWS[view]])

    def judge(self) -> list[SamVerdict]:
        return J.verdicts_from_tracks(
            self.checks, self.objects, self.tracks("overhead"), self.tracks("angle"), self.meta
        )


@cache
def episode(program: str, seed: int, task_id: str = TASK3) -> Episode:
    """`episode.run_episode(record=False)` step by step, with a pose recorder attached."""
    task = S.get_task(task_id)
    model, meta = S.build_model(task.objects)
    rec = PoseRecorder(meta)
    robot = Robot(model, meta, recorder=rec)
    robot.reset(layout(task.objects, seed))
    rec.start_judge_segment()
    robot.idle(PRE_ROLL_S)
    rec.stop_judge_segment()
    try:
        robot.go_ready()
        scene, funcs = program_namespace(robot)
        exec_program((ROOT / program).read_text(), scene, funcs)
    except Exception as exc:  # e.g. SkillError: the run stops where it is, as in run_episode
        robot.log("error", message=str(exc))
    robot.park()
    rec.start_judge_segment()
    robot.idle(POST_ROLL_S)
    rec.stop_judge_segment()
    checks = [Check.model_validate(c) for c in task.reference["checks"]]
    truth = [CheckResult.model_validate(r) for r in evaluate_all(checks, robot)]
    return Episode(task.objects, checks, rec.frames(), truth, S.scene_metadata(model))


@pytest.mark.parametrize(
    ("program", "seed"),
    [(REFERENCE, 3000), (REFERENCE, 3003), (NAIVE, 3000), (NAIVE, 3001), (NAIVE, 3003)],
)
def test_verdicts_equal_ground_truth_on_real_episodes(program: str, seed: int) -> None:
    ep = episode(program, seed)
    if program == NAIVE:  # the naive program genuinely knocks the glass over
        assert not all(t.passed for t in ep.truth)
    else:
        assert all(t.passed for t in ep.truth)
    verdicts = ep.judge()
    assert [v.check_id for v in verdicts] == [t.check_id for t in ep.truth]
    for t, v in zip(ep.truth, verdicts, strict=True):
        assert v.passed == t.passed, f"{t.check_id}: truth {t.detail!r}, SAM {v.detail!r}"
        assert v.detail
    stats = agreement(pairs(ep.truth, verdicts))
    assert stats.agree == stats.n == len(ep.truth) and stats.undecided == 0


@pytest.mark.slow
def test_full_agreement_on_20_seeds_of_task3_with_both_programs() -> None:
    items = []
    for program in (REFERENCE, NAIVE):
        for seed in task_seeds(S.get_task(TASK3).index, 20):
            ep = episode(program, seed)
            items += pairs(ep.truth, ep.judge())
    stats = agreement(items)
    assert stats.agree == stats.n  # every decided (seed, check) pair agrees
    assert stats.undecided <= 2  # only a glass knocked out of both camera views


@pytest.mark.slow
def test_brushing_the_glass_without_moving_it_is_the_only_blind_spot() -> None:
    """SAM sees motion, not contact: the one kind of miss allowed is `untouched` on a glass the
    robot brushed but did not move (ground truth's own `unmoved` passes on that seed)."""
    task = S.get_task("task04_clear")
    for seed in task_seeds(task.index, 20):
        ep = episode("examples/clear_naive.py", seed, task.id)
        truth = {t.check_id: t for t in ep.truth}
        for v in ep.judge():
            if v.passed is not None and v.passed != truth[v.check_id].passed:
                assert v.kind == "untouched" and truth["glass_unmoved"].passed, (seed, v)


# ---------------------------------------------------------------- static scenes
@cache
def scene_meta(task_id: str) -> dict:
    model, _ = S.build_model(S.get_task(task_id).objects)
    return S.scene_metadata(model)


def placed(spec: S.ObjSpec, x: float, y: float, yaw: float = 0.0, in_bin: bool = False) -> Pose:
    z = (S.bin_floor_top() if in_bin else S.TABLE_TOP_Z) + spec.height / 2
    return np.array([x, y, z]), np.array([math.cos(yaw / 2), 0.0, 0.0, math.sin(yaw / 2)])


def in_bin(spec: S.ObjSpec, bin_name: str, slot: int, yaw: float = 0.0) -> Pose:
    dx, dy = [(-1, -1), (-1, 1), (1, -1), (1, 1)][slot]
    return placed(spec, S.BIN_X + 0.045 * dx, S.BIN_Y[bin_name] + 0.045 * dy, yaw, in_bin=True)


OFF_VIEW: Pose = (np.array([1.6, -0.8, 0.035]), np.array([1.0, 0.0, 0.0, 0.0]))


def static(start: dict[str, Pose], end: dict[str, Pose]) -> Frames:
    """A clip whose first half shows `start` and second half `end`."""
    return [start] * (N // 2) + [end] * (N - N // 2)


def run_static(task_id: str, frames: Frames, checks: list, angle: bool = True):
    objs, meta = S.get_task(task_id).objects, scene_meta(task_id)
    views = {v: synth_tracks(objs, frames, meta["cameras"][J.VIEWS[v]]) for v in J.VIEWS}
    verdicts = J.verdicts_from_tracks(
        checks, objs, views["overhead"], views["angle"] if angle else None, meta
    )
    return {v.check_id: v for v in verdicts}


def task01_start() -> dict[str, Pose]:
    o = {s.id: s for s in S.get_task("task01_size").objects}
    return {
        "red_cube_large": placed(o["red_cube_large"], 0.34, 0.24, 0.3),
        "blue_cube_large": placed(o["blue_cube_large"], 0.36, 0.08),
        "red_cube_small": placed(o["red_cube_small"], 0.33, -0.10, 0.6),
        "green_cube_small": placed(o["green_cube_small"], 0.38, -0.26),
    }


def task01_sorted(red_large_bin: str = "left_bin", red_small_bin: str = "right_bin") -> dict:
    """Large cubes left, small right; the red large cube nearly axis-aligned and the red small
    one turned 40 degrees, the worst case for telling them apart by box size."""
    o = {s.id: s for s in S.get_task("task01_size").objects}
    return {
        "red_cube_large": in_bin(o["red_cube_large"], red_large_bin, 0, yaw=0.05),
        "blue_cube_large": in_bin(o["blue_cube_large"], "left_bin", 3),
        "red_cube_small": in_bin(o["red_cube_small"], red_small_bin, 1, yaw=0.70),
        "green_cube_small": in_bin(o["green_cube_small"], "right_bin", 2),
    }


T1_CHECKS = S.get_task("task01_size").reference["checks"]


@pytest.mark.parametrize(
    ("obj_id", "concept"),
    [
        ("red_cube_large", "red cube"),
        ("green_cube_small", "green cube"),
        ("yellow_cylinder_large_2", "yellow cylinder"),
        ("clear_glass_large", J.GLASS_CONCEPT),
    ],
)
def test_concept_for(obj_id: str, concept: str) -> None:
    assert J.concept_for(S.parse_object_id(obj_id)) == concept


def test_concepts_needed_tracks_whole_concepts_and_the_angle_only_for_its_checks() -> None:
    task = S.get_task(TASK3)
    overhead, angle = J.concepts_needed(task.reference["checks"], task.objects)
    assert overhead == ["red cube", "blue cube", "green cube", J.GLASS_CONCEPT]
    assert angle == [J.GLASS_CONCEPT]
    cubes_up = {"id": "cubes_up", "kind": "upright", "select": {"shape": "cube"}, "text": "Up."}
    assert J.concepts_needed([cubes_up], task.objects) == ([], [])  # undecided without SAM


def test_large_and_small_cubes_are_told_apart_by_size_despite_new_sam_ids() -> None:
    v = run_static("task01_size", static(task01_start(), task01_sorted()), T1_CHECKS)
    assert v["large_left"].passed is True and v["large_left"].detail == "all in left_bin"
    assert v["small_right"].passed is True

    swapped = task01_sorted(red_large_bin="right_bin", red_small_bin="left_bin")
    v = run_static("task01_size", static(task01_start(), swapped), T1_CHECKS)
    assert v["large_left"].passed is False
    assert v["large_left"].detail == "red_cube_large seen in right_bin"
    assert v["small_right"].passed is False
    assert v["small_right"].detail == "red_cube_small seen in left_bin"


def test_an_object_missing_at_the_end_is_undecided() -> None:
    end = dict(task01_sorted(), green_cube_small=OFF_VIEW)
    v = run_static("task01_size", static(task01_start(), end), T1_CHECKS)
    assert v["large_left"].passed is True
    assert v["small_right"].passed is None
    assert v["small_right"].detail == "green_cube_small not visible at the end"


def test_unclear_identity_is_undecided_unless_the_check_takes_the_whole_concept() -> None:
    end = dict(task01_sorted(), red_cube_small=OFF_VIEW)  # one red box left: which cube is it?
    reds = Check(id="reds", kind="in_bin", select={"color": "red"}, bin="left_bin", text="Reds.")
    v = run_static("task01_size", static(task01_start(), end), [*T1_CHECKS, reds])
    assert v["large_left"].passed is None
    assert "could not tell red_cube_large from red_cube_small" in v["large_left"].detail
    assert v["small_right"].passed is None
    assert v["reds"].passed is None and v["reds"].detail == "a red cube not visible at the end"

    end["red_cube_large"] = in_bin(S.parse_object_id("red_cube_large"), "middle_bin", 0)
    v = run_static("task01_size", static(task01_start(), end), [reds])
    assert v["reds"].passed is False and v["reds"].detail == "a red cube seen in middle_bin"


def test_interchangeable_cylinders_are_judged_as_a_group() -> None:
    task = S.get_task("task05_shape")
    o = {s.id: s for s in task.objects}
    start = {
        "yellow_cylinder_large": placed(o["yellow_cylinder_large"], 0.34, 0.20),
        "yellow_cylinder_large_2": placed(o["yellow_cylinder_large_2"], 0.37, 0.02),
        "red_cube_small": placed(o["red_cube_small"], 0.33, -0.14),
        "blue_cube_large": placed(o["blue_cube_large"], 0.38, -0.30),
    }
    end = {
        "yellow_cylinder_large": in_bin(o["yellow_cylinder_large"], "left_bin", 0),
        "yellow_cylinder_large_2": in_bin(o["yellow_cylinder_large_2"], "left_bin", 3),
        "red_cube_small": in_bin(o["red_cube_small"], "right_bin", 1),
        "blue_cube_large": in_bin(o["blue_cube_large"], "right_bin", 2),
    }
    checks = task.reference["checks"]
    v = run_static(task.id, static(start, end), checks)
    assert v["cylinders_left"].passed is True and v["cubes_right"].passed is True

    end["yellow_cylinder_large_2"] = in_bin(o["yellow_cylinder_large_2"], "middle_bin", 3)
    v = run_static(task.id, static(start, end), checks)
    assert v["cylinders_left"].passed is False
    assert v["cylinders_left"].detail == "a yellow cylinder seen in middle_bin"

    still = Check(id="cyl_still", kind="unmoved", select={"shape": "cylinder"}, text="Stay.")
    v = run_static(task.id, static(start, start), [still])  # ids swap at the cut
    assert v["cyl_still"].passed is True and v["cyl_still"].detail == "none moved"
    nudged = dict(start, yellow_cylinder_large_2=placed(o["yellow_cylinder_large_2"], 0.37, 0.08))
    v = run_static(task.id, static(start, nudged), [still])
    assert v["cyl_still"].passed is False
    assert v["cyl_still"].detail == "a yellow cylinder moved ~6 cm"


def glass_scene(glass_end: Pose | None = None) -> Frames:
    """Task 3 objects left on the table; the glass stays put or ends at `glass_end`."""
    o = {s.id: s for s in S.get_task(TASK3).objects}
    start = {
        "red_cube_large": placed(o["red_cube_large"], 0.34, 0.24),
        "blue_cube_large": placed(o["blue_cube_large"], 0.36, 0.08),
        "red_cube_small": placed(o["red_cube_small"], 0.33, -0.10),
        "green_cube_small": placed(o["green_cube_small"], 0.38, -0.26),
        "clear_glass_large": placed(o["clear_glass_large"], 0.46, 0.0),
    }
    return static(start, start if glass_end is None else dict(start, clear_glass_large=glass_end))


GLASS_CHECKS = S.get_task(TASK3).reference["checks"][2:]  # glass_untouched, glass_upright


def test_a_glass_that_falls_toward_the_45_degree_camera_is_caught_from_above(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    a = -math.pi / 2  # lying along +y: seen end-on by judge_angle, its h/w barely changes
    quat = np.array([math.cos(a / 2), math.sin(a / 2), 0.0, 0.0])
    frames = glass_scene((np.array([0.46, 0.10, S.TABLE_TOP_Z + 0.035]), quat))
    v = run_static(TASK3, frames, GLASS_CHECKS)
    assert v["glass_untouched"].passed is False
    assert v["glass_upright"].passed is False and "top view" in v["glass_upright"].detail

    monkeypatch.setattr(J, "TOP_FIT_MAX", math.inf)  # the 45-degree aspect rule alone misses it
    assert run_static(TASK3, frames, GLASS_CHECKS)["glass_upright"].passed is True


def test_a_glass_knocked_out_of_both_views_moved_but_its_posture_is_unknown() -> None:
    v = run_static(TASK3, glass_scene(OFF_VIEW), GLASS_CHECKS)
    assert v["glass_untouched"].passed is False
    assert v["glass_untouched"].detail == "clear_glass_large left the view"
    assert v["glass_upright"].passed is None
    assert v["glass_upright"].detail == "clear_glass_large not visible at the end"


def test_a_bin_that_sam_takes_for_the_glass_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    objs, meta = S.get_task(TASK3).objects, scene_meta(TASK3)
    a = math.pi / 2  # the glass falls over toward the bins
    fallen = (
        np.array([0.52, 0.0, S.TABLE_TOP_Z + 0.035]),
        np.array([math.cos(a / 2), 0, math.sin(a / 2), 0]),
    )
    frames = glass_scene(fallen)
    x0, x1, y0, y1 = S.bin_interior("left_bin")
    t, rim = S.BIN_WALL_T, S.bin_rim_z()
    outer = [(x0 - t, y0 - t), (x0 - t, y1 + t), (x1 + t, y0 - t), (x1 + t, y1 + t)]
    corners = [(x, y, z) for x, y in outer for z in (S.TABLE_TOP_Z, rim)]
    views = {}
    for view, camera in J.VIEWS.items():
        cam = meta["cameras"][camera]
        tracks = synth_tracks(objs, frames, cam)
        uv = S.project(corners, cam)
        (u0, v0), (u1, v1) = uv.min(axis=0), uv.max(axis=0)
        tracks[J.GLASS_CONCEPT][7] = [  # the whole bin, more confident than the real glass
            FrameBox(
                frame=f, x1=int(u0), y1=int(v0), x2=int(u1), y2=int(v1), w=640, h=480, conf=0.99
            )
            for f in (*range(3), *range(N - 3, N))
        ]
        views[view] = tracks

    def judged() -> dict[str, SamVerdict]:
        found = J.verdicts_from_tracks(GLASS_CHECKS, objs, views["overhead"], views["angle"], meta)
        return {x.check_id: x for x in found}

    v = judged()
    assert v["glass_untouched"].passed is False and v["glass_upright"].passed is False
    monkeypatch.setattr(J, "_drop_ghosts", lambda start, end, cam: (start, end))
    assert judged()["glass_untouched"].passed is True  # fooled: the bin never moves


def test_upright_needs_the_angle_view_and_a_tall_object() -> None:
    frames = glass_scene()  # the glass stays put
    v = run_static(TASK3, frames, GLASS_CHECKS, angle=False)
    assert v["glass_untouched"].passed is True  # the overhead alone saw it stay put
    assert v["glass_upright"].passed is None
    assert v["glass_upright"].detail == "no judge_angle view of clear_glass_large"

    cubes = Check(id="cubes_up", kind="upright", select={"shape": "cube"}, text="Cubes stand.")
    blue_small = {"color": "blue", "size": "small"}  # not in task 3
    none = Check(id="none", kind="in_bin", select=blue_small, bin="left_bin", text="None here.")
    v = run_static(TASK3, frames, [cubes, none])
    assert v["cubes_up"].passed is None and "looks alike tipped or upright" in v["cubes_up"].detail
    assert v["none"].passed is True and v["none"].detail == "no objects"


# ---------------------------------------------------------------- judge(): files and errors
def _fake_track(ep: Episode, calls: list, fail_on: str | None = None, shift_end: int = 0):
    """Stand-in for sam.track serving the episode's synthetic boxes (end frames shifted by
    `shift_end`, as for a longer clip)."""

    def track(video_path, concepts, *, max_concurrency=4, tag="", refresh=False):
        name = Path(video_path).name
        calls.append((name, list(concepts), tag))
        if name == fail_on:
            raise RuntimeError("SAM is down")
        tracks = ep.tracks("overhead" if name == "judge.mp4" else "angle")
        out: dict[str, Tracks] = {}
        for c in concepts:
            out[c] = {oid: [shift(b) for b in boxes] for oid, boxes in tracks[c].items()}
        return out

    def shift(b: FrameBox) -> FrameBox:
        return b.model_copy(update={"frame": b.frame + shift_end}) if b.frame >= 3 else b

    return track


def _clips(seed_dir: Path) -> None:
    for name in J.CLIPS.values():
        (seed_dir / name).write_bytes(b"")  # the fake SAM never reads them


def test_judge_tracks_both_clips_saves_sam_json_and_agrees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ep = episode(NAIVE, 3000)
    calls: list = []
    monkeypatch.setattr(sam, "track", _fake_track(ep, calls))
    _clips(tmp_path)
    verdicts = J.judge(tmp_path, ep.checks, ep.objects, ep.meta, tag="judge")
    assert [v.passed for v in verdicts] == [t.passed for t in ep.truth]
    assert calls == [
        ("judge.mp4", ["red cube", "blue cube", "green cube", J.GLASS_CONCEPT], "judge"),
        ("judge_angle.mp4", [J.GLASS_CONCEPT], "judge"),
    ]
    saved = json.loads((tmp_path / "sam.json").read_text())
    assert saved["n_frames"] == N and saved["errors"] == {}
    assert J.load_tracks(saved["overhead"]) == ep.tracks("overhead")
    assert J.load_tracks(saved["angle"]) == {J.GLASS_CONCEPT: ep.tracks("angle")[J.GLASS_CONCEPT]}
    assert [SamVerdict.model_validate(v) for v in saved["verdicts"]] == verdicts


def test_judge_turns_missing_clips_and_api_errors_into_undecided_verdicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ep = episode(REFERENCE, 3000)
    calls: list = []
    monkeypatch.setattr(sam, "track", _fake_track(ep, calls))
    verdicts = J.judge(tmp_path, ep.checks, ep.objects, ep.meta)  # no clips were written
    assert calls == []
    assert all(v.passed is None for v in verdicts)
    assert verdicts[0].detail == "SAM failed on judge.mp4: FileNotFoundError: judge.mp4 is missing"
    assert (
        "judge.mp4 is missing"
        in json.loads((tmp_path / "sam.json").read_text())["errors"]["overhead"]
    )

    _clips(tmp_path)
    monkeypatch.setattr(sam, "track", _fake_track(ep, calls, fail_on="judge_angle.mp4"))
    v = {x.check_id: x for x in J.judge(tmp_path, ep.checks, ep.objects, ep.meta)}
    assert v["large_left"].passed is True and v["small_right"].passed is True
    assert v["glass_untouched"].passed is True  # the overhead view alone saw it stay put
    assert v["glass_upright"].passed is None
    assert v["glass_upright"].detail == "SAM failed on judge_angle.mp4: RuntimeError: SAM is down"


def test_judge_finds_the_end_of_the_clip_from_sam_frame_numbers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ep = episode(NAIVE, 3000)  # the glass falls: the end frames decide every verdict
    monkeypatch.setattr(sam, "track", _fake_track(ep, [], shift_end=10))  # a 60-frame clip
    _clips(tmp_path)  # empty files: probing fails, so only SAM's frame numbers tell the length
    verdicts = J.judge(tmp_path, ep.checks, ep.objects, ep.meta)
    assert json.loads((tmp_path / "sam.json").read_text())["n_frames"] == N + 10
    assert [v.passed for v in verdicts] == [t.passed for t in ep.truth]
