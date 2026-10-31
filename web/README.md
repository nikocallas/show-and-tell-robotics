# Show & Tell web app

Vite + React 18 + TypeScript + Tailwind CSS 3 + react-router. It is the browser front end for the
pipeline in `showtell/` (CLAUDE.md 5.10): upload a demo, approve the spec, watch 20 simulated test
scenes fill in live, read the attempts and the report. It can also replay recorded runs with no
backend at all (demo mode), which is what the hosted Vercel deploy shows.

| Page | Route | What it shows |
| --- | --- | --- |
| New task | `/new` | Optional demo video (MP4/MOV), rule sentence with benchmark presets, scene preset, "Use the video", "Review the spec", scenes per attempt, Start |
| Job | `/jobs/:id` | Live job: stage strip, spec card (inline edit + approve), attempt timeline with diagnoses, 20-scene grid, scene modal (video, ground truth vs SAM 3.1, contacts), program with diff, VERIFIED badge, downloads |
| Replay | `/replay/:id` | The same job view driven by a recorded event log, with play/pause, restart, skip and 1x/2x/4x |
| Runs | `/runs` | Recorded runs from the demo bundle (plus jobs started from this browser in live mode) |
| Benchmark | `/bench` | `bench/results.json` as a table |

## Develop

```bash
cd web
npm install
npm run dev            # http://localhost:5173, proxies /api and /media to http://localhost:8000
```

Run the backend next to it: `uvicorn showtell.api.app:app --port 8000` from the repo root. Change the
proxy target with `VITE_PROXY_TARGET`.

### Without a backend: dev fixtures

```bash
npm run dev:fixture    # VITE_DEV_FIXTURE=1 vite
```

This serves `web/dev-fixtures/` (sample data, **not a real run**) as the demo bundle and mounts a mock
`/api` that replays the sample job over SSE and pauses for spec approval, so every screen, the live
path included, can be clicked through offline. Every page shows a "Sample data - not a real run"
banner in this mode. The fixtures live outside `public/` and the plugin only runs under `vite`
(never `vite build`), so they can't reach a deploy. Regenerate them with `npm run fixtures`
(`dev-fixtures/generate.mjs`, deterministic).

### Checks

```bash
npm run typecheck      # tsc for src/ and for vite.config.ts + dev-fixtures/plugin.ts
npm run build          # typecheck + production build into dist/
```

## Demo and live modes

| Mode | Data source | Default when |
| --- | --- | --- |
| Demo | `public/demo/` (static files) | production builds (`.env.production` sets `VITE_DEMO=1`) |
| Live | the FastAPI backend at `VITE_API_URL` (or same origin) | `npm run dev` |

`?live=1` switches to live (only when a backend is configured: `VITE_API_URL`, the dev proxy, or a
build with `VITE_DEMO=0`), `?demo=1` switches back; the header has the same switch. The choice is
remembered for the browser tab. Replays (`/replay/:id`) always read the demo bundle and live jobs
(`/jobs/:id`) always read the API, whatever the mode.

| Variable | Meaning |
| --- | --- |
| `VITE_DEMO` | `1` = demo mode by default |
| `VITE_API_URL` | Backend origin, e.g. `https://showtell-api.example.com` (no trailing slash). Empty = same origin |
| `VITE_PROXY_TARGET` | Dev server proxy target for `/api` and `/media` (default `http://localhost:8000`) |
| `VITE_DEV_FIXTURE` | `1` = dev fixtures + mock API (dev server only) |

All `VITE_*` values are compiled into the bundle; none of them is secret.

## Demo bundle (what `export-demo` writes)

`python -m showtell export-demo --job ID` (`showtell/api/export_demo.py`) writes into
`web/public/demo/`:

```
demo/index.json                [{"id", "rules", "status", "attempts", "created_at", "task_id",
                                 "use_video", "passed", "total"}], newest first
demo/<id>/report.json          JobReport (showtell/contracts.py), media paths rewritten to
                               "media/<id>/<path>"
demo/<id>/events.json          the job's events, oldest first, as a JSON array (same media paths)
demo/<id>/job.json             the GET /api/jobs/<id> shape (not read by the web app)
demo/<id>/report.md            offered as a download when present
demo/<id>/program.py           the final program
demo/media/<id>/<path>         videos, thumbnails, keyframes and repair frames at their job-folder
                               paths, e.g. demo/media/<id>/attempts/0/seed_3000/full.mp4
demo/bench.json                copy of bench/results.json, when it exists (Benchmark page)
demo/tasks.json                optional task presets [{id, rules, objects, demo}] (not exported)
```

