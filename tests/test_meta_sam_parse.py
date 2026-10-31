"""SAM output parsing: docs example lines, official parser vs regex fallback."""

from __future__ import annotations

import logging

import pytest

from showtell.meta import sam
from showtell.meta.sam import FrameBox, parse, parse_sam_text, parse_sam_text_official

# From https://dev.meta.ai/docs/sam/reading-segmentation (video output grammar, frame 0).
DOCS_VIDEO_LINE = (
    "<0f>0<|box;x1=211;y1=228;x2=270;y2=254;w=320;h=334|>"
    "<|mask;x=0;y=0;data=27,60,~!!!!M!0c[0o91w?q1!pIH4pPRVp2B3'7`e.ioeAf6-k/#Xd8%dX9x(|>"
    ",1<|box;x1=155;y1=228;x2=202;y2=254;w=320;h=334|>"
    "<|mask;x=0;y=0;data=27,48,~!!!!J!0c[q=Pj_zs=*4C4(/./x#:/`S_GnD`=o3?X{emCgO$y@|>"
)
# Confidence example (payload elided in the docs; the box must still parse).
DOCS_CONF_LINE = (
    "<0f>1<|box;x1=444;y1=625;x2=679;y2=850;w=1800;h=1200;c=0.9609375|>"
    "<|mask;x=0;y=0;c=0.9609375;data=226,236,!...|>"
)


def rec(oid: int, x1: int, y1: int, x2: int, y2: int, c: float | None = None) -> str:
    conf = f";c={c}" if c is not None else ""
    return (
        f"{oid}<|box;x1={x1};y1={y1};x2={x2};y2={y2};w=320;h=334{conf}|>"
        f"<|mask;x=0;y=0{conf};data={y2 - y1 + 1},{x2 - x1 + 1},!!!!J!0c[q=Pj_zs|>"
    )


def line(frame: int, *records: str) -> str:
    return f"<{frame}f>" + ",".join(records)


SPARSE = "\n".join(
    [
        line(0, rec(0, 10, 10, 40, 40, 0.9), rec(2, 100, 50, 130, 90, 0.8)),
        line(1, rec(2, 102, 52, 132, 92, 0.85)),  # object 0 left the frame
    ]
)
OUT_OF_ORDER = "\n".join(  # frames skip (1, 3 missing) and arrive descending
    [
        line(4, rec(5, 40, 40, 60, 60)),
        line(2, rec(5, 20, 20, 40, 40)),
        line(0, rec(5, 0, 0, 20, 20)),
    ]
)
# Payload with "~", "!", ",", "<", ">" and digits: must not split records.
TRICKY_PAYLOAD = (
    "<3f>7<|box;x1=1;y1=2;x2=11;y2=12;w=320;h=334;c=0.5|>"
    "<|mask;x=0;y=0;c=0.5;data=11,11,~a,9~!b<c>12,0!z|>"
    ",9<|box;x1=20;y1=30;x2=40;y2=50;w=320;h=334;c=0.25|>"
    "<|mask;x=0;y=0;c=0.25;data=21,21,!7,<x>~9|>"
)
FIXTURES = {
    "docs_video": DOCS_VIDEO_LINE,
    "docs_conf": DOCS_CONF_LINE,
    "sparse_ids": SPARSE,
    "out_of_order": OUT_OF_ORDER,
    "tricky_payload": TRICKY_PAYLOAD,
    "crlf": SPARSE.replace("\n", "\r\n") + "\r\n",
    "no_newlines": SPARSE.replace("\n", ""),
    "empty": "",
}


def test_docs_video_line() -> None:
    tracks = parse_sam_text(DOCS_VIDEO_LINE)
    assert tracks == {
        0: [FrameBox(frame=0, x1=211, y1=228, x2=270, y2=254, w=320, h=334)],
        1: [FrameBox(frame=0, x1=155, y1=228, x2=202, y2=254, w=320, h=334)],
    }
    box = tracks[0][0]
    assert (box.cx, box.cy) == (240.5, 241.0)
    assert box.area == 60 * 27  # inclusive box == the 27x60 mask raster
    assert box.conf is None


def test_docs_confidence_line() -> None:
    tracks = parse_sam_text(DOCS_CONF_LINE)
    assert list(tracks) == [1]
    assert tracks[1][0].conf == pytest.approx(0.9609375)
    assert (tracks[1][0].w, tracks[1][0].h) == (1800, 1200)


def test_sparse_ids_are_keyed_by_id_not_position() -> None:
    tracks = parse_sam_text(SPARSE)
    assert sorted(tracks) == [0, 2]
    assert [b.frame for b in tracks[0]] == [0]
    assert [b.frame for b in tracks[2]] == [0, 1]
    assert tracks[2][1].x1 == 102


