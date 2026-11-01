# Benchmark results

| Task | First try (video) | Final (video) | Repairs | Final (sentence only) | Judge agreement | Cost $ | Time |
|---|---|---|---|---|---|---|---|
| task01_size | no video | no video | — | 20/20 | — | 0.05 | — |
| task02_color | no video | no video | — | 20/20 | — | 0.05 | — |
| task03_size_glass | no video | no video | — | 0/20 | — | 0.08 | — |
| task04_clear | no video | no video | — | 20/20 | — | 0.06 | — |
| task05_shape | no video | no video | — | 20/20 | — | 0.04 | — |
| task06_color_exc | no video | no video | — | 0/20 (1 repair) | — | 0.14 | — |

Pass rates are k/20 randomized scenes, scored against each task's reference checks (bench/tasks.yaml), which encode what each task's demo video shows (the shot list in bench/videos/README.md): a run can pass every check in the spec it generated and still score lower here. First try = the first generated program; Final = after up to 3 repairs (sentence-only repairs in brackets). Sentence only = the same pipeline without the demo video and without the SAM judge (simulator ground truth only). "no video" = the human demo video for the task is not recorded yet, so the video condition has not run. Judge agreement = SAM 3.1 on the rendered clips vs simulator ground truth (video condition). Cost $ = list price of the Meta API calls each run made, summed over the conditions that ran; responses served from the local cache are counted at list price but billed $0. Repairs and Time = the video condition.

Own checks vs reference checks:

- task03_size_glass (sentence only): 20/20 on its own checks, 0/20 on the reference checks.
- task06_color_exc (sentence only): 20/20 on its own checks, 0/20 on the reference checks.

Updated 2026-10-31 15:51:13.
