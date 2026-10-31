"""`python -m showtell run-program`: run a hand-written program on N seeds and print the report."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from rich.console import Console
from rich.table import Table

from showtell.agent.schemas import TaskSpec
from showtell.sim.scene import get_task
from showtell.verify.runner import run_seeds, task_seeds


def run_program_cli(
    program: Path, task_id: str, n_seeds: int, spec_path: Path | None, record: bool, out: Path
) -> bool:
    con = Console()
    task = get_task(task_id)
    if spec_path:
        spec = TaskSpec.model_validate_json(Path(spec_path).read_text())
    else:
        spec = TaskSpec.model_validate({**task.reference, "rationale": "reference spec"})
    source = Path(program).read_text()
    seeds = task_seeds(task.index, n_seeds)
    con.print(f"[bold]{program}[/] on {task.id}, seeds {seeds[0]}–{seeds[-1]}")
    results = run_seeds(
        [o.id for o in task.objects],
        source,
        spec.checks,
        seeds,
        out_root=out if record else None,
        record=record,
        on_result=lambda r: con.print(
            f"  seed {r.seed}: {'[green]pass' if r.passed else '[red]FAIL'}[/] "
            f"({r.wall_seconds:.1f}s)"
        ),
    )
    table = Table(title=f"{sum(r.passed for r in results)}/{len(results)} seeds pass")
    table.add_column("check")
    table.add_column("failed seeds", justify="right")
    fails = Counter(c.check_id for r in results for c in r.checks if not c.passed)
    for c in spec.checks:
        table.add_row(f"{c.id} ({c.kind})", str(fails.get(c.id, 0)))
    errors = sum(1 for r in results if r.error)
    table.add_row("program errors", str(errors))
    con.print(table)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps([r.model_dump() for r in results], indent=1))
    con.print(f"results: {out / 'results.json'}")
    return all(r.passed for r in results)
