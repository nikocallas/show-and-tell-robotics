"""Full-screen cards, written as small HTML pages and recorded like the app (so they can animate)."""

from __future__ import annotations

import html

from demo_video.compose import FONTS

LOGO = """<svg viewBox="0 0 32 32" width="{size}" height="{size}" fill="none"><rect width="32" height="32" rx="8" fill="#27272a"/>
<path d="M9 12V9h3M20 9h3v3M23 20v3h-3M12 23H9v-3" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
<path d="m12.5 16.2 2.4 2.4 4.8-5" stroke="#5d8bff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>"""

BASE = """
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:1280px;height:640px;background:#09090b;color:#fafafa;font-family:Inter,-apple-system,sans-serif;overflow:hidden}
.c{position:absolute;inset:0;display:flex;flex-direction:column;justify-content:center;padding:0 90px}
.fade{opacity:0;animation:f .7s ease-out forwards}
@keyframes f{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
h1{font-size:52px;font-weight:700;letter-spacing:-.025em;line-height:1.1}
h2{font-size:32px;font-weight:600;letter-spacing:-.02em;line-height:1.2}
p{color:#a1a1aa}
.mono{font-family:'JetBrains Mono',monospace}
"""


def page(body: str, extra_css: str = "") -> str:
    return f"<!doctype html><html><head><meta charset=utf-8>{FONTS}<style>{BASE}{extra_css}</style></head><body>{body}</body></html>"


def title_card(n_scenes: int = 20) -> str:
    cells = "".join(
        f'<span style="animation-delay:{0.9 + i * 0.13:.2f}s"></span>' for i in range(n_scenes)
    )
    return page(
        f"""<div class="c">
  <div class="fade" style="display:flex;align-items:center;gap:18px">{LOGO.format(size=64)}
    <h1>Show &amp; Tell Robotics</h1></div>
  <p class="fade" style="animation-delay:.25s;font-size:27px;margin-top:22px;color:#e4e4e7;letter-spacing:-.01em">
    Show it once. Say the rule. Get a robot program that passed the tests.</p>
  <div class="grid">{cells}</div>
</div>""",
        """.grid{display:grid;grid-template-columns:repeat(20,34px);gap:7px;margin-top:44px}
.grid span{height:34px;border-radius:7px;background:#27272a;animation:g .35s ease-out forwards}
@keyframes g{to{background:#10b981}}""",
    )


def steps_card(wonder: list[str], sam: list[str]) -> str:
    def rows(items: list[str], delay: float) -> str:
        return "".join(
            f'<li class="fade" style="animation-delay:{delay + i * 0.32:.2f}s">{html.escape(t)}</li>'
            for i, t in enumerate(items)
        )

    return page(
        f"""<div class="c" style="flex-direction:row;align-items:center;gap:44px">
  <div class="m"><p class="k">muse-wonder-1.0</p><h2>Muse Wonder</h2><ul>{rows(wonder, 0.2)}</ul></div>
  <div class="m"><p class="k">sam-3.1</p><h2>SAM 3.1</h2><ul>{rows(sam, 1.5)}</ul></div>
</div>""",
        """.m{flex:1;background:#18181b;border:1px solid #27272a;border-radius:18px;padding:30px 34px;min-height:340px}
.k{font-family:'JetBrains Mono',monospace;font-size:15px;color:#8fb0ff;margin-bottom:8px}
ul{list-style:none;margin-top:18px}li{font-size:21px;line-height:1.3;color:#e4e4e7;padding:9px 0;border-top:1px solid #27272a}""",
    )


def facts_card(title: str, rows: list[tuple[str, str]]) -> str:
    items = "".join(
        f'<div class="r fade" style="animation-delay:{0.2 + i * 0.35:.2f}s"><b>{html.escape(a)}</b>'
        f"<span>{html.escape(b)}</span></div>"
        for i, (a, b) in enumerate(rows)
    )
    return page(
        f'<div class="c"><h2 class="fade">{html.escape(title)}</h2><div style="margin-top:26px">{items}</div></div>',
        """.r{display:flex;gap:30px;align-items:baseline;padding:15px 0;border-top:1px solid #27272a}
.r b{flex:0 0 250px;font-size:30px;font-weight:700;letter-spacing:-.02em;color:#fff;font-variant-numeric:tabular-nums}
.r span{font-size:21px;line-height:1.35;color:#d4d4d8}""",
    )


def impact_card(claim: str, cite: str, heading: str, audience: list[str]) -> str:
    who = "".join(
        f'<span class="w fade" style="animation-delay:{1.0 + i * 0.35:.2f}s">{html.escape(a)}</span>'
        for i, a in enumerate(audience)
    )
    return page(
        f"""<div class="c">
  <p class="fade q">{html.escape(claim)}</p>
  <p class="fade" style="animation-delay:.4s;margin-top:14px;font-size:16px;color:#a1a1aa">{html.escape(cite)}</p>
  <p class="fade" style="animation-delay:.8s;margin-top:44px;font-size:15px;color:#8fb0ff;font-weight:600">{html.escape(heading)}</p>
  <div style="display:flex;flex-wrap:wrap;gap:12px;margin-top:14px">{who}</div>
</div>""",
        """.q{font-size:36px;line-height:1.25;font-weight:600;letter-spacing:-.018em;color:#fafafa;max-width:1000px}
.w{background:#18181b;border:1px solid #3f3f46;border-radius:999px;padding:11px 20px;font-size:20px;color:#e4e4e7}""",
    )


def end_card(lines: list[tuple[str, str]], note: str) -> str:
    rows = "".join(
        f'<p class="fade" style="animation-delay:{0.45 + i * 0.25:.2f}s;font-size:21px;margin-top:10px">'
        f'<span style="color:#a1a1aa">{html.escape(a)}</span> '
        f'<span class="mono" style="color:#fafafa;font-size:20px">{html.escape(b)}</span></p>'
        for i, (a, b) in enumerate(lines)
    )
    return page(
        f"""<div class="c">
  <div class="fade" style="display:flex;align-items:center;gap:16px">{LOGO.format(size=52)}
    <h1 style="font-size:44px">Show &amp; Tell Robotics</h1></div>
  <div style="margin-top:28px">{rows}</div>
  <p class="fade" style="animation-delay:1.3s;font-size:17px;margin-top:30px;color:#71717a">{html.escape(note)}</p>
</div>"""
    )
