"""Render a JobReport as Markdown (the human-readable test report)."""

from __future__ import annotations

from showtell.contracts import AgreementStats, JobReport


def _pct(stats: AgreementStats | None) -> str:
    if stats is None or stats.n == 0:
        return "n/a"
    return f"{stats.pct:.0f}% ({stats.agree}/{stats.n})"


def report_markdown(r: JobReport) -> str:
    lines: list[str] = [f"# Show & Tell job `{r.job_id}`", ""]
    status = {
        "verified": f"**VERIFIED** — {r.final_passed}/{r.final_total} test scenes pass",
        "failed": f"**NOT VERIFIED** — {r.final_passed}/{r.final_total} test scenes pass after "
        f"{max(0, len(r.attempts) - 1)} repair(s)",
        "error": f"**ERROR** — {r.error}",
        "running": "running",
    }[r.status]
    lines += [
        status,
        "",
        f"- Rules: “{r.rules}”",
        f"- Demo video: {'yes' if r.use_video and r.video else 'no (sentence only)'}"
        + (f" — Wonder saw the {r.media_path}" if r.media_path != "none" else ""),
        f"- Sim objects: {', '.join(r.object_ids) or 'n/a'}",
        f"- Seeds: {r.seeds[0]}–{r.seeds[-1]}" if r.seeds else "- Seeds: n/a",
        "",
    ]
    if r.spec:
        lines += ["## Spec", "", "Rules:"]
        for rule in r.spec.rules:
            what = rule.select.describe()
            lines.append(
                f"- {what}: " + (f"put in `{rule.place_in}`" if rule.place_in else "leave in place")
            )
        lines += ["", "Checks:"]
        for c in r.spec.checks:
            lines.append(f"- `{c.id}` ({c.kind}): {c.text}")
        if r.spec.clarifying_question:
            lines += ["", f"Clarifying question: {r.spec.clarifying_question}"]
        lines += ["", f"Rationale: {r.spec.rationale}", ""]
    if r.attempts:
        lines += [
            "## Attempts",
            "",
            "| Attempt | Passed | Diagnosis | Judge agreement |",
            "|---|---|---|---|",
        ]
        for a in r.attempts:
            name = "Attempt 0" if a.kind == "initial" else f"Repair {a.n}"
            diag = (a.diagnosis or "—").replace("|", "/")
            lines.append(f"| {name} | {a.passed}/{a.total} | {diag} | {_pct(a.agreement)} |")
        lines.append("")
        last = r.attempts[-1]
        failing = [s for s in last.seeds if not s.passed]
        if failing:
            lines += ["Failing scenes in the last attempt:", ""]
            for s in failing[:10]:
                bad = "; ".join(f"{c.check_id}: {c.detail}" for c in s.checks if not c.passed)
                lines.append(f"- seed {s.seed}: {s.error or bad}")
            lines.append("")
    if r.final_program:
        lines += ["## Final program", "", "```python", r.final_program.rstrip(), "```", ""]
    if r.agreement:
        lines += [
            "## Judges",
            "",
            f"SAM 3.1 vs simulator ground truth: {_pct(r.agreement)} agreement",
        ]
        for kind, k in sorted(r.agreement.by_kind.items()):
            if k.n:
                lines.append(f"- {kind}: {k.pct:.0f}% ({k.agree}/{k.n})")
        if r.agreement.undecided:
            lines.append(f"- undecided (SAM could not see the object): {r.agreement.undecided}")
        lines += ["", "Ground truth decides pass/fail; SAM is the camera-only second opinion.", ""]
    lines += [
        "## Cost and time",
        "",
        f"- API cost at list price: ${r.cost.usd:.2f} (Wonder ${r.cost.wonder_usd:.2f}, "
        f"SAM ${r.cost.sam_usd:.2f}, {r.cost.calls} calls; repeated requests are served from the "
        "local cache for $0)",
        f"- Wall time: {r.wall_seconds / 60:.1f} min",
        "",
    ]
    return "\n".join(lines)
