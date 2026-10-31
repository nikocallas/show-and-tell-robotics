"""Phase 2: IK convergence, attach/detach, skill errors, and the generated API doc."""

import math

import mujoco
import numpy as np
import pytest

from showtell.sim import scene as S
from showtell.sim.randomizer import layout
from showtell.sim.skills import (
    TRANSIT_Z,
    Robot,
    SkillError,
    program_namespace,
    skill_api_doc,
    topdown_quat,
)


@pytest.fixture(scope="module")
def robot() -> Robot:
    task = S.get_task("task01_size")
    model, meta = S.build_model(task.objects)
    r = Robot(model, meta)
    r.reset(layout(task.objects, 1000))
    return r


@pytest.mark.parametrize(
    "pos",
    [
        (0.30, 0.0, 0.43),
        (0.40, 0.30, 0.45),
        (0.45, -0.30, 0.52),
        (0.72, 0.29, 0.46),
        (0.62, -0.20, 0.70),
    ],
)
def test_ik_converges_to_top_down_pose(robot: Robot, pos) -> None:
    yaw = robot.neutral_yaw(pos[:2])
    q = robot.solve_ik(pos, yaw, robot.REST_Q)
    d = robot._ik
    d.qpos[robot.meta.arm_qpos] = q
    mujoco.mj_kinematics(robot.model, d)
    assert np.linalg.norm(d.site_xpos[robot.meta.ee_site] - np.array(pos)) < 1e-3
    quat = np.zeros(4)
    mujoco.mju_mat2Quat(quat, d.site_xmat[robot.meta.ee_site])
    assert abs(abs(float(quat @ topdown_quat(yaw))) - 1.0) < 1e-4  # same rotation
    assert np.all(q >= robot._qlo - 1e-9) and np.all(q <= robot._qhi + 1e-9)


def test_ik_raises_skill_error_when_unreachable(robot: Robot) -> None:
    with pytest.raises(SkillError, match="cannot reach"):
        robot.solve_ik([1.4, 0.0, 0.45], 0.0, robot.REST_Q)


def test_pick_attaches_and_place_detaches_into_the_bin() -> None:
    task = S.get_task("task01_size")
    model, meta = S.build_model(task.objects)
    r = Robot(model, meta)
    r.reset(layout(task.objects, 1003))
    r.go_ready()
    scene, f = program_namespace(r)
    obj = scene.objects(color="red", size="large")[0]
    start = np.array(obj.position)
    f["pick"](obj)
    assert r.held == obj.id
    held_at = np.array(obj.position)
    assert held_at[2] > start[2] + 0.05  # lifted with the gripper (kinematic attach)
    ee, _ = r.ee_pose()
    assert np.linalg.norm(ee[:2] - held_at[:2]) < 0.01
    assert abs(ee[2] - TRANSIT_Z) < 0.01
    f["place"]("left_bin")
    assert r.held is None
    r.idle(0.5)
    x0, x1, y0, y1 = S.bin_interior("left_bin")
    x, y, z = obj.position
    assert x0 <= x <= x1 and y0 <= y <= y1 and z < S.bin_rim_z()


def test_skill_errors_for_wrong_state_and_arguments() -> None:
    task = S.get_task("task01_size")
    model, meta = S.build_model(task.objects)
    r = Robot(model, meta)
    r.reset(layout(task.objects, 1004))
    r.go_ready()
    scene, f = program_namespace(r)
    with pytest.raises(SkillError, match="nothing in the gripper"):
        f["place"]("left_bin")
    objs = scene.objects(size="small")
    with pytest.raises(SkillError, match="one object"):
        f["pick"](objs)  # a list, not an Obj
    f["pick"](objs[0])
    with pytest.raises(SkillError, match="already holding"):
        f["pick"](objs[1])
    with pytest.raises(SkillError, match="unknown bin"):
        f["place"]("top_bin")


def test_scene_objects_filters_by_attributes(robot: Robot) -> None:
    scene, _ = program_namespace(robot)
    assert {o.id for o in scene.objects(size="large")} == {"red_cube_large", "blue_cube_large"}
    assert {o.id for o in scene.objects(color="red")} == {"red_cube_large", "red_cube_small"}
    assert scene.objects(shape="glass") == []
    assert len(scene.objects()) == 4
    with pytest.raises(SkillError):
        scene.objects(size=7)


def test_obj_fields_are_read_only(robot: Robot) -> None:
    scene, _ = program_namespace(robot)
    o = scene.objects()[0]
    with pytest.raises(AttributeError):
        o.color = "blue"  # type: ignore[misc]
    assert isinstance(o.position, tuple) and len(o.position) == 3


def test_api_doc_is_generated_from_the_real_signatures() -> None:
    doc = skill_api_doc()
    for sig in (
        "pick(obj, avoid=None)",
        "place(bin_name, avoid=None)",
        "move_to(pose)",
        "avoid(obj, margin_m=0.08)",
        "scene.objects(size=None, color=None, shape=None, fragile=None)",
    ):
        assert sig in doc
    assert "left_bin" in doc and "middle_bin" in doc and "right_bin" in doc


def test_neutral_yaw_tracks_the_base_angle() -> None:
    assert math.isclose(Robot.neutral_yaw((0.5, 0.0)), math.pi / 2)
    assert math.isclose(Robot.neutral_yaw((0.0, 0.5)), math.pi)
