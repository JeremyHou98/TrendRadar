# coding=utf-8
"""
数据采集模块

从本地存储读取过去 N 天的热榜与 RSS 历史数据，按主题过滤并打路由标签。
可选触发今日实时抓取（fresh crawl）作为补充。

复用:
- trendradar.storage.get_storage_manager / StorageManager.get_today_all_data(date) / get_rss_data(date)
- trendradar.crawler.DataFetcher / trendradar.crawler.rss.RSSFetcher（可选 fresh crawl）
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import Dict, List, Optional

from trendradar.collect import topics as topic_mod
from trendradar.crawler import DataFetcher
from trendradar.crawler.rss import RSSFetcher, RSSFeedConfig
from trendradar.storage import get_storage_manager
from trendradar.utils.time import get_configured_time


@dataclass
class NewsItem:
    """采集后的扁平化新闻条目"""
    title: str
    url: str
    platform: str               # 平台ID / feedID
    platform_name: str
    source_type: str            # "hotlist" | "rss"
    date: str                   # YYYY-MM-DD
    rank: Optional[int]
    first_time: Optional[str]
    last_time: Optional[str]
    category: str               # 命中的主题分类（未命中时为空）
    route: str                  # "market" | "macro_data" | "other"


@dataclass
class CollectResult:
    """采集结果"""
    items: List[NewsItem]
    dates_read: List[str]       # 实际读到的日期
    dates_empty: List[str]      # 无数据的日期
    today_fresh: bool           # 是否触发了今日 fresh crawl


def _date_range(days: int, timezone: str) -> List[str]:
    """返回从今天往前数 N 天的日期列表（YYYY-MM-DD），今天在前"""
    now = get_configured_time(timezone)
    return [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]


def _news_data_to_items(news_data, date: str, platforms: Optional[List[str]],
                        topic_groups) -> List[NewsItem]:
    """把 storage 的 NewsData 展平为 NewsItem 列表（含过滤 + 路由）"""
    if not news_data or not getattr(news_data, "items", None):
        return []
    out: List[NewsItem] = []
    id_to_name = news_data.id_to_name or {}
    for source_id, news_list in news_data.items.items():
        if platforms is not None and source_id not in platforms:
            continue
        source_name = id_to_name.get(source_id, source_id)
        for n in news_list:
            title = (n.title or "").strip()
            if not title:
                continue
            category = topic_mod.match(title, topic_groups)
            if category is None:
                continue  # 不在主题范围内，丢弃
            route = topic_mod.route(title, category)
            out.append(NewsItem(
                title=title,
                url=n.url or "",
                platform=source_id,
                platform_name=source_name,
                source_type="hotlist",
                date=date,
                rank=(n.ranks[0] if n.ranks else n.rank) if n.ranks else n.rank,
                first_time=n.first_time or n.crawl_time or "",
                last_time=n.last_time or n.crawl_time or "",
                category=category,
                route=route,
            ))
    return out


def _rss_data_to_items(rss_data, date: str, feeds: Optional[List[str]],
                       topic_groups) -> List[NewsItem]:
    """把 storage 的 RSSData 展平为 NewsItem 列表（含过滤 + 路由）"""
    if not rss_data or not getattr(rss_data, "items", None):
        return []
    out: List[NewsItem] = []
    id_to_name = rss_data.id_to_name or {}
    for feed_id, rss_list in rss_data.items.items():
        if feeds is not None and feed_id not in feeds:
            continue
        feed_name = id_to_name.get(feed_id, feed_id)
        for r in rss_list:
            title = (r.title or "").strip()
            if not title:
                continue
            category = topic_mod.match(title, topic_groups)
            if category is None:
                continue
            route = topic_mod.route(title, category)
            out.append(NewsItem(
                title=title,
                url=r.url or "",
                platform=feed_id,
                platform_name=feed_name,
                source_type="rss",
                date=date,
                rank=None,
                first_time=r.first_time or r.crawl_time or "",
                last_time=r.last_time or r.crawl_time or "",
                category=category,
                route=route,
            ))
    return out


def _fresh_crawl_today(config, topic_groups, platforms: Optional[List[str]],
                       include_rss: bool) -> List[NewsItem]:
    """今日实时抓取（fresh crawl），展平并过滤路由"""
    items: List[NewsItem] = []
    today = get_configured_time(config.get("TIMEZONE", "Asia/Shanghai")).strftime("%Y-%m-%d")

    # 热榜
    platform_cfgs = config.get("PLATFORMS", [])
    ids_list = []
    domain_rules: Dict[str, str] = {}
    for p in platform_cfgs:
        pid = p.get("id")
        if not pid:
            continue
        if platforms is not None and pid not in platforms:
            continue
        ids_list.append((pid, p.get("name", pid)))
        ed = p.get("expected_domain")
        if ed:
            domain_rules[pid] = ed

    if ids_list:
        fetcher = DataFetcher(
            proxy_url=config.get("DEFAULT_PROXY") or None if config.get("USE_PROXY") else None,
            api_url=config.get("PLATFORMS_API_URL") or None,
        )
        results, id_to_name, failed = fetcher.crawl_websites(
            ids_list,
            request_interval=config.get("REQUEST_INTERVAL", 100),
            domain_rules=domain_rules,
        )
        for pid, title_map in results.items():
            pname = id_to_name.get(pid, pid)
            for title, info in title_map.items():
                title = (title or "").strip()
                if not title:
                    continue
                category = topic_mod.match(title, topic_groups)
                if category is None:
                    continue
                route = topic_mod.route(title, category)
                items.append(NewsItem(
                    title=title,
                    url=info.get("url", ""),
                    platform=pid,
                    platform_name=pname,
                    source_type="hotlist",
                    date=today,
                    rank=info.get("ranks", [None])[0] if info.get("ranks") else None,
                    first_time="",
                    last_time="",
                    category=category,
                    route=route,
                ))

    # RSS
    if include_rss and config.get("RSS", {}).get("ENABLED", False):
        feeds_cfg = config.get("RSS", {}).get("FEEDS", [])
        feed_configs = []
        for f in feeds_cfg:
            if not f.get("id") or not f.get("url"):
                continue
            if f.get("enabled", True) is False:
                continue
            feed_configs.append(RSSFeedConfig(
                id=f["id"],
                name=f.get("name", f["id"]),
                url=f["url"],
                enabled=True,
                max_age_days=f.get("max_age_days"),
            ))
        if feed_configs:
            rss_fetcher = RSSFetcher(
                feeds=feed_configs,
                request_interval=config.get("RSS", {}).get("REQUEST_INTERVAL", 2000),
                timeout=config.get("RSS", {}).get("TIMEOUT", 15),
                use_proxy=config.get("RSS", {}).get("USE_PROXY", False),
                proxy_url=config.get("RSS", {}).get("PROXY_URL", ""),
                timezone=config.get("TIMEZONE", "Asia/Shanghai"),
                freshness_enabled=False,
                default_max_age_days=0,
            )
            rss_data = rss_fetcher.fetch_all()
            items.extend(_rss_data_to_items(rss_data, today, None, topic_groups))

    return items


def collect(
    config: Dict,
    days: int,
    topic_groups,
    platforms: Optional[List[str]] = None,
    include_rss: bool = True,
    fresh_today: bool = False,
) -> CollectResult:
    """
    主采集入口：读取过去 N 天存储历史，过滤 + 路由；可选追加今日 fresh crawl。

    Args:
        config: trendradar load_config() 返回的配置字典
        days: 读取天数
        topic_groups: topics.load_default_topics + merge_user_topics 的结果
        platforms: 限定热榜平台ID列表（None=全部）
        include_rss: 是否读取/抓取 RSS
        fresh_today: 是否追加今日 fresh crawl

    Returns:
        CollectResult
    """
    timezone = config.get("TIMEZONE", "Asia/Shanghai")
    storage = get_storage_manager(
        backend_type=config.get("STORAGE", {}).get("BACKEND", "auto"),
        data_dir=config.get("STORAGE", {}).get("LOCAL", {}).get("DATA_DIR", "output"),
        timezone=timezone,
    )

    dates = _date_range(days, timezone)
    all_items: List[NewsItem] = []
    dates_read: List[str] = []
    dates_empty: List[str] = []

    for date in dates:
        news_data = storage.get_today_all_data(date)
        items = _news_data_to_items(news_data, date, platforms, topic_groups)
        if include_rss:
            rss_data = storage.get_rss_data(date)
            items.extend(_rss_data_to_items(rss_data, date, None, topic_groups))

        if items:
            dates_read.append(date)
        else:
            dates_empty.append(date)
        all_items.extend(items)
        print(f"[collect] {date}: 命中 {len(items)} 条")

    today_fresh = False
    if fresh_today:
        print("[collect] 触发今日 fresh crawl ...")
        fresh_items = _fresh_crawl_today(config, topic_groups, platforms, include_rss)
        if fresh_items:
            all_items.extend(fresh_items)
            today_fresh = True
            today_str = dates[0] if dates else get_configured_time(timezone).strftime("%Y-%m-%d")
            if today_str not in dates_read:
                dates_read.append(today_str)
            print(f"[collect] 今日 fresh 命中 {len(fresh_items)} 条")

    print(f"[collect] 合计命中 {len(all_items)} 条，覆盖 {len(dates_read)} 天")
    return CollectResult(items=all_items, dates_read=dates_read,
                         dates_empty=dates_empty, today_fresh=today_fresh)
