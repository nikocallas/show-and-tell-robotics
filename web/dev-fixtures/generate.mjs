#!/usr/bin/env node
// Writes the dev fixtures in this folder: index.json, sample-job/{report,events}.json, tasks.json,
// bench.json.
//
// SAMPLE DATA - NOT A REAL RUN. Hand-made, deterministic data for developing the web UI without a
// backend or an API key. The shapes follow showtell/contracts.py; the numbers mean nothing. The
// Vite dev server serves this folder only with VITE_DEV_FIXTURE=1, and it is never copied into a
// build (it lives outside web/public on purpose).
//
// Usage: node dev-fixtures/generate.mjs   (or: npm run fixtures)

import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const NOTE = "Sample data - not a real run (web/dev-fixtures, for UI development only)";
const JOB_ID = "sample-job"; // folder name: dev-fixtures/sample-job/
const TASK_ID = "task03_size_glass";
const T0 = Date.UTC(2026, 9, 30, 15, 0, 0) / 1000;
const RULES = "Sort by size, and don't touch the glass.";
const GLASS = "clear_glass_large";
const LARGE = ["red_cube_large", "blue_cube_large"];
const SMALL = ["red_cube_small", "green_cube_small"];
const OBJECTS = [...LARGE, ...SMALL, GLASS];
const SEEDS = Array.from({ length: 20 }, (_, i) => 3000 + i);

let rng = 20261030;
const rand = () => {
  rng = (rng * 1103515245 + 12345) % 2147483648;
  return rng / 2147483648;
};
const between = (a, b) => a + (b - a) * rand();
const r2 = (x) => Math.round(x * 100) / 100;
const r3 = (x) => Math.round(x * 1000) / 1000;

const sel = (o) => ({ size: null, color: null, shape: null, fragile: null, ...o });
const SPEC = {
  rules: [
    { select: sel({ size: "large", fragile: false }), place_in: "left_bin", leave: false },
    { select: sel({ size: "small", fragile: false }), place_in: "right_bin", leave: false },
    { select: sel({ shape: "glass" }), place_in: null, leave: true },
  ],
  checks: [
    { id: "large_left", kind: "in_bin", select: sel({ size: "large", fragile: false }), bin: "left_bin", text: "Every large block ends in the left bin." },
    { id: "small_right", kind: "in_bin", select: sel({ size: "small", fragile: false }), bin: "right_bin", text: "Every small block ends in the right bin." },
    { id: "glass_untouched", kind: "untouched", select: sel({ shape: "glass" }), bin: null, text: "Nothing touches the glass." },
    { id: "glass_upright", kind: "upright", select: sel({ shape: "glass" }), bin: null, text: "The glass is still standing." },
  ],
  clarifying_question: null,
  rationale:
    "In the demonstration both large blocks went into the left bowl and both small blocks into the right bowl, while the hand carried them around the tall glass. The sentence says not to touch the glass, so it is left in place with untouched and upright checks.",
};

const PROGRAM_0 = `def run(scene):
    for obj in scene.objects(size="large", fragile=False):
        pick(obj)
        place("left_bin")
    for obj in scene.objects(size="small", fragile=False):
        pick(obj)
        place("right_bin")
`;
const NOTES_0 = "Large non-fragile blocks go to the left bin and small ones to the right bin; fragile=False keeps the glass out of both loops.";
const PROGRAM_1 = `def run(scene):
    glass = scene.objects(shape="glass")
    for obj in scene.objects(size="large", fragile=False):
        pick(obj, avoid=glass)
        place("left_bin", avoid=glass)
    for obj in scene.objects(size="small", fragile=False):
        pick(obj, avoid=glass)
        place("right_bin", avoid=glass)
`;
const DIAGNOSIS =
  "The arm carries blocks at the 0.12 m transit height, below the top of the 0.20 m glass, so the carried block sweeps into the glass on its way to a bin; passing avoid=glass sends every move over and around it.";

