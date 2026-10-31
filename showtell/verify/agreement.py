"""How often the SAM judge agrees with simulator ground truth, per check kind.

Ground truth decides pass/fail; the SAM judge is the camera-only second opinion. A (seed, check)
pair agrees when SAM's verdict equals the ground truth. Pairs where SAM could not decide
(`passed=None`) are counted as undecided, never as agreement, and are left out of the rate.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Protocol

from showtell.contracts import AgreementStats, KindAgreement, SamVerdict
from showtell.verify.runner import CheckResult

Pair = tuple[str, bool, bool | None]  # (check kind, ground truth passed, SAM passed or None)


class JudgedSeed(Protocol):
    """Anything with ground-truth checks and SAM verdicts, e.g. `contracts.SeedSummary`."""

    checks: list[CheckResult]
    sam: list[SamVerdict] | None


def pairs(results: Sequence[CheckResult], verdicts: Sequence[SamVerdict]) -> list[Pair]:
    """(kind, ground truth, SAM) for each ground-truth check of one seed, matched by check id.

    A check without a SAM verdict counts as undecided.
    """
    by_id = {v.check_id: v.passed for v in verdicts}
    return [(r.kind, bool(r.passed), by_id.get(r.check_id)) for r in results]


def agreement(items: Iterable[Pair]) -> AgreementStats:
    """Agreement over (kind, ground truth, SAM) pairs, overall and per check kind.

    A kind whose pairs were all undecided is listed with n = 0.
    """
    kinds: dict[str, list[int]] = {}  # kind -> [agree, n]
    undecided = 0
    for kind, truth, verdict in items:
        k = kinds.setdefault(kind, [0, 0])
        if verdict is None:
            undecided += 1
            continue
        k[0] += int(verdict == truth)
        k[1] += 1
    return AgreementStats(
        agree=sum(a for a, _ in kinds.values()),
        n=sum(n for _, n in kinds.values()),
        undecided=undecided,
        by_kind={kind: KindAgreement(agree=a, n=n) for kind, (a, n) in sorted(kinds.items())},
    )


def from_seeds(seeds: Iterable[JudgedSeed]) -> AgreementStats:
    """Agreement over judged seeds (seeds the judge did not run on are skipped)."""
    return agreement(p for s in seeds if s.sam is not None for p in pairs(s.checks, s.sam))


def merge(stats: Iterable[AgreementStats]) -> AgreementStats:
    """Sum several AgreementStats (e.g. attempts or tasks)."""
    agree = n = undecided = 0
    kinds: dict[str, list[int]] = {}
    for s in stats:
        agree, n, undecided = agree + s.agree, n + s.n, undecided + s.undecided
        for kind, ka in s.by_kind.items():
            k = kinds.setdefault(kind, [0, 0])
            k[0] += ka.agree
            k[1] += ka.n
    return AgreementStats(
        agree=agree,
        n=n,
        undecided=undecided,
        by_kind={kind: KindAgreement(agree=a, n=m) for kind, (a, m) in sorted(kinds.items())},
    )


def format_pct(stats: AgreementStats | KindAgreement) -> str:
    """'93% (56/60)'; 'n/a' when SAM decided nothing. Never rounds a miss up to 100%."""
    if stats.n == 0:
        return "n/a"
    pct = min(round(100 * stats.agree / stats.n), 99 if stats.agree < stats.n else 100)
    return f"{pct}% ({stats.agree}/{stats.n})"
