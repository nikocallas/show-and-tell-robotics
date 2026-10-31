"""Pydantic schemas shared by perception, the agent, verification, the pipeline and the API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

Size = Literal["small", "large"]
Color = Literal["red", "blue", "green", "yellow", "clear"]
Shape = Literal["cube", "cylinder", "glass"]
BinName = Literal["left_bin", "middle_bin", "right_bin"]
CheckKind = Literal["in_bin", "untouched", "upright", "unmoved"]

VOCABULARY = {
    "size": ["small", "large"],
    "color": ["red", "blue", "green", "yellow", "clear"],
    "shape": ["cube", "cylinder", "glass"],
    "fragile": [True, False],
    "bins": ["left_bin", "middle_bin", "right_bin"],
}


class ObjectAttrs(BaseModel):
    size: Size | None = None
    color: Color | None = None
    shape: Shape | None = None
    fragile: bool | None = None


class Selector(ObjectAttrs):
    """Attribute filter; unset fields match anything."""

    def as_dict(self) -> dict[str, str | bool]:
        return {k: v for k, v in self.model_dump().items() if v is not None}

    def describe(self) -> str:
        d = self.as_dict()
        if not d:
            return "every object"
        words = [d[k] for k in ("size", "color") if k in d]
        if d.get("fragile") is True:
            words.insert(0, "fragile")
        elif d.get("fragile") is False:
            words.insert(0, "non-fragile")
        noun = {"cube": "cubes", "cylinder": "cylinders", "glass": "glasses"}.get(
            d.get("shape", ""), "objects"
        )
        return " ".join([*words, noun])


class Rule(BaseModel):
    select: Selector
    place_in: BinName | None = None
    leave: bool = False

    @model_validator(mode="after")
    def _one_action(self) -> Rule:
        if (self.place_in is None) == (not self.leave):
            raise ValueError(
                "a rule either places objects in a bin (place_in) or leaves them (leave=true)"
            )
        return self


class Check(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_]{1,40}$")
    kind: CheckKind
    select: Selector
    bin: BinName | None = None
    text: str

    @model_validator(mode="after")
    def _bin_for_in_bin(self) -> Check:
        if self.kind == "in_bin" and self.bin is None:
            raise ValueError(f"check {self.id}: in_bin needs a bin")
        if self.kind != "in_bin" and self.bin is not None:
            self.bin = None
        return self


class TaskSpec(BaseModel):
    rules: list[Rule]
    checks: list[Check]
    clarifying_question: str | None = None
    rationale: str

    @model_validator(mode="after")
    def _unique_ids(self) -> TaskSpec:
        ids = [c.id for c in self.checks]
        if len(ids) != len(set(ids)):
            raise ValueError("check ids must be unique")
        return self


class Program(BaseModel):
    source: str
    notes: str


class RepairResult(BaseModel):
    diagnosis: str  # one plain sentence
    source: str


# ------------------------------------------------------------------------- perception
class DiscoveredObject(BaseModel):
    concept: str  # SAM noun phrase, e.g. "red block"
    attrs: ObjectAttrs
    count: int = 1


class Discovery(BaseModel):
    objects: list[DiscoveredObject]
    container_concept: str = "bowl"
    notes: str = ""


class TrackedObject(BaseModel):
    track_id: str  # "<concept>#<sam object id>"
    concept: str
    attrs: ObjectAttrs
    moved: bool
    start_px: tuple[float, float]
    end_px: tuple[float, float]
    end_bin: BinName | None = None
    move_order: int | None = None  # 0 = first object the hand moved
    first_move_frame: int | None = None


class TrackSummary(BaseModel):
    objects: list[TrackedObject]
    bins: list[BinName] = ["left_bin", "middle_bin", "right_bin"]
    fps: float = 10
    frames: int = 0
    width: int = 0
    height: int = 0
    notes: list[str] = []
