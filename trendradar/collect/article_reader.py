# coding=utf-8
"""
文章正文读取工具（精简版）

通过 Jina AI Reader (https://r.jina.ai) 把 URL 转为 Markdown 正文，
用于增强 AI 总结的准确性、降低幻觉。

复制自 mcp_server/tools/article_reader.py 的核心逻辑（避免 trendradar 包
反向依赖 mcp_server 包）。内置 5 秒限速，可选 JINA_API_KEY 提升配额。
"""

import os
import time
from typing import Optional

import requests


JINA_READER_BASE = "https://r.jina.ai"
DEFAULT_TIMEOUT = 30
THROTTLE_INTERVAL = 5.0  # 秒


_last_request_time = 0.0


def _build_headers() -> dict:
    headers = {
        "Accept": "text/markdown",
        "X-Return-Format": "markdown",
        "X-No-Cache": "true",
    }
    api_key = os.environ.get("JINA_API_KEY", "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _throttle() -> None:
    global _last_request_time
    now = time.time()
    elapsed = now - _last_request_time
    if elapsed < THROTTLE_INTERVAL:
        time.sleep(THROTTLE_INTERVAL - elapsed)
    _last_request_time = time.time()


def read_article(url: str, timeout: int = DEFAULT_TIMEOUT) -> Optional[str]:
    """
    读取单篇文章正文（Markdown）。

    Args:
        url: 文章链接（http/https）
        timeout: 请求超时秒数

    Returns:
        Markdown 正文字符串；失败返回 None
    """
    if not url or not url.startswith(("http://", "https://")):
        return None
    try:
        _throttle()
        resp = requests.get(
            f"{JINA_READER_BASE}/{url}",
            headers=_build_headers(),
            timeout=timeout,
        )
        if resp.status_code == 200:
            # 截断过长正文，避免喂给 AI 超长
            text = resp.text or ""
            if len(text) > 8000:
                text = text[:8000] + "\n\n[... 正文已截断 ...]"
            return text
        # 429 限速 / 其他错误统一返回 None
        return None
    except Exception:
        return None


def read_articles(urls, max_n: int = 0, timeout: int = DEFAULT_TIMEOUT):
    """
    批量读取正文，返回 {url: markdown_or_None}。

    Args:
        urls: URL 列表
        max_n: 最多读几篇，0=不限
        timeout: 单篇超时
    """
    target = list(urls)[:max_n] if max_n > 0 else list(urls)
    out = {}
    for u in target:
        out[u] = read_article(u, timeout=timeout)
    return out
