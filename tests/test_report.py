"""The Markdown test report's cost line."""

from showtell.contracts import CostSummary, JobReport
from showtell.report import report_markdown


def _report(cost: CostSummary) -> JobReport:
    return JobReport(job_id="j", created_at=0.0, rules="Sort by size.", use_video=False, cost=cost)


def test_cost_line_shows_billed_and_list_price() -> None:
    md = report_markdown(_report(CostSummary(usd=1.74, wonder_usd=0.14, sam_usd=1.6, calls=163,
                                             paid_usd=1.49)))  # fmt: skip
    assert (
        "- API cost: $1.49 billed; $1.74 at list price (Wonder $0.14, SAM $1.60, 163 calls)" in md
    )
    assert "billed $0" in md


def test_older_reports_without_a_billed_figure_show_only_list_price() -> None:
    md = report_markdown(_report(CostSummary(usd=0.08, wonder_usd=0.08, calls=2)))
    assert "- API cost: $0.08 at list price" in md and "billed;" not in md
