# Demo video script (under 3 minutes)

Story: show it once → one sentence → the tests → the first program → one real failure → Muse
Wonder's diagnosis → the patch → 20/20, verified → two judges → task 3 → benchmark → a real arm
next. The centerpiece is the real task-6 failure and repair. Apart from the opening phone clip, one
optional insert and the end card, every shot is the web app showing a real run, and every number on
screen or in the voiceover comes from a run's `report.json` or from `bench/results.json` (sources at
the end of this file).

## What exists today, and what waits on filming

As of 2026-11-01:

- **Recorded, sentence only:** task-6 run `20261101-063309-c15793`, "Sort by color, but blue things
  stay where they are." No demo video was used. Attempt 0 passed 19/20 (scene 6004 failed), Repair 1
  passed 20/20, and SAM 3.1 agreed with the simulator on 120/120 checks. It is in the demo bundle
  with 1280×720 hero videos, and it reproduces benchmark job
  `bench-task06_color_exc-sentence-20261031-154958` exactly (same Muse Wonder responses, served from
  the local cache).
- **Recorded, stand-in video:** task-3 run `20261031-154240-8ca2da`, made from
  `data/probe/standin_task03_overhead.mp4`, a top-down simulator render of the robot doing task 3.
  It is not a human demo. 20/20 on the first try, SAM agreed on 80/80 checks, 640×480 videos.
- **Not filmed yet:** every human demo video. `bench/videos/` has no task videos, so the
  benchmark's video condition reads `missing_video` for all six tasks.

The shot list is the final cut, with the human task-6 video. `[pending: …]` marks what the
video-condition runs will fill in: take those values from the run's `report.json` and from
`bench/results.json`, never from this file. The plan after the table decides, once, which run fills
the repair segment. The table is written for case B of that plan, the only case whose repair
footage exists today.

## Shot list

