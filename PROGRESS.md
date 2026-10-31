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
4. **Voiceover + submission** (end of project).

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
