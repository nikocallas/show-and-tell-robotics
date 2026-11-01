"""Demo-video checks run on synthetic clips (ffmpeg, no API)."""

from pathlib import Path

import cv2

from showtell.perception import video_check as vc
from showtell.perception import video_prep


def test_expected_videos_cover_smoke_and_every_task() -> None:
    names = vc.expected_videos()
    assert names[0] == "smoke.mp4"
    assert "task03_size_glass.mp4" in names and "task06_color_exc.mp4" in names
    assert len(names) == len(set(names)) == 7


def test_a_good_clip_passes_except_for_its_resolution(tmp_path: Path) -> None:
    clip = video_prep.make_test_clip(tmp_path / "task01_size.mp4", seconds=3.0)
    check = vc.check_file(clip, sheet_dir=tmp_path / "sheets")
    assert check.status == "warn"  # the synthetic clip is 640x480
    assert check.problems == ["640x480: film at 1080p"]
    assert (check.width, check.height, check.normalized_frames) == (640, 480, 30)
    sheet = cv2.imread(str(check.sheet))
    assert sheet is not None and sheet.shape[1] == 4 * 360


def test_long_wrongly_named_and_missing_videos_are_reported(tmp_path: Path) -> None:
    video_prep.make_test_clip(tmp_path / "smoke.mp4", seconds=12.0)
    video_prep.make_test_clip(tmp_path / "Task 3.mp4", seconds=2.0)
    results = {r.name: r for r in vc.check_folder(tmp_path)}
    assert any("keep one take under 10 s" in p for p in results["smoke.mp4"].problems)
    assert results["task02_color.mp4"].status == "missing"
    assert results["Task 3.mp4"].status == "warn"


def test_unreadable_and_wrong_format_files_are_errors(tmp_path: Path) -> None:
    bad = tmp_path / "task01_size.mp4"
    bad.write_bytes(b"not a video")
    assert vc.check_file(bad).status == "error"
    avi = video_prep.make_test_clip(tmp_path / "clip.mp4", seconds=2.0).rename(tmp_path / "x.avi")
    check = vc.check_file(avi)
    assert check.status == "error" and "SAM needs MP4 or MOV" in check.problems[0]
