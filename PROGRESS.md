# Progress

Status of each phase in `CLAUDE.md` Section 6. Updated after every phase.

## Blocked on human

1. ~~`MODEL_API_KEY`~~ — added to `.env` on 2026-10-31. (It was also pasted into the chat; rotate it in
   the dashboard after the hackathon.)
2. **Demo videos** in `bench/videos/` (see `CLAUDE.md` 5.4): phone top-down ~80 cm above a plain table, good light,
   1080p, < 30 s, one continuous take per task. Stand-ins: blocks/cups in 2 sizes × 3 colors (red, blue, green),
   a yellow cylinder (marker cap / glue stick), a tall glass, 3 bowls in a row at the far edge.
   Files: `smoke.mp4` (5 s, a hand moving one block), `task01_size.mp4`, `task02_color.mp4`, `task03_size_glass.mp4`,
   `task04_clear.mp4`, `task05_shape.mp4`, `task06_color_exc.mp4`. Blocks Phase 3 acceptance and the benchmark.
3. **Hackathon rules**: confirm submission fields, video length limit and repo visibility.
4. **Vercel**: run `vercel login` once in a terminal (the CLI uploads the 39 MB demo bundle) and OK the public
   deploy of the demo-mode web app.
5. **Voiceover + submission** (end of project).

## Verified API facts (from https://dev.meta.ai/docs/*.md, fetched 2026-10-31)

- Base URL `https://api.meta.ai/v1`, Bearer `MODEL_API_KEY`; OpenAI SDK compatible (Responses API).
- `muse-wonder-1.0` accepts text, image, video, audio, PDF. Reasoning effort `none|minimal|low|medium|high|xhigh|max`.
  Structured output on Responses is `text.format = {type: "json_schema", name, schema, strict}` (constrained decoding).
- Video parts: `input_video` with `file_id` or `video_url` (URL or base64 data URL); `input_file` with `file_id` also works.
- Files: `POST /v1/files`, `purpose=user_data`, synchronous (200 = ready), no expiry by default.
- `sam-3.1`: one noun-phrase concept per request + one media part; `metadata` values are strings
  (`include_confidence: "true"`, `mask_encoding: "one_bit"`). Stream `response.output_text.delta`, one line per frame.
  Zero matches = empty output, not an error.
- SAM line grammar: `<Nf>ID<|box;x1=..;y1=..;x2=..;y2=..;w=..;h=..;c=..|><|mask;x=0;y=0;c=..;data=H,W,!payload|>,...`
  (x2/y2 inclusive, ids sparse, frames may skip). `meta-sam-parser` 0.1.0: `parse_responses_stream(events,
  video_segmentation_format())`, `await final_result()`; boxes come back with exclusive right/bottom.
- Prices: Wonder $4/M in, $0.20/M cached in, $20/M out. SAM video $0.20 per 1,000 frames (≥ 50 frames/request),
  images $2.50 per 1,000. `usage.video_frames_processed` reports billed frames.

## Observed API behaviour (real calls, 2026-10-31)

- SAM 3.1 raw line for concept "red square" on a 960x720 clip (metadata include_confidence + one_bit):
  `<0f>0<|box;x1=63;y1=300;x2=181;y2=419;w=960;h=720;c=0.9532163739204407|><|mask;x=0;y=0;c=0.9532163739204407;data=120,119,!!!!!8K.M22(1...|>`
  One line per frame, ascending; 50 frames billed for a 50-frame clip; `meta-sam-parser` and our regex parse it identically.
- Muse Wonder structured output (`text.format` json_schema) works on the Responses API.
- Muse Wonder accepts `input_video` with a Files API `file_id`: a 50-frame 960x720 clip cost ~940 input tokens.
  Reasoning counts against `max_output_tokens`: at effort "low" one video reply used 488 reasoning tokens and another
  ran past 1,500 and came back empty (`incomplete: max_output_tokens`), so budgets must be generous.
- First real spec + program (task 3, sentence only, effort high): valid spec in one call (1,360 in / 2,089 out
  tokens, $0.047); without the video Wonder chose small → left, large → right (the reverse of the demo), so the
  program passes its own checks 20/20 but the reference checks 0/20 — the gap the video is meant to close. The
  first program already used `avoid(glass)` on every move ($0.031), so on task 3 this model may not need a repair.
