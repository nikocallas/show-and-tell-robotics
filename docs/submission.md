# Show & Tell Robotics

Hackathon write-up · Meta Global AI Developer Hackathon, Startup track · status as of 2026-11-01.
Every number below comes from a file in this working copy or from `python -m showtell cost`
(sources at the end). Files under `data/` (the job reports in `data/jobs/`, `data/usage.jsonl`) are
git-ignored local run output, so a clone will not have them; the two recorded runs are also copied
into `web/public/demo/`. Anything that waits on the human demo videos is marked **[pending]**.

## Name and pitch

**Show & Tell Robotics.** Show a robot a task once on video, state the rule in one sentence, and get
back a short robot program that has passed 20 randomized simulation tests before you trust it.

## The problem

Telling a robot what to do still means writing a program, and the person who knows the task often
can't read it. A program that works on the layout in front of you can fail on the next one (our first
task-6 program passed 19 of 20 layouts), and because nobody wrote down what "done" means, nobody can
check. Neither input is enough on its own: a
video shows what happened once, not the rule behind it, and a sentence leaves gaps ("sort by color"
doesn't say which bowl). Our own benchmark shows the second gap: from the sentence alone, two of six
tasks ended up sorted into different bowls than the reference mapping the planned demonstration will
show (see Results).

## What it does

1. **Show.** Upload a short top-down phone video of a person doing the task (optional) and type one
   sentence, for example "Sort by color, but blue things stay where they are."
2. **Watch.** SAM 3.1 tracks the hand, the objects and the bowls. The app lists what moved, in what
   order, and which bowl each object ended in. (Not yet shown on a human video: on the stand-in
   render SAM found no bowls and there was no hand to track.)
3. **Approve the tests.** Muse Wonder turns the sentence, and the demo video when there is one, into
   a task spec: rules and pass/fail checks (for example, from the recorded sentence-only task-6 run,
   "Put red objects in the left bin." and "All blue objects remain where they started on the
   table."). You read it, edit it if needed, and approve it.
4. **Test.** Muse Wonder writes a short Python program out of tested robot skills. It runs on 20
   randomized MuJoCo scenes with a simulated Franka Panda arm, and the 20-scene grid fills in live.
5. **Repair.** If any scene fails, Muse Wonder gets the failed checks, the event log and frames from a
   failing scene, explains the failure in one sentence and patches the program. All 20 scenes run
   again, up to 3 repairs.
6. **Verified.** At 20/20 you get the program, a test report (Markdown and JSON) and the full attempt
   log: every attempt's program and the verdicts for every scene, from both judges when the SAM judge
   ran. The web app shows each patched program as a diff against the previous attempt.

The same pipeline runs from the CLI (`python -m showtell run --video … --rules "…"`) and from a
FastAPI + React web app with live progress over Server-Sent Events. The web app's demo mode replays
recorded runs from static files, with no backend.

## How it works

### Pipeline

Demo video → normalized to 720p, 10 fps H.264, plus 8 keyframes → **Muse Wonder** lists the kinds of
objects on the table (from 4 keyframes) → **SAM 3.1** tracks each kind, the bowls and the hand → a
track summary (start and end position, moved or not, move order, end bowl) → **Muse Wonder** writes
the spec from the track summary, the video itself, the sentence and the allowed attribute values →
you approve or edit it → **Muse Wonder** writes the program → AST sandbox → 20 seeded scenes in
parallel → ground-truth checks and the **SAM 3.1** judge → repair if anything failed → report. With
`--no-video` (the benchmark's ablation) the spec comes from the sentence and the list of scene
objects alone.

### A closed check vocabulary

Checks are the contract between the person and the program, so they come from a fixed set that the
simulator evaluates exactly:

- `in_bin(selector, bin)`: every matching object ends inside the bin and below its rim.
- `untouched(selector)`: nothing but the table touches those objects during the whole run (from the
  physics contact list, every step).
- `upright(selector)`: the object's axis ends within 20° of vertical.
- `unmoved(selector)`: the object ends within 2 cm of where it started.

A selector is a set of attributes (`size`, `color`, `shape`, `fragile`), never coordinates. Every
rule gets a matching check.

### Skills

Programs compose `scene.objects(**attrs)` with four skills: `pick(obj, avoid=None)`,
`place(bin_name, avoid=None)`, `avoid(obj, margin_m=0.08)` and `move_to(pose)`. Every lateral move is
lift → straight segment → descend, at a transit height of 0.12 m above the table, which is lower than
the 0.20 m glass on purpose. `avoid` raises the path to the avoided object's height + 0.10 m and adds
a detour waypoint around it. Inverse kinematics is damped least squares (λ = 0.05); a pose the arm
cannot reach within 2 cm stops the program with an error. The skill documentation the model reads is
generated from the skills' docstrings, so the two cannot drift.

### AST sandbox

A generated program must be exactly one `def run(scene):` in a small subset of Python: `for`, `if`,
assignment, calls to the skills and to `len`, `sorted`, `range`, `min` and `max`, and reads of object
fields. Imports, `while`, `lambda`, `try`, `with`, `global`, dunder names and any other call are
rejected before anything runs, with one reason per problem that goes back to the model. Call
signatures are read from the skill code. `tests/test_sandbox.py` holds 148 tests.

### One process per scene

Each scene runs in its own spawned subprocess (CPU count − 1 at a time) with no builtins except the
whitelist, a 120 s wall-clock timeout, a 2 GB memory watchdog and a cap of 200 skill calls; a crash or
timeout still comes back as a structured result. Seeds are fixed per task (task index × 1000 + i, so
task 6 uses 6000–6019): every attempt and both benchmark conditions run the same 20 layouts.

### Two judges

1. **Ground truth** from the simulator's state and contact log. It decides pass/fail.
2. **SAM 3.1, from the rendered clips.** For every scene the runner renders two 50-frame clips (the
   first 25 and the last 25 frames, at 25 fps), one from an overhead camera and one from a 45°
   camera. SAM tracks each object by a noun phrase ("red cube"; the glass is "light blue cylinder",
   the phrase SAM finds reliably). End positions are mapped to bins in the overhead view; `unmoved`
   and `untouched` become "moved less than 2 cm" (a camera cannot see a touch); `upright` compares
   box shapes, mainly the height-to-width ratio in the 45° view. Besides SAM's boxes the judge uses
   only fixed scene knowledge (camera calibration, bin positions, object sizes), never the
   simulator's state. Its verdicts are stored next to the simulator's, with the agreement rate. The
   judge is on by default in `run` and the web app; the benchmark runs it only in the video
   condition.

### Repair loop, at most 3 repairs

After a failed attempt, Muse Wonder receives the spec, the program, the failed checks grouped by
pattern with the contact log (what touched what, during which skill), and 8 time-stamped frames
around the first failure in one failing scene. It returns a one-sentence diagnosis and a corrected
program, which must pass the sandbox and differ from the failing one. All 20 scenes rerun. The loop
stops at 20/20, after 3 repairs, or when a patch fails the sandbox twice (the job then ends as
failed).

### Cost control

Every Meta Model API response is cached on disk by a hash of the request, so a rerun with the same
inputs costs $0, and every call, cached or not, is logged to `data/usage.jsonl`.
`python -m showtell cost` totals it.

## Meta models used

| Model | ID | What it does here |
|---|---|---|
| Muse Wonder | `muse-wonder-1.0` | **Object discovery:** from 4 keyframes, lists the kinds of objects on the table, with attributes from a fixed vocabulary and a noun phrase for SAM (reasoning effort medium). **Spec:** from the track summary, the demo video itself (uploaded with the Files API and sent as `input_video`), the sentence and the allowed attribute values, writes the rules and checks as structured JSON through a JSON-schema output format (effort high). **Program:** writes `def run(scene)` from the approved spec and the generated skill documentation (effort high). **Repair diagnosis:** from the failure report and 8 frames, writes the one-sentence diagnosis and the patched program (effort high). |
| SAM 3.1 | `sam-3.1` | **Tracking in the demo video:** one request per noun phrase (each object kind, the bowls, the hand), up to 4 at a time, on the normalized video; the per-frame boxes become the track summary. **Camera-only judge:** on each test scene's two 50-frame clips, one request per noun phrase: every object a check names in the overhead clip, and in the 45° clip the objects under untouched or unmoved checks and the tall ones under upright checks; it gives a verdict per check, which is compared with the simulator's. |

Both models are called through the OpenAI-compatible Responses API at `https://api.meta.ai/v1`.

## Results so far

The benchmark's video condition is **[pending: video benchmark]**: `bench/videos/` has no task
videos yet, so `bench/results.json` records `missing_video` for all six tasks. Everything below that
is not marked [pending] is finished and real.

### Benchmark, sentence only

The same pipeline without the demo video, on 20 fixed seeds per task. "Own spec" is the run's own
checks. "Against the reference" scores the final program against hand-written reference checks that
encode what the human demo is to show (`bench/tasks.yaml`; shot list in `bench/videos/README.md`);
the pipeline never sees them.

| Task | Sentence | Own spec, first try → final | Repairs | Against the reference (final) | Cost (list price) |
|---|---|---|---|---|---|
| task01_size | "Sort by size: big ones in the left bowl, small ones in the right bowl." | 20/20 | 0 | 20/20 | $0.05 |
| task02_color | "Sort by color, one bowl per color." | 20/20 | 0 | 20/20 | $0.05 |
| task03_size_glass | "Sort by size, and don't touch the glass." | 20/20 | 0 | 0/20 | $0.08 |
| task04_clear | "Clear everything into the middle bowl, but leave the glass alone." | 20/20 | 0 | 20/20 | $0.06 |
| task05_shape | "Cylinders in the left bowl, cubes in the right bowl." | 20/20 | 0 | 20/20 | $0.04 |
| task06_color_exc | "Sort by color, but blue things stay where they are." | 19/20 → 20/20 | 1 | 0/20 | $0.14 |

Every run verified its own spec; against the reference, four of six match. The two misses are bowl
choices the sentence leaves open: on task 3 Wonder put the small blocks left and the large ones right
(the reference does the reverse), and on task 6 it put green in the middle bowl (the reference uses
the right one). The task-2 sentence is just as open, and there Wonder's choice happened to match. Closing this gap is
what the demo video is for; whether it does is **[pending: video benchmark]**, along with the video
columns of the official table (first try, final, repairs, judge agreement, cost and time per task).

### The task-6 repair: a real failure, diagnosed and fixed

Recorded run `20261101-063309-c15793`, sentence only. It reproduces benchmark job
`bench-task06_color_exc-sentence-20261031-154958` exactly: the same Muse Wonder responses came from
the local cache, and the simulator gave every scene the same outcome.

- **Spec:** red → left bin, green → middle bin, blue objects unmoved.
- **Attempt 0, 19/20.** The first program registered both blue cubes as obstacles (`avoid(blues)`)
  and passed `avoid=blues` to every pick and place. In scene 6004 the arm picked up the red cube; to
  clear the small blue cube, `avoid` raised the path to z = 0.55 m (10 cm above the cube's top) and
  routed it around the cube on the robot's side, and the planned leg to that detour passed through a
  pose the arm cannot reach: (0.271, −0.050, 0.550), IK error 2.2 cm against the 2 cm limit. The
  planner checks a whole leg before moving, so the arm never went there. The program stopped, the
  gripper let go of the red cube, which dropped back onto the table, and neither red nor green
  reached a bowl. Both `in_bin` checks failed; `blue_unmoved` passed.
- **Diagnosis,** verbatim from Muse Wonder: "Avoiding the blue cubes forced an unreachable
  over-the-blue waypoint while carrying the red cube, aborting the program before placement."
- **Repair 1, 20/20, verified.** The patch removed `avoid` altogether (4 lines added, 6 removed). The
  blue cubes are low enough that the normal transit height clears them: in the rerun, nothing touched
  a blue cube in any of the 20 scenes.
- The repair call cost $0.07 at list price (4,366 input and 2,745 output tokens).

### Task 3: the glass, protected on the first try

Task 3 was built so that a careless program fails: the 0.20 m glass stands between the objects and
the bins, above the 0.12 m transit height. A hand-written size sort without `avoid` knocked it over
on 19/20 seeds; with `avoid` it passed 20/20. Muse Wonder never made that mistake. All four task-3
programs it has generated avoided the glass on the first try and passed 20/20 on their own checks:
sentence only (`20261031-141341-6cd01a`; the benchmark job got the same response from the cache),
from the stand-in video twice (`20261031-141952-83de37`, `20261031-154240-8ca2da`), and from a
sentence that never mentions the glass (`rulechange-a-155206`), where the spec added `untouched` and
`upright` checks for fragile objects on its own. So the repair loop's first real test came from
task 6, not from the failure we designed; we did not change the scene to force one.

The stand-in video is a top-down simulator render of the robot doing task 3, not a human demo. From
it, the spec put large blocks left and small blocks right, the reference mapping; from the sentence
alone the mapping was reversed (0/20 against the reference). The same comparison with a human video is
**[pending: video benchmark]**.

### Judge agreement

| Run | Checks judged | SAM 3.1 agrees with ground truth |
|---|---|---|
| Task 6, sentence only (`20261101-063309-c15793`), 2 attempts × 20 scenes | in_bin 80, unmoved 40 | 120/120 |
| Task 3, stand-in video (`20261031-154240-8ca2da`), 1 attempt × 20 scenes | in_bin 40, untouched 20, upright 20 | 80/80 |

Only 2 of these 200 verdicts were failures (the two `in_bin` checks of scene 6004), and SAM matched
both. Failing glass checks were tested separately on six task-3 scenes, three of them with the glass
knocked over: SAM agreed on 23/23 decided checks, and one was undecided because the fallen glass was
out of view. That is still too few failures to say much about how SAM judges them.

### Cost

List price per run, from each run's report:

- Task-6 demo run: $1.74 (Wonder $0.14, SAM $1.60, 163 calls). Almost all of it is the SAM judge: 160
  requests of 50 frames each.
- Task-3 stand-in run: $1.92 (Wonder $0.32, SAM $1.59, 109 calls), including $0.59 for SAM tracking of
  the 495-frame stand-in video.
- Sentence-only benchmark, all six tasks: $0.42 (Wonder only; that condition does not run the judge).

Total API spend so far (`python -m showtell cost`, 2026-11-01): **$4.75 paid** across 430 logged calls,
81 of them served from the local cache; $7.08 at list price without the cache. Of the paid total,
SAM 3.1 is $3.72 (319 paid requests, 18,620 billed frames; 390 requests and 27,510 frames logged,
including 71 cache hits) and Muse Wonder $1.03 (30 paid calls of 40 logged). The task-6 demo run paid
$1.49, all of it SAM: its Wonder calls repeated the benchmark job's requests and came from the cache.

### Speed

On our development machine, each 20-scene attempt of the task-6 run was simulated and rendered in
19.2 s and 18.9 s, and the SAM judge took another 68 s and 71 s.

## What keeps it honest

- **No injected failures.** Pass/fail is physics plus the program; nothing is scripted. The task-6
  failure was an arm-reach limit nobody planned. When task 3's designed failure never happened, we
  reported that rather than reshaping the scene until it did.
- **Ground truth decides.** SAM 3.1 is a second opinion shown next to the simulator with its agreement
  rate; it never changes a result.
- **Scored against the person, not the model.** The benchmark scores final programs against reference
  checks the pipeline never sees, so a program that verifies its own wrong spec still scores 0/20
  (tasks 3 and 6, sentence only).
- **Numbers are generated.** `bench/run_bench.py` writes `bench/results.json` and generates
  `bench/results.md` and the README table from it; the web app's Benchmark page reads the same file.
- **Same scenes for every attempt and condition.** Seeds are fixed per task.
- **Reproducible recordings.** The demo run reproduced its benchmark job from the request cache, and
  the 1280×720 re-render re-runs every scene and refuses to swap a video if any outcome changes.
  Replays in the web app say "Replaying a recorded run".
- **No tuning after the fact.** The reachability gap in `avoid` that task 6 exposed is left as it is,
  so benchmark outcomes aren't tuned to the tasks.

## Limitations

- Simulation only (MuJoCo, with the Franka Panda model from MuJoCo Menagerie); no real robot yet.
- Grasping is a kinematic attach: a held object follows the hand instead of being held by finger
  contact. Collisions of the carried object and the arm are real.
- Top-down grasps of rigid cubes, cylinders and one glass; three bins; one arm.
- Checks come from a closed vocabulary (`in_bin`, `untouched`, `upright`, `unmoved`), so a rule about
  anything else, such as order or stacking, can't be stated yet.
- `avoid` picks its detour without checking that the arm can reach it. Task 6 exposed this: scene 6004
  stopped at an unreachable pose. Muse Wonder's patch worked around it by not avoiding the low blue
  cubes; the planner itself is unchanged.
- No human demo video is filmed yet, so the full pipeline has only run on a stand-in render, and the
  benchmark's video condition is **[pending]**.
- On rendered scenes SAM needs help: "glass" finds nothing (we ask for "light blue cylinder"), a
  standing glass is not detected from overhead (glass checks use the 45° camera), and a color phrase
  can also match a bin of that color (the judge drops boxes far larger than the object). In the
  stand-in render SAM found no bowls, so the track summary fell back to splitting the frame into
  thirds.
- The judge agreement of the two demo runs rests on only two failing checks.

## What's next

1. Film the six demo videos and run the video condition once **[pending]**.
2. Make `avoid` check reachability (try the other side, a lower clearance, or no detour) and measure
   the change on the benchmark.
3. Harder scenes (more objects, clutter) to exercise the repair loop and the SAM judge on more failures.
4. A real-arm pilot: run a verified program on a physical Panda through the same skill API.
5. The hosted demo on Vercel, replaying the recorded runs **[pending: deploy]**.

## Links

- Repository: [pending: URL and visibility, per the hackathon rules]
- Demo video: [pending: recording, see `docs/demo_script.md`]
- Hosted demo: [pending: Vercel deploy]

<!-- sources:
Benchmark, bench/results.json (updated_at "2026-10-31 15:51:13", n_seeds 20):
- video condition missing for all six tasks: tasks.*.video.status = "missing_video"; bench/videos/ holds only README.md
- sentences: bench/tasks.yaml rules (also data/jobs/<tasks.*.sentence.job_id>/report.json rules)
- own spec first try -> final: tasks.*.sentence.attempts[*].passed/total (task06: 19/20 then 20/20; all others 20/20)
- repairs: tasks.*.sentence.repairs (task06: 1, others 0)
- against the reference: tasks.*.sentence.ref_final (20/20, 20/20, 0/20, 20/20, 20/20, 0/20); reference checks in bench/tasks.yaml (header: they encode what the human demo is to show); shot list in bench/videos/README.md
- cost per task: tasks.*.sentence.cost_usd (0.0457, 0.0532, 0.0777, 0.0627, 0.043, 0.1394); total 0.4217 -> $0.42 (also the web app Benchmark page Total and PROGRESS.md "Total $0.42")
- task 3 small -> left_bin / large -> right_bin and task 6 green -> middle_bin: tasks.task03_size_glass.sentence.spec.rules, tasks.task06_color_exc.sentence.spec.rules; demo mappings in bench/tasks.yaml reference
- task 2 choice "reasonable one-color-per-bin mapping since no specific bin was named": tasks.task02_color.sentence.spec.rationale
Task-6 run, data/jobs/20261101-063309-c15793/report.json:
- spec: spec.rules / spec.checks; sentence only: use_video=false, spec_source "sentence"; seeds 6000-6019: seeds
- example texts in "What it does": "All blue objects remain where they started on the table." = spec.checks[2].text; "Put red objects in the left bin." = spec.rules[0] as web/src/lib/format.ts ruleSentence renders it
- 19/20 then 20/20: attempts[0].passed/total, attempts[1].passed/total; verified: status
- first program with avoid(blues) and avoid=blues: attempts[0].program; patched program: attempts[1].program; "+4 / -6 lines": the web app diff ("Changes +4 −6") on the demo build, localhost:4173/replay/20261101-063309-c15793
- scene 6004, pose (0.271, -0.050, 0.550), IK error 2.2 cm: attempts[0].seeds[4].error; 2 cm limit: showtell/sim/skills.py IK_FAIL_M = 0.02
- detour around the small blue cube: attempts/0/seed_6004/result.json trace (t=5.774 avoid blue_cube_small clearance_m 0.006, transit_z 0.55; t=6.144 error); showtell/sim/skills.py Robot.transit raises z to the cube top (0.40 + 0.05) + AVOID_CLEARANCE_Z 0.10 and adds a detour waypoint at the cube + n * reach (reach = 0.08 + 0.025*sqrt(2) + HAND_REACH 0.105 = 0.220 m); recomputed from the logged layout (red cube 0.3006, -0.1861; small blue cube 0.411, -0.0677; left_bin slot 0.715, 0.295): waypoint about (0.244, 0.076), on the robot's side; the error pose is on the leg to it (x = 0.271 at y = -0.050, 0.52 of the way), 14 cm from the cube; move_line solves IK for the whole leg before moving
- cube released and dropped back, arm parked: attempts/0/seed_6004/result.json trace (release "program ended while holding", park) and final_positions
- both in_bin failed, blue_unmoved passed: attempts[0].seeds[4].checks
- diagnosis: attempts[1].diagnosis
- nothing touched a blue cube in the rerun: attempts[1].seeds[*].touch_events
- agreement 120/120 (in_bin 80/80, unmoved 40/40): agreement; SAM matched scene 6004: attempts[0].seeds[4].sam
- cost $1.74, Wonder $0.14, SAM $1.60, 163 calls: cost (usd 1.7394, wonder_usd 0.1394, sam_usd 1.6, calls 163)
- identical to the benchmark job: data/jobs/bench-task06_color_exc-sentence-20261031-154958/report.json (same spec, programs, pass counts, failing seed 6004 with the same error, same diagnosis)
- speed 19.2 s / 18.9 s simulate, 68 s / 71 s judge: data/jobs/20261101-063309-c15793/events.jsonl stage_done verify seconds 19.2 and 18.88, judge seconds 68.31 and 71.47
Task-3 runs:
- stand-in run data/jobs/20261031-154240-8ca2da/report.json: use_video=true, video input/standin_task03_overhead.mp4; spec large -> left_bin, small -> right_bin; attempts[0] 20/20; agreement 80/80 (in_bin 40/40, untouched 20/20, upright 20/20); cost usd 1.9186, wonder_usd 0.3246, sam_usd 1.594, calls 109; track_summary.frames 495
- four first programs with avoid on the glass, each 20/20: attempts[0] of data/jobs/20261031-141341-6cd01a, 20261031-141952-83de37, 20261031-154240-8ca2da, rulechange-a-155206 (report.json); rulechange-a spec adds fragile_untouched / fragile_upright; benchmark job's program is a cache hit (python -m showtell cost, tag bench-task03_size_glass-sentence-20261031-154759:program)
- stand-in video description: PROGRESS.md ("Video path end to end on a stand-in clip ... the robot doing task 3, overhead camera, 495 frames at 10 fps")
- naive size sort 19/20 knocked over, with avoid 20/20: PROGRESS.md Phase 2 and README.md
- probe judge 23/23 decided, 1 undecided, six scenes (three knocked over): PROGRESS.md "SAM judge end to end"
- SAM limitations on rendered scenes ("glass" finds nothing, standing glass invisible overhead, color phrase matching a bin, no bowls found in the render): PROGRESS.md "Observed API behaviour"; frame-thirds fallback: data/jobs/20261031-154240-8ca2da/report.json track_summary.notes[0]
- watching not yet shown on a human video (no bowls found, no hand in a robot render): track_summary.notes[0] ("found 0 bowl(s), need 3") of data/jobs/20261031-154240-8ca2da/report.json; the smoke test tracked a synthetic clip, not a hand: PROGRESS.md Phase 0
- stand-in clip, 495 frames at 10 fps: also ffprobe data/probe/standin_task03_overhead.mp4 (960x720, 10/1 fps, 495 frames; same sha256 as data/jobs/20261031-154240-8ca2da/input/standin_task03_overhead.mp4)
python -m showtell cost (run 2026-11-01 06:58, data/usage.jsonl, 430 lines; paid/cached split: the same file's records grouped by kind and cached_locally):
- TOTAL: 430 calls, 81 cache hits, paid $4.7539, no-cache $7.0822
- kind sam: 390 calls, 71 cache hits, paid $3.7240 = 319 paid calls / 18,620 billed frames (27,510 frames logged; the 71 cache hits carry 8,890); kind wonder: 40 calls, 10 cache hits, paid $1.0299 (30 paid calls)
- task-6 demo run paid $1.49: tag 20261101-063309-c15793:judge: 160 calls, 11 cache hits, paid 1.4900 for 7,450 billed frames (8,000 logged); :spec, :program, :repair1 paid 0.0000 (cache hits)
- judge requests per clip: data/usage.jsonl concept counts per tag (20261101-063309-c15793:judge: red cube 40, green cube 40, blue cube 80 over 40 scenes = 3 overhead + 1 at 45 degrees; 20261031-154240-8ca2da:judge: light blue cylinder 40, red/green/blue cube 20 each over 20 scenes = 4 overhead + 1 at 45 degrees)
- repair call $0.07, 4,366 in / 2,745 out tokens: tag bench-task06_color_exc-sentence-20261031-154958:repair1 (paid 0.0724)
- stand-in SAM tracking $0.59: tag 20261031-154240-8ca2da:track (no-cache 0.5940, 2,970 frames = 6 x 495)
Code facts:
- 720p, 10 fps: showtell/perception/video_prep.py NORMALIZE_VF; 8 keyframes: CLAUDE.md 5.4 and video_prep.py; discovery on 4 keyframes and effort medium: showtell/perception/track_summary.py
- spec/program/repair effort high: showtell/agent/spec.py SPEC_EFFORT, showtell/agent/program.py, showtell/agent/repair.py; input_video + file_id and json_schema format: showtell/meta/wonder.py
- SAM up to 4 concurrent requests: showtell/meta/sam.py max_concurrency=4; judge concept "light blue cylinder", 50-frame clips (25 + 25), 2 cm, 45-degree aspect test: showtell/verify/judge_sam.py
- judge concepts per clip (overhead: every object a check names; 45 degrees: untouched/unmoved, and upright for tall objects only): showtell/verify/judge_sam.py concepts_needed (ANGLE_KINDS, _tall); judge inputs are the clips, the checks, the object specs and scene_meta (camera calibration, bins), never the run's simulator state: judge_sam.judge, showtell/pipeline.py _judge_seed
- judge on by default in run and the web app, only in the benchmark's video condition: showtell/__main__.py run (judge option, default on; --no-judge), showtell/api/app.py POST /jobs use_judge Form(True), bench/run_bench.py run_condition (use_judge=use_judge and condition == "video")
- loop exits: 20/20 or n >= max_repairs (showtell/pipeline.py run_job); a patch rejected twice by the sandbox raises RepairError (showtell/agent/repair.py repair) and run_job ends the job as "failed" (also PROGRESS.md Phase 5)
- checks: showtell/sim/checks.py (UPRIGHT_COS = cos 20 deg, unmoved tol 0.02 m)
- skills: showtell/sim/skills.py (TRANSIT_Z = table + 0.12, AVOID_CLEARANCE_Z = 0.10, IK_LAMBDA = 0.05, IK_FAIL_M = 0.02, MAX_SKILL_CALLS = 200, avoid margin_m=0.08); glass 0.20 m: bench/tasks.yaml header and README.md
- sandbox: showtell/agent/sandbox.py; 148 tests: pytest --collect-only -q tests/test_sandbox.py
- per-seed process, 120 s, 2048 MB, CPU count - 1: showtell/verify/runner.py (SEED_TIMEOUT_S, DEFAULT_SEED_MEMORY_MB, default_workers, spawn); seeds task_index*1000 + i: runner.task_seeds
- at most 3 repairs: showtell/contracts.py max_repairs = 3; 8 frames: showtell/pipeline.py failure_frames(n=8)
- render-demo at 1280x720, refuses on a changed outcome: showtell/verify/demo_render.py (HD = (1280, 720))
- "2 of these 200 verdicts were failures": count of attempts[*].seeds[*].checks[*] with passed=false over data/jobs/20261101-063309-c15793/report.json and data/jobs/20261031-154240-8ca2da/report.json (2 of 200, both in seed 6004 of attempt 0)
- generated tables: bench/run_bench.py; Benchmark page reads results.json: web/src/pages/Bench.tsx, web/src/lib/bench.ts
- live progress over SSE, report.md/report.json/program.py downloads: showtell/api/app.py (GET /api/jobs/{id}/events, /report.md, /report.json, /program.py)
- diff of each patched program: web/src/components/ProgramPanel.tsx, web/src/lib/diff.ts
- disk cache by request hash, cache hits logged: showtell/meta/cache.py, showtell/meta/wonder.py call_json, showtell/meta/usage.py
- glass between the objects and the bins: showtell/sim/randomizer.py (module docstring, GLASS_X), PROGRESS.md Phase 1
- data/ and cache/ git-ignored: .gitignore; recorded runs in the bundle: web/public/demo/<id>/ (report.json, events.json)
- Vercel deploy not done: PROGRESS.md Phase 8
-->
