"""Video prep with real ffmpeg on a synthetic clip (no network)."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import cv2
import pytest

from showtell.config import settings
from showtell.perception import video_prep as vp


def run_together(fn: Callable[[], Any], n: int = 4) -> list[Any]:
    """Run ``fn`` in ``n`` threads released at once; results or raised exceptions, in order."""
    barrier = threading.Barrier(n)
    out: list[Any] = [None] * n

    def work(i: int) -> None:
        barrier.wait()
        try:
            out[i] = fn()
        except Exception as err:  # collected and asserted on by the caller
            out[i] = err

    threads = [threading.Thread(target=work, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return out


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(settings, "SHOWTELL_DATA_DIR", tmp_path / "data")
    return tmp_path


@pytest.fixture(scope="module")
def clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return vp.make_test_clip(tmp_path_factory.mktemp("clip") / "red_square.mp4", seconds=5)


def test_make_test_clip_and_probe(clip: Path) -> None:
    info = vp.probe(clip)
    assert (info.frames, info.fps, info.width, info.height) == (125, 25.0, 640, 480)
    assert info.duration == pytest.approx(5.0, abs=0.05)
    assert vp._probe_cv2(clip)[:4] == (125, 25.0, 640, 480)


def test_keyframes(clip: Path, tmp_path: Path) -> None:
    paths = vp.keyframes(clip, n=8, out_dir=tmp_path / "kf")
    assert [p.name for p in paths] == [f"keyframe_{i:02d}.jpg" for i in range(8)]
    first, last = cv2.imread(str(paths[0])), cv2.imread(str(paths[-1]))
    assert first.shape == (480, 640, 3)  # narrower than 768 px: kept as is

    def red_x(img: object) -> float:
        b, g, r = cv2.split(img)  # type: ignore[arg-type]
        ys, xs = ((r > 150) & (g < 100) & (b < 100)).nonzero()
        return float(xs.mean())

    assert red_x(first) < 150 < 450 < red_x(last)  # the square slides left to right
    assert len(vp.keyframes(clip, n=3, width=320, out_dir=tmp_path / "kf")) == 3
    assert sorted(p.name for p in (tmp_path / "kf").iterdir()) == [
        "keyframe_00.jpg",
        "keyframe_01.jpg",
        "keyframe_02.jpg",
    ]
    assert cv2.imread(str(tmp_path / "kf" / "keyframe_00.jpg")).shape == (240, 320, 3)


def test_normalize_is_720p_10fps_and_cached(clip: Path, tmp_path: Path) -> None:
    out = vp.normalize(clip, tmp_path / "norm.mp4")
    info = vp.probe(out)
    assert (info.height, info.width, info.fps, info.frames) == (720, 960, 10.0, 50)
    cached = vp.normalize(clip)
    assert cached.parent == settings.CACHE_DIR / "video"
    mtime = cached.stat().st_mtime_ns
    assert vp.normalize(clip) == cached and cached.stat().st_mtime_ns == mtime  # no re-encode
    assert out.read_bytes() == cached.read_bytes()


def test_normalize_cache_key_tracks_the_encode_args(
    clip: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = vp.normalize(clip)
    monkeypatch.setattr(vp, "X264_OUT", [*vp.X264_OUT, "-pix_fmt", "yuv420p"])
    second = vp.normalize(clip)
    assert second != first and second.exists() and first.exists()
    assert vp.normalize(clip) == second  # cached under the new arguments


def test_concurrent_normalize_shares_one_valid_output(clip: Path, tmp_path: Path) -> None:
    results = run_together(lambda: vp.normalize(clip))
    assert all(isinstance(r, Path) for r in results), results
    assert len(set(results)) == 1
    assert vp.probe(results[0]).frames == 50
    assert [p.name for p in (settings.CACHE_DIR / "video").iterdir()] == [results[0].name]
    outs = run_together(lambda: vp.normalize(clip, tmp_path / "job" / "normalized.mp4"))
    assert all(isinstance(r, Path) for r in outs), outs
    assert outs[0].read_bytes() == results[0].read_bytes()
    assert [p.name for p in (tmp_path / "job").iterdir()] == ["normalized.mp4"]


def test_concurrent_keyframes_never_lose_files(clip: Path) -> None:
    for _ in range(5):  # the old unguarded unlink loop failed about 2 rounds in 3
        results = run_together(lambda: vp.keyframes(clip))  # shared default cache folder
        for paths in results:
            assert isinstance(paths, list), paths
            assert len(paths) == 8 and all(p.exists() for p in paths)
        folder = results[0][0].parent
        assert sorted(p.name for p in folder.iterdir()) == [p.name for p in results[0]]


def test_remux_mp4_keeps_the_frames(tmp_path: Path) -> None:
    mov = vp.make_test_clip(tmp_path / "clip.mov", seconds=2)
    mp4 = vp.remux_mp4(mov)
    assert mp4.suffix == ".mp4" and mp4.parent == settings.CACHE_DIR / "video"
    assert vp.probe(mp4)[:4] == vp.probe(mov)[:4] == (50, 25.0, 640, 480)
    mtime = mp4.stat().st_mtime_ns
    assert vp.remux_mp4(mov) == mp4 and mp4.stat().st_mtime_ns == mtime  # cached


def test_clip_head(clip: Path, tmp_path: Path) -> None:
    info = vp.probe(vp.clip_head(clip, tmp_path / "head.mp4", seconds=2))
    assert info.frames == 50 and info.duration == pytest.approx(2.0, abs=0.05)


@pytest.mark.parametrize(
    ("n_frames", "n", "expected"),
    [
        (0, 8, []),
        (5, 8, [0, 1, 2, 3, 4]),
        (100, 1, [0]),
        (101, 3, [0, 50, 100]),
        (50, 8, [0, 7, 14, 21, 28, 35, 42, 49]),
    ],
)
def test_keyframe_indices(n_frames: int, n: int, expected: list[int]) -> None:
    assert vp.keyframe_indices(n_frames, n) == expected
