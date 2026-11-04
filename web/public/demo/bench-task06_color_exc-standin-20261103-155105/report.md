# Show & Tell job `bench-task06_color_exc-standin-20261103-155105`

> Stand-in video: a top-down simulator render of the robot doing this task, not a human demonstration (the human video is not recorded yet). This is the benchmark's stand-in run, started from the command line, so the spec was approved without review. Muse Wonder's answers and SAM's tracking were fresh, not cached.

**VERIFIED** — 20/20 test scenes pass

- Rules: “Sort by color, but blue things stay where they are.”
- Demo video: yes — Wonder saw the video
- Sim objects: red_cube_large, green_cube_small, blue_cube_large, blue_cube_small
- Seeds: 6000–6019

## Spec

Rules:
- red objects: put in `left_bin`
- green objects: put in `right_bin`
- blue objects: leave in place

Checks:
- `red_in_left` (in_bin): All red cubes are in the left bin.
- `green_in_right` (in_bin): All green cubes are in the right bin.
- `blue_unmoved` (unmoved): All blue cubes remain in their original positions on the table.

Rationale: The video shows the large red cube placed in the left bin and the small green cube placed in the right bin, with both blue cubes not moved. The sentence confirms sorting by color but says blue things stay where they are, so blue objects are left in place.

## Attempts

| Attempt | Passed | Diagnosis | Judge agreement |
|---|---|---|---|
| Attempt 0 | 20/20 | — | 100% (60/60) |

## Final program

```python
def run(scene):
    for obj in scene.objects(color="red"):
        pick(obj)
        place("left_bin")
    for obj in scene.objects(color="green"):
        pick(obj)
        place("right_bin")
```

## Judges

SAM 3.1 vs simulator ground truth: 100% (60/60) agreement
- in_bin: 100% (40/40)
- unmoved: 100% (20/20)

Ground truth decides pass/fail; SAM is the camera-only second opinion.

## Cost and time

- API cost: $1.12 billed; $1.12 at list price (Wonder $0.13, SAM $0.99, 88 calls). List price counts every call; calls answered from the local response cache are billed $0.
- Wall time: 2.8 min
