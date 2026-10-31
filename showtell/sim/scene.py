"""Programmatic MJCF scene: Franka Panda + table + 3 bins + task objects + 3 cameras.

The Panda comes from MuJoCo Menagerie (`third_party/mujoco_menagerie/franka_emika_panda/panda.xml`).
Notes from inspecting that file: arm joints `joint1..joint7` driven by position actuators
`actuator1..actuator7`; gripper fingers `finger_joint1/2` coupled by tendon `split`, driven by
`actuator8` (ctrl 0 = closed, 255 = open, 0.04 m per finger); end-effector body `hand`; no
fingertip site, so we add site `ee` at the fingertip pad center (hand z = 0.1034 m).

World frame: robot base at the origin of the table's near edge, facing +x. The table's far edge
holds the bins; `left_bin` is on the robot's left (+y), which is the image left in the overhead
camera (image up = +x).
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
import yaml

from showtell.config import ROOT, settings

# ---------------------------------------------------------------- geometry constants (meters)
TABLE_TOP_Z = 0.40
TABLE_CENTER_XY = (0.50, 0.0)
TABLE_HALF = (0.40, 0.60, 0.025)  # depth (x), width (y), thickness
ROBOT_BASE = (0.0, 0.0, TABLE_TOP_Z)

BIN_X = 0.67
BIN_Y = {"left_bin": 0.25, "middle_bin": 0.0, "right_bin": -0.25}
BIN_NAMES = ("left_bin", "middle_bin", "right_bin")
BIN_INNER_HALF = 0.10  # interior is 0.20 x 0.20
BIN_WALL_T = 0.01
BIN_WALL_H = 0.06
BIN_FLOOR_T = 0.01
BIN_RGBA = {
    "left_bin": (0.47, 0.55, 0.66, 1.0),  # slate
    "middle_bin": (0.70, 0.63, 0.50, 1.0),  # sand
    "right_bin": (0.52, 0.63, 0.52, 1.0),  # sage
}

EE_SITE_OFFSET = 0.1034  # hand origin -> fingertip pad center, along hand z

COLORS = {
    "red": (0.84, 0.16, 0.14, 1.0),
    "blue": (0.13, 0.34, 0.84, 1.0),
    "green": (0.15, 0.62, 0.27, 1.0),
    "yellow": (0.97, 0.79, 0.10, 1.0),
    "clear": (0.70, 0.93, 0.98, 0.55),
}
SHAPES = ("cube", "cylinder", "glass")
SIZES = ("small", "large")

CAMERAS = ("overhead", "judge_angle", "hero")

_OBJ_ID_RE = re.compile(r"^(?P<color>[a-z]+)_(?P<shape>[a-z]+)_(?P<size>[a-z]+)(?:_(?P<n>\d+))?$")


@dataclass(frozen=True)
class ObjSpec:
    """A task object and its attributes (the vocabulary programs and specs select on)."""

    id: str
    color: str
    shape: str
    size: str
    fragile: bool
    half_xy: float  # cube half side, or cylinder radius
    height: float
    mass: float

    @property
    def rgba(self) -> tuple[float, float, float, float]:
        return COLORS[self.color]

    @property
    def footprint_radius(self) -> float:
        """Radius of the circle that contains the object's footprint at any yaw."""
        return self.half_xy * (math.sqrt(2.0) if self.shape == "cube" else 1.0)

    def attrs(self) -> dict[str, str | bool]:
        return {
            "size": self.size,
            "color": self.color,
            "shape": self.shape,
            "fragile": self.fragile,
        }


def parse_object_id(obj_id: str) -> ObjSpec:
    """Build an ObjSpec from an id like `red_cube_large` or `yellow_cylinder_large_2`."""
    m = _OBJ_ID_RE.match(obj_id)
    if not m:
        raise ValueError(f"bad object id {obj_id!r}")
    color, shape, size = m["color"], m["shape"], m["size"]
    if color not in COLORS or shape not in SHAPES or size not in SIZES:
        raise ValueError(f"unknown object attributes in {obj_id!r}")
    if shape == "cube":
        side = 0.05 if size == "small" else 0.07
        return ObjSpec(
            obj_id, color, shape, size, False, side / 2, side, 0.08 if size == "small" else 0.15
        )
    if shape == "cylinder":
        if size != "large":
            raise ValueError("cylinders come in one size: large (r=3 cm, h=7 cm)")
        return ObjSpec(obj_id, color, shape, size, False, 0.03, 0.07, 0.10)
    if size != "large" or color != "clear":
        raise ValueError("the glass is clear_glass_large (r=3.5 cm, h=20 cm)")
    return ObjSpec(obj_id, color, shape, size, True, 0.035, 0.20, 0.05)


