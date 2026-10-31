"""Robot skills on the simulated Panda: move_to, pick, place, avoid (+ damped-least-squares IK).

`Robot` owns the MuJoCo model/data and steps physics. Programs never see it: they get the
program-facing `SkillAPI` functions and a `SceneView` (see `program_namespace`), whose docstrings
are also the API documentation given to the model (`skill_api_doc`), so the two cannot drift.

Motion model: every lateral move is lift -> straight lateral segment -> descend. The default
transit height is 0.12 m above the table, deliberately lower than the 0.20 m glass: a program that
does not avoid the glass genuinely sweeps through it in some layouts.
"""

from __future__ import annotations

import inspect
import math
from collections.abc import Iterable
from dataclasses import dataclass

import mujoco
import numpy as np

from showtell.sim import scene as S

TRANSIT_Z = S.TABLE_TOP_Z + 0.12
SAFE_Z = S.TABLE_TOP_Z + 0.32
AVOID_CLEARANCE_Z = 0.10  # transit height over an avoided object = its height + this
HAND_REACH = 0.105  # half-length of the Panda hand along the finger axis
READY_POS = np.array([0.30, 0.0, S.TABLE_TOP_Z + 0.30])
READY_YAW = math.pi / 2
IK_LAMBDA = 0.05
IK_FAIL_M = 0.02
V_LATERAL = 0.40  # m/s average along a lateral segment
V_VERTICAL = 0.20
GRIP_OPEN, GRIP_CLOSED = 255.0, 0.0
SLOT_OFFSET = 0.045  # 2x2 placement slots inside each bin
GRASP_ABOVE_BASE = 0.022
FINGER_MAX = 0.04  # m per finger at ctrl 255
ARM_CLEARANCE_OK = 0.02  # whole-arm clearance from avoided objects we are happy with
POSTURE_J3 = (
    0.4,
    -0.4,
    0.8,
    -0.8,
    1.2,
    -1.2,
)  # elbow postures tried when the arm would brush an avoid


class SkillError(RuntimeError):
    """A skill could not be executed (unreachable pose, bad argument, wrong state)."""


def wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


def topdown_quat(yaw: float) -> np.ndarray:
    """Gripper pointing straight down; ee x-axis at angle `yaw` in the table plane."""
    c, s = math.cos(yaw), math.sin(yaw)
    rot = np.array([[c, s, 0.0], [s, -c, 0.0], [0.0, 0.0, -1.0]])
    q = np.zeros(4)
    mujoco.mju_mat2Quat(q, rot.flatten())
    return q


def _smoothstep(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)


def _seg_closest(a: np.ndarray, b: np.ndarray, p: np.ndarray) -> tuple[float, np.ndarray]:
    ab = b - a
    denom = float(ab @ ab)
    t = 0.0 if denom < 1e-12 else float(np.clip((p - a) @ ab / denom, 0.0, 1.0))
    return t, a + t * ab


@dataclass
class _Avoid:
    obj_id: str
    margin: float


