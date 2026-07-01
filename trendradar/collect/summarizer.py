# coding=utf-8
"""
AI 总结模块

两类总结:
1. summarize_market_review: 段①市场表现综述（1 次 AI 调用，合成一段中英双语综述 + 数据点）
2. summarize_events:        段②③逐事件总结（分批 5 事件/批，每事件中英双语 exec+deeper）

复用:
- trendradar.ai.client.AIClient（LiteLLM 统一接口）
- trendradar.ai.prompt_loader.load_prompt_template
- json_repair.repair_json（容错解析 AI 输出的 JSON）
"""

import json
from typing import Dict, List, Optional

from trendradar.ai.client import AIClient
from trendradar.ai.prompt_loader import load_prompt_template
from trendradar.collect.collector import NewsItem
from trendradar.collect.deduper import NewsEvent
from trendradar.utils.time import get_configured_time


BATCH_SIZE = 5  # 事件总结每批最多 5 个事件
MAX_RETRIES = 1


def _parse_json(text: str):
    """两步解析：json.loads 失败则用 json_repair 修复"""
    if not text:
        return None
    # 剥离可能的 ```json fenced block
    s = text.strip()
    if s.startswith("```"):
        s = s.lstrip("`")
        if s.lower().startswith("json"):
            s = s[4:]
        s = s.strip()
        if s.endswith("```"):
            s = s[:-3].strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    try:
        from json_repair import repair_json
        return repair_json(s, return_objects=True)
    except Exception:
        return None


def _ai_available(ai_config: Dict) -> bool:
    return bool(ai_config and ai_config.get("API_KEY") and ai_config.get("MODEL"))


# ───────────────────────────────────────────────────────────────
# 段①：市场表现综述
# ───────────────────────────────────────────────────────────────

def _build_market_items_json(market_items: List[NewsItem],
                             article_contents: Dict[str, str]) -> str:
    """构造喂给 AI 的市场条目 JSON"""
    rows = []
    for it in market_items:
        row = {
            "title": it.title,
            "date": it.date,
            "platform": it.platform_name,
            "source_type": it.source_type,
            "url": it.url,
        }
        content = article_contents.get(it.url)
        if content:
            row["content"] = content[:1500]
        rows.append(row)
    return json.dumps(rows, ensure_ascii=False, indent=2)


def summarize_market_review(
    market_items: List[NewsItem],
    article_contents: Dict[str, str],
    ai_config: Dict,
    days: int,
    timezone: str = "Asia/Shanghai",
) -> Dict:
    """
    段①市场表现综述。

    Returns:
        {
            "review_cn": str, "review_en": str,
            "data_points": list, "fallback": bool, "error": str
        }
    """
    fallback_result = {
        "review_cn": "", "review_en": "", "data_points": [],
        "fallback": True, "error": "",
    }

    if not market_items:
        fallback_result["review_cn"] = f"过去 {days} 天未抓取到 A股/港股/日韩台股市相关条目。"
        fallback_result["review_en"] = f"No A-share/HK/JP-KR-TW market items captured in the past {days} days."
        return fallback_result

    if not _ai_available(ai_config):
        fallback_result["error"] = "AI 未配置（缺 API_KEY 或 MODEL）"
        fallback_result["review_cn"] = f"AI 不可用，过去 {days} 天共抓取到 {len(market_items)} 条市场相关条目，详见下方来源列表。"
        fallback_result["review_en"] = f"AI unavailable. {len(market_items)} market items captured in the past {days} days; see source list below."
        return fallback_result

    system_prompt, user_template = load_prompt_template(
        "collect_market_review_prompt.txt", label="Collect-MarketReview"
    )
    if not user_template:
        fallback_result["error"] = "市场综述 prompt 加载失败"
        return fallback_result

    market_json = _build_market_items_json(market_items, article_contents)
    current_time = get_configured_time(timezone).strftime("%Y-%m-%d %H:%M")
    user_prompt = user_template.replace("{days}", str(days)) \
        .replace("{current_time}", current_time) \
        .replace("{market_items_json}", market_json)

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})

    client = AIClient(ai_config)
    try:
        resp = client.chat(messages, max_tokens=ai_config.get("MAX_TOKENS", 4000))
    except Exception as e:
        fallback_result["error"] = f"AI 调用失败: {e}"
        return fallback_result

    data = _parse_json(resp)
    if not isinstance(data, dict):
        # 降级：把原始响应塞进 review_cn
        fallback_result["review_cn"] = (resp or "")[:500]
        fallback_result["error"] = "AI 输出 JSON 解析失败，已降级为原文"
        return fallback_result

    return {
        "review_cn": data.get("review_cn", "") or "",
        "review_en": data.get("review_en", "") or "",
        "data_points": data.get("data_points", []) or [],
        "fallback": False,
        "error": "",
    }


