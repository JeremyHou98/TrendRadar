# coding=utf-8
"""
TrendRadar Collect - 按需新闻搜集与研报摘要子模块

读取过去 N 天存储历史，按主题过滤、跨源跨日去重，调用 AI 产出中英双语
Executive Summary + Deeper Dive，渲染为固定三段结构的 Markdown / HTML 简报。

入口:
  python -m trendradar.collect            # 模块执行
  trendradar-collect                       # 安装后执行
"""

from trendradar.collect.cli import main

__all__ = ["main"]
