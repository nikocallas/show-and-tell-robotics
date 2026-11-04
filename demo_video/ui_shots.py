"""The screen-capture shots: the real web app, driven by script, in real time.

Each function runs its actions and returns; the caller keeps recording until the shot's length.
LIVE = `npm run dev` + uvicorn (a real job); DEMO = the production build replaying recorded runs.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from demo_video.cdp import Browser

LIVE = "http://localhost:5173"
DEMO = "http://localhost:4173"
ROOT = Path(__file__).resolve().parent.parent
LIVE_CLIP = ROOT / "bench" / "videos" / "standin" / "standin_task06_color_exc.mp4"
LIVE_RULE = "Sort by color, but blue things stay where they are."
TASK_SPEC = {"css": "section", "text": "Task spec"}
TEST_SCENES = {"css": "section", "text": "Test scenes"}
SCENES_TOP = 48  # the whole grid and its legend clear the caption band
PROGRAM = {"css": "section", "text": "def run(scene)"}
ATTEMPTS = {"css": "section", "text": "up to 3 repairs"}
DEMONSTRATION = {"css": "section", "text": "Demonstration"}
RUN_DETAILS = {"css": "section", "text": "Run details"}


def button(text: str, within: dict | None = None) -> dict:
    q = {"css": "button", "text": text}
    return {**q, "within": within} if within else q


async def click_now(b: Browser, q: dict) -> None:
    """A click no one sees: for setting a screen up before a shot starts."""
    await b.eval(f"window.__rig.find({json.dumps(q)}).click()")


# ------------------------------------------------------------------------ live job
async def form(b: Browser) -> None:
    """Drop the clip, pick the sentence, press Start."""
    await asyncio.sleep(0.4)
    await b.move({"text": "Demonstration video"}, ms=600)
    await b.set_file("input[type=file]", LIVE_CLIP)
    await asyncio.sleep(0.8)
    # From where the arm starts moving a cube: the viewer sees the demonstration happen.
    await b.eval(
        "(() => { const v = document.querySelector('video'); v.currentTime = 5.5; v.play(); })()"
    )
    await asyncio.sleep(3.6)
    await b.scroll_into_view({"text": "Try a benchmark task"}, top=200, ms=700)
    await b.click(button(LIVE_RULE), ms=700)
    await asyncio.sleep(0.8)
    await b.scroll_into_view({"css": "button[type=submit]"}, top=520, ms=600)
    await b.click({"css": "button[type=submit]"}, ms=600)


async def stages(b: Browser) -> None:
    """The job page right after Start: Prep and Discover done, SAM tracking the clip."""
    await b.wait_for({"text": "Task spec"})
    await b.eval("window.__rig.moveTo(1120, 560, 600)")


async def wait_for_approval(b: Browser) -> None:
    """Off camera: the rest of the tracking and the spec."""
    await b.wait_for({"text": "Needs your approval"}, timeout=180)
    await asyncio.sleep(0.5)


async def tracked(b: Browser) -> None:
    await b.scroll_into_view(DEMONSTRATION, top=90, ms=900)
    await b.spot(DEMONSTRATION, pad=4)


async def spec(b: Browser) -> None:
    await b.spot(None)
    await b.scroll_into_view(TASK_SPEC, top=70, ms=800)
    await asyncio.sleep(0.3)
    await b.spot(TASK_SPEC, pad=4)
    await asyncio.sleep(8.6)
    await b.spot(None)
    approve = button("Approve and run")
    await b.click(approve, ms=800)


async def program(b: Browser) -> None:
    await b.wait_for({"text": "def run(scene)"}, timeout=120)
    await b.scroll_into_view(PROGRAM, top=70, ms=800)


async def grid_live(b: Browser) -> None:
    await b.scroll_into_view(TEST_SCENES, top=SCENES_TOP, ms=800)
    await b.wait_for({"text": "20 passed"}, timeout=180)
    await asyncio.sleep(1.6)  # the last thumbnails load


# ------------------------------------------------------------------------ recorded run
async def open_replay(b: Browser, run_id: str) -> None:
    await b.goto(f"{DEMO}/replay/{run_id}", settle=1.0)
    await b.wait_for({"css": "[aria-label='Skip to the end']"})
    await b.eval("document.querySelector(\"[aria-label='Skip to the end']\").click(); 1")
    await asyncio.sleep(0.8)
    # The player would cover the bottom row of scenes; the label on screen says it is a recording.
    await b.eval(
        """document.querySelector("[aria-label='Replay controls']").parentElement"""
        """.style.display = 'none'; 1"""
    )


async def pause_modal_video(b: Browser) -> None:
    """The dialog's video stays still: robot footage gets its own full-frame shots."""
    await b.eval(
        "(() => { const v = document.querySelector('[role=dialog] video'); if (v) v.pause(); })()"
    )


