"""Seeded scene layouts.

Objects are sampled in the front of the table (the pick area nearest the robot), at least 4 cm
apart (10 cm from the glass), at least 10 cm from any bin and 0.30 m from the robot base. The
glass goes in a central band between the pick area and the bins, at a spot that lies on the
straight path from at least one object to a bin, so a low transit path genuinely sweeps
through it in some layouts.
"""

from __future__ import annotations

import math

import numpy as np

from showtell.sim import scene as S

OBJ_X = (0.27, 0.42)
OBJ_Y = (-0.36, 0.36)
GLASS_X = (0.445, 0.48)
GLASS_Y = (-0.28, 0.28)
MIN_GAP = 0.04
GLASS_GAP = 0.10  # extra room around the glass so a careful robot can work next to it
BIN_CLEARANCE = 0.10
MIN_REACH_R = 0.30  # closer to the base the arm cannot fold into a top-down pose reliably
SHADOW_DIST = 0.06

Placement = tuple[str, float, float, float]  # (obj_id, x, y, yaw)


def bin_outer_rect(name: str) -> tuple[float, float, float, float]:
    x0, x1, y0, y1 = S.bin_interior(name)
    t = S.BIN_WALL_T
    return (x0 - t, x1 + t, y0 - t, y1 + t)


def dist_to_rect(x: float, y: float, rect: tuple[float, float, float, float]) -> float:
    x0, x1, y0, y1 = rect
    dx = max(x0 - x, 0.0, x - x1)
    dy = max(y0 - y, 0.0, y - y1)
    return math.hypot(dx, dy)


def _seg_dist(a, b, p) -> float:
    a, b, p = (np.asarray(v, dtype=float) for v in (a, b, p))
    ab = b - a
    t = float(np.clip((p - a) @ ab / max(float(ab @ ab), 1e-12), 0.0, 1.0))
    return float(np.linalg.norm(a + t * ab - p))


def glass_on_a_path(glass_xy, object_xys) -> bool:
    """True if the glass is within SHADOW_DIST of the straight path from some object to some bin."""
    centers = [(S.BIN_X, S.BIN_Y[b]) for b in S.BIN_NAMES]
    return any(_seg_dist(o, c, glass_xy) < SHADOW_DIST for o in object_xys for c in centers)


def _gap_ok(
    spec: S.ObjSpec, x: float, y: float, placed: list[tuple[S.ObjSpec, float, float]]
) -> bool:
    def gap(a: S.ObjSpec, b: S.ObjSpec) -> float:
        return GLASS_GAP if "glass" in (a.shape, b.shape) else MIN_GAP

    return all(
        math.hypot(x - px, y - py) >= spec.footprint_radius + ps.footprint_radius + gap(spec, ps)
        for ps, px, py in placed
    )


def layout(task_objects, seed: int) -> list[Placement]:
    """Seeded layout for a task's objects: [(obj_id, x, y, yaw)] in the given object order."""
    objs: list[S.ObjSpec] = list(task_objects)
    rng = np.random.default_rng(seed)
    glasses = [o for o in objs if o.shape == "glass"]
    others = [o for o in objs if o.shape != "glass"]
    rects = [bin_outer_rect(b) for b in S.BIN_NAMES]
    for _attempt in range(1000):
        placed: list[tuple[S.ObjSpec, float, float]] = []
        yaws: dict[str, float] = {}
        for g in glasses:
            for _ in range(200):
                x, y = rng.uniform(*GLASS_X), rng.uniform(*GLASS_Y)
                if _gap_ok(g, x, y, placed):
                    placed.append((g, x, y))
                    yaws[g.id] = 0.0
                    break
        ok = len(placed) == len(glasses)
        for o in others if ok else []:
            for _ in range(300):
                x, y = rng.uniform(*OBJ_X), rng.uniform(*OBJ_Y)
                if min(dist_to_rect(x, y, r) for r in rects) < BIN_CLEARANCE:
                    continue
                if math.hypot(x, y) < MIN_REACH_R:
                    continue
                if _gap_ok(o, x, y, placed):
                    placed.append((o, x, y))
                    yaws[o.id] = (
                        float(rng.uniform(-math.pi / 4, math.pi / 4)) if o.shape == "cube" else 0.0
                    )
                    break
            else:
                ok = False
                break
        if not ok:
            continue
        if glasses:
            obj_xys = [(px, py) for ps, px, py in placed if ps.shape != "glass"]
            if not all(
                glass_on_a_path((px, py), obj_xys) for ps, px, py in placed if ps.shape == "glass"
            ):
                continue
        where = {ps.id: (px, py) for ps, px, py in placed}
        return [
            (o.id, round(where[o.id][0], 4), round(where[o.id][1], 4), round(yaws[o.id], 4))
            for o in objs
        ]
    raise RuntimeError(f"could not lay out {len(objs)} objects for seed {seed}")
