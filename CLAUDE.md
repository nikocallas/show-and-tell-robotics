# CLAUDE.md — Show & Tell Robotics (Meta Global AI Developer Hackathon, Indie Developer track)

You are building this project end to end. Read this whole file before writing code.
Work phase by phase (Section 6). Do not start a phase until the previous phase's
acceptance checks pass. After each phase: run its checks, commit, and update `PROGRESS.md`.

---

## 1. What we are building

**One line:** Show a robot a task once on video, state the rules in one sentence, and get back a
robot program that is *proven* to work in simulation.

**Core idea: test-driven robot programming.**
1. **Watch:** SAM 3.1 tracks hands and objects in a human demo video.
2. **Write the checks:** Muse Wonder turns the video summary + the user's sentence into a task spec:
   attribute rules + pass/fail checks. The user approves or edits it.
3. **Write the program:** Muse Wonder writes a small Python function that composes 4–5 tested robot
   skills and loops over objects *by attribute* (never by coordinates).
4. **Test:** Run the program in MuJoCo on 20 randomized scenes (Franka Panda arm).
5. **Judge:** Two independent judges per scene: (a) simulator ground truth, (b) SAM 3.1 on the
   rendered video. Log how often they agree.
6. **Repair:** If any scene fails, Muse Wonder gets the failed checks + a failure clip + the program,
   explains the failure in one line, and patches the program. Rerun all 20. Max 3 repair rounds.
7. **Verified:** All 20 pass → program + test report + full attempt log.

**Deliverables (all required):**
- Working CLI pipeline: `python -m showtell run --video X --rules "..."`.
- FastAPI backend + React web app (upload → approve spec → live 20-scene grid → attempts → report).
- Benchmark script producing `bench/results.md` and `bench/results.json`.
- A "demo mode" of the web app that replays precomputed runs (so the hosted demo can't break).
- README with setup, architecture, the Meta models used, and the results table.

**Out of scope:** real robot hardware, learned motor control, deformable objects, multiple arms.

---

## 2. Ground rules for you (Claude Code)

- **Verify every external API shape against the official docs before relying on it.** The
  snippets below are from Meta's docs as summarized by third parties; field names may differ.
  Docs to read first:
  - https://dev.meta.ai/docs/overview
  - https://dev.meta.ai/docs/models
  - https://dev.meta.ai/docs/sam/segmenting
  - https://dev.meta.ai/docs/sam/reading-segmentation
  - https://dev.meta.ai/docs/sam/client-libraries
  - https://dev.meta.ai/docs/video-understanding
  - https://dev.meta.ai/docs/coding-agents
  If you can't fetch a page, write a minimal probe script, call the API, inspect the raw response,
  and adapt. Never invent fields.
- **Never fake results.** No hard-coded pass/fail, no staged failures. The scene is designed so
  naive plans genuinely fail (Section 5.3). Every number in the README comes from `bench/`.
- **Never commit secrets.** `MODEL_API_KEY` lives in `.env` (git-ignored). Provide `.env.example`.
- Cache every paid API response to disk (`cache/`, keyed by a hash of the request) so reruns cost $0.
- Keep each module small and tested. Use `pytest`. Type-hint everything. Use `pydantic` v2 for schemas.
- Use `ruff` for lint and format.
- If a phase's acceptance check is blocked by something only the human can do (API key, filming
  videos), stop, list exactly what you need in `PROGRESS.md` under `## Blocked on human`, and work
  on the next unblocked task.

---

## 3. Human-only tasks (tell the user if any are missing)

1. **API key:** sign in at https://dev.meta.ai → Billing (add a payment method, or confirm hackathon
   credits are applied) → API keys → Create API key. Put it in `.env` as `MODEL_API_KEY=...`.
2. **Film the demo videos** (Section 5.4) and drop them in `bench/videos/`.
3. **Read the official hackathon rules**: confirm submission fields, video length limit, and repo
   visibility.
4. **Record the voiceover** for the demo video and **submit** the form.

---

## 4. Environment

