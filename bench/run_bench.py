"""Benchmark: every task twice (video + sentence, sentence only) on the same seeds.

Writes bench/results.json and bench/results.md and refreshes the README table. Resumable: a
(task, condition) already in results.json is skipped. Pass rates are scored against each task's
hand-written reference checks in bench/tasks.yaml (what the human demo shows); the pipeline itself
only ever sees its own generated spec.

    python -m showtell bench [--tasks task01_size,task03_size_glass] [--seeds 20] [--no-judge]
    python bench/run_bench.py --tasks task03_size_glass
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
RESULTS_JSON = ROOT / "bench" / "results.json"
RESULTS_MD = ROOT / "bench" / "results.md"
README = ROOT / "README.md"
CONDITIONS = ("video", "sentence")
COLUMNS = [
    "Task",
    "First try (video)",
    "Final (video)",
    "Repairs",
    "Final (sentence only)",
    "Judge agreement",
    "Cost $",
    "Time",
]


def load_results(path: Path = RESULTS_JSON) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text())
    return {"tasks": {}, "updated_at": None, "n_seeds": None}


def save_results(results: dict[str, Any], path: Path = RESULTS_JSON) -> None:
    results["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    path.write_text(json.dumps(results, indent=2) + "\n")


def reference_rate(task, program_source: str, seeds: list[int]) -> tuple[int, int]:
    """Pass count of a program against the task's reference checks (real physics, no videos)."""
    from showtell.agent.schemas import TaskSpec
    from showtell.verify.runner import run_seeds

    ref = TaskSpec.model_validate({**task.reference, "rationale": "reference"})
    results = run_seeds(
        [o.id for o in task.objects], program_source, ref.checks, seeds, record=False
    )
    return sum(r.passed for r in results), len(results)


def run_condition(task, condition: str, n_seeds: int, use_judge: bool) -> dict[str, Any]:
    from showtell.contracts import JobConfig
    from showtell.pipeline import resolve_seeds, run_job

    video = ROOT / "bench" / "videos" / task.video
    if condition == "video" and not video.exists():
        return {"status": "missing_video", "video": str(video.relative_to(ROOT))}
    job_id = f"bench-{task.id}-{condition}-{time.strftime('%Y%m%d-%H%M%S')}"
    config = JobConfig(
        job_id=job_id,
        rules_text=task.rules,
        video_path=video if condition == "video" else None,
        use_video=condition == "video",
        task_id=task.id,
        n_seeds=n_seeds,
        use_judge=use_judge and condition == "video",
    )
    t0 = time.time()
    report = run_job(config)
    seeds = resolve_seeds(config)
    out: dict[str, Any] = {
        "job_id": job_id,
        "status": report.status,
        "error": report.error,
        "repairs": max(0, len(report.attempts) - 1),
        "attempts": [
            {"n": a.n, "passed": a.passed, "total": a.total, "diagnosis": a.diagnosis}
            for a in report.attempts
        ],
        "cost_usd": report.cost.usd,
        "wall_s": round(time.time() - t0, 1),
        "agreement_pct": report.agreement.pct if report.agreement else None,
        "agreement": report.agreement.model_dump() if report.agreement else None,
        "spec": report.spec.model_dump() if report.spec else None,
        "media_path": report.media_path,
    }
    if report.attempts:
        first, final = report.attempts[0].program, report.attempts[-1].program
        out["ref_first"] = list(reference_rate(task, first, seeds))
        out["ref_final"] = list(reference_rate(task, final, seeds))
        out["final_program"] = final
    return out


def _rate(entry: dict | None, key: str) -> str:
    if not entry:
        return "—"
    if entry.get("status") == "missing_video":
        return "no video"
    if entry.get("status") == "error":
        return "error"
    val = entry.get(key)
    return f"{val[0]}/{val[1]}" if val else "—"


def _agreement(entry: dict | None) -> str:
    """Judge agreement as a whole percent that never rounds a miss up to 100%."""
    stats = (entry or {}).get("agreement") or {}
    agree, n = stats.get("agree"), stats.get("n")
    if n:
        return f"{min(round(100 * agree / n), 99 if agree < n else 100)}%"
    pct = (entry or {}).get("agreement_pct")
    return "—" if pct is None else f"{min(round(pct), 99) if pct < 100 else 100}%"


def _sentence_cell(entry: dict | None) -> str:
    """Sentence-only final rate, with the number of repairs it took when there were any."""
    cell = _rate(entry, "ref_final")
    repairs = int((entry or {}).get("repairs") or 0)
    if repairs and "/" in cell:
        cell += f" ({repairs} repair{'' if repairs == 1 else 's'})"
    return cell


