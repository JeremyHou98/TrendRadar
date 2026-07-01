# coding=utf-8
"""
渲染模块

把市场综述 + 宏观数据事件 + 其余要闻事件渲染为固定三段结构的
Markdown 与独立 HTML 简报。

Markdown 结构:
  # 财经新闻研报摘要
  > 元信息
  ## 一、过去 X 天市场表现综述
  ## 二、中国宏观经济数据
  ## 三、其余要闻

HTML: 用 markdown 库把 MD 转 HTML，套内嵌 CSS 模板，含三段锚点 TOC。
不依赖 trendradar/report/html.py 的复杂报告样式。
"""

from typing import Dict, List, Optional

from trendradar.collect.collector import NewsItem
from trendradar.collect.deduper import NewsEvent


def _md_escape(text: str) -> str:
    """转义 Markdown 中可能干扰的字符（保守起见只处理 | 与多余空白）"""
    if not text:
        return ""
    return text.strip()


def _source_agg(items: List[NewsItem]) -> str:
    """聚合来源: 平台A(N), 平台B(M)"""
    from collections import Counter
    c = Counter(it.platform_name for it in items)
    parts = [f"{name}({n})" for name, n in c.most_common()]
    return ", ".join(parts) if parts else "无"


def _source_links_md(items: List[NewsItem]) -> str:
    """来源链接 Markdown 列表（按平台分组）"""
    lines = []
    seen = set()
    for it in sorted(items, key=lambda x: (x.platform_name, x.date)):
        key = (it.platform, it.title, it.url)
        if key in seen:
            continue
        seen.add(key)
        link_part = f"]({it.url})" if it.url else "]"
        lines.append(f"- [{it.platform_name} · {it.date} - {it.title}{link_part}")
    return "\n".join(lines) if lines else "无"


def _render_market_section(market_review: Dict, market_items: List[NewsItem],
                           days: int) -> str:
    """段①市场表现综述"""
    review_cn = market_review.get("review_cn", "") or ""
    review_en = market_review.get("review_en", "") or ""
    data_points = market_review.get("data_points", []) or []

    parts = [f"## 一、过去 {days} 天市场表现综述\n"]
    parts.append("### 中文\n")
    parts.append(review_cn + "\n\n" if review_cn else "（无内容）\n\n")
    parts.append("### English\n")
    parts.append(review_en + "\n\n" if review_en else "(no content)\n\n")

    if data_points:
        parts.append("**数据点**:\n")
        for dp in data_points:
            if not isinstance(dp, dict):
                continue
            idx = dp.get("index", "")
            move = dp.get("move", "")
            src = dp.get("source", "")
            parts.append(f"- {idx} {move}（来源: {src}）\n")
        parts.append("\n")

    if market_items:
        parts.append(f"**市场条目来源**: {_source_agg(market_items)}\n")
        parts.append("\n<details><summary>市场条目来源链接</summary>\n\n")
        parts.append(_source_links_md(market_items))
        parts.append("\n\n</details>\n\n")
    else:
        parts.append("**市场条目来源**: 无\n\n")

    return "".join(parts)


def _render_event(ev: NewsEvent, summary: Dict, idx: int) -> str:
    """渲染单个事件（段②③通用）"""
    headline_cn = summary.get("headline_cn", "") or ev.representative_title
    headline_en = summary.get("headline_en", "") or ""
    exec_cn = summary.get("exec_summary_cn", []) or []
    exec_en = summary.get("exec_summary_en", []) or []
    deeper_cn = summary.get("deeper_dive_cn", "") or ""
    deeper_en = summary.get("deeper_dive_en", "") or ""

    parts = [f"### {idx}. {headline_cn}"]
    if headline_en:
        parts.append(f" / {headline_en}")
    parts.append("\n\n")

    date_range = f"{ev.date_range[0]}~{ev.date_range[1]}" if ev.date_range[0] else ""
    parts.append(f"**来源**: {_source_agg(ev.items)} · {date_range} · 跨 {ev.source_count} 源 / {ev.total_hits} 条\n\n")

    # Executive Summary
    parts.append("#### Executive Summary（中文）\n")
    if exec_cn:
        for b in exec_cn:
            parts.append(f"- {b}\n")
    else:
        parts.append("- （AI 未生成）\n")
    parts.append("\n")

    parts.append("#### Executive Summary（English）\n")
    if exec_en:
        for b in exec_en:
            parts.append(f"- {b}\n")
    else:
        parts.append("- (AI not generated)\n")
    parts.append("\n")

    # Deeper Dive
    parts.append("#### Deeper Dive（中文）\n")
    parts.append((deeper_cn + "\n\n") if deeper_cn else "（AI 未生成）\n\n")
    parts.append("#### Deeper Dive（English）\n")
    parts.append((deeper_en + "\n\n") if deeper_en else "(AI not generated)\n\n")

    # 来源链接
    parts.append("<details><summary>来源链接</summary>\n\n")
    parts.append(_source_links_md(ev.items))
    parts.append("\n\n</details>\n\n")

    return "".join(parts)


def _render_events_section(title: str, events: List[NewsEvent],
                           summaries: Dict[str, Dict]) -> str:
    """渲染一个事件段落（段②或段③）"""
    parts = [f"## {title}\n\n"]
    if not events:
        parts.append("（无匹配事件）\n\n")
        return "".join(parts)
    for i, ev in enumerate(events, 1):
        s = summaries.get(ev.event_id, {})
        parts.append(_render_event(ev, s, i))
    return "".join(parts)


