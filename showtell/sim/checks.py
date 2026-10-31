"""Ground-truth checks evaluated from simulator state.

Closed vocabulary (the spec compiles to these): in_bin, untouched, upright, unmoved.
A selector is an attribute dict, e.g. {"size": "large", "fragile": False}. Each check returns
{check_id, kind, passed, detail, objects}. A check whose selector matches no object passes
and says so.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import mujoco
import numpy as np

from showtell.sim import scene as S

UPRIGHT_COS = math.cos(math.radians(20.0))
CHECK_KINDS = ("in_bin", "untouched", "upright", "unmoved")


def _as_dict(check: Any) -> dict:
    if hasattr(check, "model_dump"):
        return check.model_dump()
    return dict(check)


def select(objects, selector: Mapping | None) -> list[S.ObjSpec]:
    sel = {k: v for k, v in (dict(selector or {})).items() if v is not None}
    out = []
    for o in objects:
        attrs = o.attrs()
        if all(
            (attrs.get(k).lower() if isinstance(attrs.get(k), str) else attrs.get(k))
            == (v.lower() if isinstance(v, str) else v)
            for k, v in sel.items()
        ):
            out.append(o)
    return out


def where_is(robot, obj_id: str) -> str:
    """Human-readable location: a bin name, 'table' or 'floor'."""
    x, y, z = robot.object_xyz(obj_id)
    for b in S.BIN_NAMES:
        x0, x1, y0, y1 = S.bin_interior(b)
        if x0 <= x <= x1 and y0 <= y <= y1 and z < S.bin_rim_z() + 0.05:
            return b
    return "table" if z > S.TABLE_TOP_Z - 0.01 else "floor"


def _in_bin(robot, obj_id: str, bin_name: str) -> bool:
    x, y, z = robot.object_xyz(obj_id)
    x0, x1, y0, y1 = S.bin_interior(bin_name)
    return x0 <= x <= x1 and y0 <= y <= y1 and z < S.bin_rim_z()


def _up_cos(robot, obj_id: str) -> float:
    rot = np.zeros(9)
    mujoco.mju_quat2Mat(rot, robot.object_pose(obj_id)["quat"])
    return float(rot[8])  # z-axis . world z


def evaluate(check: Any, robot) -> dict:
    c = _as_dict(check)
    kind, cid = c["kind"], c.get("id") or c["kind"]
    objs = select(robot.meta.objects, c.get("select"))
    ids = [o.id for o in objs]
    result = {"check_id": cid, "kind": kind, "objects": ids, "passed": True, "detail": ""}
    if kind not in CHECK_KINDS:
        result.update(passed=False, detail=f"unknown check kind {kind!r}")
        return result
    if not objs:
        result["detail"] = f"no objects match {c.get('select')}"
        return result
    bad: list[str] = []
    if kind == "in_bin":
        bin_name = c.get("bin")
        if bin_name not in S.BIN_NAMES:
            result.update(passed=False, detail=f"in_bin needs a bin name, got {bin_name!r}")
            return result
        bad = [f"{i} ended in {where_is(robot, i)}" for i in ids if not _in_bin(robot, i, bin_name)]
        ok_text = f"all in {bin_name}"
    elif kind == "untouched":
        bad = [
            f"{i} was touched by {', '.join(sorted(robot.touches[i]))}"
            for i in ids
            if robot.touches[i]
        ]
        ok_text = "nothing touched them"
    elif kind == "upright":
        for i in ids:
            cos = _up_cos(robot, i)
            if cos < UPRIGHT_COS:
                bad.append(
                    f"{i} tilted {math.degrees(math.acos(max(-1.0, min(1.0, cos)))):.0f} deg"
                )
        ok_text = "all upright"
    else:  # unmoved
        tol = float(c.get("tol_m") or 0.02)
        for i in ids:
            d = float(np.linalg.norm(robot.object_xyz(i)[:2] - robot.initial_pose[i]["pos"][:2]))
            if d > tol:
                bad.append(f"{i} moved {d * 100:.1f} cm")
        ok_text = "none moved"
    result["passed"] = not bad
    result["detail"] = "; ".join(bad) if bad else ok_text
    return result


def evaluate_all(checks, robot) -> list[dict]:
    return [evaluate(c, robot) for c in checks]