- Python 3.11+, Node 20+, ffmpeg.
- Create the venv and install:
  ```bash
  python -m venv .venv && source .venv/bin/activate
  pip install mujoco numpy scipy imageio imageio-ffmpeg opencv-python-headless \
              openai pydantic python-dotenv fastapi "uvicorn[standard]" sqlmodel \
              python-multipart httpx tenacity rich typer pytest ruff
  pip install "meta-sam-parser>=0.0.6"   # Meta's SAM output parser; verify name/version on docs
  git clone --depth 1 https://github.com/google-deepmind/mujoco_menagerie third_party/mujoco_menagerie
  ```
- Rendering: on a GPU box set `MUJOCO_GL=egl`; on CPU-only set `MUJOCO_GL=osmesa`
  (`apt-get install -y libosmesa6`). Put this in `.env` too.
- `.env.example`:
  ```
  MODEL_API_KEY=
  META_BASE_URL=https://api.meta.ai/v1
  WONDER_MODEL=muse-wonder-1.0
  SAM_MODEL=sam-3.1
  MUJOCO_GL=egl
  SHOWTELL_DATA_DIR=./data
  ```

---

## 5. Specifications

### 5.0 Repo layout

```
showtell/
  __init__.py
  __main__.py            # typer CLI: run, smoke, bench, render-demo
  config.py              # loads .env
  meta/
    client.py            # OpenAI SDK client pointed at Meta Model API
    files.py             # Files API upload (+ cache by sha256)
    wonder.py            # Muse Wonder calls: JSON-in/JSON-out with validation + retry
    sam.py               # SAM 3.1 video calls + parsing to tracks
  sim/
    scene.py             # builds MJCF: Panda + table + 3 bins + objects
    randomizer.py        # seeded scene layouts
    skills.py            # move_to, pick, place, avoid (+ IK)
    checks.py            # ground-truth predicates from sim state
    render.py            # cameras, mp4 writer (H.264, faststart)
  perception/
    video_prep.py        # ffmpeg normalize/downscale, keyframe extraction
    track_summary.py     # SAM tracks -> what moved, where, in what order
  agent/
    schemas.py           # pydantic: ObjectAttrs, TaskSpec, Program, RepairResult, ...
    prompts.py           # all prompt templates (Section 5.6)
    spec.py              # video+sentence -> TaskSpec
    program.py           # TaskSpec + skill API doc -> Program source
    sandbox.py           # AST whitelist + subprocess exec with timeout
    repair.py            # failure report -> patched program
  verify/
    runner.py            # run program on N seeds, collect results + videos
    judge_sam.py         # SAM judge on rendered clips
    agreement.py         # SAM vs ground truth agreement stats
  pipeline.py            # end-to-end orchestration (used by CLI + API)
  api/
    app.py               # FastAPI
    db.py                # SQLModel tables
    jobs.py              # background job runner
web/                     # Vite + React + TS + Tailwind
bench/
  tasks.yaml             # benchmark task definitions
  videos/                # human demo videos (from the human)
  run_bench.py
tests/
cache/  data/            # git-ignored
CLAUDE.md  PROGRESS.md  README.md  .env.example  Dockerfile
```

### 5.1 Meta Model API integration

The Meta Model API is OpenAI-SDK-compatible. Base URL `https://api.meta.ai/v1` (no trailing
slash), Bearer auth with `MODEL_API_KEY`. Muse Wonder is `muse-wonder-1.0`; SAM is `sam-3.1`. Both
use the Responses API (`POST /v1/responses`). Uploads go through `POST /v1/files` with
`purpose="user_data"`.

**`meta/client.py`**
```python
from openai import OpenAI, AsyncOpenAI
from showtell.config import settings

def client() -> OpenAI:
    return OpenAI(base_url=settings.META_BASE_URL, api_key=settings.MODEL_API_KEY,
                  timeout=300, max_retries=3)

def aclient() -> AsyncOpenAI:
    return AsyncOpenAI(base_url=settings.META_BASE_URL, api_key=settings.MODEL_API_KEY,
                       timeout=300, max_retries=3)
```

