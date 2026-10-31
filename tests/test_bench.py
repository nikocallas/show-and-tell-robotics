"""Benchmark table rendering, resumability helpers and the reference scorer (offline)."""

from pathlib import Path

from bench import run_bench as rb
from showtell.sim.scene import get_task

ROOT = Path(__file__).resolve().parent.parent

SAMPLE = {
    "n_seeds": 20,
    "updated_at": "2026-10-31 12:00:00",
    "tasks": {
        "task03_size_glass": {
            "video": {
                "status": "verified",
                "ref_first": [2, 20],
                "ref_final": [20, 20],
                "repairs": 1,
                "agreement_pct": 91.25,
                "cost_usd": 1.2,
                "wall_s": 300.0,
            },
            "sentence": {
                "status": "verified",
                "ref_first": [20, 20],
                "ref_final": [20, 20],
                "repairs": 0,
                "cost_usd": 0.4,
                "wall_s": 120.0,
            },
        },
        "task02_color": {"video": {"status": "missing_video"}},
    },
}


def test_results_table_has_the_required_columns_and_values() -> None:
    md = rb.results_markdown(SAMPLE)
    header = md.splitlines()[0]
    for col in rb.COLUMNS:
        assert col in header
    assert "| task03_size_glass | 2/20 | 20/20 | 1 | 20/20 | 91% | 1.60 | 5.0 min |" in md
    assert "| task02_color | no video |" in md


def test_update_readme_replaces_only_the_marked_block(tmp_path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text("intro\n<!-- bench:start -->\nold\n<!-- bench:end -->\noutro\n")
    assert rb.update_readme("new table\n", readme)
    assert (
        readme.read_text() == "intro\n<!-- bench:start -->\nnew table\n<!-- bench:end -->\noutro\n"
    )
    plain = tmp_path / "plain.md"
    plain.write_text("no markers")
    assert not rb.update_readme("x", plain)


def test_reference_rate_scores_programs_against_the_reference_checks() -> None:
    task = get_task("task01_size")
    good = (ROOT / "examples/reference/task01_size.py").read_text()
    swapped = good.replace('"left_bin"', '"TMP"').replace('"right_bin"', '"left_bin"')
    swapped = swapped.replace('"TMP"', '"right_bin"')
    assert rb.reference_rate(task, good, [1000, 1001]) == (2, 2)
    assert rb.reference_rate(task, swapped, [1000, 1001]) == (0, 2)
