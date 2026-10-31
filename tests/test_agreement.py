"""Phase 5: SAM-vs-ground-truth agreement math (undecided pairs never count as agreement)."""

import pytest

from showtell.contracts import KindAgreement, SamVerdict, SeedSummary
from showtell.verify.agreement import agreement, format_pct, from_seeds, merge, pairs
from showtell.verify.runner import CheckResult


def _gt(cid: str, kind: str, passed: bool) -> CheckResult:
    return CheckResult(check_id=cid, kind=kind, passed=passed, detail="")


def _sam(cid: str, kind: str, passed: bool | None) -> SamVerdict:
    return SamVerdict(check_id=cid, kind=kind, passed=passed)


def test_pairs_match_by_check_id_and_a_missing_verdict_is_undecided() -> None:
    results = [_gt("a", "in_bin", True), _gt("b", "untouched", False), _gt("c", "upright", True)]
    verdicts = [
        _sam("b", "untouched", False),
        _sam("a", "in_bin", False),
        _sam("z", "in_bin", True),
    ]
    assert pairs(results, verdicts) == [
        ("in_bin", True, False),
        ("untouched", False, False),
        ("upright", True, None),
    ]


def test_agreement_per_kind_with_undecided_counted_separately() -> None:
    items = [
        *[("in_bin", True, True)] * 3,
        ("in_bin", False, True),  # SAM wrong
        ("untouched", False, None),  # SAM undecided: neither agreement nor disagreement
        ("upright", False, False),
        ("upright", True, True),
        ("upright", True, None),
    ]
    s = agreement(items)
    assert (s.agree, s.n, s.undecided) == (5, 6, 2)
    assert s.by_kind == {
        "in_bin": KindAgreement(agree=3, n=4),
        "untouched": KindAgreement(agree=0, n=0),
        "upright": KindAgreement(agree=2, n=2),
    }
    assert s.pct == pytest.approx(100 * 5 / 6)
    assert format_pct(s) == "83% (5/6)"
    assert format_pct(s.by_kind["untouched"]) == "n/a"


def test_nothing_decided_has_no_rate() -> None:
    s = agreement([("in_bin", True, None), ("in_bin", False, None)])
    assert (s.agree, s.n, s.undecided, s.pct) == (0, 0, 2, None)
    assert format_pct(s) == "n/a"
    assert format_pct(agreement([])) == "n/a"


def test_merge_sums_overall_and_per_kind() -> None:
    a = agreement([("in_bin", True, True), ("upright", False, True), ("unmoved", True, None)])
    b = agreement([("in_bin", False, False), ("in_bin", True, False), ("upright", True, True)])
    m = merge([a, b])
    assert (m.agree, m.n, m.undecided) == (3, 5, 1)
    assert m.by_kind["in_bin"] == KindAgreement(agree=2, n=3)
    assert m.by_kind["upright"] == KindAgreement(agree=1, n=2)
    assert m.by_kind["unmoved"] == KindAgreement(agree=0, n=0)
    assert merge([]) == agreement([])


def test_from_seeds_skips_seeds_the_judge_did_not_run_on() -> None:
    judged = SeedSummary(
        seed=3000,
        passed=False,
        checks=[_gt("large_left", "in_bin", True), _gt("glass_upright", "upright", False)],
        sam=[_sam("large_left", "in_bin", True), _sam("glass_upright", "upright", False)],
    )
    unjudged = SeedSummary(seed=3001, passed=True, checks=[_gt("large_left", "in_bin", True)])
    s = from_seeds([judged, unjudged])
    assert (s.agree, s.n, s.undecided) == (2, 2, 0)


@pytest.mark.parametrize(
    ("agree", "n", "text"),
    [
        (56, 60, "93% (56/60)"),
        (5, 5, "100% (5/5)"),
        (239, 240, "99% (239/240)"),
        (0, 3, "0% (0/3)"),
    ],
)
def test_format_pct(agree: int, n: int, text: str) -> None:
    assert format_pct(KindAgreement(agree=agree, n=n)) == text