def own_check_gaps(results: dict[str, Any]) -> list[str]:
    """One line per run whose final program passes its own generated checks more often than the
    reference checks (the spec it wrote differs from what the demo shows)."""
    lines = []
    for task_id, conds in results.get("tasks", {}).items():
        for cond in CONDITIONS:
            e = conds.get(cond) or {}
            attempts, ref = e.get("attempts") or [], e.get("ref_final")
            if not attempts or not ref or attempts[-1]["passed"] == ref[0]:
                continue
            label = "sentence only" if cond == "sentence" else "video"
            own = f"{attempts[-1]['passed']}/{attempts[-1]['total']}"
            lines.append(
                f"- {task_id} ({label}): {own} on its own checks, {ref[0]}/{ref[1]} on the "
                "reference checks."
            )
    return lines


def table_rows(results: dict[str, Any]) -> list[list[str]]:
    rows = []
    for task_id, conds in results.get("tasks", {}).items():
        v, s = conds.get("video"), conds.get("sentence")
        cost = sum(float(c.get("cost_usd") or 0) for c in (v, s) if c)
        rows.append(
            [
                task_id,
                _rate(v, "ref_first"),
                _rate(v, "ref_final"),
                str(v["repairs"]) if v and "repairs" in v else "—",
                _sentence_cell(s),
                _agreement(v),
                f"{cost:.2f}",
                f"{(v or {}).get('wall_s', 0) / 60:.1f} min" if v and v.get("wall_s") else "—",
            ]
        )
    return rows


def results_markdown(results: dict[str, Any]) -> str:
    rows = table_rows(results)
    lines = ["| " + " | ".join(COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    n = results.get("n_seeds") or 20
    lines += [
        "",
        f"Pass rates are k/{n} randomized scenes, scored against each task's reference checks "
        "(bench/tasks.yaml), which encode what each task's demo video shows (the shot list in "
        "bench/videos/README.md): a run can pass every check in the spec it generated and still "
        "score lower here. First try = the first generated "
        "program; Final = after up to 3 repairs (sentence-only repairs in brackets). Sentence "
        "only = the same pipeline without the demo video and without the SAM judge (simulator "
        'ground truth only). "no video" = the human demo video for the task is not recorded '
        "yet, so the video condition has not run. Judge agreement = SAM 3.1 on the rendered "
        "clips vs simulator ground truth (video condition). Cost $ = list price of the Meta API "
        "calls each run made, summed over the conditions that ran; responses served from the "
        "local cache are counted at list price but billed $0.",
    ]
    gaps = own_check_gaps(results)
    if gaps:
        lines += ["", "Own checks vs reference checks:", "", *gaps]
    if results.get("updated_at"):
        lines.append(f"\nUpdated {results['updated_at']}.")
    return "\n".join(lines) + "\n"


def update_readme(md_table: str, readme: Path = README) -> bool:
    if not readme.exists():
        return False
    text = readme.read_text()
    pattern = re.compile(r"(<!-- bench:start -->\n)(.*?)(<!-- bench:end -->)", re.S)
    if not pattern.search(text):
        return False
    readme.write_text(pattern.sub(lambda m: m.group(1) + md_table + m.group(3), text))
    return True


def main(
    tasks: list[str] | None = None, n_seeds: int = 20, use_judge: bool = True
) -> dict[str, Any]:
    from showtell.sim.scene import load_tasks

    all_tasks = load_tasks()
    wanted = tasks or list(all_tasks)
    results = load_results()
    if results.get("n_seeds") not in (None, n_seeds):
        raise SystemExit(
            f"results.json was made with {results['n_seeds']} seeds; use --seeds "
            f"{results['n_seeds']} or move it away"
        )
    results["n_seeds"] = n_seeds
    for task_id in wanted:
        task = all_tasks[task_id]
        entry = results["tasks"].setdefault(task_id, {})
        for cond in CONDITIONS:
            if cond in entry and entry[cond].get("status") not in ("missing_video", "error"):
                print(f"skip {task_id}/{cond} (done)")
                continue
            print(f"run {task_id}/{cond} ...", flush=True)
            entry[cond] = run_condition(task, cond, n_seeds, use_judge)
            save_results(results)
    md = results_markdown(results)
    RESULTS_MD.write_text("# Benchmark results\n\n" + md)
    update_readme(md)
    print(md)
    return results


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tasks", default=None, help="comma-separated task ids")
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--no-judge", action="store_true")
    a = ap.parse_args()
    main(a.tasks.split(",") if a.tasks else None, a.seeds, not a.no_judge)