**`meta/files.py`**: upload with `client().files.create(file=open(p,"rb"), purpose="user_data")`.
Cache `{sha256: file_id}` in `cache/files.json`. The docs say the upload is synchronous (a 200 means
ready). Verify this against the docs.

**`meta/wonder.py`**: one function used everywhere:
```python
def call_json(model_cls: type[BaseModel], instructions: str, content: list[dict],
              effort: str = "high", max_output_tokens: int = 8000) -> BaseModel:
    """content = Responses API content parts, e.g.
       {"type":"input_text","text":...},
       {"type":"input_image","image_url":"data:image/jpeg;base64,..."},
       {"type":"input_video","file_id":...}   # verify Wonder accepts this; see fallback
    Calls client().responses.create(model=WONDER_MODEL, instructions=instructions,
         input=[{"type":"message","role":"user","content":content}],
         reasoning={"effort": effort}, max_output_tokens=max_output_tokens)
    Reads resp.output_text, extracts the first JSON object, validates with model_cls.
    On validation error: retry ONCE, appending the error text to the input. Then raise.
    Caches by hash(model, instructions, content)."""
```
- Try structured output (`text={"format":{"type":"json_schema",...}}`) if the docs say it's
  supported; otherwise use prompt-only JSON plus validation.
- **Video fallback:** if `input_video` is rejected for `muse-wonder-1.0`, send 6–8 keyframes as
  `input_image` parts instead. Log which path was used. Keyframes are the default for repair calls
  anyway, because they're cheaper.
- Log token usage from `resp.usage` to `data/usage.jsonl` for the cost metric.

**`meta/sam.py`**
- A concept prompt covers **one concept per request**, so send one request per noun phrase. Run
  concepts concurrently with `AsyncOpenAI` (bounded semaphore, e.g. 4).
- Request shape (verify):
  ```python
  async with aclient().responses.stream(
      model="sam-3.1",
      input=[{"type":"message","role":"user","content":[
          {"type":"input_text","text": concept},          # e.g. "red block"
          {"type":"input_video","file_id": file_id}]}],
      metadata={"include_confidence":"true", "mask_encoding":"one_bit"},
  ) as stream:
      async for ev in stream:
          if ev.type == "response.output_text.delta": lines.append(ev.delta)
  ```
- Output: one line per frame with a `<Nf>` frame marker. Frames can arrive **out of order**, so
  always key by the marker. Records look like `<0f>0<|box;x1=..;y1=..;x2=..;y2=..;w=..;h=..|>`
  followed by a mask. Use `meta-sam-parser` to parse. Also write a minimal regex fallback that
  extracts frame index, object id and box. **We only need boxes for logic** (centroids); masks are
  only for demo overlays.
- Return `Tracks = dict[obj_id, list[FrameBox(frame, x1, y1, x2, y2, conf)]]` per concept.
- Limits: MP4/MOV with H.264/H.265/MPEG-4; up to 15,000 frames; up to 16 tracked objects per frame.
  Video is billed per frame with a 50-frame minimum per request, so downscale fps before sending.

**`showtell smoke` command (Phase 0):**
1. Wonder text: `{"model":"muse-wonder-1.0","input":"Reply with the word ready."}` → prints "ready".
2. Wonder JSON: returns a valid `{"ok": true}` object through `call_json`.
3. Wonder video: tries `input_video` on a 5 s clip, then falls back to keyframes. Reports which works.
4. SAM: uploads `bench/videos/smoke.mp4` (or any short clip) and tracks "hand". Prints frame count,
   tracks found, and one parsed box.
5. Writes `data/smoke_report.json`.

### 5.2 Simulation (`sim/`)

**Scene (`scene.py`):** generate MJCF programmatically. Include
`third_party/mujoco_menagerie/franka_emika_panda/panda.xml` (inspect the file: note the
end-effector body name, joint and actuator names, and the gripper actuator; add an `ee` site at
the fingertip center if none exists). Add:
- Table: 1.2 × 0.8 m top at z = 0.4 m, matte material.
- 3 bins: open-top boxes made of 5 thin box geoms each, named `left_bin`, `middle_bin`,
  `right_bin`, along the far edge. Distinct muted colors.
