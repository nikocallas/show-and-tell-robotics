# Demo videos (filmed by a human)

Drop the files here with these exact names. Each video shows one task done once by hand; the
pipeline reads where each object ends up, so the bowl each object goes to must match the table below
(it matches the reference specs in `bench/tasks.yaml` that the benchmark scores against).

## Setup (same for every video)

- Phone **top-down**, about **80 cm** above a plain, matte table (no patterned cloth). Lock focus/exposure.
- Good, even light; avoid hard shadows. 1080p, landscape, **under 30 s**, one continuous take.
- **Turn off HDR** (iPhone: Settings → Camera → Record Video → HDR Video off). HDR clips lose
  contrast when converted for SAM; `check-videos` warns about them.
- **Three bowls in a row along the far edge** = the top of the frame. The bowl on the left of the
  frame is the *left bowl*, then *middle*, then *right*. Don't move the bowls during a take.
- Objects start spread out on the near half of the table (bottom of the frame), a few cm apart.
- Start and end with your hands out of frame for about 1 s. Move one object at a time.

## Props

- Blocks (or cups) in **two sizes** (big ≈ 1.4× the small one) and **three colors**: red, blue, green.
- A **yellow cylinder** (glue stick or marker); task 5 needs two.
- A **tall drinking glass** (tasks 3 and 4).

## Shot list

| File | Objects at the start | What your hand does |
|---|---|---|
| `smoke.mp4` (5 s) | one block | Move the block somewhere else on the table. |
| `task01_size.mp4` | big red, big blue, small red, small green | Both **big** blocks → **left** bowl; both **small** blocks → **right** bowl. |
| `task02_color.mp4` | big red, small red, big blue, small green | **Red** → **left** bowl, **blue** → **middle**, **green** → **right**. |
| `task03_size_glass.mp4` | big red, big blue, small red, small green, **tall glass** standing in the middle of the table between the blocks and the bowls | Big → **left**, small → **right**. Carry the blocks **over or around the glass without touching it**. |
| `task04_clear.mp4` | big red, small blue, yellow cylinder, **tall glass** in the middle | Everything except the glass → **middle** bowl. **Don't touch the glass.** |
| `task05_shape.mp4` | two yellow cylinders, small red block, big blue block | **Cylinders** → **left** bowl; **blocks** → **right** bowl. |
| `task06_color_exc.mp4` | big red, small green, big blue, small blue | **Red** → **left**, **green** → **right**; **both blue blocks stay where they are** (don't touch them). |

After adding videos, check them first (free, no API calls): names, length, resolution, and a
contact sheet of 4 frames per video in `data/video_check/` to confirm the framing:

```bash
.venv/bin/python -m showtell check-videos
```

Then the API smoke test (tracks the hand in `smoke.mp4`, about $0.02):

```bash
.venv/bin/python -m showtell smoke
```

Then run the benchmark's video condition. It is resumable and runs only what is missing, so the
sentence-only results already in `bench/results.json` are kept; each task's first video run is the
one the benchmark reports (about $1.50 per task with the SAM judge):

```bash
.venv/bin/python -m showtell bench
```

Single runs (`python -m showtell run --video ...`) are for trying things afterwards; with the local
response cache an unchanged request replays its earlier answer instead of drawing a new sample.
