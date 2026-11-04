"""The demo video's script and the pure parts of its build (no browser, no ffmpeg, no voice)."""

import json
from pathlib import Path

import pytest

from demo_video import cards, compose, script
from demo_video.cdp import Capture, capture_to_mp4


def test_captions_fit_two_rows_and_keep_every_word() -> None:
    text = (
        "Before any code exists, Muse Wonder watches the video and writes the tests: "
        "red ends in the left bin, green in the right, blue never moves."
    )
    chunks = compose.split_caption(text)
    assert len(chunks) > 1
    assert all(len(c) <= 115 for c in chunks)
    assert " ".join(chunks) == text
    assert compose.split_caption("Short line.") == ["Short line."]


def test_spoken_form_spells_out_names_the_voice_would_mangle() -> None:
    spoken = compose.to_speech("SAM 3.1 runs in MuJoCo. 20/20 in scene 6004.")
    assert "SAM" not in spoken and "MuJoCo" not in spoken and "20/20" not in spoken
    assert "sixty oh four" in spoken


def test_srt_time_format() -> None:
    assert compose.srt_time(3723.5) == "01:02:03,500"


def test_script_numbers_come_from_the_recorded_runs() -> None:
    facts = script.load_facts()
    assert facts.matches("sentence") == (4, 6)
    assert facts.matches("standin") == (6, 6)
    program = facts.bench["tasks"]["task03_size_glass"]["standin"]["final_program"]
    shots = script.shots(facts, program, "def run(scene): ...")
    assert all(s.lines and s.visual for s in shots)
    text = " ".join(line for s in shots for line in s.lines)
    assert facts.last["diagnosis"].rstrip(".") in text
    assert "120 checks" in text
    assert "four of six" in text and "six of six" in text
    assert len(text.split()) < 480  # under 3 minutes at the voice's pace


def test_every_stand_in_or_replay_shot_is_labelled() -> None:
    facts = script.load_facts()
    shots = {s.id: s for s in script.shots(facts, "a", "b")}
    for shot_id in ("form", "stages", "tracked", "spec", "program", "grid_live"):
        assert shots[shot_id].tag == script.TAG_STANDIN
    for shot_id in ("failure_grid", "failure_clip", "evidence", "diagnosis", "patch", "verified"):
        assert shots[shot_id].tag == script.TAG_REPLAY
    assert shots["benchmark"].tag == script.TAG_BENCH
    assert shots["cold_open"].tag == script.TAG_NAIVE
    assert "stand-in" in shots["glass"].tag


def test_cards_escape_their_text() -> None:
    page = cards.facts_card("A & B", [("<x>", "y")])
    assert "A &amp; B" in page and "&lt;x&gt;" in page


def test_capture_needs_frames(tmp_path: Path) -> None:
    empty = Capture(out_dir=tmp_path, started=1.0, stopped=2.0)
    try:
        capture_to_mp4(empty, tmp_path / "x.mp4")
    except ValueError as err:
        assert "nothing was painted" in str(err)
    else:
        raise AssertionError("expected ValueError")


def test_results_file_keeps_human_and_stand_in_runs_apart() -> None:
    results = json.loads((script.BENCH).read_text())
    for conds in results["tasks"].values():
        assert "standin" not in conds["video"].get("job_id", "")
        assert "standin" in conds["standin"]["job_id"]


def test_the_macos_voice_preview_is_never_what_gets_published() -> None:
    from demo_video import __main__ as build

    with pytest.raises(AssertionError, match="private review"):
        build.publish(build.OUT / build.PREVIEW)
