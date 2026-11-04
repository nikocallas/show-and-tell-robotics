# Show & Tell Robotics

Hackathon write-up · Meta Global AI Developer Hackathon, Indie Developer track, theme "Push the Frontier" ·
status as of 2026-11-04. Every number below comes from a file in this working copy or from
`python -m showtell cost` (sources at the end). Files under `data/` (the job reports in `data/jobs/`,
`data/usage.jsonl`) are git-ignored local run output, so a clone will not have them; the three
recorded runs are also copied into `web/public/demo/`. No human demonstration has been filmed:
everything marked "stand-in" ran on a simulator render, not a recording of a person.

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
tasks ended up sorted into different bowls than our reference mapping, the one the stand-in
demonstration clips show (see Results).

## Who it's for

Robots are already everywhere: about 542,000 industrial robots were installed in 2024 and about
4,664,000 are in operation ([IFR, World Robotics 2025](https://ifr.org/ifr-press-releases/news/global-robot-demand-in-factories-doubles-over-10-years)).
Getting one to do a new job is the expensive part: integration generally runs four to five times the
cost of the robot, per MIT's Julie Shah as reported by [MIT Sloan in 2021](https://mitsloan.mit.edu/node/29144),
and every change of part, bin or rule goes back to whoever can write the code.

We build for the people who already know the job: a line lead re-sorting parts, a lab technician, a
small shop without an integrator, an integrator handing a cell over to the customer's staff. They
show the change once and say the rule. They read the checks as plain sentences ("All blue objects
remain where they started on the table.") and approve them before any code exists, and no program
counts as verified until it has passed them on the randomized test scenes (20 by default). The report and the attempt log
are what gets handed over. This is a plan for a product, not a shipped one: today it runs in
simulation only (see What's next).

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
5. **Repair.** If any scene fails, Muse Wonder gets the failed checks, any program error and frames from a
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

### Beyond a basic integration

- **One model, four jobs that check each other.** Muse Wonder names the objects, writes the checks,
  writes the program and diagnoses the failures. The checks it writes before any code exists are what
  its own program is then tested against, on scenes it has not seen. (A repaired program reruns the
  same 20 scenes, including the failing one it has seen frames of.)
- **SAM 3.1 as a test oracle.** Besides tracking the demonstration, SAM judges every test scene from
  rendered camera clips, using nothing from the simulator's state. We run it next to physics ground
  truth and publish how often the two agree.
- **Things we did not expect the models to do:**
  - Wonder overrode bad tracking. SAM found no bowls in the rendered clips, so the track summary fell
    back to thirds of the frame and placed the green cube of task 6 in the middle. Wonder also sees the
    video itself, and wrote "the small green cube placed in the right bin", which is what the clip
    shows. On task 2, whose sentence names no bowl, the summary put the small red cube and the green cube
    in the middle; the spec still sent red left and green right.
  - Wonder traced a reach error back to its own use of `avoid`. The report said only "cannot reach pose
    (0.271, -0.050, 0.550)"; from that, the program and 8 frames, it blamed avoiding the blue cubes and
    removed `avoid` (see The task-6 repair).
  - SAM needed the right words: "glass" finds nothing and "light blue cylinder" finds the glass; from
    overhead a standing glass is not detected, and in the 45° view its box aspect went from 2.7 to 1.1
    when it fell. That made a glass check possible from a camera alone.

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
   judge is on by default in `run` and the web app; the benchmark runs it whenever there is a clip,
   and not in the sentence-only condition.

### Repair loop, at most 3 repairs

After a failed attempt, Muse Wonder receives the spec, the program, the failed checks grouped by
pattern, any program error, the contact log for failed `untouched`, `upright` or `unmoved` checks
(what touched what, during which skill), and 8 time-stamped frames around the first failure in one
failing scene. It returns a one-sentence diagnosis and a corrected
program, which must pass the sandbox and differ from the failing one. All 20 scenes rerun. The loop
stops at 20/20, after 3 repairs, or when a patch fails the sandbox twice (the job then ends as
failed).

### Cost control

Every Muse Wonder and SAM 3.1 response is cached on disk by a hash of the request, so a rerun with the
same inputs costs $0, and every such call, cached or not, is logged to `data/usage.jsonl`.
`python -m showtell cost` totals it.

## Meta models used

| Model | ID | What it does here |
|---|---|---|
| Muse Wonder | `muse-wonder-1.0` | **Object discovery:** from 4 keyframes, lists the kinds of objects on the table, with attributes from a fixed vocabulary and a noun phrase for SAM (reasoning effort medium). **Spec:** from the track summary, the demo video itself (uploaded with the Files API and sent as `input_video`), the sentence and the allowed attribute values, writes the rules and checks as structured JSON through a JSON-schema output format (effort high). **Program:** writes `def run(scene)` from the approved spec and the generated skill documentation (effort high). **Repair diagnosis:** from the failure report and 8 frames, writes the one-sentence diagnosis and the patched program (effort high). |
| SAM 3.1 | `sam-3.1` | **Tracking in the demo video:** one request per noun phrase (each object kind, the bowls, the hand), up to 4 at a time, on the normalized video; the per-frame boxes become the track summary. **Camera-only judge:** on each test scene's two 50-frame clips, one request per noun phrase: every object a check names in the overhead clip, and in the 45° clip the objects under untouched or unmoved checks and the tall ones under upright checks; it gives a verdict per check, which is compared with the simulator's. |

Both models are called through the OpenAI-compatible Responses API at `https://api.meta.ai/v1`.

## Results

### Benchmark

Six tasks on 20 fixed seeds per task, in two conditions: with a demonstration clip plus the sentence,
and from the sentence alone. Scores are against hand-written reference checks that encode what the
demonstration shows (`bench/tasks.yaml`); the pipeline never sees them.

The clips are **stand-ins**: top-down simulator renders of each task's reference program
(`bench/make_standins.py`, on a layout outside the test seeds), not recordings of a person. They show
what a demonstration adds to the sentence. They say nothing yet about how the pipeline copes with a
phone video: same renderer, same objects, no hand. The human shot list is ready in
`bench/videos/README.md`, and a filmed clip replaces its task's stand-in in the table.

| Task | Sentence | Stand-in clip + sentence: first try → final | Repairs | Sentence only: final (own spec) | SAM 3.1 agrees (stand-in run) | Cost: stand-in run / sentence only |
|---|---|---|---|---|---|---|
| task01_size | "Sort by size: big ones in the left bowl, small ones in the right bowl." | 20/20 → 20/20 | 0 | 20/20 (20/20) | 40/40 | $1.00 / $0.05 |
| task02_color | "Sort by color, one bowl per color." | 20/20 → 20/20 | 0 | 20/20 (20/20) | 60/60 | $1.03 / $0.05 |
| task03_size_glass | "Sort by size, and don't touch the glass." | 20/20 → 20/20 | 0 | **0/20** (20/20) | 80/80 | $1.92 / $0.08 |
| task04_clear | "Clear everything into the middle bowl, but leave the glass alone." | 20/20 → 20/20 | 0 | 20/20 (20/20) | 41/41, 19 undecided | $1.69 / $0.06 |
| task05_shape | "Cylinders in the left bowl, cubes in the right bowl." | 20/20 → 20/20 | 0 | 20/20 (20/20) | 21/21, 19 undecided | $1.18 / $0.04 |
| task06_color_exc | "Sort by color, but blue things stay where they are." | 20/20 → 20/20 | 0 | **0/20** (19/20 → 20/20, 1 repair) | 60/60 | $1.12 / $0.14 |

With a stand-in clip, six of six match the reference on the first try. From the sentence alone, four
of six do. The two misses are bowl choices the sentence leaves open: on task 3 Wonder put the small
blocks left and the large ones right (the reference does the reverse), and on task 6 it put green in
the middle bowl (the reference uses the right one). Both of those programs pass their own checks 20/20:
a verified program is only as right as its spec, which is why the person approves the spec and why a
demonstration matters. The task-2 sentence is just as open, and there Wonder's choice happened to match.

Every cost in the table is list price. Three of these runs billed $0, because all their responses came
from the local cache: task 3's stand-in run, which repeats an earlier stand-in run on the same clip
rather than drawing a new sample, and the sentence-only runs of tasks 1 and 3.

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
on 19/20 seeds (the opening shot of the demo video is seed 3018); with `avoid` it passed 20/20. Muse
Wonder never made that mistake. All four task-3 programs it has generated avoided the glass on the
first try and passed 20/20 on their own checks:
sentence only (`20261031-141341-6cd01a`; the benchmark job got the same response from the cache),
from the stand-in video twice (`20261031-141952-83de37`, `20261031-154240-8ca2da`), and from a
sentence that never mentions the glass (`rulechange-a-155206`), where the spec added `untouched` and
`upright` checks for fragile objects, following our prompt's rule for them. So the repair loop's first real test came from
task 6, not from the failure we designed; we did not change the scene to force one.

From the stand-in clip the spec put large blocks left and small blocks right, the reference mapping;
from the sentence alone the mapping was reversed (0/20 against the reference).

### Judge agreement

| Runs | Checks SAM decided | SAM 3.1 agrees with ground truth | Undecided |
|---|---|---|---|
| Task 6, sentence only (`20261101-063309-c15793`), 2 attempts × 20 scenes | in_bin 80, unmoved 40 | 120/120 | 0 |
| Stand-in benchmark, 6 tasks × 20 scenes | in_bin 202, untouched 40, upright 40, unmoved 20 | 302/302 | 38 |

The 38 undecided checks are all on tasks 4 and 5: from overhead, SAM found no yellow cylinder in any
frame of 38 of the 40 test-scene clips, on the open table as well as in the bin, so the check was
reported as undecided, never as agreement.
Only 2 of these 422 decided verdicts were failures (the two `in_bin` checks of scene 6004), and SAM
matched both. Failing glass checks were tested separately on six task-3 scenes, three of them with the
glass knocked over: SAM agreed on 23/23 decided checks, and one was undecided because the fallen glass
was out of view. That is still too few failures to say much about how SAM judges them.

### Cost

List price per run, from each run's report:

- A verified task with a demonstration clip and the judge: **$1.00 to $1.92** (the six stand-in runs).
  Most of it is SAM: 50-frame clips for the judge, and tracking the clip itself ($0.59 of the $1.92 on
  task 3's 495-frame clip).
- Task-6 repair run: $1.74 (Wonder $0.14, SAM $1.60, 163 calls).
- Sentence-only benchmark, all six tasks: $0.42 (Wonder only; that condition does not run the judge).

Total API spend so far (`python -m showtell cost`, 2026-11-03): **$10.77 paid** of the ~$150 credit,
across 751 paid calls: SAM 3.1 $8.82 (704 requests, 44,095 billed frames) and Muse Wonder $1.95 (47
calls). The local cache served every other logged call, so rebuilding and replaying runs costs
nothing. That log does not cover the Muse Voice Transcribe calls used to check the video's narration
against its script.

### Speed

On our development machine a whole stand-in run, from upload to report, took 2.5 to 3.6 minutes (2.0
on task 3, whose responses all came from the cache). Each
20-scene attempt of the task-6 repair run was simulated and rendered in 19.2 s and 18.9 s, and the SAM
judge took another 68 s and 71 s.

## What keeps it honest

- **No injected failures.** Pass/fail is physics plus the program; nothing is scripted. The task-6
  failure was an arm-reach limit nobody planned. When task 3's designed failure never happened, we
  reported that rather than reshaping the scene until it did.
- **Ground truth decides.** SAM 3.1 is a second opinion shown next to the simulator with its agreement
  rate; it never changes a result.
- **Scored against a hand-written reference, not the model's own spec.** The benchmark scores final programs against reference
  checks the pipeline never sees, so a program that verifies its own wrong spec still scores 0/20
  (tasks 3 and 6, sentence only).
- **Numbers are generated.** `bench/run_bench.py` writes `bench/results.json` and generates
  `bench/results.md` and the README table from it; the web app's Benchmark page reads the same file.
- **Same scenes for every attempt and condition.** Seeds are fixed per task.
- **Reproducible recordings.** The demo run reproduced its benchmark job from the request cache, and
  the 1280×720 re-render re-runs every scene and refuses to swap a video if any outcome changes.
  Replays in the web app say "Replaying a recorded run".
- **No tuning after the fact.** The reachability gap in `avoid` that task 6 exposed is left as it is,
  so benchmark outcomes aren't tuned to the tasks. The same goes for the bowl fallback below.
- **Stand-ins are labelled.** Their file names start with `standin_`, they are kept apart from human
  runs in `bench/results.json`, and the README table, the Benchmark page, the replay and the demo
  video each say so wherever a stand-in result appears.

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
- No human demo video is filmed yet, so the video path has only run on stand-in renders. How it holds
  up on a phone clip is untested, including tracking a hand.
- On rendered scenes SAM needs help: "glass" finds nothing (we ask for "light blue cylinder"), a
  standing glass is not detected from overhead (glass checks use the 45° camera), and a color phrase
  can also match a bin of that color (the judge drops boxes far larger than the object). In the
  stand-in renders SAM found no bowls, so the track summary falls back to splitting the frame into
  thirds, which misplaces some end positions (the spec still came out right, see Beyond a basic
  integration). From overhead, SAM finds a yellow cylinder in only 2 of 40 test-scene clips (38
  undecided checks).
- The judge's agreement rests on only two failing checks.
- The hosted demo replays recorded runs; it has no backend, so a visitor cannot start a live job there.
  The app runs live on a local backend (shown in the demo video, where the model answers come from
  the local cache of the stand-in run and the simulator runs every scene again) and in Docker.

## What's next

1. Film the six human demos and run the video condition once. It replaces the stand-in results in the
   benchmark, whatever it shows.
2. Make `avoid` check reachability (try the other side, a lower clearance, or no detour) and measure
   the change on the benchmark.
3. Harder scenes (more objects, clutter) to exercise the repair loop and the SAM judge on more failures.
4. A real-arm pilot: run a verified program on a physical Panda through the same skill API.

## How it was built

Everything in the repository was written during the event (first commit 2026-10-31; see the history).
Every model the product calls at runtime is a Meta model. Claude Code was the coding agent, which is
why commits carry a Claude co-author line. Main third-party parts: MuJoCo and the Franka Panda model
from MuJoCo Menagerie (both Apache 2.0). Other dependencies, pinned in `requirements.txt` and
`web/package-lock.json`, keep their own licenses, including Meta's `meta-sam-parser` (SAM License). The
project itself is open source under the MIT license.

## Links

- Repository: <https://github.com/nikocallas/show-and-tell-robotics> (public)
- Demo video: <https://show-and-tell-robotics.vercel.app/video/show-and-tell-demo.mp4> (2:57, narrated,
  captions burned in)
- Hosted demo: <https://show-and-tell-robotics.vercel.app> (demo mode: replays the three recorded runs, no backend)

<!-- sources:
Who it's for: IFR press release of 2025-09-25, see the link in the section (542,000 installed in 2024; 4,664,000 in operational use); MIT Sloan "How smaller firms can harness the potential of collaborative robots" (Sara Brown, 2021-08-24), the writer's reported speech attributed to Julie Shah: integration generally runs four to five times the cost of the actual robot
Beyond a basic integration:
- green cube in the middle third, spec says right bin, rationale quote: data/jobs/bench-task06_color_exc-standin-20261103-155105/report.json track_summary.objects[1].end_bin = middle_bin, track_summary.notes[0], spec.rules, spec.rationale ("The video shows the large red cube placed in the left bin and the small green cube placed in the right bin, ...")
- task 2 small red and green cubes in the middle third, spec red -> left, green -> right: data/jobs/bench-task02_color-standin-20261103-153905/report.json track_summary.objects[1,3].end_bin = middle_bin, spec.rules; bench/tasks.yaml rules name no bowl
- reach error wording and what Wonder received: the repair request rebuilt with showtell/agent/repair.py from data/jobs/20261101-063309-c15793 (hash matches its cache/wonder entry): "program error at 6.14 s, after pick(red_cube_large): SkillError: cannot reach pose (0.271, -0.050, 0.550) ..."; contact lines only for failed untouched/upright/unmoved checks (repair.py _watched_objects)
- rulechange-a spec adds fragile checks: data/jobs/rulechange-a-155206/report.json spec.checks, spec.rationale; the prompt's rule: showtell/agent/prompts.py
- SAM phrases, aspect 2.7 -> 1.1: PROGRESS.md "Observed API behaviour"
Stand-in benchmark, bench/results.json (updated_at "2026-11-03 15:53:56", n_seeds 20):
- tasks.*.standin: status verified; attempts [20/20]; ref_first [20,20]; ref_final [20,20]; repairs 0; job_id bench-<task>-standin-<timestamp>
- agreement: tasks.*.standin.agreement {agree, n, undecided}: 40/40, 60/60, 80/80, 41/41 + 19, 21/21 + 19, 60/60; by_kind sums in_bin 202/202, untouched 40/40, upright 40/40, unmoved 20/20; total 302/302, 38 undecided; undecided details: data/jobs/<tasks.*.standin.job_id>/report.json attempts[*].seeds[*].sam ("yellow_cylinder_large not visible at the end", "a yellow cylinder not visible at the end"); no box in any frame: attempts/0/seed_*/sam.json overhead["yellow cylinder"] empty in 38 of 40 scenes of tasks 4 and 5 (seeds 4010 and 5005 have tracks, frames 25-49 only)
- every ground-truth check in the stand-in runs passed (340 of 340 in data/jobs/<tasks.*.standin.job_id>/report.json attempts[*].seeds[*].checks), so the 2 failing verdicts are both from the task-6 repair run
- cost: tasks.*.standin.cost_usd 1.0015, 1.0299, 1.9186, 1.6907, 1.1758, 1.1187 (sum 7.9352); wall_s 147.6, 207.6, 117.7 (task 3, all cached), 218.7, 148.7, 166.1 (1006 s in all)
- fully cached runs: tasks.*.*.paid_usd = 0 for task 3 stand-in and tasks 1 and 3 sentence only (data/usage.jsonl, all rows of those job tags cached_locally); task 3's stand-in program equals data/jobs/20261031-154240-8ca2da attempts[0].program
- stand-in clips: bench/videos/standin/, bench/make_standins.py (seed = task index*1000 + 990); standin_task03_size_glass.mp4 has the same sha256 as the earlier data/probe/standin_task03_overhead.mp4
Benchmark, sentence only, bench/results.json:
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
python -m showtell cost (run 2026-11-03, data/usage.jsonl; paid/cached split: the same file's records grouped by kind and cached_locally):
- TOTAL paid $10.7704
- kind sam: paid $8.8190 = 704 paid calls / 44,095 billed frames; kind wonder: paid $1.9514 (47 paid calls); the call and cache-hit totals grow with every cached rebuild of the demo video, so they are not quoted
- the task-6 demo run paid $1.49: tag 20261101-063309-c15793:judge: 160 calls, 11 cache hits, paid 1.4900 for 7,450 billed frames (8,000 logged); :spec, :program, :repair1 paid 0.0000 (cache hits)
- judge requests per clip: data/usage.jsonl concept counts per tag (20261101-063309-c15793:judge: red cube 40, green cube 40, blue cube 80 over 40 scenes = 3 overhead + 1 at 45 degrees; 20261031-154240-8ca2da:judge: light blue cylinder 40, red/green/blue cube 20 each over 20 scenes = 4 overhead + 1 at 45 degrees)
- repair call $0.07, 4,366 in / 2,745 out tokens: tag bench-task06_color_exc-sentence-20261031-154958:repair1 (paid 0.0724)
- stand-in SAM tracking $0.59: tag 20261031-154240-8ca2da:track (no-cache 0.5940, 2,970 frames = 6 x 495)
Code facts:
- 720p, 10 fps: showtell/perception/video_prep.py NORMALIZE_VF; 8 keyframes: CLAUDE.md 5.4 and video_prep.py; discovery on 4 keyframes and effort medium: showtell/perception/track_summary.py
- spec/program/repair effort high: showtell/agent/spec.py SPEC_EFFORT, showtell/agent/program.py, showtell/agent/repair.py; input_video + file_id and json_schema format: showtell/meta/wonder.py
- SAM up to 4 concurrent requests: showtell/meta/sam.py max_concurrency=4; judge concept "light blue cylinder", 50-frame clips (25 + 25), 2 cm, 45-degree aspect test: showtell/verify/judge_sam.py
- judge concepts per clip (overhead: every object a check names; 45 degrees: untouched/unmoved, and upright for tall objects only): showtell/verify/judge_sam.py concepts_needed (ANGLE_KINDS, _tall); judge inputs are the clips, the checks, the object specs and scene_meta (camera calibration, bins), never the run's simulator state: judge_sam.judge, showtell/pipeline.py _judge_seed
- judge on by default in run and the web app, only in the benchmark's video condition: showtell/__main__.py run (judge option, default on; --no-judge), showtell/api/app.py POST /jobs use_judge Form(True), bench/run_bench.py run_condition (uses_video = condition != "sentence"; use_judge=use_judge and uses_video)
- loop exits: 20/20 or n >= max_repairs (showtell/pipeline.py run_job); a patch rejected twice by the sandbox raises RepairError (showtell/agent/repair.py repair) and run_job ends the job as "failed" (also PROGRESS.md Phase 5)
- checks: showtell/sim/checks.py (UPRIGHT_COS = cos 20 deg, unmoved tol 0.02 m)
- skills: showtell/sim/skills.py (TRANSIT_Z = table + 0.12, AVOID_CLEARANCE_Z = 0.10, IK_LAMBDA = 0.05, IK_FAIL_M = 0.02, MAX_SKILL_CALLS = 200, avoid margin_m=0.08); glass 0.20 m: bench/tasks.yaml header and README.md
- sandbox: showtell/agent/sandbox.py; 148 tests: pytest --collect-only -q tests/test_sandbox.py
- per-seed process, 120 s, 2048 MB, CPU count - 1: showtell/verify/runner.py (SEED_TIMEOUT_S, DEFAULT_SEED_MEMORY_MB, default_workers, spawn); seeds task_index*1000 + i: runner.task_seeds
- at most 3 repairs: showtell/contracts.py max_repairs = 3; 8 frames: showtell/pipeline.py failure_frames(n=8)
- render-demo at 1280x720, refuses on a changed outcome: showtell/verify/demo_render.py (HD = (1280, 720))
- "2 of these 422": 120 decided verdicts in data/jobs/20261101-063309-c15793/report.json (2 failed, both in seed 6004 of attempt 0) + 302 in the six bench-*-standin-* reports (0 failed)
- generated tables: bench/run_bench.py; Benchmark page reads results.json: web/src/pages/Bench.tsx, web/src/lib/bench.ts
- live progress over SSE, report.md/report.json/program.py downloads: showtell/api/app.py (GET /api/jobs/{id}/events, /report.md, /report.json, /program.py)
- diff of each patched program: web/src/components/ProgramPanel.tsx, web/src/lib/diff.ts
- disk cache by request hash, cache hits logged: showtell/meta/cache.py, showtell/meta/wonder.py call_json, showtell/meta/usage.py
- glass between the objects and the bins: showtell/sim/randomizer.py (module docstring, GLASS_X), PROGRESS.md Phase 1
- data/ and cache/ git-ignored: .gitignore; recorded runs in the bundle: web/public/demo/<id>/ (report.json, events.json)
- hosted demo: https://show-and-tell-robotics.vercel.app, deployed with the Vercel CLI from web/ (production build, demo mode); PROGRESS.md Phase 8
- first commit 2026-10-31: git log --reverse; Menagerie licence: third_party/mujoco_menagerie/franka_emika_panda/LICENSE (Apache 2.0)
-->
