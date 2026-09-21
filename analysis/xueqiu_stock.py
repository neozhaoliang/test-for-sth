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
雪球个股维度 (机构持仓 + 讨论热度)。

不再自行发起任何 API/XHR 请求——直接调用接口 (即使是页面内 XHR) 会被阿里云 WAF
识别为爬虫行为。改为真实导航到个股页 (https://xueqiu.com/S/{sym})，让 SPA 按
正常浏览行为自己加载报价、讨论等数据，被动收集页面渲染过程中发出的 JSON 响应
再解析。我们只"看"页面自己取回来的数据，与真人浏览的请求特征完全一致。

只喂聚合数字，不喂帖子原文——报告要的是"多少机构在什么价位持有""讨论热度处于什么量级"，
把 UGC 原文塞进 prompt 正是本项目要避免的噪音来源。
"""

import asyncio
import random
from typing import Any, Dict, List, Optional

from tools.utils import utils

_TOTAL_BUDGET_S = 90  # 雪球是辅助维度，绝不能拖住报告主流程

_QUOTE_KEYS = {
    "pe_ttm",
    "pb",
    "market_capital",
    "float_market_capital",
    "dividend_yield",
    "followers",
    "status_count",
    "current",
    "eps",
    "navps",
    "total_shares",
    "float_shares",
}


def _symbol(stock_code: str) -> str:
    """项目统一代码 (如 SH600519) 即雪球 symbol 格式。"""
    return stock_code.strip().upper()


def _collect_numbers(payload: Any, wanted: set) -> Dict[str, float]:
    """递归收集 payload 中出现的指定字段数值 (接口结构随版本有差异，按 key 匹配)。"""
    found: Dict[str, float] = {}

    def walk(node: Any, depth: int = 0) -> None:
        if depth > 8:
            return
        if isinstance(node, dict):
            for key, value in node.items():
                if key in wanted and isinstance(value, (int, float)) and not isinstance(value, bool):
                    found.setdefault(key, float(value))
                walk(value, depth + 1)
        elif isinstance(node, list):
            for item in node[:50]:
                walk(item, depth + 1)

    walk(payload)
    return found


def _collect_records(payload: Any, name_keys: tuple, limit: int = 10) -> List[Dict]:
    """收集形如 {名称: ..., 占比/持股: ...} 的记录列表。"""
    records: List[Dict] = []

    def walk(node: Any, depth: int = 0) -> None:
        if depth > 8 or len(records) >= limit:
            return
        if isinstance(node, dict):
            name = next((node[k] for k in name_keys if isinstance(node.get(k), str) and node[k]), None)
            if name:
                numbers = {
                    k: v
                    for k, v in node.items()
                    if isinstance(v, (int, float)) and not isinstance(v, bool)
                }
                if numbers:
                    records.append({"name": name, **numbers})
            for value in node.values():
                walk(value, depth + 1)
        elif isinstance(node, list):
            for item in node:
                walk(item, depth + 1)

    walk(payload)
    return records[:limit]


def _count_from(payload: Any) -> Optional[int]:
    for key in ("count", "total", "totalCount", "status_count"):
        found = _collect_numbers(payload, {key})
        if key in found:
            return int(found[key])
    return None


def _match_kind(url: str, sym: str) -> Optional[str]:
    """按 URL 特征判断页面自己发出的响应属于哪个维度。"""
    if "quote.json" in url and sym in url:
        return "quote_detail"
    if "search/status.json" in url and sym in url:
        return "discussion"
    if "/f10/" in url and sym in url:
        return "org_holding"
    return None


async def get_xueqiu_stock_data(session, stock_code: str) -> Optional[Dict]:
    """
    雪球个股聚合数据 (机构持仓、讨论热度、行情扩展字段)。

    真实导航到个股页并被动收集 SPA 自己发出的 JSON 响应 (不发起任何 API 请求)。
    仅在浏览器会话可用时调用；任何失败都返回 None，不影响报告其它维度。
    """
    page = getattr(session, "context_page", None)
    if page is None:
        return None
    sym = _symbol(stock_code)

    captured: List[Dict] = []

    def _on_response(resp) -> None:
        kind = _match_kind(resp.url, sym)
        if kind:
            captured.append({"kind": kind, "resp": resp})

    page.on("response", _on_response)
    try:
        try:
            ok = await asyncio.wait_for(
                session._goto_with_waf(f"https://xueqiu.com/S/{sym}", what=f"雪球个股 {sym}"),
                timeout=_TOTAL_BUDGET_S,
            )
        except asyncio.TimeoutError:
            utils.logger.warning(f"[xueqiu_stock] {stock_code} 个股页导航超时 ({_TOTAL_BUDGET_S}s)，跳过该维度")
            return None
        if not ok:
            return None
        # 少量拟人滚动: 讨论等首屏之外的内容由滚动触发 SPA 自行加载
        for _ in range(4):
            try:
                await page.evaluate("() => window.scrollBy(0, 500 + Math.random() * 400)")
            except Exception:
                break
            await asyncio.sleep(1 + random.random())
        await asyncio.sleep(2)  # 收尾等待, 让迟到的响应落地
    finally:
        try:
            page.remove_listener("response", _on_response)
        except Exception:
            pass

    out: Dict[str, Any] = {"available": [], "failed": []}
    for item in captured:
        try:
            payload = await item["resp"].json()
        except Exception:
            out["failed"].append(f"{item['kind']}:json-fail")
            continue
        if not isinstance(payload, dict):
            out["failed"].append(f"{item['kind']}:non-dict")
            continue
        if item["kind"] == "quote_detail":
            numbers = _collect_numbers(payload, _QUOTE_KEYS)
            if numbers:
                out["quote_detail"] = numbers
                out["available"].append("quote_detail")
        elif item["kind"] == "discussion":
            count = _count_from(payload)
            if count is not None:
                out["discussion"] = {"post_count": count}
                out["available"].append("discussion")
        elif item["kind"] == "org_holding":
            records = _collect_records(payload, ("name", "org_name", "holder_name", "sh_name"))
            if records:
                out["org_holding"] = records
                out["available"].append("org_holding")

    if not out.get("available"):
        utils.logger.warning(
            f"[xueqiu_stock] {stock_code} 个股页未捕获到任何数据 (页面可能未加载数据区或被 WAF 拦截)，该维度记为暂缺"
        )
        return None

    utils.logger.info(f"[xueqiu_stock] {stock_code} 可用维度: {out['available']}")
    if out.get("failed"):
        utils.logger.info(f"[xueqiu_stock] {stock_code} 未取到的维度: {out['failed']}")
    return out
