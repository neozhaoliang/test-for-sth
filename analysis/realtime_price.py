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

import re
import time
from typing import Dict, Optional, Tuple

import akshare as ak
import pandas as pd

from tools.utils import utils

_CACHE_TTL_SECONDS = 180
_cache: Dict[str, Tuple[float, pd.DataFrame]] = {}

_CODE_PATTERN = re.compile(r"^(sh|sz|bj)?(\d{6})$")


def _normalize_code(stock_code: str) -> str:
    return stock_code.strip().lower()


def _looks_like_code(text: str) -> bool:
    return bool(_CODE_PATTERN.match(_normalize_code(text)))


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


async def resolve_stock_code(raw_input: str) -> Optional[str]:
    """
    将用户输入 (股票代码或股票名称) 解析为项目统一使用的 stock_code 格式 (如 SH600519)。
    输入已是带市场前缀的合法代码格式时直接规范化返回，不要求该代码在实时行情表中存在
    (历史数据可能覆盖已退市/行情源暂时缺失的股票)。不带前缀的纯数字代码、或股票名称，
    则先在全市场行情表中按名称查找 (先精确匹配，再回退到包含匹配)；行情源不可用
    (如新浪接口临时故障) 或没查到时，再回退到本地历史数据里已见过的股票名称/代码，
    找不到返回 None。
    """
    raw_input = raw_input.strip()
    if not raw_input:
        return None

    if _looks_like_code(raw_input):
        match = _CODE_PATTERN.match(_normalize_code(raw_input))
        prefix, digits = match.group(1), match.group(2)
        if prefix:
            return f"{prefix.upper()}{digits}"
        df = await _get_spot_df()
        if df is not None:
            hit = df[df["代码"].str.lower().str.endswith(digits)]
            if not hit.empty:
                return str(hit.iloc[0]["代码"]).upper()
        return _resolve_from_known_stocks(digits, by_code_suffix=True)

    df = await _get_spot_df()
    if df is not None:
        exact = df[df["名称"] == raw_input]
        if not exact.empty:
            return str(exact.iloc[0]["代码"]).upper()

        partial = df[df["名称"].str.contains(raw_input, na=False, regex=False)]
        if not partial.empty:
            return str(partial.iloc[0]["代码"]).upper()

    return _resolve_from_known_stocks(raw_input, by_code_suffix=False)


def _resolve_from_known_stocks(text: str, by_code_suffix: bool) -> Optional[str]:
    """行情源不可用时的兜底：在本地历史验证数据已出现过的股票里按代码/名称匹配。"""
    from analysis.candidates import list_supported_stocks

    known = list_supported_stocks()
    if by_code_suffix:
        for stock in known:
            if stock["stock_code"].lower().endswith(text):
                return stock["stock_code"]
        return None

    for stock in known:
        if stock.get("stock_name") == text:
            return stock["stock_code"]
    for stock in known:
        if text in (stock.get("stock_name") or ""):
            return stock["stock_code"]
    return None


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


async def get_stock_name(stock_code: str) -> Optional[str]:
    """根据股票代码查询名称 (全市场快照表)，拉取失败或找不到该代码时返回 None。"""
    df = await _get_spot_df()
    if df is None:
        return None

    code = _normalize_code(stock_code)
    matched = df[df["代码"].str.lower() == code]
    if matched.empty:
        return None

    return str(matched.iloc[0]["名称"])