def test_skipped_and_out_of_order_frames_are_sorted() -> None:
    tracks = parse_sam_text(OUT_OF_ORDER)
    assert [b.frame for b in tracks[5]] == [0, 2, 4]
    assert [b.x1 for b in tracks[5]] == [0, 20, 40]


def test_tricky_payload_does_not_split_records() -> None:
    tracks = parse_sam_text(TRICKY_PAYLOAD)
    assert sorted(tracks) == [7, 9]
    assert tracks[9][0] == FrameBox(frame=3, x1=20, y1=30, x2=40, y2=50, w=320, h=334, conf=0.25)


def test_empty_output_is_no_tracks() -> None:
    assert parse_sam_text("") == {}
    assert parse_sam_text_official("") == {}
    assert parse("") == {}


def test_frames_without_newlines_are_split() -> None:
    assert parse_sam_text(SPARSE.replace("\n", "")) == parse_sam_text(SPARSE)


def test_malformed_records_are_ignored() -> None:
    text = "\n".join(
        [
            "hello, not a frame line",
            line(0, rec(1, 10, 10, 5, 20)),  # x2 < x1
            "<1f>3<|box;x1=1;y1=1;x2=2|><|mask;x=0;y=0;data=1,1,!0|>",  # missing w/h
            line(2, rec(4, 1, 1, 2, 2)),
        ]
    )
    assert list(parse_sam_text(text)) == [4]


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_official_and_regex_agree(name: str) -> None:
    text = FIXTURES[name]
    assert parse_sam_text_official(text) == parse_sam_text(text)


def test_parse_falls_back_to_regex(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(text: str) -> tuple[sam.Tracks, list[object]]:
        raise RuntimeError("parser broke")

    monkeypatch.setattr(sam, "_official", boom)
    assert parse(SPARSE) == parse_sam_text(SPARSE)


# Frame 0: object 1's box ends one past the last pixel (x2 == w), between two valid records.
REJECTED_MIDDLE = "\n".join(
    [
        line(0, rec(0, 1, 1, 10, 10), rec(1, 5, 5, 320, 20), rec(2, 30, 30, 40, 40)),
        line(1, rec(2, 31, 31, 41, 41)),
    ]
)
# A valid box whose mask token breaks the grammar (origin must be the literal 0).
BAD_MASK_TOKEN = (
    "<0f>0<|box;x1=10;y1=10;x2=40;y2=40;w=320;h=334|><|mask;x=3;y=4;data=31,31,!!!!!J!0c[q=Pj_zs|>"
)


def frames_of(tracks: sam.Tracks) -> dict[int, list[int]]:
    return {oid: [b.frame for b in boxes] for oid, boxes in tracks.items()}


def test_parse_recovers_records_the_official_parser_dropped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The official parser abandons the rest of a line at a bad record, without raising.
    assert frames_of(parse_sam_text_official(REJECTED_MIDDLE)) == {0: [0], 2: [1]}
    with caplog.at_level(logging.WARNING, logger="showtell.meta.sam"):
        tracks = parse(REJECTED_MIDDLE)
    assert frames_of(tracks) == {0: [0], 2: [0, 1]}
    assert tracks == parse_sam_text(REJECTED_MIDDLE)
    assert "invalid_box@line 1" in caplog.text and "recovered 1 box" in caplog.text


def test_parse_keeps_a_valid_box_with_a_malformed_mask_token(
    caplog: pytest.LogCaptureFixture,
) -> None:
    assert parse_sam_text_official(BAD_MASK_TOKEN) == {}  # looks like a valid zero match
    with caplog.at_level(logging.WARNING, logger="showtell.meta.sam"):
        tracks = parse(BAD_MASK_TOKEN)
    assert tracks == {0: [FrameBox(frame=0, x1=10, y1=10, x2=40, y2=40, w=320, h=334)]}
    assert "malformed_record" in caplog.text


def test_mask_only_diagnostics_keep_boxes_without_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The fixtures' fake mask payloads fail to decode: the parser keeps the boxes anyway.
    with caplog.at_level(logging.WARNING, logger="showtell.meta.sam"):
        assert parse(SPARSE) == parse_sam_text(SPARSE)
    assert caplog.records == []


def test_merge_tracks_adds_only_missing_frames() -> None:
    a = parse_sam_text(line(0, rec(1, 1, 1, 5, 5)))
    b = parse_sam_text(line(0, rec(1, 2, 2, 6, 6)) + "\n" + line(3, rec(1, 3, 3, 7, 7)))
    merged, added = sam.merge_tracks(a, b)
    assert added == 1 and frames_of(merged) == {1: [0, 3]}
    assert merged[1][0].x1 == 1  # primary wins on a shared (object, frame)


def test_short_line_truncates_mask_payloads() -> None:
    short = sam.short_line(DOCS_VIDEO_LINE, keep=4)
    assert "data=27,60,~!!!!..." in short
    assert "x1=211" in short
    assert len(short) < len(DOCS_VIDEO_LINE)