| Time | Shot | Voiceover |
|---|---|---|
| 0:00–0:10 | Phone footage, top-down, `bench/videos/task06_color_exc.mp4` [pending: film it]: a hand moves the red block into the left bowl and the green block into the right bowl; both blue blocks stay where they are. Title: **Show & Tell Robotics**. | "One take of a person doing a task: red to the left bowl, green to the right, the blue blocks stay put." |
| 0:10–0:20 | **New task** page (live take, see the checklist): drop the clip on "Demonstration video", click the preset chip "Sort by color, but blue things stay where they are." (it fills the rules and the scene), leave "Use the video" and "Review the spec before running" on, click **Start**. | "We give it that video and one sentence: sort by color, but blue things stay where they are." |
| 0:20–0:30 | Job page: the stage strip runs Prep → Discover → Track. The **Demonstration** card plays the clip and lists what SAM 3.1 tracked, in move order, with each object's end bowl [pending: video run; it should read red to left bin, green to right bin, blue not moved. If it reads otherwise, show it as it is and rewrite the line]. | "Muse Wonder names the objects. SAM 3.1 tracks the hand and each block: what moved, in what order, and into which bowl." |
| 0:30–0:42 | **Task spec** card with the chip "Needs your approval": rules as sentences with bin tags, and the checks [pending: video run spec; expected red → left bin, green → right bin, blue "Stays put", with "In bin", "In bin" and "Unmoved" checks]. Click **Approve and run**. | "Before any code, Muse Wonder writes the tests: which bowl each color ends in, and that the blue blocks don't move. You can edit them, then approve." |
| 0:42–0:56 | Cut to the replay of `20261101-063309-c15793` (bottom bar: "Replaying a recorded run"). Lower third, on screen until 2:10: **Recorded run · same task · sentence only (no video)**. Header: "Sentence only". Spec chip "From sentence only": "Put red objects in the left bin.", "Put green objects in the middle bin.", "Leave blue objects where they are." **Program** card, Attempt 0: `blues = scene.objects(color="blue")`, `avoid(blues)`, and `avoid=blues` on every `pick` and `place`. | "This recorded run had only the sentence, so Wonder chose the middle bowl for green. Its first program treats the blue blocks as obstacles on every move." |
| 0:56–1:16 | **Test scenes** on "A0 · 19/20": nineteen green borders and one red, scene 6004. Open it on the Hero camera: the arm lifts the red cube, stops, lets the cube drop back onto the table, and parks. Under the video: "The program stopped with an error" and "SkillError: cannot reach pose (0.271, -0.050, 0.550) yaw 81 deg (IK error 2.2 cm)". Checks: "All red objects are in the left bin." Fail; "All green objects are in the middle bin." Fail; "All blue objects remain where they started on the table." Pass. | "The program runs on twenty randomized scenes in MuJoCo, with a simulated Franka Panda arm. Nineteen pass. In scene 6004 the arm lifts the red cube and stops: its detour around a blue cube is out of reach. Red and green never reach a bowl." |
| 1:16–1:34 | **Attempts** card: "Attempt 0", 19/20 passed, with one red cell in its scene bar, then "Repair 1" with its **Diagnosis**. Optional insert (an edit, not a web-app screen), titled "What Muse Wonder received": the 8 frames of scene 6004, t = 4.93–7.73 s, from `web/public/demo/media/20261101-063309-c15793/attempts/0/repair_frames/`. | "Muse Wonder gets the failed checks, the event log, and eight frames from that scene. Its diagnosis, in one line: 'Avoiding the blue cubes forced an unreachable over-the-blue waypoint while carrying the red cube, aborting the program before placement.'" |
| 1:34–1:44 | **Program** card on R1, view "Changes +4 −6": the `blues` line, `avoid(blues)` and every `avoid=blues` argument are gone. | "The patch drops the detour. The blue blocks are low enough that the arm's normal travel height clears them." |
| 1:44–1:54 | **Test scenes** on "R1 · 20/20": all twenty green, 6004 included. Header badge **VERIFIED 20/20 scenes**; the Attempts card reads "After 1 repair." | "All twenty scenes run again, including the nineteen that already passed. Twenty out of twenty: verified." |
| 1:54–2:10 | Back to A0, scene 6004: the **Simulator** and **SAM 3.1** columns side by side, Fail, Fail, Pass in both, and "SAM agrees on 3 of 3 checks." Then **Run details**: Judge agreement 100%, 120/120 checks; In bin 80/80, Unmoved 40/40. | "Every scene has two judges: the simulator's ground truth, and SAM 3.1 looking only at the rendered video. On this run they agreed on all 120 checks, the failure included. The simulator decides." |
| 2:10–2:28 | Task 3, "Sort by size, and don't touch the glass.": **Program** card: `glasses = scene.objects(shape="glass")`, `avoid(glasses)`, and `avoid=glasses` on every `pick` and `place`; Attempt 0 at 20/20, "First try." [pending: the video-condition run of `task03_size_glass.mp4`; until it exists, the stand-in run `20261031-154240-8ca2da` with the lower third **Stand-in video: a simulator render, not a human demo**, framed (crop or zoom in the edit) so the Demonstration card under the Attempts card stays out of frame]. Then the spec card of the sentence-only run `bench-task03_size_glass-sentence-20261031-154759`: "Put non-fragile small objects in the left bin.", "Put non-fragile large objects in the right bin." | "On task three, every first program Wonder has written avoids the glass. But from the sentence alone, it put the small blocks left and the big ones right, the reverse of the demo: zero of twenty against it." |
| 2:28–2:44 | **Benchmark** page. Video columns: [pending: video benchmark, all six rows; see Plan steps 1–2]. "Final (sentence only)": 20/20, 20/20, 0/20, 20/20, 20/20, 0/20. | "With the video: [pending: video benchmark; a few words at most, the line already has 31 words for 16 s]. From the sentence alone, four of six match the demo: where the sentence doesn't name the bowls, Wonder guesses, and on tasks three and six it guessed wrong." |
| 2:44–2:54 | Hero camera at 1280×720: the video run's verified program on layout 6004 [pending: video run], red into the left bin, green into the right bin, the blue blocks untouched. End card: **Show & Tell Robotics · tested in simulation · real-arm pilot next**. | "Show it once, say the rule, and get a program that passed twenty test scenes. Next: a pilot on a real arm." |

