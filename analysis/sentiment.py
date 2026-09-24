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
雪球个股讨论情绪: 抓取该股讨论区最近多条来自不同用户的表态, 用 LLM 逐条
判断看多/看空/中性 (区分有论据与纯情绪), 聚合看多与看空的比例。

一致看多 = 情绪拥挤, 作为反向指标提示回调风险; 一致看空同理提示悲观极点。
情绪只作参考, 不替代基本面判断。
"""

import asyncio
from typing import Any, Dict, List, Optional

from backtest.extract import strip_html
from backtest.llm_client import call_json
from media_platform.xueqiu.client import XueqiuClient
from tools.utils import utils

_TARGET_POSTS = 300  # 目标收集条数 (页面数据质量参差, 实际以收集到的为准)
_MAX_PAGES = 8  # 每页 50 条
_BATCH_SIZE = 20  # 每批交给一次 LLM 调用
_CLASSIFY_CONCURRENCY = 3
_FETCH_BUDGET_S = 120

_SENTIMENT_PROMPT = """以下是一些雪球用户关于"{stock_name}"({stock_code})的近期表态 (来自个股讨论区)。请逐条判断该作者对这只股票的态度:
- bullish: 明确看多 (看好后市/打算买入/认为低估)
- bearish: 明确看空 (看空/打算卖出/认为高估或基本面恶化)
- neutral: 有讨论但不站队 (摆事实/提问/转述, 或只是记录操作)
- irrelevant: 与该股票无关 (灌水/广告/无关转发)
reasoned=true 表示有论据支撑 (非纯情绪喊单)。

只输出 JSON 数组，不要任何其他文字:
[{{"i": 条目序号, "stance": "bullish|bearish|neutral|irrelevant", "reasoned": true或false}}]

表态列表:
{items_block}"""


def _symbol(stock_code: str) -> str:
    return stock_code.strip().upper()


async def _fetch_discussion_posts(client: XueqiuClient, sym: str) -> List[Dict]:
    """导航翻页收集个股讨论区帖子 (导航传输自动通过 WAF JS 挑战)。"""
    posts: List[Dict] = []
    seen = set()
    for page in range(1, _MAX_PAGES + 1):
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
                    "text": text[:300],
                }
            )
        if len(posts) >= _TARGET_POSTS:
            break
    return posts


async def _classify_batch(prompt: str) -> List[Dict]:
    parsed = await call_json(prompt, max_tokens=2048)
    if not parsed or not isinstance(parsed, list):
        return []
    out: List[Dict] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        stance = item.get("stance", "")
        if stance not in ("bullish", "bearish", "neutral", "irrelevant"):
            continue
        out.append({"i": item.get("i"), "stance": stance, "reasoned": bool(item.get("reasoned"))})
    return out


async def get_stock_sentiment(
    session, stock_code: str, max_posts: int = _TARGET_POSTS
) -> Optional[Dict]:
    """
    收集并聚合该股讨论区情绪。失败返回 None (不影响报告其他维度)。
    返回 {posts_collected, users, bullish/bearish/neutral/irrelevant,
          reasoned_bullish/reasoned_bearish, bullish_ratio, consensus, note}
    """
    page = getattr(session, "context_page", None)
    if page is None:
        return None
    sym = _symbol(stock_code)
    client = XueqiuClient(playwright_page=page)
    try:
        posts = await asyncio.wait_for(
            _fetch_discussion_posts(client, sym), timeout=_FETCH_BUDGET_S
        )
    except asyncio.TimeoutError:
        utils.logger.warning(f"[sentiment] {stock_code} 讨论收集超时, 跳过该维度")
        return None
    if len(posts) < 20:
        utils.logger.warning(f"[sentiment] {stock_code} 讨论样本不足 ({len(posts)} 条), 跳过该维度")
        return None
    posts = posts[:max_posts]

    semaphore = asyncio.Semaphore(_CLASSIFY_CONCURRENCY)

    async def _one(start: int) -> List[Dict]:
        batch = posts[start : start + _BATCH_SIZE]
        block = "\n".join(
            f"[{j}] {p['text'][:150]}" for j, p in enumerate(batch)
        )
        prompt = _SENTIMENT_PROMPT.format(
            stock_name=stock_code, stock_code=stock_code, items_block=block
        )
        async with semaphore:
            try:
                return await _classify_batch(prompt)
            except Exception as e:
                utils.logger.warning(f"[sentiment] 批次 {start} 分类失败: {e}")
                return []

    starts = list(range(0, len(posts), _BATCH_SIZE))
    results = await asyncio.gather(*(_one(s) for s in starts))

    counts: Dict[str, int] = {"bullish": 0, "bearish": 0, "neutral": 0, "irrelevant": 0}
    reasoned: Dict[str, int] = {"bullish": 0, "bearish": 0}
    for start, res in zip(starts, results):
        for item in res:
            i = item.get("i")
            if not isinstance(i, int) or i < 0 or i >= len(posts[start : start + _BATCH_SIZE]):
                continue
            stance = item["stance"]
            counts[stance] = counts.get(stance, 0) + 1
            if stance in ("bullish", "bearish") and item.get("reasoned"):
                reasoned[stance] += 1

    classified = sum(counts.values())
    directional = counts["bullish"] + counts["bearish"]
    bullish_ratio = counts["bullish"] / directional if directional else 0.0
    consensus = "none"
    if directional >= 10:
        if bullish_ratio >= 0.8:
            consensus = "bullish"
        elif bullish_ratio <= 0.2:
            consensus = "bearish"
    note = ""
    if consensus == "bullish":
        note = "讨论区一致看多: 情绪拥挤, 作为反向指标提示回调风险"
    elif consensus == "bearish":
        note = "讨论区一致看空: 悲观情绪极端, 作为反向指标提示见底可能"

    users = {p["user_id"] for p in posts if p["user_id"]}
    result = {
        "posts_collected": len(posts),
        "users": len(users),
        "classified": classified,
        "bullish": counts["bullish"],
        "bearish": counts["bearish"],
        "neutral": counts["neutral"],
        "irrelevant": counts["irrelevant"],
        "reasoned_bullish": reasoned["bullish"],
        "reasoned_bearish": reasoned["bearish"],
        "bullish_ratio": round(bullish_ratio, 3),
        "consensus": consensus,
        "note": note,
    }
    utils.logger.info(
        f"[sentiment] {stock_code} 情绪聚合: {classified} 条分类, "
        f"看多 {counts['bullish']}/看空 {counts['bearish']}/中性 {counts['neutral']}, "
        f"看多占比 {bullish_ratio:.0%}, 一致度 {consensus}"
    )
    return result
