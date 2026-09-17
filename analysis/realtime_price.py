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
实时行情快照 (新浪源，ak.stock_zh_a_spot)。全市场拉取耗时较长 (~15-20s)，
用模块级缓存避免每次查询都重新拉取。
"""

import time
from typing import Dict, Optional, Tuple

import akshare as ak
import pandas as pd

from tools.utils import utils

_CACHE_TTL_SECONDS = 180
_cache: Dict[str, Tuple[float, pd.DataFrame]] = {}


def _normalize_code(stock_code: str) -> str:
    return stock_code.strip().lower()


async def _get_spot_df() -> Optional[pd.DataFrame]:
    import asyncio

    cached = _cache.get("all")
    if cached and (time.time() - cached[0]) < _CACHE_TTL_SECONDS:
        return cached[1]

    try:
        df = await asyncio.to_thread(ak.stock_zh_a_spot)
    except Exception as e:
        utils.logger.error(f"[realtime_price] stock_zh_a_spot failed: {e}")
        return None

    _cache["all"] = (time.time(), df)
    return df


async def get_realtime_quote(stock_code: str) -> Optional[Dict]:
    """
    返回 {latest_price, change_pct, volume, timestamp}，拉取失败或找不到该代码时返回 None。
    """
    df = await _get_spot_df()
    if df is None:
        return None

    code = _normalize_code(stock_code)
    matched = df[df["代码"].str.lower() == code]
    if matched.empty:
        return None

    row = matched.iloc[0]
    return {
        "latest_price": float(row["最新价"]),
        "change_pct": float(row["涨跌幅"]),
        "volume": float(row["成交量"]),
        "timestamp": str(row["时间戳"]),
    }
