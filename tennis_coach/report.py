"""Render a list of SwingReport objects as a single self-contained HTML page.

Plain string templating, no Jinja2: the page is simple enough (a card per
swing) that a template engine would add a dependency without saving much.
"""
from __future__ import annotations

import html

from .analyze import SwingReport

STROKE_ZH = {"forehand": "正拍", "backhand": "反拍", "serve": "發球"}

_CATEGORY_COLOR = {
    "excellent": "#1a7f37",
    "good": "#9a6700",
    "needs_work": "#cf222e",
}

_PAGE = """<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<title>網球動作分析報告</title>
<style>
  body {{ font-family: "PingFang TC", "Noto Sans TC", sans-serif; max-width: 760px;
         margin: 2rem auto; padding: 0 1rem; color: #1f2328; }}
  h1 {{ font-size: 1.4rem; }}
  .swing {{ border: 1px solid #d0d7de; border-radius: 8px; padding: 1rem 1.25rem;
           margin-bottom: 1.25rem; }}
  .swing h2 {{ margin: 0 0 0.25rem 0; font-size: 1.1rem; }}
  .overall {{ font-size: 1.6rem; font-weight: 700; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 0.75rem; font-size: 0.92rem; }}
  th, td {{ text-align: left; padding: 0.3rem 0.4rem; border-bottom: 1px solid #eaeef2; }}
  .summary {{ margin-top: 0.6rem; color: #444; }}
  .caveat {{ font-size: 0.85rem; color: #666; margin-top: 2rem; }}
</style>
</head>
<body>
<h1>網球動作分析報告</h1>
{swings}
<p class="caveat">評分表為初步門檻值，尚未對照真實影片校準，數值僅供參考。</p>
</body>
</html>
"""

_SWING = """<div class="swing">
  <h2>第 {i} 拍 · {stroke_zh}（觸球第 {frame} 幀）</h2>
  {overall_html}
  <table>
    <tr><th>指標</th><th>數值</th><th>分數</th><th>評語</th></tr>
    {rows}
  </table>
  <p class="summary">{summary}</p>
</div>
"""


def _esc(s: str) -> str:
    return html.escape(str(s))


def render_html(reports: list[SwingReport]) -> str:
    blocks = []
    for i, r in enumerate(reports, start=1):
        stroke_zh = STROKE_ZH.get(r.stroke, r.stroke)
        if r.score is None:
            overall_html = "<p>（沒有這個項目的評分表）</p>"
            rows = ""
        else:
            overall_html = f'<p class="overall">{r.score.overall:.0f} / 100</p>'
            rows = "".join(
                f'<tr><td>{_esc(m.name)}</td><td>{m.value:.2f}</td>'
                f'<td style="color:{_CATEGORY_COLOR.get(m.category, "#000")}">{m.score:.0f}</td>'
                f'<td>{_esc(m.feedback)}</td></tr>'
                for m in r.score.metrics
            )
        blocks.append(_SWING.format(
            i=i, stroke_zh=stroke_zh, frame=r.swing.contact,
            overall_html=overall_html, rows=rows, summary=_esc(r.summary),
        ))
    return _PAGE.format(swings="\n".join(blocks))