const TRACK = {
  objects: [
    { track_id: "large red block#1", concept: "large red block", attrs: { size: "large", color: "red", shape: "cube", fragile: false }, moved: true, start_px: [402.5, 498.0], end_px: [301.0, 118.5], end_bin: "left_bin", move_order: 0, first_move_frame: 21 },
    { track_id: "large blue block#2", concept: "large blue block", attrs: { size: "large", color: "blue", shape: "cube", fragile: false }, moved: true, start_px: [866.0, 531.5], end_px: [322.5, 131.0], end_bin: "left_bin", move_order: 1, first_move_frame: 64 },
    { track_id: "small red block#3", concept: "small red block", attrs: { size: "small", color: "red", shape: "cube", fragile: false }, moved: true, start_px: [512.0, 590.5], end_px: [968.5, 124.0], end_bin: "right_bin", move_order: 2, first_move_frame: 109 },
    { track_id: "small green block#4", concept: "small green block", attrs: { size: "small", color: "green", shape: "cube", fragile: false }, moved: true, start_px: [1010.5, 566.0], end_px: [951.0, 140.5], end_bin: "right_bin", move_order: 3, first_move_frame: 151 },
    { track_id: "glass#5", concept: "glass", attrs: { size: null, color: "clear", shape: "glass", fragile: true }, moved: false, start_px: [640.0, 372.0], end_px: [641.5, 371.0], end_bin: null, move_order: null, first_move_frame: null },
  ],
  bins: ["left_bin", "middle_bin", "right_bin"],
  fps: 10,
  frames: 214,
  width: 1280,
  height: 720,
  notes: [],
};

// ------------------------------------------------------------------ one scene
const ATTEMPT0 = {
  pass: new Set([3003, 3011, 3016]),
  error: 3014,
  tipped: new Set([3001, 3002, 3005, 3007, 3009, 3012, 3015, 3018]),
  brushed: new Set([3004, 3013]), // touched but barely moved: SAM can't see it
  samUprightMiss: 3009, // tipped against a bin wall: SAM still sees a tall box
};

function binOf(obj) {
  return LARGE.includes(obj) ? "left_bin" : "right_bin";
}

