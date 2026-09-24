# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#
# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

"""
雪球个股讨论收集器: 导航翻页抓取该股讨论区最近的用户表态 (原始帖子)。

情绪聚合与多空辩论由 analysis.debate 负责 (同一批帖子只分类一次,
get_debate 走回测分类/验证管线, derive_sentiment 从辩论结果派生
看多/看空比例——一致看多为拥挤预警, 反向指标)。
"""

import asyncio
from typing import Any, Dict, List, Optional

from backtest.extract import strip_html
from media_platform.xueqiu.client import XueqiuClient
from tools.utils import utils

_DEFAULT_TARGET = 300  # 目标收集条数
_DEFAULT_MAX_PAGES = 8  # 每页 50 条


def _symbol(stock_code: str) -> str:
    return stock_code.strip().upper()


async def fetch_discussion_posts(
    client: XueqiuClient, sym: str, target: int = _DEFAULT_TARGET, max_pages: int = _DEFAULT_MAX_PAGES
) -> List[Dict]:
    """导航翻页收集个股讨论区帖子 (导航传输自动通过 WAF JS 挑战)。"""
    posts: List[Dict] = []
    seen = set()
    for page in range(1, max_pages + 1):
        url = (
            "https://xueqiu.com/query/v1/symbol/search/status.json"
            f"?count=50&comment=0&symbol={sym}&hl=0&source=all&sort=time&page={page}&q="
        )
        try:
            res = await asyncio.wait_for(
                client._nav_json_with_retry(url, retries=2), timeout=35
            )
        except Exception as e:
            utils.logger.warning(f"[sentiment] 讨论第 {page} 页获取失败: {type(e).__name__} {str(e)[:80]}")
            break
        items = res.get("list") or res.get("statuses") or res.get("items") or []
        if not items:
            break
        for it in items:
            if not isinstance(it, dict):
                continue
            sid = str(it.get("id") or "")
            if not sid or sid in seen:
                continue
            seen.add(sid)
            user = it.get("user") or {}
            text = strip_html(it.get("description") or it.get("text") or "")
            if not text:
                continue
            posts.append(
                {
                    "status_id": sid,
                    "user_id": str(user.get("id") or it.get("user_id") or ""),
                    "created_at": int(it.get("created_at") or 0),
                    "description": it.get("description") or it.get("text") or "",
                    "text": text[:300],
                }
            )
        if len(posts) >= target:
            break
    return posts