class Robot:
    """Simulated Franka Panda with skills. Owns `model`/`data`; steps physics."""

    PARK_Q = np.array([0.0, -1.76, 0.0, -3.0, 0.0, 1.4, 0.785])
    REST_Q = np.array([0.0, 0.0, 0.0, -1.57079, 0.0, 1.57079, -0.7853])

    def __init__(self, model, meta: S.SceneMeta, recorder=None) -> None:
        self.model, self.meta = model, meta
        self.data = mujoco.MjData(model)
        self._ik = mujoco.MjData(model)
        self.recorder = recorder
        self.dt = float(model.opt.timestep)
        jids = [model.dof_jntid[d] for d in meta.arm_dof]
        self._qlo = model.jnt_range[jids, 0] + 1e-3
        self._qhi = model.jnt_range[jids, 1] - 1e-3
        self._specs = {o.id: o for o in meta.objects}
        self._jacp = np.zeros((3, model.nv))
        self._jacr = np.zeros((3, model.nv))
        self.READY_Q = self.REST_Q.copy()
        self._ready_solved = False
        self.rest = self.REST_Q.copy()
        self._robot_geoms = [
            g
            for g in range(model.ngeom)
            if meta.owner_names[meta.geom_owner[g]] == "robot"
            and (model.geom_contype[g] or model.geom_conaffinity[g])
        ]
        self.reset([])

    # ------------------------------------------------------------------ state
    def reset(self, layout: Iterable[tuple[str, float, float, float]]) -> None:
        m, d = self.model, self.data
        mujoco.mj_resetData(m, d)
        d.qpos[self.meta.arm_qpos] = self.PARK_Q
        d.qpos[self.meta.finger_qpos] = 0.04
        d.ctrl[self.meta.arm_act] = self.PARK_Q
        d.ctrl[self.meta.grip_act] = GRIP_OPEN
        for obj_id, x, y, yaw in layout:
            spec = self._specs[obj_id]
            adr = self.meta.obj_qpos[obj_id]
            d.qpos[adr : adr + 3] = (x, y, S.TABLE_TOP_Z + spec.height / 2 + 1e-4)
            d.qpos[adr + 3 : adr + 7] = (math.cos(yaw / 2), 0.0, 0.0, math.sin(yaw / 2))
        mujoco.mj_forward(m, d)
        self.held: str | None = None
        self._hold_pos = np.zeros(3)
        self._hold_quat = np.array([1.0, 0, 0, 0])
        self.persistent_avoid: dict[str, _Avoid] = {}
        self.touches: dict[str, set[str]] = {o.id: set() for o in self.meta.objects}
        self.touch_events: list[dict] = []
        self.phase = "start"
        self.initial_pose = {o.id: self.object_pose(o.id) for o in self.meta.objects}
        self.trace: list[dict] = []
        self._cmd_q = self.PARK_Q.copy()
        self._cmd_pos, self._cmd_yaw = self.ee_pose()
        self.rest = self.REST_Q.copy()
        if not self._ready_solved:
            self.READY_Q = self.solve_ik(READY_POS, READY_YAW, self.REST_Q)
            self._ready_solved = True

    def log(self, event: str, **info) -> None:
        self.trace.append({"t": round(float(self.data.time), 3), "event": event, **info})

    def object_pose(self, obj_id: str) -> dict:
        b = self.meta.obj_body[obj_id]
        return {"pos": self.data.xpos[b].copy(), "quat": self.data.xquat[b].copy()}

    def object_xyz(self, obj_id: str) -> np.ndarray:
        return self.data.xpos[self.meta.obj_body[obj_id]].copy()

    def ee_pose(self) -> tuple[np.ndarray, float]:
        rot = self.data.site_xmat[self.meta.ee_site].reshape(3, 3)
        return self.data.site_xpos[self.meta.ee_site].copy(), math.atan2(rot[1, 0], rot[0, 0])

    # ------------------------------------------------------------------ physics
    def step(self, n: int = 1) -> None:
        m, d = self.model, self.data
        for _ in range(n):
            if self.held is not None:
                self._apply_attach()
            mujoco.mj_step(m, d)
            if d.ncon:
                self._log_contacts()
            if self.recorder is not None:
                self.recorder.tick(d)

    def idle(self, seconds: float) -> None:
        self.step(max(1, int(round(seconds / self.dt))))

    def _log_contacts(self) -> None:
        d = self.data
        n = d.ncon
        own = self.meta.geom_owner[d.contact.geom[:n]]
        mask = (d.contact.dist[:n] <= 0.0) & ((own[:, 0] >= 6) | (own[:, 1] >= 6))
        if not mask.any():
            return
        names = self.meta.owner_names
        for a, b in own[mask]:
            if a == b:
                continue
            for x, y in ((a, b), (b, a)):
                if x >= 6 and y != 1:  # an object touching anything but the table
                    obj, other = names[x], names[y]
                    if other not in self.touches[obj]:
                        self.touches[obj].add(other)
                        self.touch_events.append(
                            {
                                "t": round(float(d.time), 3),
                                "obj": obj,
                                "by": other,
                                "during": self.phase,
                            }
                        )

    # ------------------------------------------------------------------ grasp attachment
    def _ee_frame(self) -> tuple[np.ndarray, np.ndarray]:
        return (
            self.data.site_xpos[self.meta.ee_site].copy(),
            self.data.site_xmat[self.meta.ee_site].reshape(3, 3).copy(),
        )

    def _attach(self, obj_id: str) -> None:
        p_ee, r_ee = self._ee_frame()
        q_ee = np.zeros(4)
        mujoco.mju_mat2Quat(q_ee, r_ee.flatten())
        pose = self.object_pose(obj_id)
        self._hold_pos = r_ee.T @ (pose["pos"] - p_ee)
        q_inv = np.zeros(4)
        mujoco.mju_negQuat(q_inv, q_ee)
        mujoco.mju_mulQuat(self._hold_quat, q_inv, pose["quat"])
        self.held = obj_id

    def _apply_attach(self) -> None:
        d = self.data
        p_ee, r_ee = self._ee_frame()
        q_ee = np.zeros(4)
        mujoco.mju_mat2Quat(q_ee, r_ee.flatten())
        adr, dof = self.meta.obj_qpos[self.held], self.meta.obj_dof[self.held]
        d.qpos[adr : adr + 3] = p_ee + r_ee @ self._hold_pos
        q = np.zeros(4)
        mujoco.mju_mulQuat(q, q_ee, self._hold_quat)
        d.qpos[adr + 3 : adr + 7] = q
        d.qvel[dof : dof + 6] = 0.0

    # ------------------------------------------------------------------ kinematics
    def solve_ik(self, pos, yaw: float, q_init, max_iter: int = 200, rest=None) -> np.ndarray:
        """Damped least squares on the `ee` site: position + top-down orientation with `yaw`.

        The 1-D null space (elbow posture) is pulled toward `rest` (default: the current posture).
        """
        m, d = self.model, self._ik
        rest = self.rest if rest is None else np.asarray(rest, dtype=float)
        q = np.array(q_init, dtype=float)
        q_target = topdown_quat(yaw)
        pos = np.asarray(pos, dtype=float)
        dof = self.meta.arm_dof
        q_cur, q_inv, q_err, w = np.zeros(4), np.zeros(4), np.zeros(4), np.zeros(3)
        err_p = err_r = np.inf
        for _ in range(max_iter):
            d.qpos[self.meta.arm_qpos] = q
            mujoco.mj_kinematics(m, d)
            mujoco.mj_comPos(m, d)
            ep = pos - d.site_xpos[self.meta.ee_site]
            mujoco.mju_mat2Quat(q_cur, d.site_xmat[self.meta.ee_site])
            mujoco.mju_negQuat(q_inv, q_cur)
            mujoco.mju_mulQuat(q_err, q_target, q_inv)
            mujoco.mju_quat2Vel(w, q_err, 1.0)
            err_p, err_r = float(np.linalg.norm(ep)), float(np.linalg.norm(w))
            if err_p < 5e-4 and err_r < 5e-3:
                break
            mujoco.mj_jacSite(m, d, self._jacp, self._jacr, self.meta.ee_site)
            jac = np.vstack([self._jacp[:, dof], self._jacr[:, dof]])
            e = np.concatenate([ep, w])
            dq = jac.T @ np.linalg.solve(jac @ jac.T + IK_LAMBDA**2 * np.eye(6), e)
            pinv = jac.T @ np.linalg.inv(jac @ jac.T + 1e-4 * np.eye(6))
            dq += (np.eye(7) - pinv @ jac) @ (0.05 * (rest - q))
            norm = float(np.linalg.norm(dq))
            if norm > 0.25:
                dq *= 0.25 / norm
            q = np.clip(q + dq, self._qlo, self._qhi)
        if err_p > IK_FAIL_M or err_r > 0.1:
            raise SkillError(
                f"cannot reach pose ({pos[0]:.3f}, {pos[1]:.3f}, {pos[2]:.3f}) "
                f"yaw {math.degrees(yaw):.0f} deg "
                f"(IK error {err_p * 100:.1f} cm)"
            )
        return q

    # ------------------------------------------------------------------ motion primitives
    def move_joints(self, q_target, duration: float) -> None:
        q0 = self._cmd_q.copy()
        q1 = np.asarray(q_target, dtype=float)
        steps = max(1, int(round(duration / self.dt)))
        for k in range(steps):
            s = _smoothstep((k + 1) / steps)
            self.data.ctrl[self.meta.arm_act] = q0 + (q1 - q0) * s
            self.step()
        self._cmd_q = q1.copy()
        self._settle()
        self._cmd_pos, self._cmd_yaw = self._fk(q1)

    def _fk(self, q) -> tuple[np.ndarray, float]:
        d = self._ik
        d.qpos[self.meta.arm_qpos] = q
        mujoco.mj_kinematics(self.model, d)
        rot = d.site_xmat[self.meta.ee_site].reshape(3, 3)
        return d.site_xpos[self.meta.ee_site].copy(), math.atan2(rot[1, 0], rot[0, 0])

    def move_line(self, pos, yaw: float, speed: float = V_LATERAL) -> None:
        """Straight-line Cartesian move of the ee to `pos` with top-down `yaw` (IK per 1 cm)."""
        p0, y0 = self._cmd_pos.copy(), self._cmd_yaw
        p1 = np.asarray(pos, dtype=float)
        dyaw = wrap(yaw - y0)
        dist = float(np.linalg.norm(p1 - p0))
        n = max(2, math.ceil(dist / 0.01), math.ceil(abs(dyaw) / 0.05))
        qs = [self._cmd_q.copy()]
        for i in range(1, n + 1):
            s = i / n
            target, tyaw = p0 + (p1 - p0) * s, y0 + dyaw * s
            try:
                q = self.solve_ik(target, tyaw, qs[-1], max_iter=80)
            except SkillError:
                q = self.solve_ik(
                    target, tyaw, self.rest, max_iter=300
                )  # restart from the rest posture
                if np.max(np.abs(q - qs[-1])) > 1.0:
                    raise SkillError(
                        f"no smooth path to ({target[0]:.3f}, {target[1]:.3f}, {target[2]:.3f})"
                    ) from None
            qs.append(q)
        duration = max(0.25, dist / speed, abs(dyaw) / 1.6)
        steps = max(1, int(round(duration / self.dt)))
        last = len(qs) - 1
        for k in range(steps):
            u = _smoothstep((k + 1) / steps) * last
            i = min(int(u), last - 1)
            frac = u - i
            self.data.ctrl[self.meta.arm_act] = qs[i] + (qs[i + 1] - qs[i]) * frac
            self.step()
        self._cmd_q = qs[-1]
        self._cmd_pos, self._cmd_yaw = p1, y0 + dyaw
        self._settle()
        actual, _ = self.ee_pose()
        if float(np.linalg.norm(actual - p1)) > IK_FAIL_M:
            raise SkillError(
                f"arm blocked: ee is {np.linalg.norm(actual - p1) * 100:.1f} cm from the target "
                f"({p1[0]:.3f}, {p1[1]:.3f}, {p1[2]:.3f})"
            )

    def _settle(self, max_s: float = 0.12) -> None:
        for _ in range(int(max_s / self.dt)):
            if np.max(np.abs(self.data.qpos[self.meta.arm_qpos] - self._cmd_q)) < 0.004:
                break
            self.step()

    def grip(self, open_: bool, seconds: float = 0.35, width: float = FINGER_MAX) -> None:
        """Open (to `width` per finger) or close the gripper; opening waits for the fingers."""
        width = min(FINGER_MAX, width)
        self.data.ctrl[self.meta.grip_act] = (
            GRIP_OPEN * width / FINGER_MAX if open_ else GRIP_CLOSED
        )
        if not open_:
            self.idle(seconds)
            return
        self.idle(min(seconds, 0.15))
        for _ in range(int(1.0 / self.dt)):
            if float(np.min(self.data.qpos[self.meta.finger_qpos])) > width - 0.0015:
                break
            self.step()

    # ------------------------------------------------------------------ whole-arm clearance
    def _arm_clearance(self, q, avoid_ids: list[str]) -> float:
        """Smallest distance between the arm (+ held object) at joint config `q` and avoids."""
        m, d = self.model, self._ik
        d.qpos[:] = self.data.qpos
        d.qpos[self.meta.arm_qpos] = q
        mujoco.mj_kinematics(m, d)
        geoms = list(self._robot_geoms)
        if self.held is not None:
            adr = self.meta.obj_qpos[self.held]
            r_ee = d.site_xmat[self.meta.ee_site].reshape(3, 3)
            q_ee = np.zeros(4)
            mujoco.mju_mat2Quat(q_ee, r_ee.flatten())
            d.qpos[adr : adr + 3] = d.site_xpos[self.meta.ee_site] + r_ee @ self._hold_pos
            q_obj = np.zeros(4)
            mujoco.mju_mulQuat(q_obj, q_ee, self._hold_quat)
            d.qpos[adr + 3 : adr + 7] = q_obj
            mujoco.mj_kinematics(m, d)
            geoms.append(self.meta.obj_geom[self.held])
        best = 1.0
        for oid in avoid_ids:
            g2 = self.meta.obj_geom[oid]
            for g in geoms:
                best = min(best, mujoco.mj_geomDistance(m, d, g, g2, 0.3, None))
        return float(best)

    def _choose_posture(self, xy, z_high: float, z_low: float, yaw: float, avoids: list[_Avoid]):
        """Pick an elbow posture whose vertical approach at `xy` keeps the whole arm off avoids.

        Returns (rest posture, joint config at the top of the approach, clearance) or None.
        """
        xy = np.asarray(xy, dtype=float)
        ids = [
            a.obj_id for a in avoids if np.linalg.norm(self.object_xyz(a.obj_id)[:2] - xy) < 0.40
        ]
        if not ids:
            return None
        cands = [self.REST_Q.copy()]
        for j3 in POSTURE_J3:
            r = self.REST_Q.copy()
            r[2] = j3
            cands.append(r)
        best = None
        for rest in cands:
            try:
                q_hi = self.solve_ik([xy[0], xy[1], z_high], yaw, rest, max_iter=400, rest=rest)
                q_mid = self.solve_ik([xy[0], xy[1], (z_high + z_low) / 2], yaw, q_hi, rest=rest)
                q_lo = self.solve_ik([xy[0], xy[1], z_low], yaw, q_mid, rest=rest)
            except SkillError:
                continue
            clr = min(self._arm_clearance(q, ids) for q in (q_hi, q_mid, q_lo))
            if best is None or clr > best[2] + 1e-4:
                best = (rest, q_hi, clr)
            if clr >= ARM_CLEARANCE_OK:
                break
        return best

    def _adopt_posture(self, choice) -> None:
        """At the top of an approach, swing the elbow into the chosen posture (ee stays put)."""
        if choice is None:
            return
        rest, q_hi, clr = choice
        self.rest = rest
        if float(np.max(np.abs(self._cmd_q - q_hi))) > 0.03:
            self.move_joints(q_hi, 0.5)
        self.log("posture", j3_rest=round(float(rest[2]), 2), clearance_m=round(clr, 3))

    # ------------------------------------------------------------------ planning helpers
    def _avoids(self, extra) -> list[_Avoid]:
        out = dict(self.persistent_avoid)
        for o in _as_obj_list(extra):
            out.setdefault(o.id, _Avoid(o.id, 0.08))
        return [a for k, a in out.items() if k != self.held]

    def transit(self, xy, yaw: float, avoids: list[_Avoid], z: float = TRANSIT_Z) -> None:
        """Lift -> lateral -> descend to (xy, z), going over/around avoided objects."""
        start = self._cmd_pos.copy()
        a2, b2 = start[:2], np.asarray(xy, dtype=float)
        z_lat = max(z, start[2])
        detours: list[tuple[float, np.ndarray]] = []
        for av in avoids:
            spec = self._specs[av.obj_id]
            g = self.object_xyz(av.obj_id)[:2]
            reach = av.margin + spec.footprint_radius + HAND_REACH
            t, c = _seg_closest(a2, b2, g)
            dist = float(np.linalg.norm(c - g))
            if dist < reach:
                top = self.object_xyz(av.obj_id)[2] + spec.height / 2
                z_lat = max(z_lat, top + AVOID_CLEARANCE_Z)
                if 0.0 < t < 1.0:
                    n = (c - g) / dist if dist > 1e-6 else np.array([-(b2 - a2)[1], (b2 - a2)[0]])
                    n /= np.linalg.norm(n)
                    detours.append((t, g + n * reach))
                self.log(
                    "avoid",
                    obj=av.obj_id,
                    clearance_m=round(dist, 3),
                    transit_z=round(float(z_lat), 3),
                )
        if start[2] < z_lat - 1e-3:
            self.move_line([start[0], start[1], z_lat], self._cmd_yaw, V_VERTICAL)
        y0 = self._cmd_yaw
        dyaw = wrap(yaw - y0)
        pts = [p for _, p in sorted(detours, key=lambda tp: tp[0])] + [b2]
        lengths = np.cumsum(
            [np.linalg.norm(p - q) for p, q in zip(pts, [a2, *pts[:-1]], strict=True)]
        )
        total = float(lengths[-1]) if len(lengths) else 0.0
        for p, acc in zip(pts, lengths, strict=True):
            frac = 1.0 if total < 1e-9 else float(acc) / total
            self.move_line([p[0], p[1], z_lat], y0 + dyaw * frac, V_LATERAL)
        if z_lat > z + 1e-3:
            self.move_line([b2[0], b2[1], z], yaw, V_VERTICAL)

    @staticmethod
    def neutral_yaw(xy) -> float:
        """Wrist yaw that keeps joint 7 near its middle when the arm reaches `xy`."""
        return math.pi / 2 + math.atan2(float(xy[1]), float(xy[0]))

    def _yaw_score(self, yaw: float, xy, avoids: list[_Avoid]) -> float:
        """Lower is better: wrist near neutral, and the hand's long axis not pointing at avoids."""
        off = abs(wrap(yaw - self.neutral_yaw(xy)))
        if off > math.pi / 2 + 1e-6:
            return 1e6 + off  # outside the comfortable wrist range
        score = off
        finger_axis = np.array([math.sin(yaw), -math.cos(yaw)])
        for av in avoids:
            g = self.object_xyz(av.obj_id)[:2]
            v = g - np.asarray(xy)
            dist = float(np.linalg.norm(v))
            if dist < 0.3:
                score += 3.0 * abs(float(finger_axis @ v) / dist) * (0.3 - dist) / 0.3
        return score

    def _grasp_yaws(self, obj_id: str) -> list[float]:
        spec = self._specs[obj_id]
        if spec.shape != "cube":
            base = self.neutral_yaw(self.object_xyz(obj_id)[:2])
            return [wrap(base + k * math.pi / 4) for k in range(-2, 3)]
        rot = np.zeros(9)
        mujoco.mju_quat2Mat(rot, self.object_pose(obj_id)["quat"])
        rot = rot.reshape(3, 3)
        axis = rot[:, int(np.argmin(np.abs(rot[2, :])))]  # most horizontal face normal
        base = math.atan2(axis[1], axis[0])
        return [wrap(base + k * math.pi / 2) for k in range(4)]

    def _choose_slot(self, bin_name: str, avoids: list[_Avoid]) -> np.ndarray:
        """A spot in the bin with room for the held object (2x2 slots, far row first)."""
        x0, x1, y0, y1 = S.bin_interior(bin_name)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        slots = [
            np.array([cx + dx, cy + dy])
            for dx in (SLOT_OFFSET, -SLOT_OFFSET)
            for dy in (SLOT_OFFSET, -SLOT_OFFSET)
        ]
        held_r = self._specs[self.held].footprint_radius if self.held else 0.035
        inside = []
        for o in self.meta.objects:
            if o.id == self.held:
                continue
            p = self.object_xyz(o.id)
            if x0 - 0.03 <= p[0] <= x1 + 0.03 and y0 - 0.03 <= p[1] <= y1 + 0.03:
                inside.append((p[:2], o.footprint_radius))

        def room(s: np.ndarray) -> float:
            return min((float(np.linalg.norm(s - q)) - r - held_r for q, r in inside), default=1.0)

        free = [s for s in slots if room(s) > 0.008]
        if not free:
            return max(slots + [np.array([cx, cy])], key=room)
        if avoids:
            gs = [self.object_xyz(a.obj_id)[:2] for a in avoids]
            return max(free, key=lambda s: min(float(np.linalg.norm(s - g)) for g in gs))
        return free[0]

    # ------------------------------------------------------------------ skills
    def go_ready(self) -> None:
        self.phase = "moving to the ready pose"
        self.move_joints(self.READY_Q, 1.1)
        self.log("ready")

    def park(self) -> None:
        self.phase = "parking"
        if self.held is not None:
            self.log("release", obj=self.held, reason="program ended while holding")
            self.held = None
            self.grip(True, 0.3)
        pos, yaw = self._cmd_pos.copy(), self._cmd_yaw
        try:
            if pos[2] < SAFE_Z:
                self.move_line([pos[0], pos[1], SAFE_Z], yaw, V_VERTICAL)
            park_pos, park_yaw = self._fk(self.PARK_Q)
            self.move_line(park_pos, park_yaw, V_LATERAL)
        except SkillError as exc:
            self.log("park_fallback", error=str(exc))
        self.move_joints(self.PARK_Q, 0.8)
        self.log("park")

    def move_to(self, pose) -> None:
        if len(pose) != 4:
            raise SkillError("move_to expects a pose (x, y, z, yaw)")
        x, y, z, yaw = (float(v) for v in pose)
        self.phase = "move_to"
        self.move_line([x, y, max(z, S.TABLE_TOP_Z + 0.02)], yaw, V_LATERAL)
        self.log("move_to", pose=[round(v, 3) for v in (x, y, z, yaw)])

    def pick(self, obj_id: str, avoid=None) -> None:
        if self.held is not None:
            raise SkillError(f"pick({obj_id}) while already holding {self.held}; place it first")
        avoids = self._avoids(avoid)
        avoids = [a for a in avoids if a.obj_id != obj_id]
        p = self.object_xyz(obj_id)
        yaw = min(self._grasp_yaws(obj_id), key=lambda y: self._yaw_score(y, p[:2], avoids))
        if self.data.ctrl[self.meta.grip_act] < GRIP_OPEN:
            self.grip(True, 0.25)
        # Grasp low (2.2 cm above the base) so the carried object hangs as little as possible.
        z_grasp = max(
            float(p[2]) - self._specs[obj_id].height / 2 + GRASP_ABOVE_BASE, S.TABLE_TOP_Z + 0.02
        )
        choice = self._choose_posture(p[:2], TRANSIT_Z, z_grasp, yaw, avoids)
        if choice is not None:
            self.rest = choice[0]
        self.phase = f"pick({obj_id}): moving above it"
        self.transit(p[:2], yaw, avoids)
        self._adopt_posture(choice)
        self.phase = f"pick({obj_id}): descending"
        self.move_line([p[0], p[1], z_grasp], yaw, V_VERTICAL)
        self.phase = f"pick({obj_id}): grasping"
        self.grip(False, 0.35)
        self._attach(obj_id)
        self.phase = f"pick({obj_id}): lifting"
        self.move_line([p[0], p[1], TRANSIT_Z], yaw, V_VERTICAL)
        self.rest = self.REST_Q.copy()
        self.log(
            "pick", obj=obj_id, at=[round(float(v), 3) for v in p], avoid=[a.obj_id for a in avoids]
        )

    def place(self, bin_name: str, avoid=None) -> None:
        if bin_name not in S.BIN_NAMES:
            raise SkillError(f"unknown bin {bin_name!r}; bins are {', '.join(S.BIN_NAMES)}")
        if self.held is None:
            raise SkillError(f"place({bin_name}) with nothing in the gripper; pick an object first")
        avoids = self._avoids(avoid)
        slot = self._choose_slot(bin_name, avoids)
        aligned = [wrap(k * math.pi / 2) for k in range(4)]
        yaw = min(aligned, key=lambda y: self._yaw_score(y, slot, avoids))
        spec = self._specs[self.held]
        z = S.bin_floor_top() + 0.008 + spec.height / 2 + float(self._hold_pos[2])
        obj = self.held
        choice = self._choose_posture(slot, TRANSIT_Z, z, yaw, avoids)
        if choice is not None:
            self.rest = choice[0]
        self.phase = f"place({bin_name}) carrying {obj}: moving above the bin"
        self.transit(slot, yaw, avoids)
        self._adopt_posture(choice)
        self.phase = f"place({bin_name}) carrying {obj}: descending"
        self.move_line([slot[0], slot[1], z], yaw, V_VERTICAL)
        self.held = None
        self.phase = f"place({bin_name}): releasing {obj}"
        self.grip(True, 0.3, width=spec.half_xy + 0.006)
        self.phase = f"place({bin_name}): lifting"
        self.move_line([slot[0], slot[1], TRANSIT_Z], yaw, V_VERTICAL)
        self.rest = self.REST_Q.copy()
        self.log(
            "place",
            obj=obj,
            bin=bin_name,
            slot=[round(float(v), 3) for v in slot],
            avoid=[a.obj_id for a in avoids],
        )

    def add_avoid(self, obj_id: str, margin_m: float) -> None:
        self.persistent_avoid[obj_id] = _Avoid(obj_id, float(margin_m))
        self.log("avoid_registered", obj=obj_id, margin_m=margin_m)


