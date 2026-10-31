"""Phase 1: scene builds, seeded layouts are valid and non-overlapping, cameras project sensibly."""

import math

import mujoco
import pytest

from showtell.sim import randomizer as R
from showtell.sim import scene as S

TASKS = S.load_tasks()


@pytest.mark.parametrize("task_id", list(TASKS))
def test_model_compiles_with_robot_cameras_and_objects(task_id: str) -> None:
    task = TASKS[task_id]
    model, meta = S.build_model(task.objects)
    for cam in S.CAMERAS:
        assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_CAMERA, cam) >= 0
    assert meta.ee_site >= 0
    assert len(meta.arm_qpos) == 7
    assert set(meta.obj_body) == {o.id for o in task.objects}


def _check_layout(objects, placements) -> None:
    specs = {o.id: o for o in objects}
    assert [p[0] for p in placements] == [o.id for o in objects]
    table_x0 = S.TABLE_CENTER_XY[0] - S.TABLE_HALF[0]
    front_x1 = table_x0 + 0.6 * 2 * S.TABLE_HALF[0]
    rects = [R.bin_outer_rect(b) for b in S.BIN_NAMES]
    for oid, x, y, _yaw in placements:
        spec = specs[oid]
        assert table_x0 < x < front_x1, f"{oid} outside the table's front 60%"
        assert abs(y) < S.TABLE_HALF[1]
        if spec.shape == "glass":
            assert R.GLASS_X[0] <= x <= R.GLASS_X[1]
        else:
            assert min(R.dist_to_rect(x, y, r) for r in rects) >= R.BIN_CLEARANCE - 1e-9
            assert math.hypot(x, y) >= R.MIN_REACH_R - 1e-9
    for i, (a, xa, ya, _) in enumerate(placements):
        for b, xb, yb, _ in placements[i + 1 :]:
            sa, sb = specs[a], specs[b]
            gap = math.hypot(xa - xb, ya - yb) - sa.footprint_radius - sb.footprint_radius
            assert gap >= R.MIN_GAP - 1e-6, f"{a} and {b} overlap (gap {gap:.3f})"


@pytest.mark.parametrize("task_id", list(TASKS))
def test_twenty_seeds_give_valid_non_overlapping_layouts(task_id: str) -> None:
    task = TASKS[task_id]
    layouts = [R.layout(task.objects, task.index * 1000 + i) for i in range(20)]
    for placements in layouts:
        _check_layout(task.objects, placements)
    assert len({tuple(p) for p in map(tuple, (tuple(map(tuple, lay)) for lay in layouts))}) == 20


def test_layout_is_deterministic_per_seed() -> None:
    objs = TASKS["task03_size_glass"].objects
    assert R.layout(objs, 3007) == R.layout(objs, 3007)
    assert R.layout(objs, 3007) != R.layout(objs, 3008)


def test_glass_always_lies_on_some_object_to_bin_path() -> None:
    task = TASKS["task03_size_glass"]
    for i in range(20):
        lay = R.layout(task.objects, task.index * 1000 + i)
        glass = [(x, y) for oid, x, y, _ in lay if oid.startswith("clear_glass")]
        others = [(x, y) for oid, x, y, _ in lay if not oid.startswith("clear_glass")]
        assert len(glass) == 1 and R.glass_on_a_path(glass[0], others)


def test_overhead_projection_puts_bins_left_to_right_at_the_top() -> None:
    model, _ = S.build_model(TASKS["task01_size"].objects)
    meta = S.scene_metadata(model, 640, 480)
    centers = {
        b: (sum(p[0] for p in v["overhead_px"]) / 4, sum(p[1] for p in v["overhead_px"]) / 4)
        for b, v in meta["bins"].items()
    }
    assert centers["left_bin"][0] < centers["middle_bin"][0] < centers["right_bin"][0]
    for u, v in centers.values():
        assert 0 < u < 640 and 0 < v < 240  # far edge = top half of the image


def test_object_ids_parse_to_the_documented_dimensions() -> None:
    small, large = S.parse_object_id("red_cube_small"), S.parse_object_id("blue_cube_large")
    assert (small.height, large.height) == (0.05, 0.07)
    cyl = S.parse_object_id("yellow_cylinder_large_2")
    assert (cyl.half_xy, cyl.height, cyl.shape) == (0.03, 0.07, "cylinder")
    glass = S.parse_object_id("clear_glass_large")
    assert glass.fragile and glass.height == 0.20 and glass.half_xy == 0.035
    with pytest.raises(ValueError):
        S.parse_object_id("purple_cube_large")
