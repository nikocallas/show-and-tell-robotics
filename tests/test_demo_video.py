"""The demo video's script and the pure parts of its build (no browser; one test needs ffmpeg)."""

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
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


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_a_voiceover_brings_only_its_sound(tmp_path: Path, monkeypatch) -> None:
    """A phone recording can hold more tracks, chapters and its location: none may reach the cut."""
    from demo_video import __main__ as build

    silent = tmp_path / "silent.mp4"
    compose.run(
        "-f", "lavfi", "-i", "color=black:s=64x64:r=10:d=4",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(silent),
    )  # fmt: skip
    chapters = tmp_path / "chapters.txt"
    chapters.write_text(";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=1000\ntitle=x\n")
    take = tmp_path / "private place name.mov"
    compose.run(
        "-f", "lavfi", "-i", "sine=frequency=220:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=330:duration=2",
        "-f", "ffmetadata", "-i", str(chapters),
        "-map", "0", "-map", "1", "-map_chapters", "2", "-c:a", "aac",
        "-metadata", "title=private place name", "-metadata", "location=+1.0+2.0/",
        "-metadata:s:a:0", "handler_name=private place name", str(take),
    )  # fmt: skip
    monkeypatch.setattr(build, "OUT", tmp_path)
    monkeypatch.setattr(build, "SILENT", silent)

    out = build.swap_voice(take)

    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_chapters", "-show_entries",
            "stream=codec_type,channels,duration:format=duration:format_tags:stream_tags",
            "-of", "json", str(out),
        ],
        check=True,
        capture_output=True,
        text=True,
    )  # fmt: skip
    info = json.loads(probe.stdout)
    assert sorted(st["codec_type"] for st in info["streams"]) == ["audio", "video"]
    audio = next(st for st in info["streams"] if st["codec_type"] == "audio")
    assert audio["channels"] == 1
    assert float(audio["duration"]) == pytest.approx(4, abs=0.1)  # the whole cut, not just the take
    assert not info.get("chapters")
    assert b"private place name" not in out.read_bytes()
    assert "location" not in info["format"].get("tags", {})
    # The first track's sound, not the second's.
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(out), "-map", "0:a", "-ss", "0.5", "-t", "1", "-f", "f32le", "-"],
        check=True,
        capture_output=True,
    ).stdout  # fmt: skip
    samples = np.frombuffer(raw, dtype=np.float32)
    freqs = np.fft.rfftfreq(len(samples), 1 / compose.SAMPLE_RATE)
    assert freqs[np.abs(np.fft.rfft(samples)).argmax()] == pytest.approx(220, abs=20)
    with pytest.raises(AssertionError, match="private review"):
        build.publish(tmp_path / build.PREVIEW)


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
