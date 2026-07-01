# coding=utf-8
"""
TrendRadar Collect CLI 入口

按需搜集总结财经新闻，输出固定三段结构的 Markdown + HTML 简报。

用法:
  trendradar-collect [--topic TXT] [--days N] [--top N] [--read-articles] ...
  python -m trendradar.collect [--topic TXT] ...
"""

import argparse
import os
import re
import sys
from datetime import timedelta
from pathlib import Path
from typing import Dict, List

from trendradar.collect import collector as collector_mod
from trendradar.collect import deduper as deduper_mod
from trendradar.collect import article_reader as article_reader_mod
from trendradar.collect import summarizer as summarizer_mod
from trendradar.collect import renderer as renderer_mod
from trendradar.collect import topics as topic_mod
from trendradar.core import load_config
from trendradar.utils.time import get_configured_time


def _slugify(text: str, max_len: int = 30) -> str:
    """把主题文本转为文件名安全的 slug"""
    if not text:
        return "digest"
    # 取前几个字，去标点
    s = re.sub(r"[\s/\\:*?\"<>|]+", "-", text).strip("-")
    # 中文保留，过长截断
    return s[:max_len] if s else "digest"


def _build_topics_label(groups) -> str:
    names = [g.name for g in groups]
    if not names:
        return "（无）"
    if len(names) <= 6:
        return " · ".join(names)
    return " · ".join(names[:5]) + f" 等 {len(names)} 个"


def _pick_market_read_urls(market_items: List, n: int) -> List[str]:
    """选市场条目池中前 N 个有 URL 的代表（按日期倒序、热榜优先）"""
    sorted_items = sorted(
        market_items,
        key=lambda x: (x.date, x.source_type == "hotlist"),
        reverse=True,
    )
    urls: List[str] = []
    seen = set()
    for it in sorted_items:
        if it.url and it.url not in seen:
            seen.add(it.url)
            urls.append(it.url)
        if len(urls) >= n:
            break
    return urls


def _pick_event_read_urls(events, n_per_event: int) -> Dict[str, List[str]]:
    """每事件选最多 n_per_event 个代表 URL（热榜优先、最近优先）"""
    out: Dict[str, List[str]] = {}
    for ev in events:
        sorted_items = sorted(
            ev.items,
            key=lambda x: (x.date, x.source_type == "hotlist"),
            reverse=True,
        )
        urls: List[str] = []
        seen = set()
        for it in sorted_items:
            if it.url and it.url not in seen:
                seen.add(it.url)
                urls.append(it.url)
            if len(urls) >= n_per_event:
                break
        out[ev.event_id] = urls
    return out


