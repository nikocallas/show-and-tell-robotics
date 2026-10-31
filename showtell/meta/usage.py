"""Usage and cost log: one JSON line per Meta Model API call in ``data/usage.jsonl``.

Prices come from ``settings`` (see https://dev.meta.ai/docs/pricing-rate-limits). Local cache hits
are logged too, with ``cached_locally=true`` and ``cost_usd=0``, keeping their original token or
frame counts so ``uncached_usd`` still shows what a run would cost from scratch.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rich.table import Table

from showtell.config import settings

_LOCK = threading.Lock()
COUNT_FIELDS = (
    "input_tokens",
    "cached_tokens",
    "output_tokens",
    "reasoning_tokens",
    "video_frames",
    "images",
)


def wonder_cost(input_tokens: int, cached_tokens: int = 0, output_tokens: int = 0) -> float:
    """USD for one Wonder call. ``input_tokens`` includes ``cached_tokens``; output includes
    reasoning tokens (both as reported by the Responses API ``usage``)."""
    uncached = max(int(input_tokens) - int(cached_tokens), 0)
    return (
        uncached * settings.WONDER_PRICE_IN_PER_M
        + int(cached_tokens) * settings.WONDER_PRICE_CACHED_IN_PER_M
        + int(output_tokens) * settings.WONDER_PRICE_OUT_PER_M
    ) / 1_000_000


def sam_cost(frames: int = 0, images: int = 0) -> float:
    """USD for ONE SAM request: video bills at least ``SAM_MIN_FRAMES_PER_REQUEST`` frames."""
    billed = max(int(frames), settings.SAM_MIN_FRAMES_PER_REQUEST) if frames > 0 else 0
    return billed * settings.SAM_PRICE_PER_FRAME + int(images) * settings.SAM_PRICE_PER_IMAGE


def _get(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, Mapping):
        return obj.get(name)
    return getattr(obj, name, None)


def usage_counts(usage: Any) -> dict[str, int]:
    """Counts from a Responses ``usage`` object (SDK model or dict); missing fields are 0."""
    values = {
        "input_tokens": _get(usage, "input_tokens"),
        "cached_tokens": _get(_get(usage, "input_tokens_details"), "cached_tokens"),
        "output_tokens": _get(usage, "output_tokens"),
        "reasoning_tokens": _get(_get(usage, "output_tokens_details"), "reasoning_tokens"),
        "video_frames": _get(usage, "video_frames_processed"),
        "images": _get(usage, "images_processed"),
    }
    return {k: int(v) if isinstance(v, int | float) else 0 for k, v in values.items()}


def list_cost(rec: Mapping[str, Any]) -> float:
    """What a logged call costs at list price, ignoring the local cache."""
    if rec.get("kind") == "sam":
        return sam_cost(int(rec.get("video_frames", 0)), int(rec.get("images", 0)))
    return wonder_cost(
        int(rec.get("input_tokens", 0)),
        int(rec.get("cached_tokens", 0)),
        int(rec.get("output_tokens", 0)),
    )


def log_usage(
    *,
    kind: str,
    model: str = "",
    tag: str = "",
    cached_locally: bool = False,
    cost_usd: float | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Append one usage record and return it. Unknown extra fields are stored as given."""
    if kind not in ("wonder", "sam"):
        raise ValueError(f"unknown usage kind {kind!r}")
    rec: dict[str, Any] = {"ts": time.time(), "kind": kind, "model": model, "tag": tag}
    rec["cached_locally"] = bool(cached_locally)
    for name in COUNT_FIELDS:
        rec[name] = int(fields.pop(name, 0) or 0)
    if cached_locally:
        cost_usd = 0.0
    elif cost_usd is None:
        cost_usd = list_cost(rec)
    rec["cost_usd"] = round(float(cost_usd), 8)
    rec.update(fields)
    path = Path(settings.usage_log)
    line = json.dumps(rec, ensure_ascii=False) + "\n"
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
    return rec


def read_log(path: Path | None = None) -> list[dict[str, Any]]:
    """All records in the usage log (missing file -> []; malformed lines are skipped)."""
    path = Path(path or settings.usage_log)
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


@dataclass
class Totals:
    n_calls: int = 0
    n_cached: int = 0
    usd: float = 0.0
    uncached_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    video_frames: int = 0

    def add(self, rec: Mapping[str, Any]) -> None:
        cached = bool(rec.get("cached_locally"))
        self.n_calls += 1
        self.n_cached += int(cached)
        paid = float(rec.get("cost_usd", 0.0) or 0.0)
        self.usd += paid
        self.uncached_usd += list_cost(rec) if cached else paid
        self.input_tokens += int(rec.get("input_tokens", 0) or 0)
        self.output_tokens += int(rec.get("output_tokens", 0) or 0)
        self.video_frames += int(rec.get("video_frames", 0) or 0)


@dataclass
class UsageSummary:
    total: Totals = field(default_factory=Totals)
    by_kind: dict[str, Totals] = field(default_factory=dict)
    by_tag: dict[str, Totals] = field(default_factory=dict)

    @property
    def total_usd(self) -> float:
        return self.total.usd

    @property
    def uncached_usd(self) -> float:
        return self.total.uncached_usd

    @property
    def n_calls(self) -> int:
        return self.total.n_calls

    def table(self) -> Table:
        t = Table(title="Meta Model API usage", title_justify="left")
        if not self.total.n_calls:
            t.add_column("Usage")
            t.add_row("no API calls logged yet (total $0.0000)")
            return t
        t.add_column("Group", no_wrap=True)
        for col in ("Calls", "Cache hits", "Paid $", "No-cache $", "In tok", "Out tok", "Frames"):
            t.add_column(col, justify="right")

        def row(name: str, x: Totals, style: str | None = None) -> None:
            t.add_row(
                name,
                str(x.n_calls),
                str(x.n_cached),
                f"{x.usd:.4f}",
                f"{x.uncached_usd:.4f}",
                f"{x.input_tokens:,}",
                f"{x.output_tokens:,}",
                f"{x.video_frames:,}",
                style=style,
            )

        for name, x in sorted(self.by_kind.items()):
            row(f"kind: {name}", x)
        for name, x in sorted(self.by_tag.items()):
            row(f"tag: {name or '(none)'}", x, style="dim")
        row("TOTAL", self.total, style="bold")
        return t


def summarize(
    path: Path | None = None, *, since: float | None = None, tag_prefix: str | None = None
) -> UsageSummary:
    """Totals over the usage log, optionally only records at/after ``since`` (epoch seconds)
    and/or whose tag starts with ``tag_prefix``."""
    s = UsageSummary()
    for rec in read_log(path):
        if since is not None and float(rec.get("ts", 0.0) or 0.0) < since:
            continue
        tag = str(rec.get("tag", "") or "")
        if tag_prefix is not None and not tag.startswith(tag_prefix):
            continue
        s.total.add(rec)
        s.by_kind.setdefault(str(rec.get("kind", "?")), Totals()).add(rec)
        s.by_tag.setdefault(tag, Totals()).add(rec)
    return s