# ---------------------------------------------------------------- tasks
@dataclass(frozen=True)
class TaskDef:
    id: str
    index: int
    rules: str
    video: str
    objects: tuple[ObjSpec, ...]
    demo: str
    reference: dict = field(default_factory=dict, hash=False, compare=False)


@lru_cache(maxsize=1)
def load_tasks(path: Path | None = None) -> dict[str, TaskDef]:
    """Read bench/tasks.yaml. Task index is 1-based order in the file (seed = index*1000 + i)."""
    raw = yaml.safe_load((path or ROOT / "bench" / "tasks.yaml").read_text())
    tasks: dict[str, TaskDef] = {}
    for i, t in enumerate(raw, start=1):
        objs = tuple(parse_object_id(o) for o in t["objects"])
        tasks[t["id"]] = TaskDef(
            t["id"], i, t["rules"], t["video"], objs, t.get("demo", ""), t.get("reference", {})
        )
    return tasks


def get_task(task_id: str) -> TaskDef:
    tasks = load_tasks()
    if task_id not in tasks:
        raise KeyError(f"unknown task {task_id!r}; known: {', '.join(tasks)}")
    return tasks[task_id]


# ---------------------------------------------------------------- MJCF helpers
def _fmt(values) -> str:
    return " ".join(f"{float(v):.6g}" for v in values)


def look_at_xyaxes(pos, target, up=(0.0, 0.0, 1.0)) -> str:
    """MuJoCo camera `xyaxes` for a camera at `pos` looking at `target` (camera looks along -z)."""
    pos, target, up = (np.asarray(v, dtype=float) for v in (pos, target, up))
    fwd = target - pos
    fwd /= np.linalg.norm(fwd)
    right = np.cross(fwd, up)
    right /= np.linalg.norm(right)
    cam_up = np.cross(right, fwd)
    return _fmt([*right, *cam_up])


def _sub(parent: ET.Element, tag: str, **attrs) -> ET.Element:
    return ET.SubElement(parent, tag, {k: str(v) for k, v in attrs.items()})


def bin_interior(name: str) -> tuple[float, float, float, float]:
    """(xmin, xmax, ymin, ymax) of a bin's interior."""
    cy = BIN_Y[name]
    h = BIN_INNER_HALF
    return (BIN_X - h, BIN_X + h, cy - h, cy + h)


def bin_floor_top() -> float:
    return TABLE_TOP_Z + BIN_FLOOR_T


def bin_rim_z() -> float:
    return TABLE_TOP_Z + BIN_WALL_H


CAMERA_POSES = {
    # name: (pos, target, fovy)
    "overhead": ((0.50, 0.0, TABLE_TOP_Z + 1.25), None, 45.0),
    "judge_angle": ((0.50, -0.95, TABLE_TOP_Z + 0.95), (0.50, 0.0, TABLE_TOP_Z + 0.08), 45.0),
    "hero": ((1.55, 1.15, TABLE_TOP_Z + 0.95), (0.40, 0.0, TABLE_TOP_Z + 0.20), 40.0),
}