def _write_output(output_dir: str, date_folder: str, time_str: str,
                  slug: str, md_text: str, html_text: str,
                  formats: List[str]) -> Dict[str, str]:
    """写输出文件，返回路径字典"""
    d = Path(output_dir) / date_folder
    d.mkdir(parents=True, exist_ok=True)
    base = f"{time_str}-{slug}"
    paths = {}
    if "md" in formats:
        p = d / f"{base}.md"
        p.write_text(md_text, encoding="utf-8")
        paths["md"] = str(p)
    if "html" in formats:
        p = d / f"{base}.html"
        p.write_text(html_text, encoding="utf-8")
        paths["html"] = str(p)
    return paths


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="trendradar-collect",
        description="按需搜集总结财经新闻，输出固定三段结构的中英双语研报简报。",
    )
    p.add_argument("--topic", action="append", default=[], metavar="TXT",
                   help="追加主题（可重复，追加到默认主题之后）")
    p.add_argument("--days", type=int, default=14, metavar="N",
                   help="回看天数，默认 14")
    p.add_argument("--top", type=int, default=30, metavar="N",
                   help="去重后最多总结事件数（段②③合计），默认 30")
    p.add_argument("--read-articles", action="store_true",
                   help="读正文增强准确性（Jina Reader，慢，5 秒/篇）")
    p.add_argument("--articles-per-event", type=int, default=2, metavar="N",
                   help="每事件读几篇正文，默认 2（仅 --read-articles 时生效）")
    p.add_argument("--market-articles", type=int, default=5, metavar="N",
                   help="段①市场综述读几篇代表文，默认 5（仅 --read-articles 时生效）")
    p.add_argument("--no-fresh", action="store_true",
                   help="跳过今日 fresh crawl，只用历史存储")
    p.add_argument("--platforms", default=None, metavar="IDS",
                   help="限定热榜平台ID，逗号分隔，默认全部")
    p.add_argument("--no-rss", action="store_true",
                   help="跳过 RSS")
    p.add_argument("--format", default="html,md", metavar="FMT",
                   help="输出格式，逗号分隔，默认 html,md")
    p.add_argument("--output-dir", default="output/collect", metavar="DIR",
                   help="输出目录，默认 output/collect")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    print("=" * 60)
    print("TrendRadar Collect — 财经新闻研报摘要")
    print("=" * 60)

    # 1. 加载配置
    config = load_config()
    timezone = config.get("TIMEZONE", "Asia/Shanghai")
    ai_config = config.get("AI", {})
    print(f"[config] 时区: {timezone} | AI 模型: {ai_config.get('MODEL', '(未配置)')}")

    # 2. 主题
    topic_groups = topic_mod.load_default_topics()
    topic_groups = topic_mod.merge_user_topics(topic_groups, args.topic)
    topics_label = _build_topics_label(topic_groups)
    if args.topic:
        topics_label += f"（含追加: {' · '.join(args.topic)}）"
    print(f"[topics] 启用 {len(topic_groups)} 个主题分组: {topics_label}")

    # 3. 采集
    platforms = None
    if args.platforms:
        platforms = [x.strip() for x in args.platforms.split(",") if x.strip()]
    collect_result = collector_mod.collect(
        config=config,
        days=args.days,
        topic_groups=topic_groups,
        platforms=platforms,
        include_rss=not args.no_rss,
        fresh_today=not args.no_fresh,
    )
    all_items = collect_result.items

    if not all_items:
        print(f"\n[warn] 过去 {args.days} 天未抓取到任何匹配主题的新闻。")
        print("       建议：1) 先运行 trendradar 主程序积累数据；2) 调整 --topic / --days。")
        # 仍写一个空结果文件
        _write_empty_result(args, timezone, topics_label, collect_result)
        return 0

    # 4. 分流
    market_items = [it for it in all_items if it.route == "market"]
    dedup_items = [it for it in all_items if it.route in ("macro_data", "other")]
    print(f"[route] 市场: {len(market_items)} | 宏观数据+其余: {len(dedup_items)}")

    # 5. 去重 + 排序 + TopN
    events = deduper_mod.dedup(dedup_items)
    events = deduper_mod.sort_by_importance(events, timezone=timezone)
    events = deduper_mod.top_n(events, args.top)
    macro_events = [ev for ev in events if ev.route == "macro_data"]
    other_events = [ev for ev in events if ev.route == "other"]
    print(f"[events] 去重后 {len(events)} 个事件（宏观 {len(macro_events)} / 其余 {len(other_events)}），Top{args.top} 截断")

    # 6. 可选读正文
    article_contents: Dict[str, str] = {}
    if args.read_articles:
        print(f"[articles] 开始读正文（Jina Reader，5 秒/篇）...")
        # 段①市场代表文
        mkt_urls = _pick_market_read_urls(market_items, args.market_articles)
        for u in mkt_urls:
            c = article_reader_mod.read_article(u)
            if c:
                article_contents[u] = c
                print(f"  [market] ✓ {u[:60]}")
        # 段②③每事件代表文
        event_urls = _pick_event_read_urls(events, args.articles_per_event)
        for ev_id, urls in event_urls.items():
            for u in urls:
                c = article_reader_mod.read_article(u)
                if c:
                    article_contents[u] = c
                    print(f"  [{ev_id}] ✓ {u[:60]}")
        print(f"[articles] 共读到 {len(article_contents)} 篇正文")

    # 7. AI 总结
    print(f"[summarize] 段①市场综述...")
    market_review = summarizer_mod.summarize_market_review(
        market_items=market_items,
        article_contents=article_contents,
        ai_config=ai_config,
        days=args.days,
        timezone=timezone,
    )
    if market_review.get("fallback"):
        print(f"  [market] 降级：{market_review.get('error', 'fallback')}")

    print(f"[summarize] 段②③逐事件总结（{len(events)} 事件，分批 {summarizer_mod.BATCH_SIZE}/批）...")
    event_summaries = summarizer_mod.summarize_events(
        events=events,
        article_contents=article_contents,
        ai_config=ai_config,
        timezone=timezone,
    )
    fallback_n = sum(1 for v in event_summaries.values() if v.get("fallback"))
    if fallback_n:
        print(f"  [events] {fallback_n} 个事件降级（AI 不可用/失败）")

    macro_summaries = {eid: event_summaries.get(eid, {}) for ev in macro_events for eid in [ev.event_id]}
    other_summaries = {eid: event_summaries.get(eid, {}) for ev in other_events for eid in [ev.event_id]}

    # 8. 渲染
    now = get_configured_time(timezone)
    date_range_str = f"{collect_result.dates_read[-1] if collect_result.dates_read else '?'} ~ {collect_result.dates_read[0] if collect_result.dates_read else '?'}"
    metadata = {
        "days": args.days,
        "date_range": date_range_str,
        "total_matched": len(all_items),
        "total_events": len(events),
        "topics_label": topics_label,
        "generated_at": now.strftime("%Y-%m-%d %H:%M %Z"),
        "title": "财经新闻研报摘要",
    }
    md_text = renderer_mod.render_markdown(
        market_review=market_review,
        market_items=market_items,
        macro_events=macro_events,
        macro_summaries=macro_summaries,
        other_events=other_events,
        other_summaries=other_summaries,
        metadata=metadata,
    )
    html_text = renderer_mod.render_html(md_text, metadata)

    # 9. 写文件
    date_folder = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H-%M")
    slug = _slugify("-".join(args.topic) if args.topic else "finance-digest")
    formats = [f.strip() for f in args.format.split(",") if f.strip()]
    paths = _write_output(args.output_dir, date_folder, time_str, slug,
                          md_text, html_text, formats)

    print("\n" + "=" * 60)
    print("生成完成：")
    for k, p in paths.items():
        print(f"  [{k.upper()}] {p}")
    print("=" * 60)
    return 0


def _write_empty_result(args, timezone, topics_label, collect_result) -> None:
    """无匹配时写一个空结果文件提示"""
    now = get_configured_time(timezone)
    md_text = (
        "# 财经新闻研报摘要\n\n"
        f"> 时间范围: 过去 {args.days} 天 | 匹配: 0 条\n"
        f"> 主题: {topics_label}\n"
        f"> 生成时间: {now.strftime('%Y-%m-%d %H:%M')}\n\n"
        f"过去 {args.days} 天未抓取到任何匹配主题的新闻。\n\n"
        "建议：\n"
        "1. 先运行 trendradar 主程序积累历史数据；\n"
        "2. 调整 --topic / --days / 默认主题词配置。\n"
    )
    html_text = renderer_mod.render_html(md_text, {"title": "财经新闻研报摘要"})
    date_folder = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H-%M")
    slug = _slugify("-".join(args.topic) if args.topic else "finance-digest")
    formats = [f.strip() for f in args.format.split(",") if f.strip()]
    _write_output(args.output_dir, date_folder, time_str, slug,
                  md_text, html_text, formats)


if __name__ == "__main__":
    sys.exit(main())
