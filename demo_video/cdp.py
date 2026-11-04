"""A small Chrome DevTools Protocol client: drive headless Chrome and record what it paints."""

from __future__ import annotations

import asyncio
import base64
import json
import shutil
import subprocess
import tempfile
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import websockets

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# Injected into every page: a visible cursor, click ripples, eased scrolling and a spotlight.
RIG_JS = r"""
(() => {
  if (window.__rig) return;
  const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
  const css = `
    #__cursor{position:fixed;left:0;top:0;z-index:2147483646;pointer-events:none;width:26px;height:26px;
      filter:drop-shadow(0 2px 4px rgba(0,0,0,.35));will-change:transform}
    #__spot{position:fixed;z-index:2147483640;pointer-events:none;border-radius:14px;opacity:0;
      box-shadow:0 0 0 4000px rgba(9,9,11,.45);outline:2.5px solid #6366f1;outline-offset:0;
      transition:left .45s cubic-bezier(.4,0,.2,1),top .45s cubic-bezier(.4,0,.2,1),
        width .45s cubic-bezier(.4,0,.2,1),height .45s cubic-bezier(.4,0,.2,1),opacity .35s}
    .__ripple{position:fixed;z-index:2147483645;pointer-events:none;width:44px;height:44px;margin:-22px 0 0 -22px;
      border-radius:50%;background:rgba(99,102,241,.35);animation:__r .5s ease-out forwards}
    @keyframes __r{from{transform:scale(.2);opacity:1}to{transform:scale(1.2);opacity:0}}
  `;
  const state = { x: -60, y: -60, target: null, pad: 8 };
  const setup = () => {
    if (document.getElementById("__cursor")) return;
    const st = document.createElement("style");
    st.textContent = css;
    document.head.appendChild(st);
    const cur = document.createElement("div");
    cur.id = "__cursor";
    cur.innerHTML = '<svg viewBox="0 0 24 24" width="26" height="26"><path d="M4 2l16 10.5-7.2 1.4L9.6 21z" fill="#111" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>';
    cur.style.transform = `translate(${state.x}px,${state.y}px)`;
    document.body.appendChild(cur);
    const spot = document.createElement("div");
    spot.id = "__spot";
    document.body.appendChild(spot);
  };
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const find = (q) => {
    if (typeof q === "string") return document.querySelector(q);
    const scope = q.within ? find(q.within) : document;
    if (!scope) return null;
    if (q.css) {
      const all = [...scope.querySelectorAll(q.css)].filter(visible);
      if (q.text) {
        const hit = all.filter((e) => (e.textContent || "").includes(q.text));
        return hit[q.nth || 0] || null;
      }
      return all[q.nth || 0] || null;
    }
    // The innermost visible element whose text contains q.text.
    const all = [...scope.querySelectorAll("body *")].concat(scope === document ? [] : [...scope.querySelectorAll("*")]);
    const hits = all.filter((e) => visible(e) && (e.textContent || "").includes(q.text)
      && ![...e.children].some((c) => visible(c) && (c.textContent || "").includes(q.text)));
    return hits[q.nth || 0] || null;
  };
  const rect = (q) => {
    if (q && q.column) { // a table column, header to last row
      const th = [...document.querySelectorAll("th")].find((t) => t.textContent.includes(q.column));
      if (!th) return null;
      const tb = th.closest("table").getBoundingClientRect();
      const r = th.getBoundingClientRect();
      return { x: r.left, y: r.top, w: r.width, h: tb.bottom - r.top };
    }
    const el = find(q);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { x: r.left, y: r.top, w: r.width, h: r.height };
  };
  const placeSpot = () => {
    const spot = document.getElementById("__spot");
    if (!spot || !state.target) return;
    const r = rect(state.target);
    if (!r) return;
    const p = state.pad;
    spot.style.left = `${r.x - p}px`;
    spot.style.top = `${r.y - p}px`;
    spot.style.width = `${r.w + 2 * p}px`;
    spot.style.height = `${r.h + 2 * p}px`;
  };
  window.__rig = {
    setup, find, rect,
    moveTo(x, y, ms) {
      setup();
      const cur = document.getElementById("__cursor");
      const x0 = state.x, y0 = state.y, t0 = performance.now();
      return new Promise((done) => {
        const step = (now) => {
          const t = Math.min(1, (now - t0) / Math.max(1, ms));
          const k = ease(t);
          state.x = x0 + (x - x0) * k;
          state.y = y0 + (y - y0) * k;
          cur.style.transform = `translate(${state.x}px,${state.y}px)`;
          t < 1 ? requestAnimationFrame(step) : done();
        };
        requestAnimationFrame(step);
      });
    },
    ripple(x, y) {
      const r = document.createElement("div");
      r.className = "__ripple";
      r.style.left = `${x}px`;
      r.style.top = `${y}px`;
      document.body.appendChild(r);
      setTimeout(() => r.remove(), 600);
    },
    scrollTo(top, ms, box) {
      const el = box ? find(box) : null;
      const read = () => (el ? el.scrollTop : window.scrollY);
      const write = (v) => (el ? (el.scrollTop = v) : window.scrollTo(0, v));
      const y0 = read(), t0 = performance.now();
      return new Promise((done) => {
        const step = (now) => {
          const t = Math.min(1, (now - t0) / Math.max(1, ms));
          write(y0 + (top - y0) * ease(t));
          placeSpot();
          t < 1 ? requestAnimationFrame(step) : done();
        };
        requestAnimationFrame(step);
      });
    },
    spot(q, pad) {
      setup();
      const spot = document.getElementById("__spot");
      state.target = q;
      state.pad = pad == null ? 8 : pad;
      if (!q) {
        spot.style.opacity = "0";
        return;
      }
      if (spot.style.opacity !== "1") {
        // Coming back from hidden: appear in place, without sliding over from the last target.
        const was = spot.style.transition;
        spot.style.transition = "opacity .35s";
        placeSpot();
        spot.getBoundingClientRect();
        spot.style.transition = was;
      } else {
        placeSpot();
      }
      spot.style.opacity = "1";
    },
  };
  document.addEventListener("DOMContentLoaded", setup);
})();
"""