- Objects (free joints), each with attributes `{id, size, color, shape, fragile}`:
  - Blocks: cube 5 cm (`small`) and 7 cm (`large`) in `red`, `blue`, `green`.
  - Cylinder: `yellow`, r = 3 cm, h = 7 cm (`shape=cylinder`).
  - Glass: tall thin cylinder, r = 3.5 cm, **h = 20 cm**, light-cyan, slightly translucent
    (`fragile=true`, `shape=glass`). Low mass so it tips when hit.
- Lighting: 2 directional lights + ambient; soft shadows; skybox neutral grey.
- Cameras: `overhead` (straight down, used by the SAM judge for bin assignment),
  `judge_angle` (45°, used by the SAM judge for glass displacement), `hero` (cinematic, demo only).

**Randomizer (`randomizer.py`):** `layout(task_objects, seed) -> list[(obj_id, x, y, yaw)]`.
Sample positions in the table's front 60%, reject overlaps (min 4 cm gap) and positions within
10 cm of bins. Always place the glass *between* the robot base's sweep and at least one object
(at a seeded random spot inside a central band) so naive low transit paths hit it.

**Skills (`skills.py`):** a `Robot` class that owns `model`/`data` and steps physics.
- `move_to(pose)`: pose is `(x, y, z, yaw)`. Use damped-least-squares IK on the `ee` site
  (`mujoco.mj_jacSite`, λ = 0.05, position + yaw error), iterate to joint targets, then interpolate
  `data.ctrl` toward those targets over ~1 s, stepping physics each control tick and recording
  frames. Raise `SkillError` if it fails to converge within 2 cm.
- **Transit height default = 0.12 m above the table.** This is deliberately lower than the glass
  (0.20 m). Naive plans that don't use `avoid` will genuinely collide.
- `pick(obj, avoid=None)`: open gripper → move above obj (transit height) → descend → close
  gripper → **kinematic attach**: each physics step while attached, set the object's free-joint
  qpos to the ee pose × stored offset and zero its qvel. Collisions of the carried object still
  happen. → lift to transit height.
- `place(bin_name, avoid=None)`: move above bin center (transit height) → descend → detach → open →
  lift.
- `avoid(obj, margin_m=0.08)` / the `avoid=` argument: every lateral move is planned as
  lift → lateral → descend. If the straight lateral segment passes within `margin_m` of any avoided
  object's xy, raise the transit height to `avoided.height + 0.10` for that segment and add a
  detour waypoint around it.
- `scene.objects(**attrs) -> list[Obj]`: filter by attributes. `Obj` exposes read-only `id`,
  `size`, `color`, `shape`, `fragile` and `position` (current xyz).
- Every skill appends frames to the active recorder (when recording) and logs a step trace.

**Ground-truth checks (`checks.py`):** a closed vocabulary that the spec compiles to:
- `in_bin(selector, bin)`: every object matching selector ends with its xy inside the bin
  interior and z below the bin rim.
- `untouched(selector)`: zero contacts between the selector's objects and anything except the
  table during the whole run (iterate `data.contact` each step; map geom → body → object).
- `upright(selector)`: the object's z-axis · world z > cos(20°) at the end.
- `unmoved(selector, tol_m=0.02)`: final xy within tol of initial xy.
- `selector` = attribute dict, e.g. `{"size":"large","fragile":false}`.
Each check returns `{check_id, passed, detail}`.

