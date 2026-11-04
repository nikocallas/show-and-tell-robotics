"""Command-line entry point: `python -m showtell <command>`."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from showtell.config import settings

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Show & Tell Robotics CLI")
console = Console()


@app.command()
def smoke(
    video: Path = typer.Option(
        Path("bench/videos/smoke.mp4"), help="Short clip for SAM/video checks"
    ),
) -> None:
    """Phase 0: check the Meta Model API (Wonder text/JSON/video, SAM video tracking)."""
    from showtell.meta.smoke import run_smoke

    report = run_smoke(video)
    raise typer.Exit(code=0 if report["ok"] else 1)


@app.command("check-videos")
def check_videos(
    folder: Path | None = typer.Option(
        None, help="Folder with the demo videos  [default: bench/videos in this repo]"
    ),
) -> None:
    """Check the human demo videos (no API calls) and write a contact sheet for each."""
    from rich.table import Table

    from showtell.config import ROOT
    from showtell.perception.video_check import check_folder

    folder = folder or ROOT / "bench" / "videos"  # the benchmark's folder, from any directory
    if not folder.is_dir():
        console.print(f"[red]no folder {folder}[/]")
        raise typer.Exit(code=1)
    sheets = settings.SHOWTELL_DATA_DIR / "video_check"
    results = check_folder(folder, sheet_dir=sheets)
    table = Table(title=f"Demo videos in {folder}")
    for col in ("File", "Status", "Size", "Length", "Problems"):
        table.add_column(col)
    style = {"ok": "green", "warn": "yellow", "error": "red", "missing": "dim"}
    for r in results:
        size = f"{r.width}x{r.height} @ {r.fps:g} fps" if r.width else "—"
        length = f"{r.seconds:.1f} s" if r.seconds else "—"
        table.add_row(
            r.name, f"[{style[r.status]}]{r.status}[/]", size, length, "; ".join(r.problems)
        )
    console.print(table)
    if any(r.sheet for r in results):
        console.print(f"Contact sheets (4 frames each, check the framing by eye): {sheets}")
    raise typer.Exit(code=1 if any(r.status == "error" for r in results) else 0)


@app.command("render-scene")
def render_scene(
    seed: int = typer.Option(1, help="Layout seed"),
    task: str = typer.Option("task03_size_glass", help="Task id from bench/tasks.yaml"),
    out: Path = typer.Option(Path("data/render_scene"), help="Output folder"),
    hd: bool = typer.Option(False, help="Render the hero camera at 1280x720"),
) -> None:
    """Phase 1: render PNGs from all three cameras plus a 4 s MP4 of a seeded layout."""
    from showtell.sim.render import render_scene_preview

    paths = render_scene_preview(task_id=task, seed=seed, out_dir=out, hd=hd)
    for p in paths:
        console.print(f"wrote {p}")


@app.command("run-program")
def run_program(
    program: Path = typer.Argument(..., help="Python file defining run(scene)"),
    task: str = typer.Option("task03_size_glass", help="Task id from bench/tasks.yaml"),
    seeds: int = typer.Option(20, help="Number of seeds"),
    spec: Path | None = typer.Option(
        None, help="TaskSpec JSON (defaults to the task's reference spec)"
    ),
    record: bool = typer.Option(True, help="Write videos"),
    out: Path = typer.Option(Path("data/runs/manual"), help="Output folder"),
) -> None:
    """Run a hand-written program on N seeds and print the ground-truth report."""
    from showtell.verify.cli import run_program_cli

    ok = run_program_cli(program, task, seeds, spec, record, out)
    raise typer.Exit(code=0 if ok else 1)


@app.command()
def run(
    video: Path | None = typer.Option(None, help="Human demo video"),
    rules: str = typer.Option(..., help="Rule sentence, e.g. 'Sort by size.'"),
    task: str | None = typer.Option(
        None, help="Task id (sets the sim objects); inferred if omitted"
    ),
    seeds: int = typer.Option(20, help="Seeds per attempt"),
    no_video: bool = typer.Option(
        False, "--no-video", help="Build the spec from the sentence only"
    ),
    interactive: bool = typer.Option(False, help="Pause for spec approval"),
    judge: bool = typer.Option(True, help="Run the SAM judge on rendered clips"),
    out: Path | None = typer.Option(None, help="Output folder"),
) -> None:
    """Full pipeline: video + rules -> spec -> program -> verify -> repair -> report."""
    from showtell.pipeline import run_cli

    ok = run_cli(
        video=video,
        rules=rules,
        task_id=task,
        n_seeds=seeds,
        use_video=not no_video,
        interactive=interactive,
        use_judge=judge,
        out_dir=out,
    )
    raise typer.Exit(code=0 if ok else 1)


@app.command()
def bench(
    tasks: str | None = typer.Option(None, help="Comma-separated task ids (default: all)"),
    seeds: int = typer.Option(20, help="Seeds per attempt"),
    judge: bool = typer.Option(True, help="Run the SAM judge"),
    conditions: str | None = typer.Option(
        None, help="Comma-separated: video, standin, sentence (default: all three)"
    ),
) -> None:
    """Run the benchmark (every condition with a clip) and write bench/results.{json,md}."""
    from bench.run_bench import CONDITIONS
    from bench.run_bench import main as bench_main

    bench_main(
        tasks=tasks.split(",") if tasks else None,
        n_seeds=seeds,
        use_judge=judge,
        conditions=tuple(conditions.split(",")) if conditions else CONDITIONS,
    )


@app.command("make-standins")
def make_standins(
    tasks: str | None = typer.Option(None, help="Comma-separated task ids (default: all)"),
) -> None:
    """Render labelled stand-in demo clips (simulator renders, not human demos). No API calls."""
    from bench.make_standins import main as standins_main

    standins_main(tasks.split(",") if tasks else None)


@app.command("render-demo")
def render_demo(
    job_dir: Path = typer.Argument(..., help="A finished run folder"),
) -> None:
    """Re-render a finished run's programs at 1280x720 on the hero camera."""
    from showtell.verify.demo_render import render_demo as _render

    _render(job_dir)


@app.command("export-demo")
def export_demo(
    job: str = typer.Option(..., "--job", help="Job id (API database) or a run folder"),
    out: Path | None = typer.Option(
        None, help="Demo bundle folder  [default: web/public/demo in this repo]"
    ),
    title: str | None = typer.Option(None, help="Short title for the run in the hosted demo"),
    note: str | None = typer.Option(
        None, help="How the run was made (e.g. stand-in video, cached responses); shown with it"
    ),
) -> None:
    """Export a finished job as static JSON + media for the web app's demo mode."""
    from showtell.api.export_demo import DEFAULT_OUT
    from showtell.api.export_demo import export_demo as _export

    _export(job, out or DEFAULT_OUT, title=title, note=note)


@app.command()
def cost() -> None:
    """Print total API spend from data/usage.jsonl."""
    from showtell.meta.usage import summarize

    summary = summarize()
    console.print(summary.table())
    if summary.total_usd > settings.BUDGET_WARN_USD:
        console.print(f"[bold red]Spend ${summary.total_usd:.2f} is past the warning line.[/]")


if __name__ == "__main__":
    app()
