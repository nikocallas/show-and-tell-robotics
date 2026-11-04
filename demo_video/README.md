# Demo video build

`python -m demo_video` builds the submission's demo video from this repository: real screen capture of
the web app, simulator footage of real programs on real seeds, and the captions in `script.py`. No video editor is involved, so the
cut can be rebuilt after any change. The recorded run's numbers, the benchmark counts and the per-task
cost range are read from the run data and asserted in `script.py`, and each simulator clip's outcome
is checked on every build. The naive program's 19 of 20 is guarded by `tests/test_phase2_gate.py`.

macOS only: it needs Google Chrome, the `say` command and ffmpeg.

## Run it

Three servers first (`.claude/launch.json` has them as `demo`, `web` and `api`):

```bash
cd web && npm run build && npx vite preview --port 4173 --strictPort
```

```bash
npm --prefix web run dev
```

```bash
.venv/bin/uvicorn showtell.api.app:app --port 8000 --timeout-graceful-shutdown 3
```

Then:

```bash
.venv/bin/python -m demo_video
```

Output in `demo_video/out/`:

- `show_and_tell_demo_silent.mp4`: the cut, captions only.
- `show_and_tell_demo.srt`: the captions as a file.
- `preview_macos_voice.mp4`: the same cut read by a macOS system voice, **for your own review only**.
  macOS's license keeps system voices to personal, non-commercial use, so do not publish this file. The
  build also uses that voice to time the captions.

It also refreshes the shot list in `docs/demo_script.md`.

The live shots start a real job on the local backend. Its answers come from the local cache of the
benchmark's stand-in run, so a rebuild on a machine that has that cache pays nothing.

## Sound and publishing

- `--voiceover my_voice.m4a` adds a recorded voice to the captions-only cut and writes
  `demo_video/out/show_and_tell_demo_voiced.mp4`. Only the sound comes over: the first audio track, as
  mono at -16 LUFS, without the recording's metadata.
- `--publish FILE` copies a finished cut and its captions to `web/public/video/`, where the hosted demo
  serves them after the next deploy.

## Other flags

- `--only form,spec`: re-capture only those shots and keep the rest. The live shots share one job, so
  re-capture them together.
- `--no-capture`: reuse every capture; only re-time and re-compose.
- `--voice NAME`: another macOS voice for the timing and the preview.

## Files

| File | What it does |
|---|---|
| `script.py` | Every shot: narration, labels, what is on screen. Loads and asserts the facts. |
| `ui_shots.py` | The screen shots: the web app driven by script. |
| `cdp.py` | Small Chrome DevTools client: cursor, spotlight, recording. |
| `clips.py` | Simulator footage at 1920×1080: real programs on real seeds. |
| `cards.py` | Title, models, impact and end cards, as animated HTML. |
| `compose.py` | Timing, captions, labels and the final cut with ffmpeg. |
| `__main__.py` | The build. |