function scene(attempt, seed) {
  const repaired = attempt > 0;
  const per = repaired ? between(12, 14) : between(9, 11);
  const order = [...LARGE, ...SMALL];
  const touches = [];
  const touchedBy = new Set();
  let tipped = false;
  let error = null;
  const failing = !repaired && !ATTEMPT0.pass.has(seed) && seed !== ATTEMPT0.error;
  const hitIndex = Math.floor(rand() * 4);

  order.forEach((obj, k) => {
    if (error) return;
    const t = 2.0 + k * per;
    if (!repaired && seed === ATTEMPT0.error && k === 2) {
      error = `SkillError: pick(${obj}): could not reach a grasp pose (IK error 3.4 cm > 2 cm)`;
      return;
    }
    touches.push({ t: r3(t + 2.4), obj, by: "robot", during: `pick(${obj}): grasping` });
    if (failing && k === hitIndex) {
      touches.push({ t: r3(t + 5.1), obj: GLASS, by: obj, during: `place(${binOf(obj)}) carrying ${obj}: moving above the bin` });
      touchedBy.add(obj);
      if (rand() < 0.4) {
        touches.push({ t: r3(t + 5.3), obj: GLASS, by: "robot", during: `place(${binOf(obj)}) carrying ${obj}: moving above the bin` });
        touchedBy.add("robot");
      }
      if (ATTEMPT0.tipped.has(seed)) {
        tipped = true;
        const victim = SMALL.find((s) => s !== obj) ?? SMALL[0];
        touches.push({ t: r3(t + 5.9), obj: GLASS, by: victim, during: `place(${binOf(obj)}) carrying ${obj}: moving above the bin` });
        touches.push({ t: r3(t + 5.9), obj: victim, by: GLASS, during: `place(${binOf(obj)}) carrying ${obj}: moving above the bin` });
        touchedBy.add(victim);
      }
    }
    touches.push({ t: r3(t + 7.8), obj, by: binOf(obj), during: `place(${binOf(obj)}) carrying ${obj}: descending` });
  });

  const smallDone = !error;
  const checks = [
    { check_id: "large_left", kind: "in_bin", passed: true, detail: "all in left_bin", objects: LARGE },
    {
      check_id: "small_right",
      kind: "in_bin",
      passed: smallDone,
      detail: smallDone ? "all in right_bin" : SMALL.map((s) => `${s} ended in table`).join("; "),
      objects: SMALL,
    },
    {
      check_id: "glass_untouched",
      kind: "untouched",
      passed: touchedBy.size === 0,
      detail: touchedBy.size ? `${GLASS} was touched by ${[...touchedBy].sort().join(", ")}` : "nothing touched them",
      objects: [GLASS],
    },
    {
      check_id: "glass_upright",
      kind: "upright",
      passed: !tipped,
      detail: tipped ? `${GLASS} tilted ${Math.round(between(62, 89))} deg` : "all upright",
      objects: [GLASS],
    },
  ];

  // SAM verdicts on the rendered judge clips (mostly agreeing, a few honest misses).
  const sam = checks.map((c) => {
    if (c.kind === "in_bin") {
      if (seed === 3008 && c.check_id === "small_right") {
        return { check_id: c.check_id, kind: c.kind, passed: null, detail: "small green block not found in the last overhead frame" };
      }
      return { check_id: c.check_id, kind: c.kind, passed: c.passed, detail: c.passed ? "every box centroid inside the bin" : "red block centroid outside every bin in the last frame" };
    }
    if (c.kind === "untouched") {
      if (!c.passed && !repaired && ATTEMPT0.brushed.has(seed) && !tipped) {
        return { check_id: c.check_id, kind: c.kind, passed: true, detail: "glass centroid moved 2 px overhead, 1 px at 45° (threshold 6 px)" };
      }
      const px = c.passed ? 1 : Math.round(between(18, 44));
      return { check_id: c.check_id, kind: c.kind, passed: c.passed, detail: `glass centroid moved ${px} px overhead, ${Math.max(1, Math.round(px * 0.6))} px at 45°` };
    }
    // upright
    if (repaired && seed === 3017) {
      return { check_id: c.check_id, kind: c.kind, passed: null, detail: "glass not found at 45° in the last frame" };
    }
    if (!c.passed && seed === ATTEMPT0.samUprightMiss) {
      return { check_id: c.check_id, kind: c.kind, passed: true, detail: "box height/width at 74% of the first frame (threshold 70%)" };
    }
    return { check_id: c.check_id, kind: c.kind, passed: c.passed, detail: `box height/width at ${c.passed ? Math.round(between(94, 100)) : Math.round(between(31, 52))}% of the first frame` };
  });

  touches.sort((a, b) => a.t - b.t);
  return {
    summary: {
      seed,
      passed: !error && checks.every((c) => c.passed),
      checks,
      error,
      videos: {},
      sim_seconds: r2(2 + 4 * per + 3.5),
      touch_events: touches.slice(0, 12),
      sam: null,
    },
    sam,
  };
}

function agreementOf(scenes) {
  const out = { agree: 0, n: 0, undecided: 0, by_kind: {} };
  for (const { summary, sam } of scenes) {
    for (const c of summary.checks) {
      const v = sam.find((x) => x.check_id === c.check_id);
      if (!v) continue;
      if (v.passed === null) {
        out.undecided++;
        continue;
      }
      const k = (out.by_kind[c.kind] ??= { agree: 0, n: 0 });
      k.n++;
      out.n++;
      if (v.passed === c.passed) {
        k.agree++;
        out.agree++;
      }
    }
  }
  return out;
}

function mergeAgreement(list) {
  const out = { agree: 0, n: 0, undecided: 0, by_kind: {} };
  for (const a of list) {
    out.agree += a.agree;
    out.n += a.n;
    out.undecided += a.undecided;
    for (const [k, v] of Object.entries(a.by_kind)) {
      const t = (out.by_kind[k] ??= { agree: 0, n: 0 });
      t.agree += v.agree;
      t.n += v.n;
    }
  }
  return out;
}

// ------------------------------------------------------------------ the event log
const events = [];
let t = T0;
const at = (type, data, ts) => events.push({ type, ts: r3(ts), job_id: JOB_ID, data });
const ev = (type, data, dt = 0) => {
  t += dt;
  at(type, data, t);
};

