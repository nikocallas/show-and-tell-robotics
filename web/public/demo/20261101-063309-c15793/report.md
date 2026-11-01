# Show & Tell job `20261101-063309-c15793`

> Sentence only: the human demo video for this task is not recorded yet, so the spec comes from the sentence alone. Run from the command line, so the spec was approved without review. Muse Wonder's spec, program and repair came from the local response cache: identical requests were first answered in benchmark run bench-task06_color_exc-sentence-20261031-154958, and this run reproduced its outcome on every scene. The simulation and the SAM 3.1 judge ran fresh. Hero videos were re-rendered at 1280x720 afterwards, with the same outcome on every scene.

**VERIFIED** — 20/20 test scenes pass

- Rules: “Sort by color, but blue things stay where they are.”
- Demo video: no (sentence only)
- Sim objects: red_cube_large, green_cube_small, blue_cube_large, blue_cube_small
- Seeds: 6000–6019

## Spec

Rules:
- red objects: put in `left_bin`
- green objects: put in `middle_bin`
- blue objects: leave in place

Checks:
- `red_in_left_bin` (in_bin): All red objects are in the left bin.
- `green_in_middle_bin` (in_bin): All green objects are in the middle bin.
- `blue_unmoved` (unmoved): All blue objects remain where they started on the table.

Rationale: The sentence says to sort by color, so red and green objects each get their own bin, assigned to the left and middle bins respectively because the sentence does not name specific bins. Blue objects are the stated exception, so they are left in place with an unmoved check.

## Attempts

| Attempt | Passed | Diagnosis | Judge agreement |
|---|---|---|---|
| Attempt 0 | 19/20 | — | 100% (60/60) |
| Repair 1 | 20/20 | Avoiding the blue cubes forced an unreachable over-the-blue waypoint while carrying the red cube, aborting the program before placement. | 100% (60/60) |

## Final program

```python
def run(scene):
    for obj in scene.objects(color="red"):
        pick(obj)
        place("left_bin")
    for obj in scene.objects(color="green"):
        pick(obj)
        place("middle_bin")
```

## Judges

SAM 3.1 vs simulator ground truth: 100% (120/120) agreement
- in_bin: 100% (80/80)
- unmoved: 100% (40/40)

Ground truth decides pass/fail; SAM is the camera-only second opinion.

## Cost and time

- API cost at list price: $1.74 (Wonder $0.14, SAM $1.60, 163 calls; repeated requests are served from the local cache for $0)
- Wall time: 3.0 min