def build_mjcf(objects: tuple[ObjSpec, ...] | list[ObjSpec]) -> str:
    """Compose the full MJCF string for a task's objects (initial object poses are set later)."""
    panda_dir = settings.MENAGERIE_DIR / "franka_emika_panda"
    if not (panda_dir / "panda.xml").exists():
        raise FileNotFoundError(
            f"{panda_dir}/panda.xml missing: git clone --depth 1 "
            "https://github.com/google-deepmind/mujoco_menagerie third_party/mujoco_menagerie"
        )
    root = ET.parse(panda_dir / "panda.xml").getroot()
    root.set("model", "showtell")
    root.find("compiler").set("meshdir", str(panda_dir / "assets"))
    opt = root.find("option")
    opt.set("timestep", "0.002")
    opt.set("cone", "elliptic")
    opt.set("impratio", "10")

    # Keyframes would need every free joint; we set the home pose in code instead.
    for key in root.findall("keyframe"):
        root.remove(key)

    visual = _sub(root, "visual")
    _sub(visual, "global", offwidth=1920, offheight=1080, azimuth=150, elevation=-25)
    _sub(visual, "quality", shadowsize=4096, offsamples=8)
    _sub(visual, "headlight", ambient=".30 .30 .30", diffuse=".10 .10 .10", specular="0 0 0")
    _sub(visual, "map", znear=0.01, zfar=20, shadowclip=2.0)
    _sub(visual, "rgba", haze=".55 .56 .58 1")

    asset = root.find("asset")
    _sub(
        asset,
        "texture",
        type="skybox",
        builtin="gradient",
        rgb1=".62 .63 .65",
        rgb2=".50 .51 .53",
        width=256,
        height=256,
    )
    _sub(
        asset,
        "texture",
        name="floor_tex",
        type="2d",
        builtin="checker",
        rgb1=".40 .41 .43",
        rgb2=".37 .38 .40",
        width=512,
        height=512,
    )
    _sub(
        asset,
        "material",
        name="floor_mat",
        texture="floor_tex",
        texrepeat="6 6",
        specular="0",
        shininess="0",
        reflectance="0",
    )
    _sub(
        asset,
        "material",
        name="table_mat",
        rgba=".74 .70 .63 1",
        specular="0",
        shininess="0",
        reflectance="0",
    )
    _sub(
        asset, "material", name="pedestal_mat", rgba=".22 .23 .25 1", specular=".1", shininess=".1"
    )

    wb = root.find("worldbody")
    for light in wb.findall("light"):
        wb.remove(light)
    _sub(
        wb,
        "light",
        name="key",
        directional="true",
        dir=_fmt([-0.35, 0.45, -1.0]),
        diffuse=".50 .50 .48",
        specular=".08 .08 .08",
        castshadow="true",
    )
    _sub(
        wb,
        "light",
        name="fill",
        directional="true",
        dir=_fmt([0.45, -0.35, -1.0]),
        diffuse=".20 .20 .22",
        specular="0 0 0",
        castshadow="false",
    )

    link0 = wb.find("body[@name='link0']")
    link0.set("pos", _fmt(ROBOT_BASE))
    for body in link0.iter("body"):  # ideal gravity compensation, like the real arm's controller
        body.set("gravcomp", "1")
    hand = root.find(".//body[@name='hand']")
    _sub(
        hand,
        "site",
        name="ee",
        pos=_fmt([0, 0, EE_SITE_OFFSET]),
        size="0.006",
        rgba="1 0.2 0.2 1",
        group="5",
    )

    # Floor, pedestal, table.
    _sub(wb, "geom", name="floor", type="plane", size="4 4 0.05", material="floor_mat")
    _sub(
        wb,
        "geom",
        name="pedestal",
        type="cylinder",
        size=_fmt([0.11, TABLE_TOP_Z / 2]),
        pos=_fmt([ROBOT_BASE[0], ROBOT_BASE[1], TABLE_TOP_Z / 2 - 0.0005]),
        material="pedestal_mat",
    )
    tx, ty = TABLE_CENTER_XY
    _sub(
        wb,
        "geom",
        name="table",
        type="box",
        size=_fmt(TABLE_HALF),
        pos=_fmt([tx, ty, TABLE_TOP_Z - TABLE_HALF[2]]),
        material="table_mat",
        friction="0.9 0.005 0.0001",
    )
    leg_h = (TABLE_TOP_Z - 2 * TABLE_HALF[2]) / 2
    for i, (sx, sy) in enumerate([(-1, -1), (-1, 1), (1, -1), (1, 1)]):
        _sub(
            wb,
            "geom",
            name=f"table_leg{i}",
            type="box",
            size=_fmt([0.025, 0.025, leg_h]),
            pos=_fmt([tx + sx * (TABLE_HALF[0] - 0.05), ty + sy * (TABLE_HALF[1] - 0.05), leg_h]),
            material="table_mat",
            contype="0",
            conaffinity="0",
        )

    # Bins: floor + 4 walls each, static (part of the world).
    for name in BIN_NAMES:
        cx, cy = BIN_X, BIN_Y[name]
        rgba = _fmt(BIN_RGBA[name])
        outer = BIN_INNER_HALF + BIN_WALL_T
        z0 = TABLE_TOP_Z
        _sub(
            wb,
            "geom",
            name=f"{name}_floor",
            type="box",
            rgba=rgba,
            size=_fmt([outer, outer, BIN_FLOOR_T / 2]),
            pos=_fmt([cx, cy, z0 + BIN_FLOOR_T / 2]),
        )
        wh = BIN_WALL_H / 2
        walls = {
            "n": ([cx + BIN_INNER_HALF + BIN_WALL_T / 2, cy], [BIN_WALL_T / 2, outer]),
            "s": ([cx - BIN_INNER_HALF - BIN_WALL_T / 2, cy], [BIN_WALL_T / 2, outer]),
            "e": ([cx, cy - BIN_INNER_HALF - BIN_WALL_T / 2], [BIN_INNER_HALF, BIN_WALL_T / 2]),
            "w": ([cx, cy + BIN_INNER_HALF + BIN_WALL_T / 2], [BIN_INNER_HALF, BIN_WALL_T / 2]),
        }
        for side, (xy, half) in walls.items():
            _sub(
                wb,
                "geom",
                name=f"{name}_wall_{side}",
                type="box",
                rgba=rgba,
                size=_fmt([half[0], half[1], wh]),
                pos=_fmt([xy[0], xy[1], z0 + wh]),
            )

    # Task objects on free joints (placed off-table; the randomizer sets their poses).
    for k, obj in enumerate(objects):
        body = _sub(wb, "body", name=f"obj_{obj.id}", pos=_fmt([2.0 + 0.3 * k, 2.0, 0.2]))
        _sub(body, "freejoint", name=f"obj_{obj.id}")
        common = dict(
            name=f"obj_{obj.id}",
            rgba=_fmt(obj.rgba),
            mass=obj.mass,
            friction="0.9 0.005 0.0001",
            solref="0.01 1",
        )
        if obj.shape == "cube":
            _sub(
                body, "geom", type="box", size=_fmt([obj.half_xy] * 2 + [obj.height / 2]), **common
            )
        else:
            _sub(body, "geom", type="cylinder", size=_fmt([obj.half_xy, obj.height / 2]), **common)

    for name in CAMERAS:
        pos, target, fovy = CAMERA_POSES[name]
        if target is None:  # straight down, image up = +x, image left = +y
            xyaxes = "0 -1 0 1 0 0"
        else:
            xyaxes = look_at_xyaxes(pos, target)
        _sub(wb, "camera", name=name, pos=_fmt(pos), xyaxes=xyaxes, fovy=f"{fovy:.4g}")

    return ET.tostring(root, encoding="unicode")