# ---------------------------------------------------------------------- program-facing API
class Obj:
    """A scene object. Read-only fields: id, size, color, shape, fragile, position (x, y, z)."""

    __slots__ = ("_robot", "_spec")

    def __init__(self, robot: Robot, spec: S.ObjSpec) -> None:
        self._robot = robot
        self._spec = spec

    @property
    def id(self) -> str:
        return self._spec.id

    @property
    def size(self) -> str:
        return self._spec.size

    @property
    def color(self) -> str:
        return self._spec.color

    @property
    def shape(self) -> str:
        return self._spec.shape

    @property
    def fragile(self) -> bool:
        return self._spec.fragile

    @property
    def position(self) -> tuple[float, float, float]:
        p = self._robot.object_xyz(self._spec.id)
        return (round(float(p[0]), 4), round(float(p[1]), 4), round(float(p[2]), 4))

    def __repr__(self) -> str:
        return f"Obj({self.id})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Obj) and other.id == self.id

    def __hash__(self) -> int:
        return hash(self.id)

    def __lt__(self, other: Obj) -> bool:
        return self.id < other.id


OBJ_FIELDS = ("id", "size", "color", "shape", "fragile", "position")
SELECT_KEYS = ("size", "color", "shape", "fragile")


def _as_obj_list(x) -> list[Obj]:
    if x is None:
        return []
    if isinstance(x, Obj):
        return [x]
    if isinstance(x, (list, tuple)) and all(isinstance(o, Obj) for o in x):
        return list(x)
    raise SkillError(
        f"avoid must be an object, a list of objects, or None (got {type(x).__name__})"
    )


