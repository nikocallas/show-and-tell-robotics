"""Demo-video checks run on synthetic clips (ffmpeg, no API)."""

from pathlib import Path

import cv2
import pytest

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


def _hdr_clip(path: Path) -> Path:
    """A 2 s clip tagged the way an iPhone films HDR by default: BT.2020 primaries and matrix,
    HLG transfer, 10-bit (8-bit if this ffmpeg's x264 has no 10-bit)."""
    tags = "setparams=color_primaries=bt2020:color_trc=arib-std-b67:colorspace=bt2020nc"
    for pix_fmt in ("yuv420p10le", "yuv420p"):
        try:
            video_prep._run(
                [video_prep.ffmpeg_exe(), "-y", "-f", "lavfi", "-i", "testsrc=s=640x480:r=25:d=2"]
                + ["-vf", tags, "-c:v", "libx264", "-pix_fmt", pix_fmt, str(path)]
            )
            return path
        except RuntimeError:
            continue
    pytest.skip("this ffmpeg cannot write an HDR-tagged test clip")


def test_hdr_footage_gets_a_warning(tmp_path: Path) -> None:
    clip = _hdr_clip(tmp_path / "task02_color.mp4")
    color = video_prep.probe_color(clip)
    assert (color["color_transfer"], color["color_primaries"]) == ("arib-std-b67", "bt2020")
    check = vc.check_file(clip)
    assert check.status == "warn" and check.normalized_frames == 20  # it still normalizes
    (hdr,) = [p for p in check.problems if p.startswith("HDR video")]
    assert "HLG" in hdr and "turn off HDR Video" in hdr
    sdr = video_prep.make_test_clip(tmp_path / "sdr.mp4", seconds=2.0)
    assert vc.check_file(sdr).problems == ["640x480: film at 1080p"]


def test_hdr_problem_reads_the_colour_tags() -> None:
    assert vc.hdr_problem({}) is None
    sdr10 = {"pix_fmt": "yuv420p10le", "color_transfer": "bt709", "color_primaries": "bt709"}
    assert vc.hdr_problem(sdr10) is None  # 10-bit SDR converts to 8-bit fine
    hlg = {"pix_fmt": "yuv420p10le", "color_transfer": "arib-std-b67", "color_primaries": "bt2020"}
    assert str(vc.hdr_problem(hlg)).startswith("HDR video (HLG, 10-bit): ")
    assert str(vc.hdr_problem({"color_transfer": "smpte2084"})).startswith("HDR video (PQ): ")
    for bt2020 in ({"color_primaries": "bt2020"}, {"color_space": "bt2020nc"}):
        assert str(vc.hdr_problem(bt2020)).startswith("HDR video (BT.2020): ")


def test_misnamed_files_are_matched_once_with_a_rename_hint(tmp_path: Path) -> None:
    video_prep.make_test_clip(tmp_path / "task02_color.MP4", seconds=2.0)  # other case
    video_prep.make_test_clip(tmp_path / "task01_size.MOV", seconds=2.0)  # same name, .MOV
    (tmp_path / "._task03_size_glass.mp4").write_bytes(b"\x00\x05\x16\x07")  # macOS AppleDouble
    (tmp_path / ".DS_Store").write_bytes(b"\x00")
    results = vc.check_folder(tmp_path)
    assert [r.name for r in results] == vc.expected_videos()  # one row each, no extra rows
    by_name = {r.name: r for r in results}
    color, size = by_name["task02_color.mp4"], by_name["task01_size.mp4"]
    assert color.status == size.status == "warn" and color.normalized_frames == 20
    assert color.problems[-1] == (
        "named task02_color.MP4: rename it to exactly task02_color.mp4 (Linux and the Docker "
        "backend match names case-sensitively)"
    )
    assert size.problems[-1] == "found task01_size.MOV: rename it to task01_size.mp4"
    assert by_name["task03_size_glass.mp4"].status == "missing"


def test_a_contact_sheet_failure_is_a_warning_not_an_error(tmp_path: Path, monkeypatch) -> None:
    clip = video_prep.make_test_clip(tmp_path / "task01_size.mp4", seconds=2.0)

    def broken(video: Path, out: Path, **kw: object) -> Path:
        raise RuntimeError("disk full")

    monkeypatch.setattr(vc, "contact_sheet", broken)
    check = vc.check_file(clip, sheet_dir=tmp_path / "sheets")
    assert check.status == "warn" and check.normalized_frames == 20 and check.sheet is None
    assert check.problems == ["640x480: film at 1080p", "contact sheet failed: disk full"]


def test_check_videos_looks_in_the_repo_folder_from_any_directory(tmp_path: Path, monkeypatch):
    from typer.testing import CliRunner

    from showtell.__main__ import app
    from showtell.config import ROOT

    real_check_folder, seen = vc.check_folder, []

    def fake(folder: Path, *, sheet_dir: Path | None = None) -> list[vc.VideoCheck]:
        seen.append(Path(folder))
        return [vc.VideoCheck(name="smoke.mp4", status="missing")]

    monkeypatch.setattr(vc, "check_folder", fake)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["check-videos"])
    assert result.exit_code == 0, result.output
    assert seen == [ROOT / "bench" / "videos"]
    result = CliRunner().invoke(app, ["check-videos", "--folder", "no_such_folder"])
    assert result.exit_code == 1 and "no folder no_such_folder" in result.output
    with pytest.raises(FileNotFoundError, match="no folder"):
        real_check_folder(tmp_path / "no_such_folder")