@dataclass
class Capture:
    """Screencast frames with their paint times."""

    out_dir: Path
    frames: list[tuple[float, Path]] = field(default_factory=list)
    started: float = 0.0
    stopped: float = 0.0
    marks: dict[str, float] = field(default_factory=dict)


class Browser:
    def __init__(self, width: int = 1280, height: int = 640, scale: float = 1.5, port: int = 9333):
        self.width, self.height, self.scale, self.port = width, height, scale, port
        self._next_id = 0
        self._waiting: dict[int, asyncio.Future] = {}
        self._events: dict[str, asyncio.Event] = {}
        self.capture: Capture | None = None
        self.proc: subprocess.Popen | None = None
        self.profile: str | None = None
        self.ws = None

    # ---------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        self.profile = tempfile.mkdtemp(prefix="showtell-chrome-")
        self.proc = subprocess.Popen(
            [
                CHROME,
                "--headless=new",
                f"--remote-debugging-port={self.port}",
                f"--user-data-dir={self.profile}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-extensions",
                "--hide-scrollbars",
                "--mute-audio",
                "--autoplay-policy=no-user-gesture-required",
                f"--force-device-scale-factor={self.scale}",  # screencast frames at device pixels
                f"--window-size={self.width},{self.height + 100}",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        target = None
        for _ in range(100):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/list") as r:
                    pages = [t for t in json.load(r) if t.get("type") == "page"]
                if pages:
                    target = pages[0]
                    break
            except OSError:
                pass
            await asyncio.sleep(0.1)
        if not target:
            raise RuntimeError("Chrome did not open a debugging port")
        self.ws = await websockets.connect(target["webSocketDebuggerUrl"], max_size=None)
        self._reader = asyncio.create_task(self._read_loop())
        for domain in ("Page", "Runtime", "DOM", "Network"):
            await self.send(f"{domain}.enable")
        await self.send(
            "Emulation.setDeviceMetricsOverride",
            width=self.width,
            height=self.height,
            deviceScaleFactor=0,  # keep the real factor: an emulated one caps screencast frames
            mobile=False,
        )
        await self.send("Page.addScriptToEvaluateOnNewDocument", source=RIG_JS)

    async def close(self) -> None:
        if self.ws:
            await self.ws.close()
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self.profile:
            shutil.rmtree(self.profile, ignore_errors=True)

    # ---------------------------------------------------------------- protocol
    async def _read_loop(self) -> None:
        async for raw in self.ws:
            msg = json.loads(raw)
            if "id" in msg:
                fut = self._waiting.pop(msg["id"], None)
                if fut and not fut.done():
                    if "error" in msg:
                        fut.set_exception(RuntimeError(str(msg["error"])))
                    else:
                        fut.set_result(msg.get("result", {}))
            elif msg.get("method") == "Page.screencastFrame":
                self._on_frame(msg["params"])
            elif msg.get("method") == "Page.loadEventFired":
                self._events.setdefault("load", asyncio.Event()).set()

    async def send(self, method: str, **params: Any) -> dict:
        self._next_id += 1
        fut = asyncio.get_running_loop().create_future()
        self._waiting[self._next_id] = fut
        await self.ws.send(json.dumps({"id": self._next_id, "method": method, "params": params}))
        return await fut

    def _on_frame(self, params: dict) -> None:
        cap = self.capture
        asyncio.ensure_future(self.send("Page.screencastFrameAck", sessionId=params["sessionId"]))
        if cap is None:
            return
        path = cap.out_dir / f"{len(cap.frames):06d}.jpg"
        path.write_bytes(base64.b64decode(params["data"]))
        cap.frames.append((time.time(), path))

    # ---------------------------------------------------------------- page helpers
    async def eval(self, js: str) -> Any:
        res = await self.send(
            "Runtime.evaluate", expression=js, awaitPromise=True, returnByValue=True
        )
        if res.get("exceptionDetails"):
            raise RuntimeError(f"page error: {res['exceptionDetails']}\n{js[:300]}")
        return res.get("result", {}).get("value")

    async def goto(self, url: str, settle: float = 0.6) -> None:
        ev = self._events.setdefault("load", asyncio.Event())
        ev.clear()
        await self.send("Page.navigate", url=url)
        await asyncio.wait_for(ev.wait(), timeout=30)
        await asyncio.sleep(settle)
        await self.eval("window.__rig && window.__rig.setup()")

    async def wait_for(self, query: dict | str, timeout: float = 30.0) -> dict:
        """Wait until the element exists and has a size; returns its rect."""
        q = json.dumps(query)
        t0 = time.time()
        while time.time() - t0 < timeout:
            r = await self.eval(f"window.__rig.rect({q})")
            if r:
                return r
            await asyncio.sleep(0.1)
        raise TimeoutError(f"no element for {query}")

    async def rect(self, query: dict | str) -> dict | None:
        return await self.eval(f"window.__rig.rect({json.dumps(query)})")

    async def scroll_y(self) -> float:
        return await self.eval("window.scrollY")

    async def scroll_into_view(
        self, query: dict | str, top: float = 70.0, ms: int = 800, box: dict | str | None = None
    ) -> None:
        """Eased scroll that leaves the element `top` px under the viewport's top edge."""
        r = await self.wait_for(query)
        if box:
            current = await self.eval(f"window.__rig.find({json.dumps(box)}).scrollTop")
        else:
            current = await self.scroll_y()
        box_js = json.dumps(box) if box else "null"
        target = max(0, current + r["y"] - top)
        await self.eval(f"window.__rig.scrollTo({target}, {ms}, {box_js})")

    async def move(self, query: dict | str, ms: int = 700) -> tuple[float, float]:
        r = await self.wait_for(query)
        x, y = r["x"] + r["w"] / 2, r["y"] + r["h"] / 2
        await self.eval(f"window.__rig.moveTo({x}, {y}, {ms})")
        return x, y

    async def click(self, query: dict | str, ms: int = 700) -> None:
        x, y = await self.move(query, ms)
        await self.send("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
        await asyncio.sleep(0.08)
        await self.eval(f"window.__rig.ripple({x}, {y})")
        for kind in ("mousePressed", "mouseReleased"):
            await self.send(
                "Input.dispatchMouseEvent", type=kind, x=x, y=y, button="left", clickCount=1
            )

    async def spot(self, query: dict | str | None, pad: int = 8) -> None:
        await self.eval(f"window.__rig.spot({json.dumps(query)}, {pad})")

    async def set_file(self, selector: str, path: Path) -> None:
        root = await self.send("DOM.getDocument")
        node = await self.send(
            "DOM.querySelector", nodeId=root["root"]["nodeId"], selector=selector
        )
        await self.send("DOM.setFileInputFiles", nodeId=node["nodeId"], files=[str(path)])

    async def screenshot(self, path: Path, transparent: bool = False) -> Path:
        if transparent:
            await self.send(
                "Emulation.setDefaultBackgroundColorOverride",
                color={"r": 0, "g": 0, "b": 0, "a": 0},
            )
        res = await self.send("Page.captureScreenshot", format="png")
        path.write_bytes(base64.b64decode(res["data"]))
        if transparent:
            await self.send("Emulation.setDefaultBackgroundColorOverride")
        return path

    # ---------------------------------------------------------------- recording
    async def start_capture(self, out_dir: Path) -> Capture:
        out_dir = Path(out_dir)
        if out_dir.exists():
            shutil.rmtree(out_dir)
        out_dir.mkdir(parents=True)
        self.capture = Capture(out_dir=out_dir, started=time.time())
        await self.send(
            "Page.startScreencast",
            format="jpeg",
            quality=92,
            everyNthFrame=1,
            maxWidth=int(self.width * self.scale),
            maxHeight=int(self.height * self.scale),
        )
        return self.capture

    async def stop_capture(self) -> Capture:
        await self.send("Page.stopScreencast")
        cap = self.capture
        cap.stopped = time.time()
        self.capture = None
        return cap

    def mark(self, name: str) -> None:
        if self.capture:
            self.capture.marks[name] = time.time()


def capture_to_mp4(
    cap: Capture,
    out: Path,
    start: float | None = None,
    end: float | None = None,
    fps: int = 30,
    crf: int = 16,
) -> Path:
    """Constant-frame-rate H.264 from a capture's frames, between two wall-clock times."""
    start = cap.started if start is None else start
    end = cap.stopped if end is None else end
    frames = cap.frames
    if not frames:
        raise ValueError(f"nothing was painted in {cap.out_dir}")
    # The frame showing at `start` is the last one painted before it.
    i = 0
    while i + 1 < len(frames) and frames[i + 1][0] <= start:
        i += 1
    chosen = []
    t = start
    for j in range(i, len(frames)):
        ts, path = frames[j]
        if ts >= end:
            break
        next_ts = frames[j + 1][0] if j + 1 < len(frames) else end
        stop = min(next_ts, end)
        if stop > t:
            chosen.append((path, stop - t))
            t = stop
    if t < end and chosen:  # nothing painted to the end: hold the last frame
        chosen[-1] = (chosen[-1][0], chosen[-1][1] + end - t)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    listing = out.with_suffix(".txt")
    lines = ["ffconcat version 1.0"]
    for path, dur in chosen:
        lines += [f"file '{path.resolve()}'", f"duration {dur:.4f}"]
    lines.append(f"file '{chosen[-1][0].resolve()}'")
    listing.write_text("\n".join(lines) + "\n")
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
            "-vf", f"fps={fps},format=yuv420p", "-t", f"{end - start:.3f}",
            "-c:v", "libx264", "-preset", "fast", "-crf", str(crf), str(out),
        ],
        check=True,
    )  # fmt: skip
    listing.unlink()
    return out
