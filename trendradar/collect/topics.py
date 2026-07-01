# coding=utf-8
"""
主题词与路由分类器模块

职责:
1. 加载 config/collect_default_topics.txt 默认主题分组（用于过滤 + 段③分类）
2. 合并用户通过 CLI --topic 追加的临时主题
3. 提供 market_index / macro_data / China 路由分类器，把每条匹配新闻路由到
   段①（市场表现综述）/ 段②（中国宏观数据）/ 段③（其余要闻）
4. route() 路由优先级: market > macro_data(中国) > other，互斥
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


# ───────────────────────────────────────────────────────────────
# 段①路由：市场指数关键词（识别"市场表现"条目，A股/港股/日韩台）
# ───────────────────────────────────────────────────────────────
MARKET_INDEX_KEYWORDS: List[str] = [
    # A股
    "上证指数", "沪深300", "沪深300指数", "科创50", "深证成指", "创业板指",
    "北证50", "A股", "沪指", "深成指", "创业板",
    # 港股
    "恒生指数", "恒生科技", "恒生科技指数", "国企指数", "H股指数", "恒指", "港股",
    # 日韩台
    "日经225", "日经", "东证指数", "东证", "KOSPI", "韩国综合",
    "台湾加权", "台股加权", "台股", "加权指数",
]

# ───────────────────────────────────────────────────────────────
# 段②路由：中国宏观数据关键词（数据发布类）
# ───────────────────────────────────────────────────────────────
MACRO_DATA_KEYWORDS: List[str] = [
    "CPI", "PPI", "PMI", "GDP", "社融", "社会融资", "M2", "M1", "M0",
    "进出口", "贸易顺差", "贸易逆差", "外贸进出口",
    "工业增加值", "规模以上工业", "社零", "社会消费品零售",
    "财新PMI", "官方PMI", "城镇调查失业率", "固定资产投资",
    "采购经理指数", "用电量", "货运量",
]

# 中国相关信号（段②地理判定）
CHINA_SIGNALS: List[str] = [
    "中国", "我国", "统计局", "央行", "人民银行", "财新", "国常会",
    "国务院", "海关总署", "财政部", "发改委", "证监会", "金管局",
]

# 外国信号（出现则不归入段②中国宏观数据）
FOREIGN_SIGNALS: List[str] = [
    "美国", "欧盟", "欧洲", "日本", "韩国", "印度", "英国", "德国",
    "法国", "美联储", "欧央行", "欧洲央行", "BOJ", "ECB", "Fed",
]


@dataclass
class TopicGroup:
    """主题分组"""
    name: str                        # 组名 / 分类标签
    keywords: List[str] = field(default_factory=list)
    _compiled: List[re.Pattern] = field(default_factory=list, repr=False)

    def matches(self, title: str) -> bool:
        """标题是否命中本组任一关键词（大小写不敏感）"""
        if not title:
            return False
        title_lower = title.lower()
        for kw in self.keywords:
            if not kw:
                continue
            if kw.startswith("/") and kw.endswith("/") and len(kw) >= 2:
                # 正则
                pat = self._get_or_compile(kw)
                if pat and pat.search(title):
                    return True
            elif kw.lower() in title_lower:
                return True
        return False

    def _get_or_compile(self, kw: str) -> Optional[re.Pattern]:
        # 简单缓存：正则模式每次重新编译开销可接受，这里直接编译
        pattern_str = kw[1:-1]
        try:
            return re.compile(pattern_str, re.IGNORECASE)
        except re.error:
            return None


def load_default_topics(path: str = "config/collect_default_topics.txt") -> List[TopicGroup]:
    """
    从配置文件加载默认主题分组。

    文件格式:
        [组名]
        关键词1 关键词2 关键词3
        关键词4

        [另一组]
        ...

    忽略空行与 # 开头的注释行。
    """
    p = Path(path)
    if not p.exists():
        print(f"[topics] 默认主题文件不存在: {path}，使用内置默认")
        return _builtin_defaults()

    groups: List[TopicGroup] = []
    current: Optional[TopicGroup] = None

    for raw_line in p.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]") and len(line) >= 3:
            if current and current.keywords:
                groups.append(current)
            current = TopicGroup(name=line[1:-1].strip())
            continue
        if current is None:
            continue
        # 关键词行：空格分隔；支持 /regex/ 形式（含空格的正则需用 /.../ 包裹且独占一行）
        if line.startswith("/") and line.endswith("/") and len(line) >= 2:
            current.keywords.append(line)
        else:
            for kw in line.split():
                if kw:
                    current.keywords.append(kw)

    if current and current.keywords:
        groups.append(current)

    if not groups:
        return _builtin_defaults()
    return groups


def _builtin_defaults() -> List[TopicGroup]:
    """文件缺失时的内置兜底默认主题"""
    return [
        TopicGroup("中国股市", ["A股", "沪深", "上证", "深证", "创业板", "北交所", "科创板"]),
        TopicGroup("香港市场", ["港股", "恒生", "香港股市", "恒生科技"]),
        TopicGroup("亚洲市场", ["日经", "东证", "KOSPI", "台湾加权", "台股"]),
        TopicGroup("宏观经济", ["GDP", "通胀", "降息", "加息", "利率", "汇率", "央行", "美联储"]),
        TopicGroup("政策监管", ["监管", "新规", "政策", "发改委", "证监会", "金管局", "反垄断"]),
        TopicGroup("金融市场", ["债市", "汇市", "期货", "原油", "黄金", "美元", "人民币"]),
        TopicGroup("产业重磅", ["并购", "重组", "IPO", "上市", "融资", "量产", "发布"]),
    ]


def merge_user_topics(defaults: List[TopicGroup], user_topics: List[str]) -> List[TopicGroup]:
    """
    合并用户通过 CLI --topic 追加的主题。

    每个 user_topic 自成一个新分组，组名为该主题原文（去重）。
    关键词按空白拆分；单字/单词也作为关键词。
    """
    if not user_topics:
        return defaults
    merged = list(defaults)
    existing_names = {g.name for g in merged}
    for topic in user_topics:
        topic = topic.strip()
        if not topic:
            continue
        name = topic
        # 避免重名
        suffix = 1
        while name in existing_names:
            suffix += 1
            name = f"{topic} ({suffix})"
        # 拆分关键词：中文按字符，英文按空白
        kws = [w for w in topic.split() if w]
        if not kws:
            kws = [topic]
        merged.append(TopicGroup(name=name, keywords=kws))
        existing_names.add(name)
    return merged


def match(title: str, groups: List[TopicGroup]) -> Optional[str]:
    """返回命中的第一个分组名（按 groups 顺序），未命中返回 None"""
    if not title:
        return None
    for g in groups:
        if g.matches(title):
            return g.name
    return None


def is_market_index(title: str) -> bool:
    """标题是否包含市场指数关键词（路由到段①）"""
    if not title:
        return False
    title_lower = title.lower()
    for kw in MARKET_INDEX_KEYWORDS:
        if kw.lower() in title_lower:
            return True
    return False


def _contains_any(text: str, words: List[str]) -> bool:
    text_lower = text.lower()
    return any(w.lower() in text_lower for w in words)


def is_china_macro_data(title: str) -> bool:
    """
    标题是否属于"中国宏观数据"（路由到段②）。

    判定: 含宏观数据关键词，且含中国信号或不含外国信号
    （避免"美国CPI"被误归入段②）。
    """
    if not title:
        return False
    if not _contains_any(title, MACRO_DATA_KEYWORDS):
        return False
    has_china = _contains_any(title, CHINA_SIGNALS)
    has_foreign = _contains_any(title, FOREIGN_SIGNALS)
    return has_china or not has_foreign


def route(title: str, matched_category: str) -> str:
    """
    路由分类（互斥，优先级 market > macro_data > other）。

    Args:
        title: 新闻标题
        matched_category: topics.match() 返回的命中分类名（未命中传 None）

    Returns:
        "market" | "macro_data" | "other"
    """
    if is_market_index(title):
        return "market"
    if is_china_macro_data(title):
        return "macro_data"
    return "other"