ev("job_started", { rules: RULES, use_video: true, task_id: TASK_ID, n_seeds: SEEDS.length });
for (const [stage, secs] of [["prep", 3.1], ["discover", 9.6], ["track", 27.4], ["summary", 0.4]]) {
  ev("stage_started", { stage }, 0.05);
  ev("stage_done", { stage, seconds: secs }, secs);
}
ev("track_summary", { track_summary: TRACK }, 0.01);
ev("log", { message: "Muse Wonder read 8 keyframes of the demo for the spec." }, 0.2);
ev("stage_started", { stage: "spec" }, 0.05);
ev("stage_done", { stage: "spec", seconds: 21.8 }, 21.8);
ev("spec_ready", { spec: SPEC, source: "video", media_path: "keyframes" }, 0.01);
ev("stage_started", { stage: "approval" }, 0.01);
ev("awaiting_approval", { spec: SPEC, clarifying_question: null }, 0.01);
ev("stage_done", { stage: "approval", seconds: 34.2 }, 34.2);
ev("spec_approved", { spec: SPEC, edited: false }, 0.01);
ev("stage_started", { stage: "program" }, 0.02);
ev("stage_done", { stage: "program", seconds: 16.9 }, 16.9);
ev("program_ready", { attempt: 0, source: PROGRAM_0, notes: NOTES_0, kind: "initial" }, 0.01);

function runAttempt(n, kind, program, notes, diagnosis) {
  const start = t + 0.05;
  at("attempt_started", { attempt: n, seeds: SEEDS }, start);
  at("stage_started", { stage: "verify", attempt: n }, start + 0.01);
  // a pool of 7 workers, seeds handed out in order
  const workers = Array(7).fill(start + 0.5);
  const scenes = SEEDS.map((seed) => ({ seed, ...scene(n, seed) }));
  const timed = [];
  for (const sc of scenes) {
    const w = workers.indexOf(Math.min(...workers));
    const done = workers[w] + between(n ? 44 : 36, n ? 58 : 50);
    workers[w] = done;
    timed.push({ ts: done, type: "seed_done", data: { attempt: n, seed: sc.summary } });
    timed.push({ ts: done + between(6, 12), type: "judge_done", data: { attempt: n, seed: sc.seed, verdicts: sc.sam } });
  }
  timed.sort((a, b) => a.ts - b.ts);
  const verifyEnd = Math.max(...timed.filter((x) => x.type === "seed_done").map((x) => x.ts));
  for (const x of timed.filter((x) => x.ts <= verifyEnd)) at(x.type, x.data, x.ts);
  at("stage_done", { stage: "verify", attempt: n, seconds: r2(verifyEnd - start) }, verifyEnd + 0.01);
  at("stage_started", { stage: "judge", attempt: n }, verifyEnd + 0.02);
  const rest = timed.filter((x) => x.ts > verifyEnd);
  for (const x of rest) at(x.type, x.data, x.ts);
  const judgeEnd = Math.max(verifyEnd + 0.05, ...rest.map((x) => x.ts)) + 0.02;
  at("stage_done", { stage: "judge", attempt: n, seconds: r2(judgeEnd - verifyEnd) }, judgeEnd);
  const agreement = agreementOf(scenes);
  const passed = scenes.filter((s) => s.summary.passed).length;
  t = judgeEnd + 0.05;
  at("attempt_done", {
    attempt: n,
    passed,
    total: scenes.length,
    failed_seeds: scenes.filter((s) => !s.summary.passed).map((s) => s.seed),
    agreement,
    diagnosis,
  }, t);
  return {
    n,
    kind,
    program,
    notes,
    diagnosis,
    passed,
    total: scenes.length,
    seeds: scenes.map((s) => ({ ...s.summary, sam: s.sam })),
    agreement,
    wall_seconds: r2(t - start),
  };
}

