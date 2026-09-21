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

雪球的个股接口裸 HTTP 会被要求登录 (stock.xueqiu.com/v5 返回 error_code 400016)，
必须借道已经打开并登录的浏览器页面，用页面内 XHR 发出请求 (见 XueqiuClient._xhr_json)，
才能带上 cookies 与阿里云 WAF 的校验 token。

只喂聚合数字，不喂帖子原文——报告要的是"多少机构在什么价位持有""讨论热度处于什么量级"，
把 UGC 原文塞进 prompt 正是本项目要避免的噪音来源。
"""

import asyncio
from typing import Any, Dict, List, Optional

from tools.utils import utils

_HOST = "https://stock.xueqiu.com"
_TOTAL_BUDGET_S = 90  # 雪球是辅助维度，绝不能拖住报告主流程

# 端点路径以运行期实际可用情况为准：不同时期雪球的 F10 路径有差异，
# 逐个尝试并记录哪个真正返回了数据，全部失败时该维度降级为"暂缺"。
_CANDIDATES: Dict[str, List[str]] = {
    "org_holding": [
        _HOST + "/v5/stock/f10/cn/org_holding.json?symbol={sym}&count=10",
        _HOST + "/v5/stock/f10/cn/holders.json?symbol={sym}&count=10",
        _HOST + "/v5/stock/f10/cn/main_holder.json?symbol={sym}&count=10",
    ],
    "quote_detail": [
        _HOST + "/v5/stock/quote.json?symbol={sym}&extend=detail",
    ],
    "discussion": [
        "https://xueqiu.com/query/v1/symbol/search/status.json"
        "?count=10&comment=0&symbol={sym}&hl=0&source=all&sort=&page=1&q=",
    ],
}

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


async def _try_endpoints(client, stock_code: str) -> Dict[str, Any]:
    sym = _symbol(stock_code)
    out: Dict[str, Any] = {"available": [], "failed": []}

    for name, urls in _CANDIDATES.items():
        for url in urls:
            target = url.format(sym=sym)
            try:
                payload = await client._xhr_json_with_retry(target, retries=2)
            except Exception as e:
                out["failed"].append(f"{name}:{type(e).__name__}")
                continue
            if not isinstance(payload, dict):
                out["failed"].append(f"{name}:non-dict")
                continue

            if name == "org_holding":
                records = _collect_records(payload, ("name", "org_name", "holder_name", "sh_name"))
                if records:
                    out["org_holding"] = records
            elif name == "quote_detail":
                numbers = _collect_numbers(payload, _QUOTE_KEYS)
                if numbers:
                    out["quote_detail"] = numbers
            elif name == "discussion":
                count = _count_from(payload)
                if count is not None:
                    out["discussion"] = {"post_count": count}

            if name in out:
                out["available"].append(name)
                break

    return out


async def get_xueqiu_stock_data(session, stock_code: str) -> Optional[Dict]:
    """
    雪球个股聚合数据 (机构持仓、讨论热度、行情扩展字段)。
    仅在浏览器会话可用时调用；任何失败都返回 None，不影响报告其它维度。
    """
    client = getattr(session, "xueqiu_client", None)
    if client is None:
        return None

    try:
        data = await asyncio.wait_for(_try_endpoints(client, stock_code), timeout=_TOTAL_BUDGET_S)
    except asyncio.TimeoutError:
        utils.logger.warning(f"[xueqiu_stock] {stock_code} 雪球个股数据超时 ({_TOTAL_BUDGET_S}s)，跳过该维度")
        return None
    except Exception as e:
        utils.logger.warning(f"[xueqiu_stock] {stock_code} 雪球个股数据失败: {e}")
        return None

    if not data.get("available"):
        utils.logger.warning(
            f"[xueqiu_stock] {stock_code} 雪球端点均不可用 (失败明细: {data.get('failed')})，该维度记为暂缺"
        )
        return None

    utils.logger.info(f"[xueqiu_stock] {stock_code} 雪球可用端点: {data['available']}")
    if data.get("failed"):
        utils.logger.info(f"[xueqiu_stock] {stock_code} 未取到的端点: {data['failed']}")
    return data