- SAM 3.1 on our rendered judge clips (640x480, 50 frames): "red cube"/"blue cube" tracked on all 50 frames
  (c 0.91–0.95, ~30–43 px boxes); "green cube" also matched the green right_bin (108 px box) → the judge must
  reject boxes much larger than the object's projected size. "glass", "drinking glass", "glass cup",
  "transparent cylinder" found nothing; "light blue cylinder" finds the glass (c 0.94–0.96). Overhead, the standing
  glass is a small disc SAM does not detect (it is found only once knocked over), so glass checks use the
  45° camera, where the box aspect (h/w) went 2.7 → 1.1 when the glass fell. SAM keeps object ids across the
  cut between the first 25 and last 25 frames. Cost: $0.0002/frame as documented.
- SAM judge end to end (`verify/judge_sam.py`, real SAM, 6 task-3 scenes: 3 verified, 3 naive with the glass
  knocked over): with the glass concept "light blue cylinder" SAM agreed with ground truth on 23/23 decided checks
  (1 undecided: the fallen glass was not visible at the end); with "glass" every glass check was undecided. The judge
  now uses "light blue cylinder". About $0.075 of SAM per judged scene.
- Video path end to end on a stand-in clip (the robot doing task 3, overhead camera, 495 frames at 10 fps; a
  human video is still needed for the Phase 3 acceptance): discovery listed the 4 cubes with correct sizes and the
  glass; SAM tracked them; the spec from video mapped large → left_bin and small → right_bin (the reference
  mapping; the sentence-only spec had it reversed); program verified 20/20. $0.92 total ($0.59 SAM for 6 concepts
  x 495 frames). Rough edges on rendered video: SAM finds no "bowl" in the box-shaped sim bins (fallback: frame
  thirds), "blue cube"/"green cube" also matched the same-colored bins (to filter by relative size), the glass
  needs "light blue cylinder", and discovery's first reply ran past 8,000 tokens (the doubled budget fixed it).
  The summary now drops static tracks that are really a bowl or bin (re-run on the cached SAM tracks of that
  job: both bin tracks dropped, 8x and 10x the median object box).
