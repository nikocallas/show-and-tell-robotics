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


def test_video_columns_show_a_stand_in_run_labelled_and_a_human_run_first() -> None:
    standin = {
        "status": "verified",
        "ref_first": [19, 20],
        "ref_final": [20, 20],
        "repairs": 1,
        "cost_usd": 1.0,
        "wall_s": 60.0,
        "agreement_pct": 100.0,
    }
    sentence = {"status": "verified", "ref_final": [0, 20], "repairs": 0, "cost_usd": 0.5}
    human = {**standin, "ref_first": [3, 20], "ref_final": [18, 20], "cost_usd": 2.0}
    only_standin = {
        "n_seeds": 20,
        "tasks": {
            "task06_color_exc": {
                "video": {"status": "missing_video"},
                "standin": standin,
                "sentence": sentence,
            }
        },
    }
    row = rb.table_rows(only_standin)[0]
    assert row[1:3] == ["19/20 (stand-in)", "20/20 (stand-in)"]
    assert row[6] == "1.50"
    assert '"(stand-in)" = the human demo is not filmed' in rb.results_markdown(only_standin)
    both = {
        "tasks": {"task06_color_exc": {"video": human, "standin": standin, "sentence": sentence}}
    }
    row = rb.table_rows(both)[0]
    assert row[1:3] == ["3/20", "18/20"]  # the human run wins and carries no mark
    assert row[6] == "2.50"


def test_judge_agreement_names_undecided_checks() -> None:
    entry = {"agreement": {"agree": 21, "n": 21, "undecided": 19}, "agreement_pct": 100.0}
    assert rb._agreement(entry) == "100% (19 undecided)"
    assert rb._agreement({"agreement": {"agree": 60, "n": 60, "undecided": 0}}) == "100%"


def test_stand_in_file_names_and_seeds_are_labelled_and_outside_the_test_seeds() -> None:
    from bench.make_standins import standin_path, standin_seed

    assert standin_path("task03_size_glass").name == "standin_task03_size_glass.mp4"
    assert standin_seed(3) == 3990
    assert standin_seed(3) not in range(3000, 3020)


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


def test_sentence_repairs_and_own_check_gaps_are_shown() -> None:
    results = {
        "n_seeds": 20,
        "tasks": {
            "task06_color_exc": {
                "video": {"status": "missing_video"},
                "sentence": {
                    "status": "verified",
                    "repairs": 1,
                    "attempts": [{"passed": 19, "total": 20}, {"passed": 20, "total": 20}],
                    "ref_first": [0, 20],
                    "ref_final": [0, 20],
                },
            },
            "task01_size": {
                "sentence": {
                    "repairs": 0,
                    "attempts": [{"passed": 20, "total": 20}],
                    "ref_final": [20, 20],
                }
            },
        },
    }
    rows = {r[0]: r for r in rb.table_rows(results)}
    assert rows["task06_color_exc"][4] == "0/20 (1 repair)"
    assert rows["task01_size"][4] == "20/20"
    assert rb.own_check_gaps(results) == [
        "- task06_color_exc (sentence only): 20/20 on its own checks, 0/20 on the reference checks."
    ]
    md = rb.results_markdown(results)
    assert '"no video" = the human demo video' in md and "without the SAM judge" in md


def test_own_check_gaps_lists_any_difference_and_the_footnote_names_every_column() -> None:
    results = {
        "n_seeds": 20,
        "tasks": {
            "task01_size": {  # fewer passes on its own checks than on the reference checks
                "video": {"attempts": [{"passed": 15, "total": 20}], "ref_final": [20, 20]}
            },
            "task02_color": {  # the same count: no line
                "video": {"attempts": [{"passed": 20, "total": 20}], "ref_final": [20, 20]}
            },
        },
    }
    assert rb.own_check_gaps(results) == [
        "- task01_size (video): 15/20 on its own checks, 20/20 on the reference checks."
    ]
    footnote = rb.results_markdown(SAMPLE).split("\n\n")[1]
    assert footnote.endswith("Repairs and Time = the video condition.")
    for column in ("First try", "Final", "Sentence only", "Judge agreement", "Cost $"):
        assert f"{column} = " in footnote
