# Demo video

Built by `python -m demo_video` from this repository (see `demo_video/README.md`): 1920×1080, H.264,
2:57, narrated by a person, captions burned in. The copy at `web/public/video/show-and-tell-demo.mp4` ships with
the hosted demo.

Story: a program knocks over a glass → show it once, say the rule → the tests → the program → 20
scenes → a real failure → Muse Wonder's diagnosis → the patch → 20/20, verified → two judges → why the
video matters → who it is for → a real arm next.

## What is real, and how it is labelled

- **Screen footage** is the real web app in headless Chrome, driven by a script and recorded in real
  time. There is one exception, labelled on screen: the live grid runs sped up to fit its narration. The
  replay's player bar is hidden in the capture because it would cover the bottom row of scenes; the
  label on screen says it is a recording.
- **Live shots** (New task to the grid) are a real job on a local backend. Muse Wonder's and SAM's
  answers come from the local cache of the benchmark's stand-in run, and the label says so. The job
  page marks only Muse Wonder's stages "Cached". The simulator ran every scene again.
- **Recorded-run shots** are the production build replaying `20261101-063309-c15793` (task 6, sentence
  only), the benchmark's one real repair.
- **Simulator footage** is the real program on the real seed, re-rendered at 1080p. Each clip's outcome is
  checked on every build: the naive program must knock the glass over on seed 3018, Muse Wonder's
  program must pass on it, and the recorded first program must stop at the same pose on seed 6004.
- **Every stand-in result is labelled** on screen, and the end card says so too. The narration says
  it when the clip first appears and on the benchmark shot.
- **Cards** (title, models, impact, end) are ours, written in `demo_video/cards.py`.
- **The narration** is a person reading the script along with the captions, in one take. Its video
  stream is the captions-only cut, unchanged.

## Shot list

Generated from `demo_video/script.py` with the shots' real lengths.

<!-- shots:start -->
| Time | On screen | Label | Narration |
|---|---|---|---|
| 0:00–0:06 | Hero camera, 1080p: `examples/size_sort_naive.py` on task-3 layout 3018. The arm carries the red cube into the glass and the glass falls. The program's source is on screen. | Hand-written baseline · no obstacle handling · simulated physics, not scripted | This program looks correct. It also knocks over the glass in 19 of 20 random layouts. |
| 0:06–0:15 | Title card; 20 cells fill green. | — | Telling a robot what to do still means writing a program, and the person who knows the task usually can't read it. Show and Tell Robotics changes that. |
| 0:15–0:27 | New task page, live backend: the stand-in clip drops in and plays, the preset sentence is picked, Start. | Stand-in clip: a simulator render, not a human demo · model answers cached | You show the task once on video, and say the rule in one sentence: sort by color, but blue things stay where they are. Here, a simulator render stands in for the phone clip. |
| 0:27–0:30 | Job page, live: Prep and Discover done, Track running. | Stand-in clip: a simulator render, not a human demo · model answers cached | Muse Wonder names the objects on the table. |
| 0:30–0:35 | Demonstration card: what SAM 3.1 tracked, in move order, with the note that no bowls were found in the render. | Stand-in clip: a simulator render, not a human demo · model answers cached | SAM 3.1 tracks each one: what moved, in what order, and what stayed put. |
| 0:35–0:47 | Task spec card, chip "Needs your approval": rules and checks, then Approve and run. | Stand-in clip: a simulator render, not a human demo · model answers cached | Before any code exists, Muse Wonder watches the video and writes the tests: red ends in the left bin, green in the right, blue never moves. You read them, edit them, approve. |
| 0:47–0:51 | Program card: the generated program. | Stand-in clip: a simulator render, not a human demo · model answers cached | Then it writes a short program out of tested robot skills. |
| 0:51–0:59 | Test scenes filling in live, sped up with a label on screen, until 20 passed. | Stand-in clip: a simulator render, not a human demo · model answers cached | It runs on 20 randomized scenes in MuJoCo, with a simulated Franka arm. This one passes all 20 on the first try. |
| 0:59–1:06 | Replay of the recorded sentence-only run: Test scenes on A0, 19 green, scene 6004 red. | Recorded run · same task · sentence only (no video) | So what happens when a scene fails? In this recorded run, from the sentence alone, 19 scenes pass. |
| 1:06–1:16 | Hero camera, 1080p: the recorded run's first program on layout 6004. The arm lifts the red cube, the program stops, the cube drops and the arm parks. | Recorded run · same task · sentence only (no video) | In scene 6004, the program stops after the lift: its detour around a blue cube is out of reach, so the arm lets go and parks. |
| 1:16–1:20 | Scene dialog: simulator and SAM columns, then the 8 frames sent to Muse Wonder. | Recorded run · same task · sentence only (no video) | Muse Wonder gets the failed checks, the error and eight frames from that scene. |
| 1:20–1:31 | Attempts card: the diagnosis, verbatim. | Recorded run · same task · sentence only (no video) | Its diagnosis, word for word: Avoiding the blue cubes forced an unreachable over-the-blue waypoint while carrying the red cube, aborting the program before placement. |
| 1:31–1:36 | Program card on R1, view "Changes +4 −6". | Recorded run · same task · sentence only (no video) | The patch drops the detour: the blue cubes are low enough for the arm's normal travel height. |
| 1:36–1:40 | Test scenes on R1, then the VERIFIED badge. | Recorded run · same task · sentence only (no video) | All 20 scenes run again. 20/20: verified. |
| 1:40–1:53 | Scene dialog: Simulator and SAM 3.1 side by side. Run details: 120/120, In bin 80/80, Unmoved 40/40. | Recorded run · same task · sentence only (no video) | Every scene also gets a second judge: SAM 3.1, looking only at the rendered video. It agreed with the simulator on all 120 checks, the failure included. The simulator still decides. |
| 1:53–2:12 | Benchmark page: the sentence-only column, then the video column, with "stand-in clip" under every video value. | Video columns: stand-in clips, not human demos | Why the video? From the sentence alone, four of six tasks match our reference: where the sentence doesn't name the bin, Wonder has to guess. The run you just saw is a miss: it guessed the middle bin for green. With a stand-in clip, six of six match, on the first try. Human clips are next. |
| 2:12–2:22 | Hero camera, 1080p: the stand-in task-3 run's program on the same layout 3018; the arm goes up and around the glass. The program's source is on screen. | Muse Wonder's program · from a stand-in clip · same layout | And the glass? On that same layout, Muse Wonder's program lifts above it and goes around. Every program it has written for that task avoids the glass. |
| 2:22–2:35 | Card: the jobs of Muse Wonder and SAM 3.1. | — | Muse Wonder does four jobs here, and SAM 3.1 does two. Generated code runs in a sandbox, pass or fail comes from physics, and a verified task costs one to two dollars in API calls. |
| 2:35–2:48 | Card: Julie Shah's integration-cost figure and who the product is for. | — | Integration generally runs four to five times the cost of the robot, says MIT's Julie Shah. We want the person who knows the job to change it: one demonstration, one sentence, one test report. |
| 2:48–2:56 | End card: links, models, and what is not done yet. | — | It runs in simulation today. Next is a pilot on a real arm, through the same skills and the same tests. |
<!-- shots:end -->

