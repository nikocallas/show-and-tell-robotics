# Benchmark results

| Task | First try (video) | Final (video) | Repairs | Final (sentence only) | Judge agreement | Cost $ | Time |
|---|---|---|---|---|---|---|---|
| task01_size | 20/20 (stand-in) | 20/20 (stand-in) | 0 | 20/20 | 100% | 1.05 | 2.5 min |
| task02_color | 20/20 (stand-in) | 20/20 (stand-in) | 0 | 20/20 | 100% | 1.08 | 3.5 min |
| task03_size_glass | 20/20 (stand-in) | 20/20 (stand-in) | 0 | 0/20 | 100% | 2.00 | 2.0 min |
| task04_clear | 20/20 (stand-in) | 20/20 (stand-in) | 0 | 20/20 | 100% (19 undecided) | 1.75 | 3.6 min |
| task05_shape | 20/20 (stand-in) | 20/20 (stand-in) | 0 | 20/20 | 100% (19 undecided) | 1.22 | 2.5 min |
| task06_color_exc | 20/20 (stand-in) | 20/20 (stand-in) | 0 | 0/20 (1 repair) | 100% | 1.26 | 2.8 min |

Pass rates are k/20 randomized scenes, scored against each task's reference checks (bench/tasks.yaml), which encode what each task's demo video is to show (the shot list in bench/videos/README.md): a run can pass every check in the spec it generated and still score lower here. First try = the first generated program; Final = after up to 3 repairs (sentence-only repairs in brackets). Sentence only = the same pipeline without the demo video and without the SAM judge (simulator ground truth only). "(stand-in)" = the human demo is not filmed, so that run used a stand-in clip instead: a top-down simulator render of the task's reference program (bench/make_standins.py), not a recording of a person. It tests whether Muse Wonder reads the mapping from a demonstration, not how the pipeline copes with a phone video. Judge agreement = SAM 3.1 on the rendered clips vs simulator ground truth (video condition), over the checks SAM could decide; undecided = SAM did not find an object the check needs, never counted as agreement. Cost $ = list price of the Meta API calls each run made, summed over the conditions that ran; responses served from the local cache are counted at list price but billed $0, and take no API time, so a cached run's Time is shorter (fully cached here: task01_size sentence only, task03_size_glass stand-in video, task03_size_glass sentence only). Repairs and Time = the video condition.

Own checks vs reference checks:

- task03_size_glass (sentence only): 20/20 on its own checks, 0/20 on the reference checks.
- task06_color_exc (sentence only): 20/20 on its own checks, 0/20 on the reference checks.

Updated 2026-11-03 15:53:56.