class SceneView:
    """The `scene` argument of `run(scene)`."""

    def __init__(self, robot: Robot) -> None:
        self._robot = robot
        self._objs = [Obj(robot, s) for s in robot.meta.objects]

    def objects(self, size=None, color=None, shape=None, fragile=None) -> list:
        """Objects whose attributes match every given value (all objects if none given).

        size: "small" | "large"; color: "red" | "blue" | "green" | "yellow" | "clear";
        shape: "cube" | "cylinder" | "glass"; fragile: True | False.
        Returns a list of Obj in a fixed order.
        """
        want = {"size": size, "color": color, "shape": shape, "fragile": fragile}
        for k, v in want.items():
            if v is not None and k != "fragile" and not isinstance(v, str):
                raise SkillError(f"scene.objects({k}=...) expects a string")
        return [
            o
            for o in self._objs
            if all(
                v is None or getattr(o, k) == (v.lower() if isinstance(v, str) else v)
                for k, v in want.items()
            )
        ]


class SkillAPI:
    """Program-facing skills, bound to one Robot."""

    def __init__(self, robot: Robot) -> None:
        self._robot = robot

    def pick(self, obj, avoid=None) -> None:
        """Pick up `obj` (an Obj from scene.objects) with a top-down grasp.

        Opens the gripper, moves above the object at the transit height (0.12 m above the table),
        descends, closes the gripper and lifts back to the transit height. `avoid` is an Obj or a
        list of Obj: any lateral move that would pass near them goes over them (their height +
        0.10 m) and around them. Raises an error if the gripper already holds an object.
        """
        if not isinstance(obj, Obj):
            raise SkillError(
                f"pick expects one object from scene.objects (got {type(obj).__name__})"
            )
        self._robot.pick(obj.id, avoid)

    def place(self, bin_name, avoid=None) -> None:
        """Put the held object into a bin: "left_bin", "middle_bin" or "right_bin".

        Moves above a free spot in the bin at the transit height, descends, releases and lifts.
        `avoid` works as in pick. Raises an error if nothing is held.
        """
        if not isinstance(bin_name, str):
            raise SkillError("place expects a bin name string")
        self._robot.place(bin_name, avoid)

    def move_to(self, pose) -> None:
        """Move the gripper in a straight line to pose = (x, y, z, yaw) in meters/radians.

        Low-level; prefer pick/place. Coordinates are world-frame (table top at z = 0.40).
        """
        self._robot.move_to(tuple(pose))

    def avoid(self, obj, margin_m=0.08) -> None:
        """Avoid `obj` (an Obj or list of Obj) for every later move, keeping `margin_m` clearance.

        Equivalent to passing avoid=obj to every later pick/place call.
        """
        for o in _as_obj_list(obj):
            self._robot.add_avoid(o.id, margin_m)