@dataclass
class SceneMeta:
    """Ids and addresses resolved from a compiled model."""

    objects: tuple[ObjSpec, ...]
    obj_body: dict[str, int]
    obj_qpos: dict[str, int]  # free-joint qpos address (7 values: xyz + wxyz quat)
    obj_dof: dict[str, int]  # free-joint dof address (6 values)
    obj_geom: dict[str, int]
    geom_owner: np.ndarray  # geom id -> owner tag index (see `owner_names`)
    owner_names: list[str]
    table_geom: int
    arm_qpos: np.ndarray  # 7 qpos addresses
    arm_dof: np.ndarray
    finger_qpos: np.ndarray
    arm_act: np.ndarray
    grip_act: int
    ee_site: int
    hand_body: int


def build_model(objects: tuple[ObjSpec, ...] | list[ObjSpec]):
    """Compile the MJCF and resolve ids. Returns (mujoco.MjModel, SceneMeta)."""
    import mujoco

    objects = tuple(objects)
    model = mujoco.MjModel.from_xml_string(build_mjcf(objects))

    def oid(kind, name):
        i = mujoco.mj_name2id(model, kind, name)
        if i < 0:
            raise KeyError(name)
        return i

    J, B, G = mujoco.mjtObj.mjOBJ_JOINT, mujoco.mjtObj.mjOBJ_BODY, mujoco.mjtObj.mjOBJ_GEOM
    A = mujoco.mjtObj.mjOBJ_ACTUATOR
    obj_body = {o.id: oid(B, f"obj_{o.id}") for o in objects}
    obj_joint = {o.id: oid(J, f"obj_{o.id}") for o in objects}
    obj_geom = {o.id: oid(G, f"obj_{o.id}") for o in objects}

    owner_names = ["world", "table", "robot", *BIN_NAMES, *[o.id for o in objects]]
    owner_index = {n: i for i, n in enumerate(owner_names)}
    robot_bodies = {
        oid(B, n)
        for n in [
            "link0",
            "link1",
            "link2",
            "link3",
            "link4",
            "link5",
            "link6",
            "link7",
            "hand",
            "left_finger",
            "right_finger",
        ]
    }
    geom_owner = np.zeros(model.ngeom, dtype=np.int32)
    table_geom = oid(G, "table")
    for g in range(model.ngeom):
        b = int(model.geom_bodyid[g])
        name = mujoco.mj_id2name(model, G, g) or ""
        if g == table_geom:
            geom_owner[g] = owner_index["table"]
        elif b in robot_bodies:
            geom_owner[g] = owner_index["robot"]
        elif any(name.startswith(bn) for bn in BIN_NAMES):
            geom_owner[g] = owner_index[name.split("_wall")[0].split("_floor")[0]]
        else:
            geom_owner[g] = owner_index["world"]
    for o in objects:
        geom_owner[obj_geom[o.id]] = owner_index[o.id]

    arm_joints = [oid(J, f"joint{i}") for i in range(1, 8)]
    meta = SceneMeta(
        objects=objects,
        obj_body=obj_body,
        obj_qpos={k: int(model.jnt_qposadr[j]) for k, j in obj_joint.items()},
        obj_dof={k: int(model.jnt_dofadr[j]) for k, j in obj_joint.items()},
        obj_geom=obj_geom,
        geom_owner=geom_owner,
        owner_names=owner_names,
        table_geom=table_geom,
        arm_qpos=np.array([model.jnt_qposadr[j] for j in arm_joints]),
        arm_dof=np.array([model.jnt_dofadr[j] for j in arm_joints]),
        finger_qpos=np.array(
            [model.jnt_qposadr[oid(J, "finger_joint1")], model.jnt_qposadr[oid(J, "finger_joint2")]]
        ),
        arm_act=np.array([oid(A, f"actuator{i}") for i in range(1, 8)]),
        grip_act=oid(A, "actuator8"),
        ee_site=oid(mujoco.mjtObj.mjOBJ_SITE, "ee"),
        hand_body=oid(B, "hand"),
    )
    return model, meta


