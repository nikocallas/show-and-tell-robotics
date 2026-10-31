# Benchmark results

| Task | First try (video) | Final (video) | Repairs | Final (sentence only) | Judge agreement | Cost $ | Time |
|---|---|---|---|---|---|---|---|
| task01_size | no video | no video | — | 20/20 | — | 0.05 | — |
| task02_color | no video | no video | — | 20/20 | — | 0.05 | — |
| task03_size_glass | no video | no video | — | 0/20 | — | 0.08 | — |
| task04_clear | no video | no video | — | 20/20 | — | 0.06 | — |
| task05_shape | no video | no video | — | 20/20 | — | 0.04 | — |
| task06_color_exc | no video | no video | — | 0/20 | — | 0.14 | — |

Pass rates are k/20 randomized scenes, scored against each task's reference checks (bench/tasks.yaml). First try = the first generated program; Final = after up to 3 repairs. Sentence only = the same pipeline without the demo video. Judge agreement = SAM 3.1 on the rendered clips vs simulator ground truth. Cost covers both conditions at list price.

Updated 2026-10-31 15:51:13.