## Sound

The build times the cut with a macOS voice but does not publish it: macOS's license keeps its system
voices to personal, non-commercial use, so `demo_video/out/preview_macos_voice.mp4` is for your own review.
The published cut has a recorded human voice.

To record a new take, about three minutes:

1. Open `demo_video/out/show_and_tell_demo_silent.mp4` in QuickTime and play it. The captions are the
   script, so read along with them.
2. Record with Voice Memos at the same time, starting with the first caption at about 0:00.3.
3. Export the recording, then add it and publish the result. Only the sound comes over: the first audio
   track, as mono at -16 LUFS. A phone recording's metadata and file name, which can hold your location, do not.

```bash
.venv/bin/python -m demo_video --voiceover my_voice.m4a
```

```bash
.venv/bin/python -m demo_video --publish demo_video/out/show_and_tell_demo_voiced.mp4
```

4. Redeploy `web/` so the hosted link serves the new file.

If your pace drifts, a retake is quicker than editing.

## If you film a human demo

The best upgrade is a real clip of task 6: red to the left bowl, green to the right, both blue blocks
stay. `bench/videos/README.md` has the setup. Then:

1. `.venv/bin/python -m showtell check-videos` (free).
2. `.venv/bin/python -m showtell bench --conditions video --tasks task06_color_exc` (about $1.50). The
   first run is the result: never re-run it to get a different outcome, and never clear the cache.
3. Point `LIVE_CLIP` in `demo_video/ui_shots.py` at the clip, take the stand-in label off the live shots
   in `demo_video/script.py` (and their entry in `tests/test_demo_video.py`), match the narration to what
   the run shows, rebuild, and record a new take (see Sound).
4. `.venv/bin/python -m showtell export-demo --job data/jobs/<that job>`, then publish the new voiced cut
   and redeploy `web/`.

If the first program fails a scene and a repair verifies it, that run can carry the whole repair
segment. If it passes on the first try, keep the recorded sentence-only run for that segment, with its
label.

<!-- sources:
- narration checked against the script: Muse Voice Transcribe on the whole recording and on a window around each of the 32 narration lines, with script terms as keyword hints (including "SAM 3.1" and "6004"). Mean line similarity 0.93; about a quarter of the gap is the transcriber repeating words, the rest is neighbouring words in the window, spelling and changed words. Every number but one came back as written; "SAM 3.1" came back as "SAM 31" in all 3 line cuts and once in the whole-file run, while the phone's own transcript, given no hints, has "3.1" at 0:30 and "3.one" at 1:40 and 2:22. Video stream of the voiced cut = the captions-only cut: same MD5 with ffmpeg -c copy -f md5.
Script numbers read and asserted at build time from web/public/demo/20261101-063309-c15793/{report,job}.json and bench/results.json: demo_video/script.py load_facts, Facts.matches. Robot outcomes checked on every build: demo_video/__main__.py robot_clips. Naive count: tests/test_phase2_gate.py.
- 19 of 20 knocked over, seed 3018 tilted 90 deg: examples/size_sort_naive.py on seeds 3000-3019 (19 fail untouched and upright, tilt 90-111 deg; seed 3012 passes); tests/test_phase2_gate.py; PROGRESS.md Phase 2
- "Every program it has written for that task avoids the glass": attempts[*].program of every data/jobs/*/report.json with task_id task03_size_glass or a glass rule; all contain avoid(
- "one to two dollars": bench/results.json tasks.*.standin.cost_usd 1.0015 to 1.9186 (list price)
- "four jobs", "does two": docs/submission.md Meta models used
- "four to five times", Julie Shah: https://mitsloan.mit.edu/node/29144
- live job's "Cached": StageStrip labels from stage_done.cached, Wonder stages only (showtell/contracts.py); no paid calls: data/usage.jsonl tags of the live job's id, all cached_locally
- system voices: macOS software license, section 2.F "Voices; Live Captions" (/System/Library/CoreServices/Setup Assistant.app/Contents/Resources/en.lproj/OSXSoftwareLicense.html)
-->