const a0 = runAttempt(0, "initial", PROGRAM_0, NOTES_0, null);
ev("repair_started", { attempt: 1 }, 0.05);
ev("stage_started", { stage: "repair", attempt: 1 }, 0.01);
ev("stage_done", { stage: "repair", attempt: 1, seconds: 24.7 }, 24.7);
ev("repair_done", { attempt: 1, diagnosis: DIAGNOSIS, source: PROGRAM_1 }, 0.01);
ev("program_ready", { attempt: 1, source: PROGRAM_1, notes: "", kind: "repair", diagnosis: DIAGNOSIS }, 0.01);
const a1 = runAttempt(1, "repair", PROGRAM_1, "", DIAGNOSIS);

const report = {
  _note: NOTE,
  job_id: JOB_ID,
  created_at: T0,
  rules: RULES,
  use_video: true,
  task_id: TASK_ID,
  object_ids: OBJECTS,
  video: null,
  spec: SPEC,
  spec_source: "video",
  media_path: "keyframes",
  track_summary: TRACK,
  attempts: [a0, a1],
  status: "verified",
  final_program: PROGRAM_1,
  final_passed: a1.passed,
  final_total: a1.total,
  agreement: mergeAgreement([a0.agreement, a1.agreement]),
  cost: { usd: 0.71, wonder_usd: 0.57, sam_usd: 0.14, calls: 29 },
  wall_seconds: 0,
  error: null,
  seeds: SEEDS,
};
ev("stage_started", { stage: "report" }, 0.05);
ev("stage_done", { stage: "report", seconds: 0.3 }, 0.3);
report.wall_seconds = r2(t - T0);
ev("job_done", { status: "verified", report }, 0.01);

// ------------------------------------------------------------------ presets and bench
// GET /api/tasks rows. has_video = bench/videos/<video> exists (here: task03 only, to exercise both).
const TASKS = [
  { id: "task01_size", index: 1, video: "task01_size.mp4", has_video: false, rules: "Sort by size: big ones in the left bowl, small ones in the right bowl.", objects: ["red_cube_large", "blue_cube_large", "red_cube_small", "green_cube_small"], demo: "Move both big blocks into the left bowl and both small blocks into the right bowl." },
  { id: "task02_color", index: 2, video: "task02_color.mp4", has_video: false, rules: "Sort by color, one bowl per color.", objects: ["red_cube_large", "red_cube_small", "blue_cube_large", "green_cube_small"], demo: "Red blocks into the left bowl, the blue block into the middle bowl, the green block into the right bowl." },
  { id: "task03_size_glass", index: 3, video: "task03_size_glass.mp4", has_video: true, rules: RULES, objects: OBJECTS, demo: "Big blocks into the left bowl, small blocks into the right bowl; carry them around the tall glass without touching it." },
  { id: "task04_clear", index: 4, video: "task04_clear.mp4", has_video: false, rules: "Clear everything into the middle bowl, but leave the glass alone.", objects: ["red_cube_large", "blue_cube_small", "yellow_cylinder_large", "clear_glass_large"], demo: "Every block and the yellow cylinder go into the middle bowl; the glass stays where it is." },
  { id: "task05_shape", index: 5, video: "task05_shape.mp4", has_video: false, rules: "Cylinders in the left bowl, cubes in the right bowl.", objects: ["yellow_cylinder_large", "yellow_cylinder_large_2", "red_cube_small", "blue_cube_large"], demo: "Both yellow cylinders into the left bowl, both blocks into the right bowl." },
  { id: "task06_color_exc", index: 6, video: "task06_color_exc.mp4", has_video: false, rules: "Sort by color, but blue things stay where they are.", objects: ["red_cube_large", "green_cube_small", "blue_cube_large", "blue_cube_small"], demo: "The red block into the left bowl, the green block into the right bowl; both blue blocks stay put." },
];

