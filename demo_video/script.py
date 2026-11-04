"""The demo video's script: every shot, its narration and what is on screen.

The recorded run's numbers, the benchmark counts, the per-task cost range and the first-try results
are read from web/public/demo/<id>/ and bench/results.json and asserted in load_facts, so the build
stops if they change. The naive program's 19 of 20 is guarded by tests/test_phase2_gate.py.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "web" / "public" / "demo"
BENCH = ROOT / "bench" / "results.json"
REPAIR_RUN = "20261101-063309-c15793"  # task 6, sentence only: 19/20 -> diagnosis -> 20/20
GLASS_SEED = 3018  # a task-3 test layout where the naive program's carried cube hits the glass
TAG_REPLAY = "Recorded run · same task · sentence only (no video)"
TAG_STANDIN = "Stand-in clip: a simulator render, not a human demo · model answers cached"
TAG_NAIVE = "Hand-written baseline · no obstacle handling · simulated physics, not scripted"
TAG_GENERATED = "Muse Wonder's program · from a stand-in clip · same layout"
TAG_BENCH = "Video columns: stand-in clips, not human demos"
VIEW_NOTE = "This camera faces the robot: its left bin is on the right of the view."


VISUALS = {
    "cold_open": "Hero camera, 1080p: `examples/size_sort_naive.py` on task-3 layout 3018. The arm carries the red cube into the glass and the glass falls. The program's source is on screen.",
    "title": "Title card; 20 cells fill green.",
    "form": "New task page, live backend: the stand-in clip drops in and plays, the preset sentence is picked, Start.",
    "stages": "Job page, live: Prep and Discover done, Track running.",
    "tracked": "Demonstration card: what SAM 3.1 tracked, in move order, with the note that no bowls were found in the render.",
    "spec": 'Task spec card, chip "Needs your approval": rules and checks, then Approve and run.',
    "program": "Program card: the generated program.",
    "grid_live": "Test scenes filling in live, sped up with a label on screen, until 20 passed.",
    "failure_grid": "Replay of the recorded sentence-only run: Test scenes on A0, 19 green, scene 6004 red.",
    "failure_clip": "Hero camera, 1080p: the recorded run's first program on layout 6004. The arm lifts the red cube, the program stops, the cube drops and the arm parks.",
    "evidence": "Scene dialog: simulator and SAM columns, then the 8 frames sent to Muse Wonder.",
    "diagnosis": "Attempts card: the diagnosis, verbatim.",
    "patch": 'Program card on R1, view "Changes +4 −6".',
    "verified": "Test scenes on R1, then the VERIFIED badge.",
    "judges": "Scene dialog: Simulator and SAM 3.1 side by side. Run details: 120/120, In bin 80/80, Unmoved 40/40.",
    "benchmark": 'Benchmark page: the sentence-only column, then the video column, with "stand-in clip" under every video value.',
    "glass": "Hero camera, 1080p: the stand-in task-3 run's program on the same layout 3018; the arm goes up and around the glass. The program's source is on screen.",
    "craft": "Card: the jobs of Muse Wonder and SAM 3.1.",
    "impact": "Card: Julie Shah's integration-cost figure and who the product is for.",
    "end": "End card: links, models, and what is not done yet.",
}


@dataclass
class Shot:
    id: str
    kind: str  # "clip": full-frame footage; "ui": screen capture; "card": animated card
    lines: list[str]
    tag: str | None = None
    visual: str = ""  # what is on screen, for the shot list in docs/demo_script.md
    speed: float = 1.0  # footage plays this many times faster than it was recorded
    code: str | None = None  # small program listing over the footage
    lead: float = 0.2
    tail: float = 0.3
    extra: dict = field(default_factory=dict)


@dataclass
class Facts:
    repair: dict
    bench: dict

    @property
    def first(self) -> dict:
        return self.repair["attempts"][0]

    @property
    def last(self) -> dict:
        return self.repair["attempts"][-1]

    @property
    def failed_seed(self) -> dict:
        return next(s for s in self.first["seeds"] if not s["passed"])

    def rows(self) -> dict:
        return self.bench["tasks"]

    def matches(self, cond: str) -> tuple[int, int]:
        """Tasks whose final program passes all 20 reference scenes, in one condition."""
        vals = [t.get(cond, {}).get("ref_final") for t in self.rows().values()]
        vals = [v for v in vals if v]
        return sum(1 for v in vals if v[0] == v[1]), len(vals)


def load_facts() -> Facts:
    repair = json.loads((DEMO / REPAIR_RUN / "report.json").read_text())
    bench = json.loads(BENCH.read_text())
    f = Facts(repair, bench)
    assert repair["rules"] == "Sort by color, but blue things stay where they are."
    assert repair["use_video"] is False
    assert (f.first["passed"], f.first["total"]) == (19, 20)
    assert (f.last["passed"], f.last["total"]) == (20, 20)
    assert len(repair["attempts"]) == 2
    assert f.failed_seed["seed"] == 6004
    assert (repair["agreement"]["agree"], repair["agreement"]["n"]) == (120, 120)
    frames = json.loads((DEMO / REPAIR_RUN / "job.json").read_text())["attempts"][0]
    assert len(frames["failure_frames"]) == 8
    for conds in bench["tasks"].values():
        standin = conds["standin"]
        assert standin["ref_first"] == [20, 20]  # "on the first try"
        assert 1 <= standin["cost_usd"] <= 2  # "one to two dollars"
    return f


def word_number(n: int) -> str:
    words = {4: "four", 5: "five", 6: "six", 19: "nineteen", 20: "twenty"}
    return words[n]


def shots(*args, **kwargs) -> list[Shot]:
    out = _shots(*args, **kwargs)
    for s in out:
        s.visual = VISUALS[s.id]
    return out


def _shots(f: Facts, glass_program: str, naive_program: str) -> list[Shot]:
    s_k, s_n = f.matches("sentence")
    v_k, v_n = f.matches("standin")
    diagnosis = f.last["diagnosis"].rstrip(".")
    return [
        Shot(
            "cold_open",
            "clip",
            [
                "This program looks correct.",
                "It also knocks over the glass in 19 of 20 random layouts.",
            ],
            tag=TAG_NAIVE,
            code=naive_program,
            extra={"clip": "naive", "start": 3.4},
            lead=0.3,
            tail=0.7,
        ),
        Shot(
            "title",
            "card",
            [
                "Telling a robot what to do still means writing a program, and the person who knows the task usually can't read it.",
                "Show and Tell Robotics changes that.",
            ],
            extra={"card": "title"},
        ),
        Shot(
            "form",
            "ui",
            [
                "You show the task once on video, and say the rule in one sentence: sort by color, but blue things stay where they are.",
                "Here, a simulator render stands in for the phone clip.",
            ],
            tag=TAG_STANDIN,
            extra={"ui": "form"},
        ),
        Shot(
            "stages",
            "ui",
            ["Muse Wonder names the objects on the table."],
            tag=TAG_STANDIN,
            extra={"ui": "stages"},
            tail=0.2,
        ),
        Shot(
            "tracked",
            "ui",
            ["SAM 3.1 tracks each one: what moved, in what order, and what stayed put."],
            tag=TAG_STANDIN,
            extra={"ui": "tracked"},
        ),
        Shot(
            "spec",
            "ui",
            [
                "Before any code exists, Muse Wonder watches the video and writes the tests: red ends in the left bin, green in the right, blue never moves.",
                "You read them, edit them, approve.",
            ],
            tag=TAG_STANDIN,
            extra={"ui": "spec"},
            tail=0.2,
        ),
        Shot(
            "program",
            "ui",
            ["Then it writes a short program out of tested robot skills."],
            tag=TAG_STANDIN,
            extra={"ui": "program"},
            tail=0.2,
        ),
        Shot(
            "grid_live",
            "ui",
            [
                "It runs on 20 randomized scenes in MuJoCo, with a simulated Franka arm.",
                "This one passes all 20 on the first try.",
            ],
            tag=TAG_STANDIN,
            extra={"ui": "grid_live", "fit": True},
        ),
        Shot(
            "failure_grid",
            "ui",
            [
                "So what happens when a scene fails?",
                "In this recorded run, from the sentence alone, 19 scenes pass.",
            ],
            tag=TAG_REPLAY,
            extra={"ui": "failure_grid"},
            tail=0.2,
        ),
        Shot(
            "failure_clip",
            "clip",
            [
                "In scene 6004, the program stops after the lift: "
                "its detour around a blue cube is out of reach, so the arm lets go and parks."
            ],
            tag=TAG_REPLAY,
            extra={"clip": "stuck", "start": 3.6},
            tail=0.5,
        ),
        Shot(
            "evidence",
            "ui",
            ["Muse Wonder gets the failed checks, the error and eight frames from that scene."],
            tag=TAG_REPLAY,
            extra={"ui": "evidence"},
            tail=0.2,
        ),
        Shot(
            "diagnosis",
            "ui",
            [f"Its diagnosis, word for word: {diagnosis}."],
            tag=TAG_REPLAY,
            extra={"ui": "diagnosis"},
        ),
        Shot(
            "patch",
            "ui",
            [
                "The patch drops the detour: the blue cubes are low enough for the arm's normal travel height."
            ],
            tag=TAG_REPLAY,
            extra={"ui": "patch"},
        ),
        Shot(
            "verified",
            "ui",
            ["All 20 scenes run again. 20/20: verified."],
            tag=TAG_REPLAY,
            extra={"ui": "verified"},
        ),
        Shot(
            "judges",
            "ui",
            [
                "Every scene also gets a second judge: SAM 3.1, looking only at the rendered video.",
                f"It agreed with the simulator on all {f.repair['agreement']['n']} checks, the failure included. The simulator still decides.",
            ],
            tag=TAG_REPLAY,
            extra={"ui": "judges"},
        ),
        Shot(
            "benchmark",
            "ui",
            [
                f"Why the video? From the sentence alone, {word_number(s_k)} of {word_number(s_n)} tasks match our reference: where the sentence doesn't name the bin, Wonder has to guess.",
                "The run you just saw is a miss: it guessed the middle bin for green.",
                f"With a stand-in clip, {word_number(v_k)} of {word_number(v_n)} match, on the first try. Human clips are next.",
            ],
            tag=TAG_BENCH,
            extra={"ui": "benchmark"},
        ),
        Shot(
            "glass",
            "clip",
            [
                "And the glass? On that same layout, Muse Wonder's program lifts above it and goes around.",
                "Every program it has written for that task avoids the glass.",
            ],
            tag=TAG_GENERATED,
            code=glass_program,
            extra={"clip": "generated", "start": 3.4},
        ),
        Shot(
            "craft",
            "card",
            [
                "Muse Wonder does four jobs here, and SAM 3.1 does two.",
                "Generated code runs in a sandbox, pass or fail comes from physics, "
                "and a verified task costs one to two dollars in API calls.",
            ],
            extra={"card": "models"},
        ),
        Shot(
            "impact",
            "card",
            [
                "Integration generally runs four to five times the cost of the robot, "
                "says MIT's Julie Shah.",
                "We want the person who knows the job to change it: "
                "one demonstration, one sentence, one test report.",
            ],
            extra={"card": "impact"},
        ),
        Shot(
            "end",
            "card",
            [
                "It runs in simulation today. Next is a pilot on a real arm, through the same skills and the same tests."
            ],
            extra={"card": "end"},
            tail=1.6,
        ),
    ]