SKILL_NAMES = ("pick", "place", "move_to", "avoid")


def program_namespace(robot: Robot) -> tuple[SceneView, dict]:
    """(scene, functions) given to a program's run(scene)."""
    api = SkillAPI(robot)
    return SceneView(robot), {name: getattr(api, name) for name in SKILL_NAMES}


def _sig(fn) -> str:
    sig = inspect.signature(fn)
    params = [p for p in sig.parameters.values() if p.name != "self"]
    return f"{fn.__name__}({', '.join(str(p) for p in params)})"


def skill_api_doc() -> str:
    """API documentation for program generation, generated from the code above."""
    lines = [
        "def run(scene): receives `scene`; call these functions directly (no imports).",
        "",
        f"scene.{_sig(SceneView.objects)}",
        inspect.cleandoc(SceneView.objects.__doc__ or ""),
        "",
        f"Obj: {inspect.cleandoc(Obj.__doc__ or '')}",
        "",
    ]
    for name in SKILL_NAMES:
        fn = getattr(SkillAPI, name)
        lines += [_sig(fn), inspect.cleandoc(fn.__doc__ or ""), ""]
    lines += [
        "Bins: left_bin, middle_bin, right_bin. Allowed builtins: len, sorted, range, min, max.",
        "Errors (unreachable pose, wrong argument, picking while holding) stop the program.",
    ]
    return "\n".join(lines).strip() + "\n"