// bench/results.json in the shape bench/run_bench.py writes: {tasks: {task_id: {video, sentence}},
// updated_at, n_seeds}. Covers a full run, a failed condition, an "error" run (no rates) and a
// "missing_video" condition. Pass rates are [passed, total] against the reference checks.
const PROGRAM_STUB = 'def run(scene):\n    for obj in scene.objects(size="large"):\n        pick(obj)\n        place("left_bin")\n';
function benchEntry(task, cond, o) {
  if (o.status === "missing_video") return { status: "missing_video", video: `bench/videos/${task.video}` };
  const repairs = o.repairs ?? 0;
  const attempts = Array.from({ length: o.first ? repairs + 1 : 0 }, (_, n) => ({
    n,
    passed: n === repairs ? o.final[0] : o.first[0] + Math.round(((o.final[0] - o.first[0]) * n) / Math.max(1, repairs)),
    total: o.first[1],
    diagnosis: n === 0 ? null : "Sample diagnosis (not a real run).",
  }));
  const agreement = cond === "video" && o.agree ? { agree: o.agree[0], n: o.agree[1], undecided: 0, by_kind: {} } : null;
  return {
    job_id: `bench-${task.id}-${cond}-20261030-160000`,
    status: o.status ?? (o.final && o.final[0] === o.final[1] ? "verified" : "failed"),
    error: o.error ?? null,
    repairs,
    attempts,
    cost_usd: o.cost,
    wall_s: o.wall,
    agreement_pct: agreement ? (100 * agreement.agree) / agreement.n : null,
    agreement,
    spec: null,
    media_path: cond === "video" ? "keyframes" : "none",
    ...(o.first ? { ref_first: o.first, ref_final: o.final, final_program: PROGRAM_STUB } : {}),
  };
}
const BENCH_RUNS = [
  [TASKS[0], { first: [18, 20], final: [20, 20], repairs: 1, cost: 0.41, wall: 655.2, agree: [77, 80] }, { first: [20, 20], final: [20, 20], cost: 0.11, wall: 301.4 }],
  [TASKS[1], { first: [20, 20], final: [20, 20], cost: 0.24, wall: 402.9, agree: [76, 80] }, { first: [9, 20], final: [11, 20], repairs: 3, cost: 0.31, wall: 911.0 }],
  [TASKS[2], { first: [3, 20], final: [20, 20], repairs: 1, cost: 0.58, wall: 612.5, agree: [93, 100] }, { first: [4, 20], final: [19, 20], repairs: 3, cost: 0.52, wall: 1180.3 }],
  [TASKS[3], { first: [5, 20], final: [20, 20], repairs: 2, cost: 0.9, wall: 1034.0, agree: [91, 100] }, { status: "error", error: "WonderJSONError: no JSON object in the reply (sample)", cost: 0.05, wall: 40.2 }],
  [TASKS[4], { first: [20, 20], final: [20, 20], cost: 0.22, wall: 388.7, agree: [80, 80] }, { first: [20, 20], final: [20, 20], cost: 0.08, wall: 250.0 }],
  [TASKS[5], { status: "missing_video" }, { first: [0, 20], final: [0, 20], repairs: 3, cost: 0.29, wall: 955.5 }],
];
const BENCH = {
  _note: NOTE,
  tasks: Object.fromEntries(
    BENCH_RUNS.map(([task, video, sentence]) => [
      task.id,
      { video: benchEntry(task, "video", video), sentence: benchEntry(task, "sentence", sentence) },
    ]),
  ),
  updated_at: "2026-10-30 17:00:00",
  n_seeds: 20,
};

const INDEX = {
  _note: NOTE,
  jobs: [
    {
      id: JOB_ID,
      title: "Sample data - not a real run",
      rules: RULES,
      task_id: TASK_ID,
      status: "verified",
      final_passed: a1.passed,
      final_total: a1.total,
      attempts: 2,
      use_video: true,
      created_at: T0,
      wall_seconds: report.wall_seconds,
    },
  ],
};

const write = (rel, data) => {
  const p = join(HERE, rel);
  mkdirSync(dirname(p), { recursive: true });
  writeFileSync(p, JSON.stringify(data, null, 1) + "\n");
  console.log(`wrote ${rel}`);
};
write("index.json", INDEX);
write("sample-job/report.json", report);
write("sample-job/events.json", { _note: NOTE, events });
write("tasks.json", TASKS);
write("bench.json", BENCH);
console.log(`${events.length} events; attempt 0 ${a0.passed}/${a0.total}, attempt 1 ${a1.passed}/${a1.total}`);
