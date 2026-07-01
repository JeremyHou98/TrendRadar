# coding=utf-8
"""
标题去重与事件聚类模块

把跨源、跨日出现的同一新闻事件聚成一个 NewsEvent，供段②③逐事件总结使用。

算法:
1. 标题归一化（去标点/emoji/空白/toLowerCase）
2. 分词：中文按字符 bigram，英文按空白 token（不引 jieba，零新依赖）
3. token 集合 Jaccard 相似度，阈值 0.5
4. 贪心聚类：每条与已有簇的代表标题比对，命中则并入，否则新建
5. 合并 source_count / date_range / total_hits，选最长或最高频标题为代表
6. 按 source_count × recency 排序，TopN 截断

仅对 route in {macro_data, other} 的事件去重；market 条目不走事件流。
"""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple

from trendradar.collect.collector import NewsItem
from trendradar.utils.time import get_configured_time


# 去除标点与符号的正则（保留中英文字母数字）
_PUNCT_RE = re.compile(r"[\s\W_]+", re.UNICODE)
# emoji 范围（粗略）
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FFFF\U00002600-\U000027BF\U0001F300-\U0001F9FF]", re.UNICODE
)


@dataclass
class NewsEvent:
    """去重后的新闻事件"""
    event_id: str
    category: str                       # 命中主题分类
    route: str                          # "macro_data" | "other"
    representative_title: str
    items: List[NewsItem] = field(default_factory=list)
    source_count: int = 0               # distinct platform 数
    date_range: Tuple[str, str] = ("", "")
    total_hits: int = 0
    _tokens: frozenset = field(default_factory=frozenset, repr=False)

    @property
    def last_date(self) -> str:
        return self.date_range[1] if self.date_range[1] else ""


def normalize(title: str) -> str:
    """标题归一化：去 emoji/标点/空白，转小写"""
    if not title:
        return ""
    # NFKC 归一化（全角→半角等）
    title = unicodedata.normalize("NFKC", title)
    title = _EMOJI_RE.sub(" ", title)
    title = _PUNCT_RE.sub(" ", title)
    return title.strip().lower()


def tokenize(normalized: str) -> frozenset:
    """
    分词：英文按空白切 token；中文按字符 bigram。
    混合时同时产生英文 token 与中文 bigram。
    """
    if not normalized:
        return frozenset()
    tokens = set()
    for raw in normalized.split():
        if re.fullmatch(r"[a-z0-9]+", raw):
            tokens.add(raw)
        else:
            # 中文段：按字符 bigram
            chars = [c for c in raw if not c.isspace()]
            if len(chars) == 1:
                tokens.add(chars[0])
            for i in range(len(chars) - 1):
                tokens.add(chars[i] + chars[i + 1])
    return frozenset(tokens)


def _jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter == 0:
        return 0.0
    return inter / len(a | b)


def _pick_representative(items: List[NewsItem]) -> str:
    """选最长标题作为代表（最长通常信息量最大）"""
    if not items:
        return ""
    return max(items, key=lambda x: len(x.title)).title


def _compute_source_count(items: List[NewsItem]) -> int:
    return len({(it.source_type, it.platform) for it in items})


def _compute_date_range(items: List[NewsItem]) -> Tuple[str, str]:
    dates = sorted({it.date for it in items if it.date})
    if not dates:
        return ("", "")
    return (dates[0], dates[-1])


def dedup(items: List[NewsItem], threshold: float = 0.5) -> List[NewsEvent]:
    """
    对 items 去重聚类。仅处理 route in {macro_data, other}。

    Returns:
        聚类后的 NewsEvent 列表（未排序、未截断）
    """
    events: List[NewsEvent] = []
    eid_counter = 0

    for it in items:
        if it.route == "market":
            continue  # 市场条目不走事件流
        norm = normalize(it.title)
        toks = tokenize(norm)
        if not toks:
            continue

        merged = False
        for ev in events:
            if ev.route != it.route:
                continue
            score = _jaccard(toks, ev._tokens)
            if score >= threshold:
                ev.items.append(it)
                ev._tokens = ev._tokens | toks  # 累积 token 提升后续召回
                merged = True
                break

        if not merged:
            eid_counter += 1
            events.append(NewsEvent(
                event_id=f"ev-{eid_counter:03d}",
                category=it.category,
                route=it.route,
                representative_title=it.title,
                items=[it],
                _tokens=toks,
            ))

    # 收尾：计算聚合字段 + 选代表标题
    for ev in events:
        ev.representative_title = _pick_representative(ev.items)
        ev.source_count = _compute_source_count(ev.items)
        ev.date_range = _compute_date_range(ev.items)
        ev.total_hits = len(ev.items)

    return events


def _recency_weight(last_date: str, timezone: str) -> float:
    """距今越近权重越高；解析失败返回 1.0"""
    if not last_date:
        return 1.0
    try:
        now = get_configured_time(timezone)
        d = datetime.strptime(last_date, "%Y-%m-%d")
        delta = (now - now.replace(hour=0, minute=0, second=0, microsecond=0)).days
        # 简化：用日期差
        today = now.strftime("%Y-%m-%d")
        today_d = datetime.strptime(today, "%Y-%m-%d")
        days_since = (today_d - d).days
        return 1.0 / (days_since + 1)
    except Exception:
        return 1.0


def sort_by_importance(events: List[NewsEvent], timezone: str = "Asia/Shanghai") -> List[NewsEvent]:
    """按重要性排序: source_count × recency_weight 降序"""
    scored = [(ev, ev.source_count * _recency_weight(ev.last_date, timezone)) for ev in events]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [ev for ev, _ in scored]


def top_n(events: List[NewsEvent], n: int) -> List[NewsEvent]:
    """截断到前 N 个"""
    if n <= 0:
        return events
    return events[:n]