## Plan: which run fills the repair segment (decide once, after filming)

1. Film all six task videos in the `bench/videos/README.md` shot list: the Benchmark shot at 2:28
   needs a video result in every row. Two of them carry the story. `task06_color_exc.mp4`, as
   `bench/videos/README.md` and `bench/tasks.yaml` describe it: big red, small green, big blue and
   small blue blocks; red → left bowl, green → right bowl, both blue blocks stay where they are (the
   benchmark's reference checks encode exactly that demo). `task03_size_glass.mp4`: big → left,
   small → right, around the glass without touching it. Check the files with
   `python -m showtell check-videos` (no API calls).
2. Run the video condition once, through the benchmark, before any other paid run on these clips
   and before recording 2:28: `python -m showtell bench` (all tasks; the finished sentence-only
   entries are skipped). The video runs include the SAM judge. Their folders are
   `data/jobs/bench-<task>-video-<timestamp>/`. A later `python -m showtell run` on the same clip and
   sentence gets the cached response for every request that has not changed (as the task-6 demo run
   did), so it is not a new sample; a changed request is a new, paid call (the second stand-in run
   reused the cached discovery and SAM tracks, but its changed track summary made its spec and
   program calls new). Either way the benchmark run is the result: never use a later run to replace
   it, and never clear the cache or change a request to get another outcome. Skip the
   `python -m showtell run` step at the end of `bench/videos/README.md` until the benchmark video run
   exists. If only tasks 3 and 6 get filmed, run `python -m showtell bench --tasks
   task06_color_exc,task03_size_glass`, change the 2:28 shot to: Video columns: task03_size_glass and
   task06_color_exc [pending]; the other four rows read "no video". Change the voiceover to: "With
   the video, on tasks three and six: [pending, at most six words]. From the sentence alone, ..."
   (the rest of the line as written).
3. Read the task-6 outcome in that job's `report.json` (`status`, `attempts[*].passed`,
   `attempts[*].diagnosis`) and take the matching case. Never re-run the video condition to get a
   different outcome: the first run is the result, in the benchmark and in this video.
   - **A. The first program fails at least one scene and a repair verifies it.** Use the video run
     from 0:20 to 2:10 and at 2:44. Drop the lower third and the 0:42 line about the middle bowl,
     and swap in that run's own values: the failing scene(s) and what happens in them, the error or
     failed checks, the diagnosis (quote `attempts[n].diagnosis` verbatim), the diff counts, the
     pass counts and number of repairs, and the judge agreement. Rewrite each line to match.
   - **B. The first program passes on the first try.** Keep the table as written. The repair
     segment (0:42–2:10) is the recorded sentence-only run, with its lower third on screen the whole
     time, and the 0:42 line says why green goes to the middle bowl. The video run's first-try
     result appears on the Benchmark page at 2:28.
   - **C. Anything else** (not verified after 3 repairs, or a pipeline error): show that result as
     it is at 2:28, keep the recorded run for the repair segment as in B, and ask the team before
     changing the story. `python -m showtell bench` re-runs every entry whose status is `error` (or
     `missing_video`), so after a pipeline error leave that task out of `--tasks` in later benchmark
     runs.
4. Render and export each run you show (no API calls): `python -m showtell render-demo
   data/jobs/<id>` (1280×720 hero videos; it refuses to replace them if any scene's outcome
   changes), then `python -m showtell export-demo --job data/jobs/<id>`. For the 2:10 spec card,
   also run `python -m showtell export-demo --job
   data/jobs/bench-task03_size_glass-sentence-20261031-154759`.
5. Fill every `[pending: …]` from `report.json` and `bench/results.json`, and update the sources
   comment below. Recheck the 2:10 line: it stays true only if the task-3 video run's first program
   also avoids the glass.

## Capture checklist

- **Where to record.** Replays come from the demo build: `cd web && npm run build && npx vite
  preview --port 4173` (production builds open in demo mode), or the Vercel URL once it is deployed
  [pending: deploy]. Never record from `npm run dev:fixture`: it serves sample data.
