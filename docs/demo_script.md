# Demo video script (under 3 minutes)

Story: failure → catch → repair → 20/20 → rule change → results → vision. Every clip is a real
run; screen recordings come from the web app in demo mode (replaying an exported job) so takes are
repeatable. Numbers in brackets are filled from `bench/results.json` and the exported demo job.

| Time | Shot | Voiceover |
|---|---|---|
| 0:00–0:12 | Phone footage, top-down: a hand sorts blocks into three bowls around a tall glass. Title: **Show & Tell Robotics**. | "Teaching a robot a new task takes an engineer days. What if you could just show it — once?" |
| 0:12–0:30 | Web app, New task: drop the video, type *"Sort by size, and don't touch the glass."*, Start. Cut to the SAM 3.1 boxes on hand, blocks and bowls. | "We upload one demo and one sentence. SAM 3.1 tracks every block, bowl and the hand, so we know what moved where." |
| 0:30–0:48 | Spec card: rules and checks as plain sentences — "Every large block ends in the left bin", "Nothing touches the glass". Click **Approve**. | "Muse Wonder turns that into tests first: rules, and pass/fail checks you can read and edit." |
| 0:48–1:12 | Program panel (the first program). The 20-scene grid fills in; red borders appear. Open a red scene: the carried block clips the glass, it falls. Check row: *untouched — failed*. | "Then it writes the robot program and we run it on twenty randomized scenes. It sorts perfectly — and knocks the glass over in [k] of them. Real physics, nothing staged." |
| 1:12–1:35 | Attempt timeline: the one-line diagnosis. Program diff highlights `avoid=glass`. Grid reruns, turns green, **VERIFIED 20/20**. | "Wonder reads the failed checks, the contact log and the frames: *[diagnosis]*. It patches the program — and all twenty pass." |
| 1:35–1:55 | Scene modal: ground truth vs the SAM verdict side by side; agreement chip [x%]. | "Every scene has two judges: the simulator, and SAM 3.1 looking only at the rendered video. They agree [x]% of the time." |
| 1:55–2:15 | New rule: *"Clear everything into the middle bowl, but leave the glass alone."* → new spec → verified grid. | "Change one sentence, and you get a new verified program in minutes." |
| 2:15–2:35 | Benchmark page: First try / Final / Sentence only columns, cost per task. | "Across six tasks: [first-try] on the first try, [final] after repair. Without the video, the sentence alone gets [sentence-only] — it can't know which bowl is which. About $[cost] per verified task." |
| 2:35–2:55 | Hero render of the arm sorting; end card. | "Show it once. Prove it twenty times. Next: running the verified program on a real arm." |

## Capture checklist

- Export the demo job: `python -m showtell render-demo data/jobs/<id>` then
  `python -m showtell export-demo --job <id>`; record the screen from the Vercel URL.
- Use the task-3 job from the benchmark run as it happened; do not re-run jobs to fish for a
  failure. If its first program already passed, show that honestly and take the failure → repair
  segment from a benchmark job where a repair really happened (the results table says which).
- Keep the cursor slow; 1280×720 or larger; no notifications on screen.