# ------------------------------------------------------- camera projection (for the SAM judge)
def camera_intrinsics(model, cam_name: str, width: int, height: int) -> dict:
    """Pinhole intrinsics/extrinsics of a fixed world camera. Pixel (u, v) has origin top-left."""
    import mujoco

    cid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_CAMERA, cam_name)
    fovy = math.radians(float(model.cam_fovy[cid]))
    f = 0.5 * height / math.tan(fovy / 2)
    rot = np.array(model.cam_mat0[cid]).reshape(3, 3)  # columns: camera x, y, z axes in world
    return {
        "camera": cam_name,
        "width": width,
        "height": height,
        "fx": f,
        "fy": f,
        "cx": width / 2,
        "cy": height / 2,
        "pos": [float(v) for v in model.cam_pos[cid]],
        "rot": rot.tolist(),
    }


def project(points_world, cam: dict) -> np.ndarray:
    """Project Nx3 world points into pixel coordinates (u right, v down) for `camera_intrinsics`."""
    pts = np.atleast_2d(np.asarray(points_world, dtype=float))
    rot = np.asarray(cam["rot"])
    rel = (pts - np.asarray(cam["pos"])) @ rot  # camera-frame coords: x right, y up, -z forward
    depth = -rel[:, 2]
    u = cam["cx"] + cam["fx"] * rel[:, 0] / depth
    v = cam["cy"] - cam["fy"] * rel[:, 1] / depth
    return np.stack([u, v], axis=1)


def scene_metadata(model, width: int = 640, height: int = 480) -> dict:
    """Bin interiors projected to judge-camera pixels, plus camera parameters."""
    out: dict = {"cameras": {}, "bins": {}}
    for cam in ("overhead", "judge_angle"):
        info = camera_intrinsics(model, cam, width, height)
        out["cameras"][cam] = info
    ov = out["cameras"]["overhead"]
    for name in BIN_NAMES:
        x0, x1, y0, y1 = bin_interior(name)
        z = bin_floor_top()
        corners = [(x0, y0, z), (x0, y1, z), (x1, y1, z), (x1, y0, z)]
        out["bins"][name] = {
            "interior_world": [x0, x1, y0, y1],
            "overhead_px": project(corners, ov).round(1).tolist(),
        }
    return out
