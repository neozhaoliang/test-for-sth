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
融资融券数据 (融资盘): 按交易所逐日回溯抓取该股的融资余额序列
(akshare 的 stock_margin_detail_sse/szse 是"某日全市场明细"接口,
需要回溯多个交易日拼出序列)。筹码分析用它判断杠杆资金的规模与方向:
融资余额快速上升 + 股价高位 = 杠杆资金拥挤风险; 持续回落 = 去杠杆。
"""

import logging
import asyncio
import datetime
from typing import Dict, List, Optional

import akshare as ak


logger = logging.getLogger("MediaCrawler")

_MAX_TRADING_DAYS = 6  # 收集的交易日数
_MAX_CALENDAR_BACK = 14  # 最多回溯的自然日 (跨周末/长假)
_FETCH_BUDGET_S = 60


def _exchange_of(stock_code: str) -> Optional[str]:
    code = stock_code.strip().upper()
    if code.startswith("SH"):
        return "sse"
    if code.startswith("SZ"):
        return "szse"
    return None


def _fetch_series(
    exchange: str,
    symbol: str,
    as_of: Optional[datetime.date] = None,
) -> List[Dict]:
    series: List[Dict] = []
    reference = as_of or datetime.date.today()
    for back in range(0, _MAX_CALENDAR_BACK):
        d = (reference - datetime.timedelta(days=back)).strftime("%Y%m%d")
        try:
            if exchange == "sse":
                df = ak.stock_margin_detail_sse(date=d)
                row = df[df["标的证券代码"].astype(str) == symbol]
                if len(row):
                    series.append({"date": d, "balance_yuan": float(row.iloc[0]["融资余额"])})
            else:
                df = ak.stock_margin_detail_szse(date=d)
                row = df[df["证券代码"].astype(str) == symbol]
                if len(row):
                    series.append({"date": d, "balance_yuan": float(row.iloc[0]["融资余额"])})
        except Exception:
            continue  # 非交易日或接口异常, 继续回溯
        if len(series) >= _MAX_TRADING_DAYS:
            break
    return series


async def get_margin_signal(
    stock_code: str,
    *,
    as_of: Optional[datetime.date] = None,
) -> Optional[Dict]:
    """
    该股近几个交易日的融资余额序列。样本少于 3 天或接口不可用时返回 None
    (不影响报告其他维度)。
    """
    exchange = _exchange_of(stock_code)
    if exchange is None:
        return None
    symbol = stock_code.strip().upper()[2:]
    try:
        series = await asyncio.wait_for(
            asyncio.to_thread(_fetch_series, exchange, symbol, as_of), timeout=_FETCH_BUDGET_S
        )
    except asyncio.TimeoutError:
        logger.warning(f"[margin] {stock_code} 融资融券数据获取超时, 跳过该维度")
        return None
    if len(series) < 3:
        logger.warning(f"[margin] {stock_code} 融资融券数据不足 ({len(series)} 天), 跳过该维度")
        return None
    series.sort(key=lambda s: s["date"])
    first = series[0]["balance_yuan"]
    latest = series[-1]["balance_yuan"]
    change_pct = (latest - first) / first * 100 if first else 0.0
    return {
        "series": [
            {"date": s["date"], "balance_yi": round(s["balance_yuan"] / 1e8, 2)} for s in series
        ],
        "latest_date": series[-1]["date"],
        "latest_balance_yi": round(latest / 1e8, 2),
        "first_balance_yi": round(first / 1e8, 2),
        "change_pct": round(change_pct, 2),
        "days": len(series),
    }