- Task 3 never fails on the first try with this model: Wonder's program uses `avoid(glass)` every time. Even
  with the glass left out of the sentence ("Sort by size: big ones in the left bowl, small ones in the right
  bowl." on the task-3 scene), the spec added untouched/upright checks for fragile objects on its own and the
  program avoided them: 20/20, nothing touched the glass; re-testing that program after adding "and don't touch
  the glass" also passed 20/20 (jobs `rulechange-a/b-155206`, $0.11). Tightening the randomizer band cannot
  change this (the program avoids the glass in every layout), so the planned "naive program hits the glass"
  moment does not happen naturally on task 3.
- First real repair (benchmark, task 6 sentence only, job `bench-task06_color_exc-sentence-20261031-154958`):
  the first program avoided the blue cubes on every move; on seed 6004, carrying the red cube, the straight path
  passed 6 mm from the small blue cube, so `avoid` raised it to z = 0.55 m with a detour point beside the cube;
  the leg toward that point ran through (0.271, -0.050, 0.550), which the arm cannot reach (IK error 2.2 cm), so
  the program stopped before placing anything and failed 19/20. Wonder's diagnosis: "Avoiding the blue cubes forced an unreachable over-the-blue
  waypoint while carrying the red cube, aborting the program before placement." The patch dropped `avoid` for
  the short blue cubes (the 0.12 m transit height already clears them): 20/20. Known limitation behind it:
  `avoid` picks its detour waypoint without checking reachability; left as is so benchmark outcomes are not
  tuned after the fact.

## Phase log

- **Phase 0 — Setup and API smoke test**: **done** (2026-10-31).
  - `python -m showtell smoke` passes every step against the real API ($0.02): Wonder text ("ready"),
    Wonder JSON through `text.format` json_schema, Wonder video through `input_video` + `file_id` (no keyframe
    fallback needed), SAM 3.1 tracking (1 track, 50 frames billed). `data/smoke_report.json` has `ok: true`.
  - The SAM step used a synthetic 5 s clip (a red square sliding) because `bench/videos/smoke.mp4` (a hand
    moving a block) is not filmed yet; with that file present it tracks "hand".
  - `meta/`: OpenAI SDK client, Files upload cache (sha256 → file id), Wonder `call_json` (constrained JSON,
    one validation retry, budget doubling when reasoning eats `max_output_tokens`, retries on mid-stream
    server errors), SAM streaming per concept with both parsers, disk cache by request hash, usage log + cost.
  - Deviation: `normalize` adds `-pix_fmt yuv420p` to the CLAUDE.md ffmpeg command so phone HDR clips stay
    browser-playable 8-bit H.264.
- **Phase 1 — Scene and rendering**: done.
  - `python -m showtell render-scene --seed 1` writes `data/render_scene/seed1_{overhead,judge_angle,hero}.png`
    and a 3 s `seed1_hero.mp4` (`--hd` renders the hero camera at 1280x720).
  - Scene: Menagerie Panda (added `ee` site at the fingertip pad center, gravity compensation), table
    1.2 x 0.8 m at z = 0.40, three static bins (0.20 m interiors) at x = 0.67 along the far edge,
    objects on free joints, 2 directional lights + ambient, cameras `overhead` (image up = far edge,
    image left = `left_bin`), `judge_angle` (45°), `hero`. Rendering uses `MUJOCO_GL=cgl` on macOS.
  - Randomizer: objects in the front of the table (x 0.27–0.42 m), >= 4 cm apart, >= 10 cm from bins,
    >= 0.30 m from the base; the glass in a central band (x 0.445–0.48 m) on the straight path from at
    least one object to a bin, with a 10 cm gap to other objects. Tests: 20 seeds per task are valid
    and non-overlapping (`tests/test_sim_scene.py`).
- **Phase 2 — Skills and hard-coded program (GO/NO-GO)**: **GO**.
  - `examples/size_sort.py` (with `avoid=glass`) passes all checks on **20/20** seeds of task 3.
  - `examples/size_sort_naive.py` (no avoid) fails `untouched`/`upright` on **19/20** seeds: the
    carried block or the hand really knocks the glass over at the 0.12 m transit height.
  - Reference programs for all six tasks (`examples/reference/`) pass **100/100** seeds each.
  - Skills: DLS IK (λ = 0.05) with a null-space elbow posture, straight-line Cartesian moves (IK
    every 1 cm), kinematic attach while holding, contact log with timestamps and the active skill
    phase (for diagnoses), `avoid` that raises transit height to the object's height + 0.10 m with a
    detour waypoint, and whole-arm clearance (`mj_geomDistance`) to pick an elbow posture for
    approaches next to an avoided object.
  - Tests: IK convergence, attach/detach, skill errors, every check on constructed states
    (`tests/test_sim_skills.py`, `tests/test_sim_checks.py`); the gate itself is
    `pytest -m slow tests/test_phase2_gate.py` (2 passed).
  - Fixes found by the gate: wrist-yaw choice near joint limits, fingers dragging a released
    cylinder, slot choice inside bins, forearm brushing the glass rim.
- **Phase 3 — Perception and spec**: code **done**; acceptance **blocked on the human video**
  `bench/videos/task03_size_glass.mp4`.
  - `perception/video_prep.py` (normalize, 8 keyframes), `perception/track_summary.py` (Wonder discovery on 4
    keyframes → one SAM request per concept → start/end centroids, moved, move order, end bowl; sizes from box
    area; bowl/background tracks dropped), `agent/schemas.py`, `agent/prompts.py`, `agent/spec.py` (Wonder sees
    the track summary + the video by `file_id` + the sentence + the sim vocabulary; `ensure_checks` adds a check
    for every rule; `validate_spec` notes contradictions).
  - Evidence until the video exists: stand-in clip job `20261031-141952-83de37` (spec from video correct) and
    the `--no-video` spec for task 3 (job `20261031-141341-6cd01a`, mapping reversed).
- **Phase 4 — Program generation and verification**: **done**.
  - Generated task-1 program ran in the sandbox on 20 seeds: **20/20** with a report (job
    `20261031-141431-892a1c`).
  - Sandbox: AST whitelist (one `def run(scene)`, whitelisted calls with signatures taken from `skills.py`);
    each seed runs in a spawned subprocess with a 120 s timeout, a 2 GB memory watchdog and a 200 skill-call
    cap. `tests/test_sandbox.py` rejects imports, dunder access, while loops, lambdas, try/with, globals and
    more (148 tests).
- **Phase 5 — SAM judge, repair loop, full CLI**: code **done**; acceptance run **blocked on the human video**.
  - `verify/judge_sam.py` (overhead + 45° clips → per-check verdicts; real SAM agreed 23/23 on task-3 scenes),
    `verify/agreement.py`, `agent/repair.py` (failure report with the first contacts + 8 frames around the first
    failure → Wonder diagnosis + patch, sandbox-checked with one retry), `pipeline.py`, `python -m showtell run`.
  - Offline test: the naive size sort fails, the repair double adds `avoid`, 3/3 pass (real physics). A patch
    rejected twice by the sandbox ends the job as "failed" with the reason logged.
- **Phase 6 — API and web app**: **done** (2026-10-31).
  - Browser run on fresh `uvicorn` + `npm run dev` (`.claude/launch.json`): uploaded the stand-in task-3 video,
    the job paused with the spec from video (large → left, small → right, glass untouched/upright), approved in
    the spec card, the 20-scene grid filled live over SSE, attempt 0 passed 20/20, VERIFIED badge; report.md,
    report.json and program.py downloads return 200 as attachments; the scene view plays the hero video and shows
    simulator vs SAM verdicts. SAM judge agreed 80/80 (job `20261031-154240-8ca2da`, $1.11 paid).
  - FastAPI (`api/app.py`, `jobs.py`, `db.py`): jobs, SSE with resume, spec approval/edit, media with Range,
    downloads; POST needs a token (or comes from this machine) and paid jobs stop at `SHOWTELL_BUDGET_USD`.
  - Web (`web/`): new task, live job (spec card, attempt timeline, 20-scene grid, program diff, verified badge,
    downloads), runs, benchmark, demo mode replaying `web/public/demo/`. 49 API tests; type-check and build pass.
- **Phase 7 — Benchmark**: **half done**; the video condition is **blocked on the human videos**.
  - `python -m showtell bench` ran all six tasks; the video condition records "no video" and re-runs once the
    files exist (resumable). Sentence only, scored against the reference checks: tasks 1, 2, 4, 5 20/20; task 3
    0/20 (bins reversed: small → left); task 6 0/20 (green → middle instead of right) after one real repair.
    Total $0.42. `bench/results.{json,md}` and the README table are generated from these runs.
- **Phase 8 — Demo mode, polish, deploy**: in progress.
  - `showtell export-demo --job 20261031-154240-8ca2da` → `web/public/demo/` (17.4 MB); the Runs page and the
    replay work in demo mode (`?demo=1`) with media served from the bundle.
  - A 720p hero render is about 2 MB per scene (44 s), about 40 MB per 20-scene attempt.
  - Docker: the image builds and runs a job end to end (token auth, offscreen rendering, media served).
  - Demo story (chosen 2026-11-01): the real task-6 failure → diagnosis → repair. Demo run
    `20261101-063309-c15793` (sentence only until `task06_color_exc.mp4` exists): attempt 0 19/20 (seed 6004,
    unreachable detour), the same diagnosis, attempt 1 20/20 VERIFIED; it reproduces the benchmark job exactly
    (Wonder responses from the cache). SAM judge agreed 120/120, including the failing scene (it saw the red and
    green cubes outside the bins). $1.49 paid (SAM judge only). Hero videos re-rendered at 1280x720 with
    `render-demo` (every outcome matched). Bundle: both runs, 39 MB.
  - Vercel: the connector reaches the account (Hobby), but the CLI is not logged in; a 39 MB bundle needs the
    CLI (`vercel login` once, by you), and the public deploy waits for your OK.

