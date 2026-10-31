"""Re-render a finished job's hero videos at 1280x720 for the demo (no API calls).

Physics is deterministic, so re-running each attempt's program on the same seeds reproduces the
same outcome; we check that every seed's pass/fail matches the original run before swapping files.
The original 640x480 clips are kept as full_sd.mp4 / thumb_sd.jpg.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from rich.console import Console

from showtell.contracts import JobReport
from showtell.verify.runner import run_seeds

HD = (1280, 720)


def render_demo(job_dir: Path) -> JobReport:
    con = Console()
    job_dir = Path(job_dir)
    report = JobReport.model_validate_json((job_dir / "report.json").read_text())
    if report.spec is None or not report.attempts:
        raise SystemExit("job has no spec/attempts to render")
    for att in report.attempts:
        tmp = job_dir / "attempts" / str(att.n) / "_hd"
        con.print(f"attempt {att.n}: rendering {len(report.seeds)} seeds at {HD[0]}x{HD[1]}")
        results = run_seeds(
            report.object_ids,
            att.program,
            report.spec.checks,
            report.seeds,
            out_root=tmp,
            record=True,
            judge_clips=False,
            hero_size=HD,
        )
        recorded = {s.seed: s for s in att.seeds}
        mismatch = [
            r.seed for r in results if r.seed in recorded and r.passed != recorded[r.seed].passed
        ]
        if mismatch:
            shutil.rmtree(tmp, ignore_errors=True)
            raise SystemExit(
                f"attempt {att.n}: outcome changed on seeds {mismatch}; not replacing videos"
            )
        for r in results:
            seed_dir = job_dir / "attempts" / str(att.n) / f"seed_{r.seed}"
            hd_dir = tmp / f"seed_{r.seed}"
            for name, sd_name in (("full.mp4", "full_sd.mp4"), ("thumb.jpg", "thumb_sd.jpg")):
                if (seed_dir / name).exists() and not (seed_dir / sd_name).exists():
                    shutil.move(str(seed_dir / name), seed_dir / sd_name)
                if (hd_dir / name).exists():
                    shutil.move(str(hd_dir / name), seed_dir / name)
        shutil.rmtree(tmp, ignore_errors=True)
    con.print(f"[green]done[/]: hero videos in {job_dir} are now {HD[0]}x{HD[1]}")
    return report