# ───────────────────────────────────────────────────────────────
# 段②③：逐事件总结
# ───────────────────────────────────────────────────────────────

def _build_events_json(events: List[NewsEvent],
                       article_contents: Dict[str, str]) -> str:
    """构造喂给 AI 的事件 JSON（含所有来源条目 + 可选正文）"""
    rows = []
    for ev in events:
        sources = []
        for it in ev.items:
            sources.append({
                "platform": it.platform_name,
                "source_type": it.source_type,
                "date": it.date,
                "title": it.title,
                "url": it.url,
            })
        row = {
            "id": ev.event_id,
            "category": ev.category,
            "representative_title": ev.representative_title,
            "date_range": f"{ev.date_range[0]}~{ev.date_range[1]}",
            "source_count": ev.source_count,
            "sources": sources,
        }
        # 取该事件第一篇有正文的 URL 的正文作为参考
        for it in ev.items:
            content = article_contents.get(it.url)
            if content:
                row["article_content"] = content[:1500]
                break
        rows.append(row)
    return json.dumps(rows, ensure_ascii=False, indent=2)


def _fallback_event_summary(ev: NewsEvent, reason: str) -> Dict:
    return {
        "id": ev.event_id,
        "headline_cn": ev.representative_title,
        "headline_en": "",
        "exec_summary_cn": [],
        "exec_summary_en": [],
        "deeper_dive_cn": f"[AI 不可用：{reason}]",
        "deeper_dive_en": f"[AI unavailable: {reason}]",
        "fallback": True,
    }


def summarize_events(
    events: List[NewsEvent],
    article_contents: Dict[str, str],
    ai_config: Dict,
    timezone: str = "Asia/Shanghai",
) -> Dict[str, Dict]:
    """
    段②③逐事件总结（分批调用）。

    Returns:
        {event_id: {headline_cn, headline_en, exec_summary_cn, exec_summary_en,
                    deeper_dive_cn, deeper_dive_en, fallback}}
    """
    results: Dict[str, Dict] = {}

    if not events:
        return results

    # 无 AI：全部降级
    if not _ai_available(ai_config):
        for ev in events:
            results[ev.event_id] = _fallback_event_summary(ev, "AI 未配置 API_KEY/MODEL")
        return results

    system_prompt, user_template = load_prompt_template(
        "collect_summary_prompt.txt", label="Collect-Events"
    )
    if not user_template:
        for ev in events:
            results[ev.event_id] = _fallback_event_summary(ev, "prompt 加载失败")
        return results

    client = AIClient(ai_config)
    current_time = get_configured_time(timezone).strftime("%Y-%m-%d %H:%M")

    # 分批
    for i in range(0, len(events), BATCH_SIZE):
        batch = events[i:i + BATCH_SIZE]
        batch_events_json = _build_events_json(batch, article_contents)
        user_prompt = (user_template
                       .replace("{events_count}", str(len(batch)))
                       .replace("{current_time}", current_time)
                       .replace("{events_json}", batch_events_json))

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        parsed: Optional[List] = None
        last_err = ""
        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = client.chat(messages, max_tokens=ai_config.get("MAX_TOKENS", 4000))
                data = _parse_json(resp)
                if isinstance(data, list):
                    parsed = data
                    break
                last_err = "AI 输出非 JSON 数组"
            except Exception as e:
                last_err = f"AI 调用失败: {e}"

        if not parsed:
            print(f"[summarizer] 批 {i//BATCH_SIZE+1} 失败：{last_err}，降级")
            for ev in batch:
                results[ev.event_id] = _fallback_event_summary(ev, last_err)
            continue

        # 按 id 映射回事件
        parsed_map = {}
        for item in parsed:
            if isinstance(item, dict) and item.get("id"):
                parsed_map[item["id"]] = item

        for ev in batch:
            item = parsed_map.get(ev.event_id)
            if not item:
                results[ev.event_id] = _fallback_event_summary(ev, "AI 输出缺失该事件")
                continue
            results[ev.event_id] = {
                "id": ev.event_id,
                "headline_cn": item.get("headline_cn", "") or ev.representative_title,
                "headline_en": item.get("headline_en", "") or "",
                "exec_summary_cn": item.get("exec_summary_cn", []) or [],
                "exec_summary_en": item.get("exec_summary_en", []) or [],
                "deeper_dive_cn": item.get("deeper_dive_cn", "") or "",
                "deeper_dive_en": item.get("deeper_dive_en", "") or "",
                "fallback": False,
            }

    return results
