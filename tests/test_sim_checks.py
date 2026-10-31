"""Phase 2: each ground-truth check on constructed simulator states."""

import math

import mujoco
import numpy as np
import pytest

from showtell.sim import scene as S
from showtell.sim.checks import evaluate, select
from showtell.sim.randomizer import layout
from showtell.sim.skills import Robot


@pytest.fixture()
def robot() -> Robot:
    task = S.get_task("task03_size_glass")
    model, meta = S.build_model(task.objects)
    r = Robot(model, meta)
    r.reset(layout(task.objects, 3002))
    r.idle(0.3)
    return r


def _set_pose(r: Robot, oid: str, xyz, quat=(1.0, 0.0, 0.0, 0.0)) -> None:
    adr = r.meta.obj_qpos[oid]
    r.data.qpos[adr : adr + 3] = xyz
    r.data.qpos[adr + 3 : adr + 7] = quat
    r.data.qvel[r.meta.obj_dof[oid] : r.meta.obj_dof[oid] + 6] = 0
    mujoco.mj_forward(r.model, r.data)


def test_select_matches_attribute_dicts(robot: Robot) -> None:
    ids = {o.id for o in select(robot.meta.objects, {"size": "large", "fragile": False})}
    assert ids == {"red_cube_large", "blue_cube_large"}
    assert [o.id for o in select(robot.meta.objects, {"shape": "glass"})] == ["clear_glass_large"]


def test_in_bin_passes_only_when_every_selected_object_is_inside(robot: Robot) -> None:
    check = {
        "id": "large_left",
        "kind": "in_bin",
        "select": {"size": "large", "fragile": False},
        "bin": "left_bin",
    }
    res = evaluate(check, robot)
    assert not res["passed"] and "ended in table" in res["detail"]
    _set_pose(
        robot,
        "red_cube_large",
        (S.BIN_X + 0.045, S.BIN_Y["left_bin"] + 0.045, S.bin_floor_top() + 0.035),
    )
    _set_pose(
        robot,
        "blue_cube_large",
        (S.BIN_X - 0.045, S.BIN_Y["left_bin"] - 0.045, S.bin_floor_top() + 0.035),
    )
    assert evaluate(check, robot)["passed"]
    wrong = dict(check, bin="right_bin")
    assert not evaluate(wrong, robot)["passed"]


def test_untouched_fails_after_a_real_contact(robot: Robot) -> None:
    check = {"id": "g", "kind": "untouched", "select": {"shape": "glass"}}
    assert evaluate(check, robot)["passed"]
    g = robot.object_xyz("clear_glass_large")
    # Drop a cube onto the glass and let physics run: the contact is logged.
    _set_pose(robot, "red_cube_small", (g[0], g[1], g[2] + 0.10 + 0.04))
    robot.idle(0.5)
    res = evaluate(check, robot)
    assert not res["passed"] and "red_cube_small" in res["detail"]


def test_upright_fails_when_the_glass_tips_past_20_degrees(robot: Robot) -> None:
    check = {"id": "g", "kind": "upright", "select": {"shape": "glass"}}
    assert evaluate(check, robot)["passed"]
    g = robot.object_xyz("clear_glass_large")
    a = math.radians(45)
    _set_pose(robot, "clear_glass_large", g, (math.cos(a / 2), math.sin(a / 2), 0.0, 0.0))
    res = evaluate(check, robot)
    assert not res["passed"] and "tilted 45 deg" in res["detail"]


def test_unmoved_uses_a_2cm_tolerance(robot: Robot) -> None:
    check = {"id": "g", "kind": "unmoved", "select": {"shape": "glass"}}
    g = robot.object_xyz("clear_glass_large")
    _set_pose(robot, "clear_glass_large", g + np.array([0.015, 0.0, 0.0]))
    assert evaluate(check, robot)["passed"]
    _set_pose(robot, "clear_glass_large", g + np.array([0.05, 0.0, 0.0]))
    res = evaluate(check, robot)
    assert not res["passed"] and "moved 5.0 cm" in res["detail"]


def test_vacuous_and_malformed_checks(robot: Robot) -> None:
    vac = evaluate(
        {"id": "v", "kind": "in_bin", "select": {"color": "purple"}, "bin": "left_bin"}, robot
    )
    assert vac["passed"] and "no objects match" in vac["detail"]
    bad = evaluate({"id": "b", "kind": "levitate", "select": {}}, robot)
    assert not bad["passed"]
    nobin = evaluate({"id": "n", "kind": "in_bin", "select": {"size": "small"}, "bin": None}, robot)
    assert not nobin["passed"]