- **Live take, 0:10–0:42.** Start works and Approve appears only in live mode (`uvicorn
  showtell.api.app:app --port 8000`, then `cd web && npm run dev` and open
  `http://localhost:5173/new?live=1`). In demo mode the New task page shows Start disabled ("Switch
  to the live backend to start a task."), and a replay's spec card shows the recorded approval
  instead of a button. A live job is a new pipeline run and calls the Meta API for any request that
  is not already in the local cache. After the Approve click it runs to the end: the API has no
  cancel route, and stopping uvicorn does not interrupt a job that is already in the simulator. So
  the take pays for every request not in the cache, the SAM judge included (the task-6 demo run's
  judge alone paid $1.49). Record it only after the benchmark video run, with the same clip,
  sentence and scene preset; afterwards check `python -m showtell cost` for paid calls under the new
  job's tag; use the take only if its spec card matches the benchmark run's spec; and cut to the
  replay at the Approve click. Its results are never shown. If any of that fails, record the New
  task form without pressing Start (no job exists until Start) and take 0:20–0:42 from the video
  run's replay. That run was auto-approved, so its spec card shows the "Approved" chip, not "Needs
  your approval", and there is no click: show the chip and keep the voiceover's "You can edit them,
  then approve."
- **Cameras.** The hero camera sits beyond the bins on the robot's left, so the left bin is the one
  nearest the camera. When the voiceover names a bowl, switch the scene view to **Overhead** (left
  bin on the left of the image).
- **Window.** Record a 1920×1080 browser window: the job page puts the Attempts and Run details
  cards beside the grid only at 1024 px and wider. Zoom in the edit.
- **Replay controls.** 1x shows the grid filling scene by scene; use 2x, 4x or Skip for waits.
  Switch attempts with "A0 · 19/20" / "R1 · 20/20" in Test scenes and "A0" / "R1" in Program; the
  diff is the "Changes +4 −6" view on R1.
- **Lower thirds, verbatim:** "Recorded run · same task · sentence only (no video)" (case B,
  0:42–2:10); "Stand-in video: a simulator render, not a human demo" (only if the task-3 stand-in
  run is shown).
- **Don't show the stand-in run's Demonstration card.** Its subtitle reads "What SAM 3.1 tracked in
  the human demo", which is wrong for a render, and its list puts the large blue cube "to middle
  bin" (SAM found no bowls in the render, so the bins came from frame thirds). The app always
  renders it right under the Attempts card, so crop it out whenever "First try." is on screen.
- **Not in the web app:** the 8 repair frames (an edit insert from files) and SAM boxes drawn on the
  demo video (no screen shows them; don't promise them in the voiceover).
- **End-shot fallback.** If the video run is not ready, end on the recorded run's R1 scene 6004 (red
  into the left bin, green into the middle bin), keep its lower third, and change the closing line
  to: "One sentence, one program, twenty test scenes passed. Next: a pilot on a real arm."
- **Speed.** The phone clip (under 30 s) and the end-shot hero video (18.6 s for the recorded run's
  R1 scene 6004) run longer than their slots: trim them, or speed them up and show the speed on
  screen (for example "2×").
- Slow cursor, no notifications, output 1280×720 or larger. The replay bar may stay in frame; never
  call a replay "live" in the voiceover.

<!-- sources:
Recorded task-6 run, data/jobs/20261101-063309-c15793/report.json:
- sentence only: use_video=false, spec_source="sentence"; rules = "Sort by color, but blue things stay where they are."
- 20 scenes, seeds 6000–6019: seeds
- spec red -> left_bin, green -> middle_bin, blue leave + unmoved; check texts: spec.rules, spec.checks
- Attempt 0 19/20: attempts[0].passed / attempts[0].total
- failing scene 6004: attempts[0].seeds[4] (seed 6004, passed=false)
- "SkillError: cannot reach pose (0.271, -0.050, 0.550) yaw 81 deg (IK error 2.2 cm)": attempts[0].seeds[4].error
- red/green "ended in table", blue "none moved": attempts[0].seeds[4].checks[*].detail
- arm lifts the red cube, stops, the cube drops back, the arm parks: attempts/0/seed_6004/result.json trace (pick t=5.774, error t=6.144, release "program ended while holding" t=6.144, park t=9.168), final_positions; frames attempts/0/repair_frames/seed6004_f03_t6.13s.jpg and seed6004_f07_t7.73s.jpg
- "its detour around a blue cube is out of reach": the same trace (t=5.774 avoid blue_cube_small clearance_m 0.006, transit_z 0.55) and showtell/sim/skills.py Robot.transit (detour waypoint beside an avoided object the path passes within reach of); recomputed from the logged layout, the waypoint is about (0.244, 0.076) on the robot's side of the small blue cube, and the error pose lies on the leg to it, 14 cm from the cube (details in docs/submission.md sources)
- SAM agrees 3 of 3 on 6004: attempts[0].seeds[4].sam vs attempts[0].seeds[4].checks
- Attempt 0 program (avoid(blues), avoid=blues): attempts[0].program; Repair 1 program: attempts[1].program
- diagnosis, verbatim: attempts[1].diagnosis
- Repair 1 20/20, 1 repair: attempts[1].passed / attempts[1].total; len(attempts) = 2
- no blue cube touched in Repair 1: attempts[1].seeds[*].touch_events (no entry for a blue object)
- 120/120, In bin 80/80, Unmoved 40/40: agreement (by_kind)
- 8 frames, t = 4.93–7.73 s: file names in data/jobs/20261101-063309-c15793/attempts/0/repair_frames/ (also in the demo bundle)
- reproduces the benchmark job: data/jobs/bench-task06_color_exc-sentence-20261031-154958/report.json (same spec, programs, 19/20 -> 20/20, diagnosis); python -m showtell cost, tags 20261101-063309-c15793:spec, :program, :repair1 (1 call, 1 cache hit each)
- 1280x720 hero videos, 18.6 s for R1 scene 6004: ffprobe web/public/demo/media/20261101-063309-c15793/attempts/{0,1}/seed_6004/full.mp4 (1280x720, 30 fps; A0 10.17 s, R1 18.63 s)
- phone clip under 30 s: bench/videos/README.md (setup) and CLAUDE.md 5.4
- on-screen labels ("Sentence only", "From sentence only", "A0 · 19/20", "R1 · 20/20", "Changes +4 −6", "After 1 repair.", "SAM agrees on 3 of 3 checks.", "Judge agreement" / "100%" / "120/120 checks" (three separate elements in Run details), "Replaying a recorded run"): the demo build at http://localhost:4173/replay/20261101-063309-c15793 (read 2026-11-01) and web/src (JobView, SpecCard, SeedGrid, SeedModal, AttemptTimeline, ProgramPanel, ReplayControls, StageStrip, VerifiedBadge, pages/NewTask, pages/Bench, lib/format, lib/bench)
- other labels: "Demonstration video", the preset chip (the task's rule sentence), "Use the video", "Review the spec before running", "Start" and the disabled Start's "Switch to the live backend to start a task.": web/src/pages/NewTask.tsx; "Prep", "Discover", "Track": web/src/components/StageStrip.tsx; "VERIFIED" + "20/20 scenes": web/src/components/VerifiedBadge.tsx; "Stays put", "Needs your approval", "Approve and run", "Approved": web/src/components/SpecCard.tsx; "In bin", "Unmoved" and the rule sentences: web/src/lib/format.ts (KIND_LABEL, ruleSentence); "Final (sentence only)": web/src/pages/Bench.tsx; "no video": web/src/lib/bench.ts
- the Demonstration card always sits right under the Attempts card (right column): web/src/components/JobView.tsx (AttemptTimeline, then DemonstrationCard, then DetailsCard)
- no approval wait in the recorded runs, so their replays show "Approved": the events.jsonl of 20261101-063309-c15793 and of the benchmark job have spec_ready and spec_approved but no awaiting_approval; `run` defaults to interactive off (showtell/__main__.py) and bench/run_bench.py builds JobConfig without interactive (default False, showtell/contracts.py)
Task-3 runs:
- stand-in run: data/jobs/20261031-154240-8ca2da/report.json: use_video=true, video="input/standin_task03_overhead.mp4"; attempts[0] 20/20, program `glasses = scene.objects(shape="glass")`, `avoid(glasses)`, `avoid=glasses` on each pick and place: attempts[0].program; agreement 80/80; 640x480: ffprobe web/public/demo/media/20261031-154240-8ca2da/attempts/0/seed_3000/full.mp4
- second stand-in run's cache use: python -m showtell cost, tags 20261031-154240-8ca2da:discover (1 call, 1 cache hit), :track (6 calls, 6 cache hits), :spec and :program (1 call each, 0 cache hits, paid); the spec request carries the track summary (showtell/agent/spec.py spec_request_text), the two runs' track summaries differ (track_summary.notes and objects of 20261031-141952-83de37 vs 20261031-154240-8ca2da), and so do their specs (the upright check's text), which the program request carries
- stand-in Demonstration card: subtitle in web/src/components/JobView.tsx; "to middle bin" = report.json track_summary.objects[1].end_bin; frame thirds = track_summary.notes[0]
- "every first program Wonder has written avoids the glass" (4 programs): attempts[0].program in data/jobs/20261031-141341-6cd01a, 20261031-141952-83de37, 20261031-154240-8ca2da, rulechange-a-155206 (report.json); bench-task03_size_glass-sentence-20261031-154759 is the cached copy of the first (python -m showtell cost: its :program row is a cache hit)
- sentence-only task 3, small -> left_bin, large -> right_bin, 0/20: bench/results.json tasks.task03_size_glass.sentence.spec.rules, .ref_final = [0, 20]
Benchmark:
- Final (sentence only) 20/20, 20/20, 0/20, 20/20, 20/20, 0/20 ("four of six"): bench/results.json tasks.*.sentence.ref_final
- task 6 sentence only green -> middle_bin vs reference right_bin: bench/results.json tasks.task06_color_exc.sentence.spec; bench/tasks.yaml task06_color_exc.reference
- video condition missing: bench/results.json tasks.*.video.status = "missing_video"; bench/videos/ holds only README.md
- task-6 and task-3 demo descriptions: bench/tasks.yaml demo fields; bench/videos/README.md shot list (all six videos; its last step is a `python -m showtell run` on task 3)
- "31 words for 16 s": the 2:28–2:44 voiceover outside its [pending] placeholder, counted
- bench resumable, skips finished entries, re-runs "missing_video" and "error" entries, folders bench-<task>-<condition>-<timestamp>, SAM judge only in the video condition: bench/run_bench.py (main, run_condition)
Commands and modes:
- render-demo, export-demo --job <id or run folder>, check-videos: showtell/__main__.py, showtell/verify/demo_render.py, showtell/perception/video_check.py
- demo mode in production builds: web/.env.production (VITE_DEMO=1); ?live=1: web/src/config.ts; dev:fixture: web/package.json
- a live job calls the API only for requests not in the cache (cache key = sha256 of the request, no job id or tag): showtell/meta/cache.py, showtell/meta/wonder.py call_json; SAM and uploads keyed by file hash: showtell/meta/sam.py, showtell/meta/files.py
- live job runs to the end: no cancel route in showtell/api/app.py (POST /jobs, POST /jobs/{id}/spec only); showtell/api/jobs.py JobManager.shutdown ("A job inside the simulator cannot be interrupted; it keeps the process alive until done."); judge on by default (showtell/api/app.py use_judge Form(True)); task-6 demo run's judge paid $1.49: python -m showtell cost, tag 20261101-063309-c15793:judge
Other:
- hero camera position: showtell/sim/scene.py (hero camera at x 1.55, y 1.15; bins at x 0.67; left_bin at y +0.25)
- layout breakpoint 1024 px: Tailwind lg in web/src/components/JobView.tsx; replay speeds 1x/2x/4x: web/src/demo.ts SPEEDS
- at most 3 repairs (case C): showtell/contracts.py max_repairs = 3
- no SAM box overlay and no repair-frame view in the web app: web/src (failure_frames appears only in web/src/types.ts, never rendered)
-->