Media paths starting with `media/` are relative to the bundle root, so the app loads
`/demo/media/<id>/<path>`. Live URLs (`/media/jobs/<id>/<path>`) and bare job-folder paths map to the
same place. Only `id` is required in `index.json` (a `{"jobs": [...]}` wrapper and `final_passed` /
`final_total` keys are accepted too); missing fields are read from the run's `report.json`. Events may
also be JSON Lines or `{"events": [...]}`. With no `index.json` the Runs page shows "No recorded runs
yet".

The replay applies the recorded events through the same reducer as the live stream, compressing long
gaps (at most 1.5 s between events at 1x, shorter for background events) and holding briefly after
key moments (spec ready, approval, attempt done, repair done). If a bundle has no event log the job
page shows the final report directly.

## Backend contract used

- `POST /api/jobs` multipart: `video?`, `rules`, `use_video`, `auto_approve`, `task_id?`, `seeds` ->
  `{job_id}`. With `use_video` on and no upload, the API runs on the task preset's recorded video
  (`bench/videos/<video>`, reported as `has_video` by `/api/tasks`); the New task page enables "Use
  the video" for such presets. Uploads over 300 MB get a 413, so the page refuses them first.
- `GET /api/jobs/{id}` -> `{job, attempts, report, media}` (`showtell/api/jobs.py`): `job` has
  `rules`, `video` (the demo video as a `/media/jobs/<id>/...` URL), `n_seeds`, `status`, `spec`, ...;
  each attempt has `program`, `passed`, `total`, `done`, `seeds`, ...
- `POST /api/jobs/{id}/spec` with the edited `TaskSpec`, or `{"approve": true}`
- `GET /api/jobs/{id}/events` Server-Sent Events: `event: <type>`, `data: <Event JSON>`; all past events
  are replayed on connect (the client de-duplicates, batches bursts, and falls back to polling the
  snapshot every 3 s if the stream can't connect)
- `GET /api/jobs/{id}/report.md`, `/report.json`, `/program.py`
- `GET /api/tasks` -> `[{id, rules, objects, demo, video, has_video}]`
- `GET /api/bench` -> `bench/results.json` as `bench/run_bench.py` writes it (`{tasks: {<task_id>:
  {video, sentence}}, updated_at, n_seeds}`); `src/lib/bench.ts` turns it into the same cells as
  `bench/results.md`
- `/media/...` videos; relative paths from events/reports are requested as `/media/jobs/<id>/<path>`

With a separate API origin the backend must allow CORS for the web origin (including the SSE route).

## Deploy to Vercel

1. Import the repo in Vercel and set **Root Directory** to `web` (framework preset: Vite).
2. `web/vercel.json` sets `npm ci`, `npm run build` and `dist`, and rewrites every path to
   `index.html` except `/demo/*` and `/assets/*`, so a missing demo file is a real 404 instead of the
   app's HTML.
3. Export the demo job into `web/public/demo/` and commit it (videos included; keep the bundle
   small: 640x480 judge clips, 720p hero clips).
4. Optional: set `VITE_API_URL` in the project's environment variables so `?live=1` can reach a live
   backend. Without it the deploy is demo-only.

Node 20.19+ is required (Vite 7).

## Layout

```
src/
  types.ts          TS mirrors of schemas.py / contracts.py / API responses
  state.ts          JobState + applyEvent reducer (shared by live SSE and replays), derived values
  api.ts            live client, SSE subscription, recent jobs (localStorage)
  demo.ts           demo bundle loader, media paths, replay timing, Replayer
  hooks.ts          useLiveJob, useReplayJob, useAsync
  config.ts, mode.tsx   env vars and the demo/live mode
  lib/              format (spec sentences, numbers), bench (results.json -> table rows),
                    diff (LCS line diff), python (tokenizer)
  components/       JobView, SpecCard, AttemptTimeline, SeedGrid, SeedModal, ProgramPanel,
                    StageStrip, VerifiedBadge, ReplayControls, Layout, ui, icons
  pages/            NewTask, Job (live + replay), Bench, DemoIndex
dev-fixtures/       sample data + mock API plugin (dev only, not a real run)
```
