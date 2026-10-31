# Progress

Status of each phase in `CLAUDE.md` Section 6. Updated after every phase.

## Blocked on human

1. **`MODEL_API_KEY`** — `.env` exists (copied from `.env.example`, `MUJOCO_GL=cgl` for this Mac) but the key is empty.
   Sign in at https://dev.meta.ai → Billing (payment method or hackathon credits) → API keys → Create API key,
   then set `MODEL_API_KEY=...` in `.env`. Blocks the Phase 0 acceptance check and every Wonder/SAM call after it.
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

## Phase log

- **Phase 0 — Setup and API smoke test**: code in progress. Repo skeleton, config, CLI shell done;
  `meta/` modules + `showtell smoke` being written with offline tests. Acceptance blocked on
  `MODEL_API_KEY` (see above).
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
