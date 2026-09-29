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
股东回报与筹码维度: 股东户数环比变化 (散户情绪代理)、历史分红、历史回购。
akshare 接口均以不带市场前缀的 6 位数字代码为入参。
"""

import time
from typing import Dict, List, Optional, Tuple

import akshare as ak

from tools.utils import utils

_BUYBACK_CACHE_TTL_SECONDS = 3600
_buyback_cache: Optional[Tuple[float, "object"]] = None

_MAX_DIVIDEND_RECORDS = 30
_MAX_BUYBACK_RECORDS = 20


def _bare_code(stock_code: str) -> str:
    return stock_code.strip()[-6:]


async def get_shareholder_count_trend(stock_code: str) -> Optional[Dict]:
    """
    最近一期股东户数环比变化，用作散户情绪/筹码集中度的代理指标。
    股东户数增加 -> 筹码趋于分散 (可能是散户跟风入场)；减少 -> 筹码趋于集中。

    stock_zh_a_gdhs_detail_em 依赖的东财报表 (RPT_HOLDERNUM_DET) 已失效 (返回
    "结果数据为空"，属 akshare 上游未跟进的问题)，改用同样来自东财但仍可用的
    全市场"最新"股东户数快照接口 stock_zh_a_gdhs(symbol='最新')，再按股票代码筛选。
    该快照对个别股票 (如披露延迟) 可能没有记录，查不到时返回 None。
    """
    import asyncio

    try:
        df = await asyncio.to_thread(ak.stock_zh_a_gdhs, symbol="最新")
    except Exception as e:
        utils.logger.error(f"[shareholder] stock_zh_a_gdhs(最新) failed: {e}")
        return None

    if df is None or df.empty:
        return None

    sub = df[df["代码"] == _bare_code(stock_code)]
    if sub.empty:
        return None

    row = sub.iloc[0]
    change_pct = row.get("股东户数-增减比例")
    if change_pct is None or change_pct != change_pct:  # NaN
        return None

    return {
        "latest_count": int(row["股东户数-本次"]),
        "change_pct": float(change_pct),
        "trend": "increasing" if float(change_pct) > 0 else "decreasing",
        "as_of": str(row["股东户数统计截止日-本次"]),
    }


async def get_dividend_history(stock_code: str) -> List[Dict]:
    """最近几年的分红记录 (年度公告日期 + 每股派息)，拉取失败返回空列表。"""
    import asyncio

    try:
        df = await asyncio.to_thread(
            ak.stock_history_dividend_detail, symbol=_bare_code(stock_code), indicator="分红"
        )
    except Exception as e:
        utils.logger.error(f"[shareholder] stock_history_dividend_detail({stock_code}) failed: {e}")
        return []

    if df is None or df.empty:
        return []

    records = []
    for _, row in df.head(_MAX_DIVIDEND_RECORDS).iterrows():
        records.append(
            {
                "announce_date": str(row["公告日期"]),
                "dividend_per_10_shares": float(row["派息"]) if row["派息"] == row["派息"] else 0.0,
                "progress": str(row["进度"]),
            }
        )
    return records


async def _get_buyback_df():
    import asyncio

    global _buyback_cache
    if _buyback_cache and (time.time() - _buyback_cache[0]) < _BUYBACK_CACHE_TTL_SECONDS:
        return _buyback_cache[1]

    try:
        df = await asyncio.to_thread(ak.stock_repurchase_em)
    except Exception as e:
        utils.logger.error(f"[shareholder] stock_repurchase_em failed: {e}")
        return None

    _buyback_cache = (time.time(), df)
    return df


async def get_buyback_history(stock_code: str) -> List[Dict]:
    """该股票的历史回购公告 (金额、进度、均价)，拉取失败或无记录返回空列表。"""
    df = await _get_buyback_df()
    if df is None:
        return []

    sub = df[df["股票代码"] == _bare_code(stock_code)]
    if sub.empty:
        return []

    records = []
    for _, row in sub.head(_MAX_BUYBACK_RECORDS).iterrows():
        records.append(
            {
                "announce_date": str(row["最新公告日期"]),
                "progress": str(row["实施进度"]),
                "planned_amount_range": [
                    float(row["计划回购金额区间-下限"]) if row["计划回购金额区间-下限"] == row["计划回购金额区间-下限"] else None,
                    float(row["计划回购金额区间-上限"]) if row["计划回购金额区间-上限"] == row["计划回购金额区间-上限"] else None,
                ],
                "actual_amount": float(row["已回购金额"]) if row["已回购金额"] == row["已回购金额"] else None,
            }
        )
    return records
