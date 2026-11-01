"""Demo bundle export on a synthetic finished job folder (no pipeline, no API calls).

Media paths are checked the way the web app resolves them in demo mode (web/src/demo.ts
demoMediaUrl): with a Python port, and with the real TypeScript when node is available.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest

from showtell.api import db
from showtell.api.export_demo import _Bundle, _refuse_local_paths, export_demo
from showtell.contracts import AttemptReport, Event, JobReport, SeedSummary
from showtell.verify.runner import CheckResult

JOB_ID = "20261031-140000-abcdef"
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32
ROOT = Path(__file__).resolve().parent.parent
DEMO_BASE = "/demo/"
MEDIA_RE = re.compile(r"\S+\.(mp4|mov|m4v|webm|jpe?g|png|gif)$", re.IGNORECASE)


def _seed(root: Path, n: int, seed: int, passed: bool) -> SeedSummary:
    rel = f"attempts/{n}/seed_{seed}"
    (root / rel).mkdir(parents=True, exist_ok=True)
    for name in ("full.mp4", "judge.mp4", "judge_angle.mp4"):
        (root / rel / name).write_bytes(MP4 + name.encode())
    (root / rel / "thumb.jpg").write_bytes(b"\xff\xd8 thumb \xff\xd9")
    (root / rel / "result.json").write_text("{}")  # not media: never exported
    return SeedSummary(
        seed=seed,
        passed=passed,
        checks=[
            CheckResult(check_id="glass_untouched", kind="untouched", passed=passed, detail="")
        ],
        videos={
            "full": f"{rel}/full.mp4",
            "overhead": f"{rel}/judge.mp4",
            "judge_angle": f"{rel}/judge_angle.mp4",
            "thumb": f"{rel}/thumb.jpg",
        },
    )


def make_job(data_dir: Path, job_id: str = JOB_ID, created_at: float = 1_793_400_000.0) -> Path:
    """A finished job folder: 2 attempts x 2 seeds, input video, keyframes, repair frames."""
    root = data_dir / "jobs" / job_id
    (root / "input").mkdir(parents=True)
    (root / "input" / "demo.mp4").write_bytes(MP4 + b"input")
    (root / "prep" / "keyframes").mkdir(parents=True)
    (root / "prep" / "normalized.mp4").write_bytes(MP4 + b"normalized")
    for i in range(2):
        (root / "prep" / "keyframes" / f"kf_{i:02d}.jpg").write_bytes(b"\xff\xd8 kf \xff\xd9")
    (root / "attempts" / "0" / "repair_frames").mkdir(parents=True)
    (root / "attempts" / "0" / "repair_frames" / "frame_00.jpg").write_bytes(b"\xff\xd8 f \xff\xd9")
    a0 = [_seed(root, 0, 3000, True), _seed(root, 0, 3001, False)]
    a1 = [_seed(root, 1, 3000, True), _seed(root, 1, 3001, True)]
    report = JobReport(
        job_id=job_id,
        created_at=created_at,
        rules="Sort by size, and don't touch the glass.",
        use_video=True,
        task_id="task03_size_glass",
        object_ids=["red_cube_large", "clear_glass_large"],
        video="input/demo.mp4",
        attempts=[
            AttemptReport(
                n=0,
                kind="initial",
                program="def run(scene):\n    pass\n",
                passed=1,
                total=2,
                seeds=a0,
            ),
            AttemptReport(
                n=1,
                kind="repair",
                program="def run(scene):\n    return\n",
                diagnosis="The block hit the glass.",
                passed=2,
                total=2,
                seeds=a1,
            ),
        ],  # fmt: skip
        status="verified",
        final_program="def run(scene):\n    return\n",
        final_passed=2,
        final_total=2,
        seeds=[3000, 3001],
    )
    (root / "report.json").write_text(report.model_dump_json(indent=2))
    (root / "report.md").write_text("# Show & Tell job\n\n**VERIFIED**\n")
    events = [
        Event(type="job_started", ts=created_at, job_id=job_id, data={"rules": report.rules}),
        Event(type="seed_done", ts=created_at + 5, job_id=job_id,
              data={"attempt": 0, "seed": a0[1].model_dump(mode="json")}),
        Event(type="job_done", ts=created_at + 60, job_id=job_id,
              data={"status": "verified", "report": report.model_dump(mode="json")}),
    ]  # fmt: skip
    (root / "events.jsonl").write_text("".join(e.model_dump_json() + "\n" for e in events))
    return root


def read(path: Path) -> Any:
    return json.loads(path.read_text())


def _encode_path(path: str) -> str:
    return "/".join(quote(part, safe="!'()*~") for part in path.split("/"))


def demo_media_url(path: str | None, job_id: str) -> str | None:
    """Python port of web/src/demo.ts demoMediaUrl (DEMO_BASE = "/demo/"), the fallback when
    node or web/node_modules is missing."""
    if not path:
        return None
    if re.match(r"^(https?:|data:|blob:)", path):
        return path
    live = re.match(r"^/media/jobs/([^/]+)/(.+)$", path)
    if live:
        return f"{DEMO_BASE}media/{_encode_path(live[1])}/{_encode_path(live[2])}"
    if path.startswith("/"):
        return path
    rel = re.sub(r"^(\./)+", "", path)
    if rel.startswith("media/"):
        return DEMO_BASE + _encode_path(rel)
    return f"{DEMO_BASE}media/{_encode_path(job_id)}/{_encode_path(rel)}"


def ts_demo_media_urls(paths: list[str], job_id: str) -> list[str] | None:
    """The real demoMediaUrl from web/src/demo.ts (transpiled with web's TypeScript), or None."""
    node, typescript = shutil.which("node"), ROOT / "web" / "node_modules" / "typescript"
    demo_ts = ROOT / "web" / "src" / "demo.ts"
    if not node or not typescript.is_dir() or not demo_ts.is_file():
        return None
    script = """
const fs = require("fs");
const ts = require(process.argv[1]);
const src = fs.readFileSync(process.argv[2], "utf8");
const js = ts.transpileModule(src, { compilerOptions: { module: ts.ModuleKind.CommonJS,
  target: ts.ScriptTarget.ES2020 } }).outputText;
const mod = { exports: {} };
const req = (name) => (name === "./config" ? { DEMO_BASE: "/demo/" } : {});
new Function("module", "exports", "require", js)(mod, mod.exports, req);
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const urls = input.paths.map((p) => mod.exports.demoMediaUrl(p, input.job));
process.stdout.write(JSON.stringify(urls));
"""
    proc = subprocess.run(
        [node, "-e", script, str(typescript), str(demo_ts)],
        input=json.dumps({"paths": paths, "job": job_id}),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def media_strings(obj: Any) -> list[str]:
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in media_strings(v)]
    if isinstance(obj, list):
        return [s for v in obj for s in media_strings(v)]
    return [obj] if isinstance(obj, str) and MEDIA_RE.fullmatch(obj) else []


def assert_web_resolves(out: Path, job_id: str, *docs: Any) -> list[str]:
    """Every media path in `docs`, resolved the way the web app does it in demo mode (the real
    web/src/demo.ts when node is available), names a file that is in the bundle."""
    paths = sorted({p for doc in docs for p in media_strings(doc)})
    assert paths, "no media paths found"
    real = ts_demo_media_urls(paths, job_id)
    urls = real if real is not None else [demo_media_url(p, job_id) for p in paths]
    for path, url in zip(paths, urls, strict=True):
        assert url is not None and url.startswith(DEMO_BASE), (path, url)
        assert (out / url.removeprefix(DEMO_BASE)).is_file(), (
            f"{path} -> {url} is not in the bundle"
        )
    return paths


def test_export_folder_bundle(tmp_path: Path):
    data, out = tmp_path / "data", tmp_path / "demo"
    job_dir = make_job(data)
    bench = tmp_path / "results.json"
    bench.write_text(json.dumps({"tasks": []}))
    result = export_demo(job_dir, out, data_dir=data, bench_results=bench, verbose=False)
    assert result.job_id == JOB_ID and result.missing == []

    media = f"media/{JOB_ID}/"
    report = read(out / JOB_ID / "report.json")
    seed = report["attempts"][0]["seeds"][1]
    assert seed["videos"]["full"] == media + "attempts/0/seed_3001/full.mp4"
    assert seed["videos"]["thumb"] == media + "attempts/0/seed_3001/thumb.jpg"
    assert report["video"] == media + "input/demo.mp4"
    assert report["final_program"] == "def run(scene):\n    return\n"  # code is never rewritten
    for rel in (
        "attempts/1/seed_3000/full.mp4",
        "attempts/1/seed_3000/judge.mp4",
        "attempts/1/seed_3000/judge_angle.mp4",
        "attempts/0/seed_3001/thumb.jpg",
        "input/demo.mp4",
        "prep/normalized.mp4",
        "prep/keyframes/kf_01.jpg",
        "attempts/0/repair_frames/frame_00.jpg",
    ):
        assert (out / media / rel).is_file(), rel
    assert not list(out.rglob("result.json"))
    assert (out / media / "input/demo.mp4").read_bytes() == MP4 + b"input"
    assert result.files == 4 * 4 + 1 + 1 + 2 + 1

    events = read(out / JOB_ID / "events.json")
    assert [e["type"] for e in events] == ["job_started", "seed_done", "job_done"]
    assert [e["ts"] for e in events] == [1_793_400_000.0, 1_793_400_005.0, 1_793_400_060.0]
    assert (
        events[1]["data"]["seed"]["videos"]["overhead"] == media + "attempts/0/seed_3001/judge.mp4"
    )
    assert events[2]["data"]["report"]["video"] == media + "input/demo.mp4"

    job = read(out / JOB_ID / "job.json")
    assert job["job"]["id"] == JOB_ID and job["job"]["status"] == "verified"
    assert job["job"]["video"] == media + "input/demo.mp4"
    assert job["job"]["links"]["report_md"] == f"{JOB_ID}/report.md"
    assert [a["passed"] for a in job["attempts"]] == [1, 2]
    assert job["attempts"][0]["failure_frames"] == [media + "attempts/0/repair_frames/frame_00.jpg"]
    assert job["media"]["keyframes"] == [
        media + "prep/keyframes/kf_00.jpg",
        media + "prep/keyframes/kf_01.jpg",
    ]
    assert job["media"]["normalized_video"] == media + "prep/normalized.mp4"
    assert (out / JOB_ID / "report.md").read_text().startswith("# Show & Tell job")
    assert (out / JOB_ID / "program.py").read_text() == "def run(scene):\n    return\n"

    # what the web app loads in demo mode really is in the bundle
    resolved = assert_web_resolves(out, JOB_ID, report, events, job)
    assert media + "input/demo.mp4" in resolved and len(resolved) >= 4 * 4 + 1

    assert read(out / "index.json") == [
        {
            "id": JOB_ID,
            "rules": "Sort by size, and don't touch the glass.",
            "status": "verified",
            "attempts": 2,
            "created_at": 1_793_400_000.0,
            "task_id": "task03_size_glass",
            "use_video": True,
            "passed": 2,
            "total": 2,
        }
    ]
    assert read(out / "bench.json") == {"tasks": []}


def test_web_media_url_port_matches_web_demo_ts():
    """The fallback port agrees with web/src/demo.ts on every path form the bundle can hold."""
    full = f"media/{JOB_ID}/attempts/0/seed_1/full.mp4"
    cases = {
        full: f"/demo/{full}",
        "attempts/0/seed_1/thumb.jpg": f"/demo/media/{JOB_ID}/attempts/0/seed_1/thumb.jpg",
        "/media/jobs/other/input/a.mp4": "/demo/media/other/input/a.mp4",
        "/demo/media/x/a.mp4": "/demo/media/x/a.mp4",
        "https://cdn.test/a.mp4": "https://cdn.test/a.mp4",
    }
    assert {p: demo_media_url(p, JOB_ID) for p in cases} == cases
    real = ts_demo_media_urls(list(cases), JOB_ID)
    if real is None:
        pytest.skip("node or web/node_modules/typescript is not available")
    assert dict(zip(cases, real, strict=True)) == cases


def test_index_is_updated_not_duplicated(tmp_path: Path):
    data, out = tmp_path / "data", tmp_path / "demo"
    make_job(data)
    newer = "20261031-150000-123456"
    make_job(data, newer, created_at=1_793_500_000.0)
    no_bench = tmp_path / "missing.json"
    export_demo(JOB_ID, out, data_dir=data, bench_results=no_bench, verbose=False)
    export_demo(newer, out, data_dir=data, bench_results=no_bench, verbose=False)
    export_demo(JOB_ID, out, data_dir=data, bench_results=no_bench, verbose=False)  # re-export
    assert [e["id"] for e in read(out / "index.json")] == [newer, JOB_ID]
    assert not (out / "bench.json").exists()


def test_index_with_a_jobs_wrapper_is_kept(tmp_path: Path):
    data, out = tmp_path / "data", tmp_path / "demo"
    make_job(data)
    out.mkdir()
    (out / "index.json").write_text(json.dumps({"jobs": [{"id": "older-run", "created_at": 1.0}]}))
    export_demo(JOB_ID, out, data_dir=data, bench_results=tmp_path / "none.json", verbose=False)
    assert [e["id"] for e in read(out / "index.json")] == [JOB_ID, "older-run"]


def test_export_by_job_id_uses_the_database_report(tmp_path: Path):
    data, out = tmp_path / "data", tmp_path / "demo"
    job_dir = make_job(data)
    report_json = (job_dir / "report.json").read_text()
    (job_dir / "report.json").unlink()  # only the DB copy is left
    engine = db.make_engine(db.db_path(data))
    with db.session(engine) as s:
        s.add(db.Job(id=JOB_ID, rules_text="Sort by size.", status="verified", auto_approve=False,
                     report_json=report_json))  # fmt: skip
        s.commit()
    engine.dispose()
    export_demo(JOB_ID, out, data_dir=data, bench_results=tmp_path / "none.json", verbose=False)
    job = read(out / JOB_ID / "job.json")
    assert job["job"]["auto_approve"] is False  # from the DB row
    assert (
        read(out / JOB_ID / "report.json")["attempts"][1]["diagnosis"] == "The block hit the glass."
    )


def test_external_video_and_missing_media(tmp_path: Path):
    data, out = tmp_path / "data", tmp_path / "demo"
    job_dir = make_job(data)
    outside = tmp_path / "bench_videos" / "task03_size_glass.mp4"
    outside.parent.mkdir()
    outside.write_bytes(MP4 + b"bench")
    report = read(job_dir / "report.json")
    report["video"] = str(outside)  # e.g. a CLI run with --video bench/videos/...
    report["attempts"][1]["seeds"][0]["videos"]["full"] = "attempts/1/seed_3000/gone.mp4"
    (job_dir / "report.json").write_text(json.dumps(report))
    result = export_demo(job_dir, out, data_dir=data, bench_results=tmp_path / "x", verbose=False)
    exported = read(out / JOB_ID / "report.json")
    assert exported["video"] == f"media/{JOB_ID}/input/task03_size_glass.mp4"
    assert (
        out / "media" / JOB_ID / "input" / "task03_size_glass.mp4"
    ).read_bytes() == MP4 + b"bench"
    assert result.missing == ["attempts/1/seed_3000/gone.mp4"]
    exported["attempts"][1]["seeds"][0]["videos"].pop("full")  # the one missing file
    assert_web_resolves(out, JOB_ID, exported)


def test_unknown_or_unfinished_job(tmp_path: Path):
    data = tmp_path / "data"
    with pytest.raises(FileNotFoundError, match="no job folder"):
        export_demo("20261031-000000-000000", tmp_path / "demo", data_dir=data, verbose=False)
    job_dir = make_job(data)
    (job_dir / "report.json").unlink()
    with pytest.raises(FileNotFoundError, match="has the job finished"):
        export_demo(job_dir, tmp_path / "demo", data_dir=data, verbose=False)


def test_titles_and_notes_label_runs_and_survive_a_re_export(tmp_path: Path):
    data, out = tmp_path / "data", tmp_path / "demo"
    make_job(data)
    no_bench = tmp_path / "none.json"
    note = "Stand-in video: a render of the robot, not a human demonstration."
    export_demo(JOB_ID, out, data_dir=data, bench_results=no_bench, verbose=False,
                title="Sort by size", note=note)  # fmt: skip
    export_demo(JOB_ID, out, data_dir=data, bench_results=no_bench, verbose=False)  # keeps both
    (entry,) = read(out / "index.json")
    assert (entry["title"], entry["note"]) == ("Sort by size", note)
    assert read(out / JOB_ID / "report.json")["demo_note"] == note
    md = (out / JOB_ID / "report.md").read_text()
    assert md.startswith(f"# Show & Tell job\n\n> {note}\n\n**VERIFIED**")
    assert "demo_note" not in read(data / "jobs" / JOB_ID / "report.json")  # source untouched
    export_demo(JOB_ID, out, data_dir=data, bench_results=no_bench, verbose=False, note="")
    (entry,) = read(out / "index.json")
    assert "note" not in entry and "demo_note" not in read(out / JOB_ID / "report.json")


def local_paths_in(out: Path, *folders: Path) -> list[str]:
    """`<bundle file>: <local path>` for every text file of the bundle (JSON, Markdown, program)
    that names one of `folders`, the temp folder, the home folder or the repo, as given or
    resolved."""
    roots = (*folders, Path(tempfile.gettempdir()), Path.home(), ROOT)
    needles = sorted({str(p) for f in roots for p in (f, f.resolve())})
    hits: list[str] = []
    for path in sorted(out.rglob("*")):
        if path.suffix in (".json", ".md", ".py"):
            text = path.read_text(encoding="utf-8")
            hits += [f"{path.relative_to(out)}: {n}" for n in needles if n in text]
    return hits


def test_job_detail_never_carries_an_absolute_local_video_path(tmp_path: Path):
    base = tmp_path / "with space"  # like the checkout's folder, "Meta Hackathon"
    data, out = base / "data", tmp_path / "demo"
    job_dir = make_job(data)
    engine = db.make_engine(db.db_path(data))
    with db.session(engine) as s:  # an API job: the row holds the absolute upload path
        s.add(db.Job(id=JOB_ID, rules_text="Sort by size.", status="verified",
                     video_path=str(job_dir / "input" / "demo.mp4"),
                     report_json=(job_dir / "report.json").read_text()))  # fmt: skip
        s.commit()
    engine.dispose()
    export_demo(JOB_ID, out, data_dir=data, bench_results=tmp_path / "none.json", verbose=False)
    assert local_paths_in(out, base) == []
    assert read(out / JOB_ID / "job.json")["job"]["video"] == f"media/{JOB_ID}/input/demo.mp4"


def test_a_video_from_outside_the_job_folder_is_bundled_without_any_local_path(tmp_path: Path):
    """An older CLI or benchmark run: report.video is the absolute --video path, outside the job
    folder, in a folder whose name has a space (as in the checkout's "Meta Hackathon")."""
    base = tmp_path / "with space"
    data, out = base / "data", tmp_path / "demo"
    job_dir = make_job(data)
    shutil.rmtree(job_dir / "input")  # the video was never copied into the job folder
    video = base / "bench" / "videos" / "task03_size_glass.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(MP4 + b"bench")
    report = read(job_dir / "report.json")
    report["video"] = str(video)
    (job_dir / "report.json").write_text(json.dumps(report))
    lines = (job_dir / "events.jsonl").read_text().splitlines()
    done = json.loads(lines[-1])
    done["data"]["report"]["video"] = str(video)
    (job_dir / "events.jsonl").write_text("\n".join([*lines[:-1], json.dumps(done)]) + "\n")

    result = export_demo(job_dir, out, data_dir=data, bench_results=tmp_path / "x", verbose=False)
    media = f"media/{JOB_ID}/input/task03_size_glass.mp4"
    assert read(out / JOB_ID / "report.json")["video"] == media
    assert read(out / JOB_ID / "job.json")["job"]["video"] == media
    assert read(out / JOB_ID / "job.json")["report"]["video"] == media
    assert read(out / JOB_ID / "events.json")[-1]["data"]["report"]["video"] == media
    assert (out / media).read_bytes() == MP4 + b"bench"  # the human video is in the bundle
    assert result.missing == []
    assert local_paths_in(out, tmp_path, base, video.parent) == []
    assert_web_resolves(out, JOB_ID, read(out / JOB_ID / "report.json"))


def test_a_moved_outside_video_never_leaks_its_old_path(tmp_path: Path):
    data, out = tmp_path / "data", tmp_path / "demo"
    job_dir = make_job(data)
    report = read(job_dir / "report.json")
    report["video"] = str(tmp_path / "gone" / "task03_size_glass.mp4")  # moved since the run
    (job_dir / "report.json").write_text(json.dumps(report))
    no_bench = tmp_path / "none.json"
    result = export_demo(job_dir, out, data_dir=data, bench_results=no_bench, verbose=False)
    media = f"media/{JOB_ID}/input/task03_size_glass.mp4"
    assert read(out / JOB_ID / "report.json")["video"] == media
    assert result.missing == ["input/task03_size_glass.mp4"]
    assert local_paths_in(out, tmp_path) == []
    # the job folder's own copy (the pipeline links a video given from outside there) is used
    (job_dir / "input" / "task03_size_glass.mp4").write_bytes(MP4 + b"linked")
    result = export_demo(job_dir, out, data_dir=data, bench_results=no_bench, verbose=False)
    assert result.missing == [] and (out / media).read_bytes() == MP4 + b"linked"


def test_an_export_that_would_publish_a_local_path_is_refused(tmp_path: Path):
    data, out = tmp_path / "data", tmp_path / "demo"
    job_dir = make_job(data)
    report = read(job_dir / "report.json")
    report["error"] = f"FileNotFoundError: {job_dir / 'prep' / 'frames.txt'}"  # free text
    (job_dir / "report.json").write_text(json.dumps(report))
    kw: dict[str, Any] = {"data_dir": data, "bench_results": tmp_path / "none.json"}
    with pytest.raises(ValueError, match=f"{JOB_ID}/report.json would publish a local path"):
        export_demo(job_dir, out, verbose=False, **kw)
    assert not out.exists()  # refused before anything was written

    make_job(data, "20261031-150000-123456")
    with pytest.raises(ValueError, match="index.json would publish a local path"):
        export_demo("20261031-150000-123456", out, verbose=False, title=f"{Path.home()}/x", **kw)
    bench = tmp_path / "results.json"  # JSON may escape "/" as "\/": still caught
    bench.write_text('{"error": "' + str(data / "v.mp4").replace("/", "\\/") + ' gone"}')
    with pytest.raises(ValueError, match=r"bench\.json \(from results\.json\)"):
        export_demo("20261031-150000-123456", out, data_dir=data, bench_results=bench)
    assert not out.exists()


def test_every_absolute_media_path_is_mapped_except_api_urls(tmp_path: Path):
    job_dir = make_job(tmp_path / "data")
    bundle = _Bundle(job_dir, JOB_ID)
    media = f"media/{JOB_ID}/"
    assert bundle._map(str(job_dir / "input" / "demo.mp4")) == media + "input/demo.mp4"
    assert bundle._map("/Volumes/USB stick/Clip 1.MOV") == media + "input/Clip_1.mov"
    assert bundle._map("/media/niko/usb/task01_size.mp4") == media + "input/task01_size.mp4"
    assert bundle._map("/media/jobs/other/input/a.mp4") == "/media/jobs/other/input/a.mp4"
    assert bundle._map("Sort by size, then clip.mp4") == "Sort by size, then clip.mp4"
    assert bundle.files["input/demo.mp4"] == job_dir / "input" / "demo.mp4"
    assert bundle.files["input/Clip_1.mov"] is None  # not on this machine: reported missing


def test_local_path_guard_reads_keys_and_ignores_lookalikes():
    roots = ["/home/me"]
    fine = {"a": "/api/home/me", "b": "/home/me2/x", "c": "see /home/meadow", "d": ["x"]}
    _refuse_local_paths({"a.json": fine, "b.md": "no paths here", "c.json": None}, roots)
    with pytest.raises(ValueError, match="a.json"):
        _refuse_local_paths({"a.json": {"by_path": {"/home/me/clip.mp4": 1}}}, roots)  # a key
    with pytest.raises(ValueError, match="b.md"):
        _refuse_local_paths({"b.md": "error:\n/home/me"}, roots)
    _refuse_local_paths({"a.json": {"x": "/home/me"}}, [])  # no roots: nothing to refuse


def test_folder_export_job_json_says_how_the_run_went(tmp_path: Path):
    """A run without a database row (CLI, benchmark): job.json's auto_approve and use_judge
    come from its events and report, not from the database defaults (False, True)."""
    data, out = tmp_path / "data", tmp_path / "demo"
    job_dir = make_job(data)  # no awaiting_approval, no judge
    kw: dict[str, Any] = {"data_dir": data, "bench_results": tmp_path / "x", "verbose": False}
    export_demo(job_dir, out, **kw)
    job = read(out / JOB_ID / "job.json")["job"]
    assert (job["auto_approve"], job["use_judge"]) == (True, False)

    paused = Event(type="awaiting_approval", job_id=JOB_ID, data={"spec": None})
    judged = Event(type="judge_done", job_id=JOB_ID, data={"attempt": 0, "seed": 3000})
    with (job_dir / "events.jsonl").open("a") as f:
        f.write(paused.model_dump_json() + "\n" + judged.model_dump_json() + "\n")
    export_demo(job_dir, out, **kw)
    job = read(out / JOB_ID / "job.json")["job"]
    assert (job["auto_approve"], job["use_judge"]) == (False, True)

    make_job(data, "20261031-150000-123456")  # judged, recorded before judge_done was saved
    folder = data / "jobs" / "20261031-150000-123456"
    report = read(folder / "report.json")
    report["attempts"][0]["agreement"] = {"agree": 2, "n": 2, "undecided": 0, "by_kind": {}}
    (folder / "report.json").write_text(json.dumps(report))
    export_demo(folder, out, **kw)
    assert read(out / "20261031-150000-123456" / "job.json")["job"]["use_judge"] is True


def test_a_note_with_paragraphs_is_quoted_whole_and_reaches_job_json(tmp_path: Path):
    data, out = tmp_path / "data", tmp_path / "demo"
    make_job(data)
    kw: dict[str, Any] = {"data_dir": data, "bench_results": tmp_path / "x", "verbose": False}
    note = "Stand-in video: a render.\n\n# Not a heading\nResponses came from the cache."
    export_demo(JOB_ID, out, note=note, **kw)
    md = (out / JOB_ID / "report.md").read_text()
    assert md.startswith(
        "# Show & Tell job\n\n> Stand-in video: a render.\n>\n> # Not a heading\n"
        "> Responses came from the cache.\n\n**VERIFIED**\n"
    )
    assert read(out / JOB_ID / "report.json")["demo_note"] == note
    assert read(out / JOB_ID / "job.json")["report"]["demo_note"] == note
    export_demo(JOB_ID, out, note="", **kw)  # cleared everywhere
    assert "demo_note" not in read(out / JOB_ID / "job.json")["report"]
    assert "> " not in (out / JOB_ID / "report.md").read_text()


def test_a_note_never_swallows_the_line_after_the_title(tmp_path: Path):
    data, out = tmp_path / "data", tmp_path / "demo"
    job_dir = make_job(data)
    (job_dir / "report.md").write_text("# Show & Tell job\n**VERIFIED**\n")  # no blank line
    export_demo(JOB_ID, out, data_dir=data, bench_results=tmp_path / "x", verbose=False, note="N")
    assert (out / JOB_ID / "report.md").read_text() == "# Show & Tell job\n\n> N\n\n**VERIFIED**\n"


def test_a_pipeline_run_on_an_outside_video_exports_with_the_video_and_no_local_path(
    tmp_path: Path,
):
    """End to end, the way the benchmark runs the video condition (an absolute --video under the
    checkout): the pipeline links the video into the job folder and the report names it
    input/<name>; the export carries it and holds no local path. No model calls: the spec and
    program are given and the SAM judge is off."""
    from showtell import pipeline
    from showtell.agent.schemas import TaskSpec
    from showtell.contracts import JobConfig
    from showtell.perception.video_prep import make_test_clip
    from showtell.sim.scene import get_task

    base = tmp_path / "with space"
    video = make_test_clip(base / "bench" / "videos" / "task01_size.mp4", seconds=1.0)
    task = get_task("task01_size")
    root = base / "data" / "jobs" / "bench-task01_size-video"
    config = JobConfig(
        job_id=root.name,
        rules_text=task.rules,
        video_path=video,
        use_video=True,
        task_id=task.id,
        n_seeds=1,
        use_judge=False,
        spec=TaskSpec.model_validate({**task.reference, "rationale": "given"}),
        program_source=(ROOT / "examples/reference/task01_size.py").read_text(),
        out_dir=root,
    )
    report = pipeline.run_job(config)
    assert report.status == "verified", report.error
    assert report.video == "input/task01_size.mp4"
    assert (root / report.video).read_bytes() == video.read_bytes()
    for name in ("report.json", "events.jsonl", "report.md"):
        assert str(base) not in (root / name).read_text(), name

    out = tmp_path / "demo"
    result = export_demo(
        root, out, data_dir=base / "data", bench_results=tmp_path / "x", verbose=False
    )
    media = out / "media" / root.name / "input" / "task01_size.mp4"
    assert media.read_bytes() == video.read_bytes() and result.missing == []
    assert (
        read(out / root.name / "report.json")["video"] == f"media/{root.name}/input/task01_size.mp4"
    )
    assert local_paths_in(out, tmp_path, base) == []