def render_markdown(
    market_review: Dict,
    market_items: List[NewsItem],
    macro_events: List[NewsEvent],
    macro_summaries: Dict[str, Dict],
    other_events: List[NewsEvent],
    other_summaries: Dict[str, Dict],
    metadata: Dict,
) -> str:
    """
    渲染固定三段 Markdown。

    metadata keys: days, date_range(str), total_matched(int), total_events(int),
                   topics_label(str), generated_at(str)
    """
    days = metadata.get("days", 14)
    date_range = metadata.get("date_range", "")
    total_matched = metadata.get("total_matched", 0)
    total_events = metadata.get("total_events", 0)
    topics_label = metadata.get("topics_label", "")
    generated_at = metadata.get("generated_at", "")

    parts = ["# 财经新闻研报摘要\n\n"]
    parts.append(f"> 时间范围: {date_range}（{days} 天） | 匹配: {total_matched} 条 | 去重事件: {total_events}\n")
    parts.append(f"> 主题: {topics_label}\n")
    parts.append(f"> 生成时间: {generated_at}\n\n")
    parts.append("---\n\n")

    parts.append(_render_market_section(market_review, market_items, days))
    parts.append("---\n\n")
    parts.append(_render_events_section("二、中国宏观经济数据", macro_events, macro_summaries))
    parts.append("---\n\n")
    parts.append(_render_events_section("三、其余要闻", other_events, other_summaries))

    return "".join(parts)


# ───────────────────────────────────────────────────────────────
# HTML 渲染
# ───────────────────────────────────────────────────────────────

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
:root {{
  --bg: #fafafa; --card: #ffffff; --text: #1a1a1a; --muted: #666;
  --border: #e5e5e5; --accent: #2563eb; --tag: #eef2ff;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg: #0f172a; --card: #1e293b; --text: #e2e8f0; --muted: #94a3b8;
    --border: #334155; --accent: #60a5fa; --tag: #1e293b;
  }}
}}
* {{ box-sizing: border-box; }}
body {{
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
    "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
  background: var(--bg); color: var(--text); line-height: 1.7;
  max-width: 900px; margin: 0 auto; padding: 24px 16px 80px;
}}
h1 {{ font-size: 1.6em; border-bottom: 2px solid var(--accent); padding-bottom: 8px; }}
h2 {{
  font-size: 1.3em; margin-top: 2em; padding: 8px 12px;
  background: var(--accent); color: #fff; border-radius: 6px;
}}
h3 {{ font-size: 1.1em; margin-top: 1.5em; color: var(--accent); }}
h4 {{ font-size: 1em; margin-top: 1.2em; color: var(--muted); font-weight: 600; }}
blockquote {{
  border-left: 3px solid var(--accent); background: var(--card);
  padding: 8px 14px; margin: 12px 0; color: var(--muted); border-radius: 0 6px 6px 0;
}}
hr {{ border: none; border-top: 1px solid var(--border); margin: 2em 0; }}
ul {{ padding-left: 1.4em; }}
li {{ margin: 3px 0; }}
a {{ color: var(--accent); text-decoration: none; word-break: break-all; }}
a:hover {{ text-decoration: underline; }}
details {{
  background: var(--card); border: 1px solid var(--border);
  border-radius: 6px; padding: 8px 12px; margin: 10px 0;
}}
summary {{ cursor: pointer; color: var(--muted); font-size: 0.9em; }}
code {{ background: var(--tag); padding: 1px 5px; border-radius: 3px; font-size: 0.9em; }}
strong {{ color: var(--text); }}
.toc {{
  position: sticky; top: 0; background: var(--card); border: 1px solid var(--border);
  border-radius: 6px; padding: 10px 14px; margin-bottom: 1.5em; font-size: 0.9em;
  display: flex; gap: 18px; flex-wrap: wrap;
}}
.toc a {{ color: var(--accent); }}
</style>
</head>
<body>
<div class="toc">
  <strong>目录:</strong>
  <a href="#section-1">一、市场表现综述</a>
  <a href="#section-2">二、中国宏观经济数据</a>
  <a href="#section-3">三、其余要闻</a>
</div>
{body}
</body>
</html>
"""


def render_html(markdown_text: str, metadata: Optional[Dict] = None) -> str:
    """把 Markdown 转 HTML，套模板。依赖 markdown 库。"""
    try:
        import markdown as md_lib
        body = md_lib.markdown(
            markdown_text,
            extensions=["extra", "sane_lists", "nl2br"],
        )
    except Exception:
        # markdown 库不可用时退化为 <pre>
        body = "<pre>" + (markdown_text or "").replace("<", "&lt;") + "</pre>"

    # 给三个二级标题加 id 用于 TOC 锚点
    import re
    body = re.sub(r"<h2>一、", '<h2 id="section-1">一、', body, count=1)
    body = re.sub(r"<h2>二、", '<h2 id="section-2">二、', body, count=1)
    body = re.sub(r"<h2>三、", '<h2 id="section-3">三、', body, count=1)

    title = (metadata or {}).get("title", "财经新闻研报摘要")
    return _HTML_TEMPLATE.format(title=title, body=body)