async def close_modal(b: Browser) -> None:
    await pause_modal_video(b)
    await b.send("Input.dispatchKeyEvent", type="keyDown", key="Escape", code="Escape")
    await b.send("Input.dispatchKeyEvent", type="keyUp", key="Escape", code="Escape")
    await asyncio.sleep(0.2)


async def before_failure_grid(b: Browser) -> None:
    await b.scroll_into_view(TEST_SCENES, top=SCENES_TOP, ms=10)
    await asyncio.sleep(0.2)
    await click_now(b, button("A0", TEST_SCENES))
    await asyncio.sleep(0.5)


async def failure_grid(b: Browser, seed: int = 6004) -> None:
    """Nineteen green, one red."""
    await asyncio.sleep(3.2)
    await b.spot(button(str(seed), TEST_SCENES), pad=3)


async def evidence(b: Browser, seed: int = 6004) -> None:
    """The red scene's error and checks, then the frames Muse Wonder got."""
    await b.spot(None)
    await b.scroll_into_view(TEST_SCENES, top=SCENES_TOP, ms=10)
    await b.click(button(str(seed), TEST_SCENES), ms=600)
    await asyncio.sleep(0.3)
    await pause_modal_video(b)
    await b.spot({"css": "[role=dialog] table"}, pad=4)
    await asyncio.sleep(2.2)
    await b.spot(None)
    await close_modal(b)
    frames = {"text": "What Muse Wonder saw"}
    await b.scroll_into_view(frames, top=120, ms=600)


async def diagnosis(b: Browser) -> None:
    await b.spot({"css": "p", "text": "Avoiding the blue cubes"}, pad=10)


async def before_patch(b: Browser) -> None:
    await b.spot(None)
    await b.scroll_into_view(PROGRAM, top=70, ms=10)
    await asyncio.sleep(0.3)


async def patch(b: Browser) -> None:
    await b.click(button("R1", PROGRAM), ms=500)
    await asyncio.sleep(0.4)
    await b.click(button("Changes", PROGRAM), ms=600)


async def before_verified(b: Browser) -> None:
    await b.scroll_into_view(TEST_SCENES, top=SCENES_TOP, ms=10)
    await asyncio.sleep(0.3)


async def verified(b: Browser) -> None:
    await b.click(button("R1 ·", TEST_SCENES), ms=400)
    await asyncio.sleep(1.2)
    badge = {"css": "[role=status]", "text": "VERIFIED"}
    await b.scroll_into_view(badge, top=90, ms=500)
    await b.spot(badge, pad=8)


async def before_judges(b: Browser) -> None:
    await b.spot(None)
    await b.scroll_into_view(TEST_SCENES, top=SCENES_TOP, ms=10)
    await asyncio.sleep(0.2)
    await click_now(b, button("A0", TEST_SCENES))
    await asyncio.sleep(0.4)


async def judges(b: Browser, seed: int = 6004) -> None:
    await b.click(button(str(seed), TEST_SCENES), ms=400)
    await asyncio.sleep(0.3)
    await pause_modal_video(b)
    await b.spot({"css": "[role=dialog] table"}, pad=4)
    await asyncio.sleep(4.2)
    await b.spot(None)
    await close_modal(b)
    await b.scroll_into_view(RUN_DETAILS, top=80, ms=700)
    await b.spot(RUN_DETAILS, pad=4)


async def before_benchmark(b: Browser) -> None:
    await b.spot(None)
    await b.goto(f"{DEMO}/bench", settle=1.0)
    await b.wait_for({"text": "task01_size"})
    await asyncio.sleep(0.6)
    await b.scroll_into_view({"css": "table"}, top=66, ms=10)
    await asyncio.sleep(0.3)


async def benchmark(b: Browser, switch_at: float) -> None:
    """The sentence-only column, then the video column when the narration gets to it."""
    await asyncio.sleep(0.8)
    await b.spot({"column": "Final (sentence only)"}, pad=2)
    await asyncio.sleep(max(0.0, switch_at - 0.8))
    await b.spot({"column": "Final (video)"}, pad=2)
