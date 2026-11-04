# Submission form answers

Paste-ready text for the portal. The form itself sits behind a login, so its exact fields are not known
yet; the answers below cover what the official rules judge: impact, use of Meta's tooling, technical
depth and the demo. The full write-up is [submission.md](submission.md), with sources for its numbers at
the end of that file.

## Project

**Name:** Show & Tell Robotics

**Track:** Startup

**Theme:** Push the Frontier

**Tagline:** Show a robot a task once, say the rule, and get a program that passed 20 simulated tests.

**Links**

- Repository: <https://github.com/nikocallas/show-and-tell-robotics>
- Write-up: <https://github.com/nikocallas/show-and-tell-robotics/blob/main/docs/submission.md>
- Demo video: <https://show-and-tell-robotics.vercel.app/video/show-and-tell-demo.mp4>
- Hosted demo: <https://show-and-tell-robotics.vercel.app>

## Short description (about 60 words)

Show & Tell is test-driven robot programming. Show a task once on video and say the rule in one
sentence. Muse Wonder writes the tests, you approve them, and it writes the program. It runs on 20
randomized MuJoCo scenes, judged by physics and SAM 3.1, and failures get patched. Simulation only so
far; the demo clips are simulator renders.

## Long description (about 250 words)

Telling a robot what to do still means writing a program, and the person who knows the task usually
can't read it. Integration generally runs four to five times the cost of the robot, and every change
of part or rule goes back to whoever can write the code.

Show & Tell lets that person show the task once and say the rule in one sentence. SAM 3.1 tracks what
moved. Muse Wonder writes pass/fail checks as plain sentences, the person approves them, and only then
does it write a short program out of tested robot skills. The program runs on 20 randomized scenes in
MuJoCo with a simulated Franka arm. Simulator ground truth decides, and SAM 3.1 judges each scene again
from rendered video. If a scene fails, Muse Wonder gets the failed checks, any error and 8 frames,
explains the failure in one sentence, and patches the program. All 20 scenes run again.

In our benchmark, from the sentence alone, four of six tasks match our reference. With a demonstration
clip, six of six match on the first try. Those clips are simulator renders standing in for phone video:
no human demo has been filmed yet. In one real run a program failed one scene in 20 on an arm-reach
limit nobody planned; Muse Wonder traced it and the patch passed its own checks on all 20 scenes. Written from
the sentence alone, that run's spec put green in a different bowl than our reference, so the benchmark
scores it 0/20.

## Who it's for

Robots are already everywhere: about 542,000 industrial robots were installed in 2024 and about
4,664,000 are in operation (IFR, World Robotics 2025). Getting one to do a new job is the expensive part:
integration generally runs four to five times the cost of the robot, per MIT's Julie Shah as reported by
MIT Sloan in 2021, and every change of part, bin or rule goes back to whoever can write the code.

We build for the people who already know the job: a line lead re-sorting parts, a lab technician, a
small shop without an integrator, an integrator handing a cell over to the customer's staff. They show
the change once and say the rule. They read the checks as plain sentences and approve them before any
code exists, and no program counts as verified until it has passed them on the randomized test scenes
(20 by default). The report and the attempt log are what gets handed over. That is the plan for a product; today it runs in
simulation only.

## How do you use Meta's models?

Every model the product calls at runtime is a Meta model, through the Meta Model API.

**Muse Wonder (`muse-wonder-1.0`)** does four jobs. It names the objects in the demonstration from
keyframes. It writes the task spec as schema-constrained JSON from the demo video, the track summary
and the sentence. It writes the robot program. And it diagnoses failures from the failed checks, any
program error and 8 frames, then patches the program.

**SAM 3.1 (`sam-3.1`)** does two. It tracks each kind of object Muse Wonder names, one concept per
request (so far on simulator renders standing in for a phone clip, where it missed some, such as the
standing glass). It also acts as a test oracle: it
judges every simulated test scene from rendered camera clips alone, and we publish how often it agrees
with physics ground truth.

Some of this surprised us. SAM found no bowls in the rendered clips, so the tracking summary misplaced
some objects, yet Wonder, seeing the video too, wrote the right bins. And it traced a reach error back to
its own use of `avoid`, then patched the program.

## Results

- Benchmark, six tasks, 20 fixed scenes each, against hand-written reference checks: stand-in clip +
  sentence 6 of 6 tasks on the first try; sentence only 4 of 6.
- SAM 3.1 agrees with the simulator on 302 of 302 checks it could decide on the stand-in runs (38
  undecided: from overhead it found no yellow cylinder in most clips of two tasks), and on 120 of 120 on
  the repair run. Only 2 of those verdicts were failures, so this says little yet about how SAM judges
  failures.
- A hand-written program with no obstacle handling knocks over the glass in 19 of 20 scenes. Every
  program Muse Wonder wrote for that task avoids it.
- $1.00 to $1.92 of API calls per verified task with a clip and the judge, at list price.

## Technical highlights

AST-whitelist sandbox (148 tests, 106 of them programs it must reject), one spawned process per scene
with timeout and memory limits, damped-least-squares IK, a closed check vocabulary evaluated from
physics state and the contact log, fixed seeds across every attempt and condition, all responses cached
by request hash, benchmark tables generated from one results file, more than 500 automated tests.

## What was built during the event

All of it: the first commit is 2026-10-31 and the history is public. Main third-party parts are MuJoCo
and the Franka Panda model from MuJoCo Menagerie (both Apache 2.0); other dependencies keep their own
licenses, including Meta's `meta-sam-parser` (SAM License). Claude Code was the coding agent, so commits
carry a Claude co-author line and GitHub lists it as a contributor.

## Limitations, stated up front

Simulation only. No human demo video has been filmed, so the video path has run only on simulator
renders. Grasping is a kinematic attach. The hosted demo replays recorded runs. The live app runs on a
local backend, as shown in the video: in that take the model answers came from the local cache of the
benchmark's stand-in run, and the simulator ran every scene again.

## What's next

Film the human demos and run that benchmark condition once. Make `avoid` check reachability. Harder
scenes. A pilot on a real arm through the same skill API.

## Video title and description (if you upload it)

**Title:** Show & Tell Robotics: test-driven robot programming with Muse Wonder and SAM 3.1

**Description:**

Show a robot a task once, say the rule in one sentence, and get a program that passed 20 randomized
simulated scenes. Built for the Meta Global AI Developer Hackathon.

Every run, program and number on screen is real. The screen footage is the real app (one shot is sped up
and labelled), the simulator footage is real programs on real seeds re-rendered at 1080p, and the cards
are ours. The demonstration clip is a simulator render standing in for a phone video, labelled on screen.

Code: https://github.com/nikocallas/show-and-tell-robotics
Recorded runs: https://show-and-tell-robotics.vercel.app

## Optional post

Telling a robot what to do still means writing a program. We built test-driven robot programming with
Muse Wonder and SAM 3.1: show it once, say the rule, approve the tests, and get a program that passed
20 randomized simulated scenes. When one fails, it explains why and patches itself.
https://show-and-tell-robotics.vercel.app
