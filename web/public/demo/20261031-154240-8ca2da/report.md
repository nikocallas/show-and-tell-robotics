# Show & Tell job `20261031-154240-8ca2da`

> Stand-in video: a top-down render of the robot doing this task, not a human demonstration (the human video is not recorded yet). Run from the web app; the spec was reviewed and approved in the spec card. Object discovery and SAM tracking of the clip came from the local response cache of an earlier run on the same clip; the spec, the program, the simulation and the SAM 3.1 judge ran fresh.

**VERIFIED** — 20/20 test scenes pass

- Rules: “Sort by size, and don't touch the glass.”
- Demo video: yes — Wonder saw the video
- Sim objects: red_cube_large, blue_cube_large, red_cube_small, green_cube_small, clear_glass_large
- Seeds: 3000–3019

## Spec

Rules:
- non-fragile large objects: put in `left_bin`
- non-fragile small objects: put in `right_bin`
- glasses: leave in place

Checks:
- `large_in_left_bin` (in_bin): All large non-fragile objects are in the left bin.
- `small_in_right_bin` (in_bin): All small non-fragile objects are in the right bin.
- `glass_untouched` (untouched): The glass has not been touched.
- `glass_upright` (upright): The glass remains upright on the table.

Rationale: The video shows the large red and blue cubes placed in the left bin and the small red and green cubes placed in the right bin, while the clear glass was left alone. The sentence confirms sorting by size and not touching the glass, so large non-fragile objects go left, small non-fragile objects go right, and the glass is left untouched.

## Attempts

| Attempt | Passed | Diagnosis | Judge agreement |
|---|---|---|---|
| Attempt 0 | 20/20 | — | 100% (80/80) |

## Final program

```python
def run(scene):
    glasses = scene.objects(shape="glass")
    avoid(glasses)
    for obj in scene.objects(size="large", fragile=False):
        pick(obj, avoid=glasses)
        place("left_bin", avoid=glasses)
    for obj in scene.objects(size="small", fragile=False):
        pick(obj, avoid=glasses)
        place("right_bin", avoid=glasses)
```

## Judges

SAM 3.1 vs simulator ground truth: 100% (80/80) agreement
- in_bin: 100% (40/40)
- untouched: 100% (20/20)
- upright: 100% (20/20)

Ground truth decides pass/fail; SAM is the camera-only second opinion.

## Cost and time

- API cost at list price: $1.92 (Wonder $0.32, SAM $1.59, 109 calls; repeated requests are served from the local cache for $0)
- Wall time: 3.3 min