**Rendering (`render.py`):** `mujoco.Renderer(model, 480, 640)`. Write MP4 with H.264 via
imageio-ffmpeg using `-preset veryfast -crf 23 -movflags +faststart -pix_fmt yuv420p`.
- Per seed write: `full.mp4` (hero cam, 30 fps, for the UI) and **`judge.mp4` = exactly 50 frames
  at 25 fps from the overhead camera**: the first 25 frames of the run + the last 25 frames
  (SAM's 50-frame billing minimum). Also write `judge_angle.mp4` the same way.
- Benchmark renders at 640×480; demo renders at 1280×720 on the hero camera.

### 5.3 Why naive plans fail (honest demo failure)

The glass is taller than the default transit height and is placed between the base and some
objects. A program that doesn't pass `avoid=glass` sweeps through it in some seeds, so `untouched`
or `upright` fails there. A good repair adds `avoid=glass`. This is real physics, not a script. If
first-try programs *never* fail on task 3, tighten the randomizer band. Never inject failures.

### 5.4 Human demo videos (human films these; you validate them)

Guidance to show the user: phone mounted top-down about 80 cm above a plain table, good light,
1080p, under 30 s, one continuous take per task. Real stand-ins: blocks or cups in 2 sizes and 3
colors, a yellow cylinder (e.g. a marker cap or glue stick), a tall glass, 3 bowls in a row at the
far edge. File names: `bench/videos/task01_size.mp4`, etc. (see `bench/tasks.yaml`). Also film
`smoke.mp4`: 5 s of a hand moving one block.

`perception/video_prep.py` normalizes every input:
`ffmpeg -y -i IN -vf "scale=-2:720,fps=10" -an -c:v libx264 -preset veryfast -crf 23 -movflags +faststart OUT`
and extracts 8 evenly spaced keyframes (JPEG, 768 px wide).

### 5.5 Perception → track summary (`perception/track_summary.py`)

1. **Object discovery:** call Wonder with 4 keyframes. It returns
   `[{concept:"red block", attrs:{size?, color, shape, fragile}}]` plus `"bowl"` and `"hand"`.
2. **Track:** run SAM per concept on the normalized video.
3. **Summarize** per tracked object id: start centroid, end centroid, moved? (displacement > 5% of
   frame width), first-move frame, and end container (which bowl's box contains the end centroid;
   bowls are tracked too and ordered left to right → `left_bin`/`middle_bin`/`right_bin`).
   "Size" comes from relative box area within the same color/shape when Wonder is unsure.
4. Output `TrackSummary` JSON:
   `{"objects":[{"track_id","concept","attrs","moved","start_px","end_px","end_bin","move_order"}],
     "bins":["left_bin","middle_bin","right_bin"], "fps":10, "frames":N}`.

### 5.6 Agent (`agent/`)

**Schemas (`schemas.py`)**
```python
class Selector(BaseModel): size: str|None=None; color: str|None=None; shape: str|None=None; fragile: bool|None=None
class Rule(BaseModel): select: Selector; place_in: Literal["left_bin","middle_bin","right_bin"]|None = None; leave: bool=False
class Check(BaseModel): id: str; kind: Literal["in_bin","untouched","upright","unmoved"]; select: Selector; bin: str|None=None; text: str
class TaskSpec(BaseModel): rules: list[Rule]; checks: list[Check]; clarifying_question: str|None=None; rationale: str
class Program(BaseModel): source: str; notes: str
class RepairResult(BaseModel): diagnosis: str; source: str     # diagnosis = one plain sentence
```

**Prompts (`prompts.py`).** Keep each one as a module constant. System-style `instructions`:

- `SPEC_INSTRUCTIONS`: "You convert a human demonstration and a rule sentence into a robot task
  spec. Rules map object attributes to bins. Checks must be from this closed set: in_bin,
  untouched, upright, unmoved. Every rule must have a matching check. Constraints in the sentence
  ('don't touch', 'leave', 'be careful with') become untouched/upright/unmoved checks. Infer bin
  assignments from where each demonstrated object ended up. If the video and sentence conflict,
  the sentence wins. If something critical is ambiguous, set clarifying_question; otherwise null.
  Output only JSON matching this schema: {schema}."
  Content: track summary JSON + keyframes (or video) + the sentence + the sim object vocabulary
  (allowed attribute values).
- `PROGRAM_INSTRUCTIONS`: "Write a Python function `def run(scene):` that achieves the spec using
  ONLY this API: {skill_api_doc}. Select objects with scene.objects(**attrs); never use
  coordinates. No imports, no other functions, no while loops. Output JSON {source, notes}."
- `REPAIR_INSTRUCTIONS`: "A robot program failed some test scenes. You get: the spec, the program,
  the failed checks per seed with details, and frames from one failing scene. Diagnose the root
  cause in one plain sentence, then return a corrected program that keeps passing scenes passing.
  Output JSON {diagnosis, source}."

The skill API doc string given to the model must exactly match `skills.py` signatures (generate it
from docstrings so they can't drift).

**Sandbox (`sandbox.py`)**
- Parse with `ast`. Allow only: one `FunctionDef` named `run(scene)`; `For`, `If`, `Assign`,
  `Expr`, `Return`, `Compare`, `BoolOp`, `Name`, `Constant`, `List`, `Tuple`, `keyword`,
  `Attribute` (only `scene.objects` and Obj fields), and `Call` to the whitelist:
  `scene.objects, pick, place, move_to, avoid, len, sorted, range, min, max`. Reject dunder names,
  `Import`, `While`, `Lambda`, `With`, `Try`, comprehensions with side effects, and `global`.
- Execute in a **subprocess** (`multiprocessing`, spawn) with `{"__builtins__": {}}` plus the skill
  functions, and a 120 s wall-clock timeout per seed. Return a structured result even on crash.

### 5.7 Verification (`verify/`)

- `runner.run(spec, program, seeds, record=True)` runs seeds in a process pool (CPU count − 1).
  Per seed it builds the scene → executes the program → evaluates ground-truth checks → writes
  videos. Returns `SeedResult{seed, passed, checks[], error, video paths, sim_seconds}`.
- `judge_sam.judge(seed_dir, spec)`: run SAM on `judge.mp4` (overhead) for each object concept
  ("red block", …, "glass"). Project the bin interiors to overhead pixels with the camera
  intrinsics/extrinsics (compute once; store in scene metadata). Map each object's final-frame box
  centroid to a bin. `untouched`/`unmoved` for the glass = displacement of its box centroid between
  frame 0 and frame 49 in both cameras < threshold. `upright` = in `judge_angle`, the glass box
  aspect ratio (h/w) stays > 70% of its initial value. Output SAM verdicts per check.
- `agreement.py`: per check kind, percentage of (seed, check) pairs where SAM verdict == ground
  truth. **Ground truth decides pass/fail; SAM is reported as the camera-ready judge, plus agreement.**
- Default N = 20 seeds; seeds are fixed per task (`seed = task_index*1000 + i`) so conditions are
  comparable.

### 5.8 Pipeline (`pipeline.py`)

```
prep video → discover objects → SAM tracks → track summary
→ spec (pause for approval if interactive; auto-approve in CLI/bench unless --interactive)
→ program → sandbox check
→ attempt 0: verify on 20 seeds (+ SAM judge)
→ while failures and attempt < 3: repair (failed checks + 8 frames from the first failing seed) → verify all 20
→ report (JSON + Markdown): spec, final program, per-attempt pass counts, diagnoses, agreement, cost, wall time
```
A `--no-video` flag builds the spec from the sentence only (the ablation). Track discovery is
skipped, and Wonder gets only the sentence + object vocabulary.

Every stage emits events (`stage_started`, `seed_done`, `attempt_done`, …) to a callback so the API
can stream progress.

### 5.9 Backend (`api/`)

SQLModel tables: `Job(id, created_at, status, rules_text, use_video, video_path, spec_json,
approved, report_json)`, `Attempt(id, job_id, n, program_src, diagnosis, passed, total)`,
`SeedRun(id, attempt_id, seed, passed, checks_json, sam_json, full_video, judge_video)`.

Endpoints:
- `POST /api/jobs` (multipart: `video?`, `rules`, `use_video`, `auto_approve`) → `{job_id}`
- `GET /api/jobs/{id}` → job + attempts + seed runs
- `POST /api/jobs/{id}/spec` (body: edited TaskSpec or `{"approve": true}`) → resumes pipeline
- `GET /api/jobs/{id}/events` → Server-Sent Events stream of pipeline events
- `GET /api/bench` → `bench/results.json`
- Static `/media/...` → videos under `SHOWTELL_DATA_DIR`
Jobs run in a background worker (a `ThreadPoolExecutor` that drives the pipeline; the sim pool
lives inside). CORS for the web origin.

### 5.10 Web app (`web/`)

Vite + React + TypeScript + Tailwind. Clean, minimal, product-grade (not a hackathon look).
- **New task:** video drop zone (optional), rules text box, "Use video" toggle, Start.
- **Job page:**
  - Spec card: rules and checks as readable sentences, editable; a clarifying question if present;
    an Approve button.
  - Attempt timeline: Attempt 0 → Repair 1 → …, each with "k/20 passed" and the one-line diagnosis.
  - 20-scene grid: video thumbnails with green/red borders. Click one to open the full video, its
    checks and the SAM verdict next to ground truth.
  - Program panel: current program with a diff against the previous attempt.
  - "VERIFIED" badge when 20/20, plus downloads for the report (Markdown/JSON) and program.
- **Benchmark page:** renders the results table from `/api/bench`.
- **Demo mode:** `VITE_DEMO=1` reads precomputed jobs from `web/public/demo/*.json` and videos from
  `web/public/demo/media/`, and replays events with realistic timing. `showtell export-demo
  --job ID` produces these files.

### 5.11 Benchmark (`bench/`)

`bench/tasks.yaml`:
```yaml
- id: task01_size      ; video: task01_size.mp4  ; rules: "Sort by size: big ones in the left bowl, small ones in the right bowl."
- id: task02_color     ; video: task02_color.mp4 ; rules: "Sort by color, one bowl per color."
- id: task03_size_glass; video: task03_size_glass.mp4; rules: "Sort by size, and don't touch the glass."
- id: task04_clear     ; video: task04_clear.mp4 ; rules: "Clear everything into the middle bowl, but leave the glass alone."
- id: task05_shape     ; video: task05_shape.mp4 ; rules: "Cylinders in the left bowl, cubes in the right bowl."
- id: task06_color_exc ; video: task06_color_exc.mp4; rules: "Sort by color, but blue things stay where they are."
```
(Write this as valid YAML; the `;` form above is shorthand.) Each task also lists the sim objects
present.

`run_bench.py` runs every task twice (video+sentence, sentence only), with the same seeds, and writes
`bench/results.json` and `bench/results.md` with columns:
`Task | First try (video) | Final (video) | Repairs | Final (sentence only) | Judge agreement | Cost $ | Time`.
Pass rates are "k/20". Cost comes from `data/usage.jsonl` × published prices ($4 in / $20 out per
1M Wonder tokens; read the SAM per-frame price from the docs and make it configurable). It must be
resumable (skips tasks already in results) and support `--tasks` and `--seeds`.

### 5.12 Deploy

- `Dockerfile` for the backend: `python:3.11-slim`, `apt-get install ffmpeg libosmesa6`,
  `MUJOCO_GL=osmesa`, copy the repo plus the menagerie Panda folder, `uvicorn showtell.api.app:app`.
- The web app deploys to Vercel. The production deploy uses **demo mode** by default (robust); a
  `?live=1` query switches to the live backend URL if one is set.

---

## 6. Phases and acceptance checks (do in order, no dates)

### Phase 0 — Setup and API smoke test
Repo skeleton, `.env.example`, config, CLI shell, `meta/` modules, `showtell smoke`.
**Accept:** `python -m showtell smoke` passes the Wonder text and JSON checks and the SAM check;
video support is reported; `data/smoke_report.json` exists. Note the SAM output format you
observed in `PROGRESS.md`.

### Phase 1 — Scene and rendering
`sim/scene.py`, `randomizer.py`, `render.py`.
**Accept:** `python -m showtell render-scene --seed 1` writes PNGs from all 3 cameras and a 3 s MP4;
20 seeds produce valid, non-overlapping layouts (test); the scene looks clean in the hero camera.

### Phase 2 — Skills and hard-coded program (GO/NO-GO gate)
`skills.py`, `checks.py`, plus a hand-written `examples/size_sort.py` program.
**Accept:**
- The hand-written program *with* `avoid=glass` passes all checks on ≥ 18/20 seeds.
- The same program *without* `avoid` fails `untouched`/`upright` on ≥ 3/20 seeds (proves the demo
  failure is real).
- Tests cover IK convergence, attach/detach and each check.
**If blocked here:** simplify (top-down grasps only, larger bins, slower moves) before moving on.
This phase decides the project.

### Phase 3 — Perception and spec
`video_prep.py`, `track_summary.py`, `agent/schemas.py`, `prompts.py`, `spec.py`.
**Accept:** on `task03_size_glass.mp4`, the spec has correct size→bin rules plus an `untouched`
and/or `upright` check on the glass; the track summary JSON is saved; a `--no-video` spec is also
produced.

### Phase 4 — Program generation and verification
`program.py`, `sandbox.py`, `verify/runner.py`.
**Accept:** a generated program for task 1 runs in the sandbox on 20 seeds with a report; malicious
programs (imports, dunder access, while loops) are rejected (tests).

### Phase 5 — SAM judge, repair loop, full CLI
`judge_sam.py`, `agreement.py`, `repair.py`, `pipeline.py`.
**Accept:** `python -m showtell run --video bench/videos/task03_size_glass.mp4 --rules "Sort by
size, and don't touch the glass."` completes end to end and writes a report with per-attempt pass
counts, diagnoses and judge agreement. Ideally at least one repair happens on task 3. If not, check
the randomizer band (5.3), not the prompts.

### Phase 6 — API and web app
`api/`, `web/`.
**Accept:** a job runs from the browser: upload → spec approval → live grid updating via SSE →
attempts → verified badge → report download. Works on a fresh `uvicorn` + `npm run dev`.

### Phase 7 — Benchmark
`bench/`.
**Accept:** `bench/results.md` is filled with real numbers for tasks 1–6 in both conditions; the
README table is generated from it.

### Phase 8 — Demo mode, polish, deploy
Hero-camera renders at 720p for the demo job, `export-demo`, demo mode, Dockerfile, Vercel deploy.
**Accept:** the Vercel URL replays the task-3 job (failure → diagnosis → 20/20) with no backend;
`docker build` succeeds.

### Phase 9 — Submission assets
- README: pitch, architecture diagram (Mermaid), which Meta model does what, setup, results table,
  limitations (sim only; real-arm pilot next), and a "Built with Muse Spark Open" line *only if*
  it was used.
- `docs/demo_script.md`: shot list for the under-3-minute video (failure → catch → repair → 20/20 →
  rule change → results → vision).
- `docs/submission.md`: the write-up with real numbers filled in.
**Accept:** a fresh clone + README steps reproduce a run; every number in the README traces to
`bench/results.json`.

---

## 7. Budget guardrails

- Hackathon credits are about $150. Track spend in `data/usage.jsonl`. `showtell cost` prints the
  total.
- Wonder estimate: about $1–1.50 per verified task (spec + program + ≤ 3 repairs). Use keyframes,
  not full video, for repair calls.
- SAM: 50-frame judge clips only; cache by file hash; one request per concept.
- Stop and warn in `PROGRESS.md` if spend passes $100.

## 8. Troubleshooting

- **404 / "model not found":** wrong model id or a trailing slash in the base URL. Use
  `https://api.meta.ai/v1` exactly.
- **Can't create an API key:** a billing method is required in the dashboard (human task).
- **SAM returns nothing for a concept:** try a more visual phrase ("red cube" vs "red block"),
  increase lighting contrast, or check that the video is H.264 MP4 with faststart.
- **SAM frames out of order:** expected. Always index by the `<Nf>` marker.
- **IK doesn't converge:** keep targets inside the reachable band, use top-down orientation only,
  and raise the iteration count.
- **Renders are black or crash:** set `MUJOCO_GL` correctly (egl on GPU, osmesa on CPU).

## 9. Optional: run Claude Code on Muse Wonder

Meta's API also serves an Anthropic-compatible Messages endpoint (`ANTHROPIC_BASE_URL=https://api.meta.ai`,
no `/v1`). Coding on Muse can be mentioned in the write-up as further use of Meta's tools.
See https://dev.meta.ai/docs/coding-agents. This is optional and not required for the build.
