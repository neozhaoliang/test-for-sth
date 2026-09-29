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
大盘与风格维度: 判断个股在 A 股市场生态中的位置——今年是科技牛还是红利补涨、
个股相对主要指数与风格指数是领先还是滞后、处于自身历史区间的什么位置。

个股只用市场生态的客观数字 (指数涨跌、个股相对涨跌、历史区间位置)，
风格判断的定性结论由 LLM 结合行业数据块完成。所有比率在 Python 里算好。
"""

import asyncio
import time
from datetime import date
from typing import Dict, List, Optional, Tuple

import akshare as ak
import pandas as pd

from backtest.price_source import get_price_history
from tools.utils import utils

# 风格覆盖: 大盘 (上证/沪深300)、科技成长 (科创50/创业板指)、红利 (上证红利)。
_INDEX_UNIVERSE: List[Tuple[str, str]] = [
    ("sh000001", "上证指数"),
    ("sh000300", "沪深300"),
    ("sh000015", "上证红利"),
    ("sh000688", "科创50"),
    ("sz399006", "创业板指"),
]

_TTL_SECONDS = 6 * 3600
_index_cache: Optional[Tuple[float, Dict[str, pd.Series]]] = None

# 个股历史区间起点: 覆盖 2021 年集运景气高点这类上一轮周期的参照系。
_STOCK_HISTORY_START = date(2018, 1, 1)


async def _get_index_series() -> Dict[str, pd.Series]:
    """主要指数日收盘序列 (index=日期)，模块级缓存，多只股票分析共享。"""
    import asyncio

    global _index_cache
    if _index_cache and (time.time() - _index_cache[0]) < _TTL_SECONDS:
        return _index_cache[1]

    result: Dict[str, pd.Series] = {}
    for symbol, name in _INDEX_UNIVERSE:
        try:
            df = await asyncio.to_thread(ak.stock_zh_index_daily, symbol=symbol)
        except Exception as e:
            utils.logger.error(f"[market_context] stock_zh_index_daily({symbol}) failed: {e}")
            continue
        if df is None or df.empty:
            continue
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"])
        result[name] = df.set_index("date")["close"].astype(float)

    if result:
        _index_cache = (time.time(), result)
    return result


def _period_ret(series: pd.Series, start_inclusive: str, end_inclusive: str) -> Optional[float]:
    """区间 [start_inclusive, end_inclusive] 的涨跌幅 (%)。起点当日无数据时向前取最近收盘。"""
    base = series[series.index <= start_inclusive]
    tail = series[series.index <= end_inclusive]
    if base.empty or tail.empty or float(base.iloc[-1]) == 0:
        return None
    return (float(tail.iloc[-1]) / float(base.iloc[-1]) - 1) * 100


def _summary(series: pd.Series) -> Dict:
    """单个序列的年内分段涨跌: 全年/上半年/下半年。最新收盘晚于年末时下半年照常算。"""
    last = float(series.iloc[-1])
    ytd = _period_ret(series, f"{series.index[-1].year - 1}-12-31", str(series.index[-1].date()))
    h1 = _period_ret(series, f"{series.index[-1].year - 1}-12-31", f"{series.index[-1].year}-06-30")
    h2 = _period_ret(series, f"{series.index[-1].year}-06-30", str(series.index[-1].date()))
    return {
        "latest": round(last, 2),
        "latest_date": str(series.index[-1].date()),
        "ytd_pct": round(ytd, 1) if ytd is not None else None,
        "h1_pct": round(h1, 1) if h1 is not None else None,
        "h2_pct": round(h2, 1) if h2 is not None else None,
    }


async def get_market_context(
    stock_code: str,
    *,
    as_of: Optional[date] = None,
) -> Optional[Dict]:
    """
    返回 {indices: [{name, latest, ytd_pct, h1_pct, h2_pct}], stock: {...}}。
    stock 额外带 52 周高低点与 2018 年以来高低点 (用于判断"相对历史区间的位置")。
    指数与个股都取不到时返回 None。
    """
    index_series = await _get_index_series()
    cutoff = pd.Timestamp(as_of) if as_of is not None else None
    if cutoff is not None:
        index_series = {
            name: s[s.index <= cutoff]
            for name, s in index_series.items()
            if not s[s.index <= cutoff].empty
        }

    start = _STOCK_HISTORY_START
    if as_of is not None and as_of < _STOCK_HISTORY_START:
        start = date(max(1990, as_of.year - 5), 1, 1)
    try:
        stock_df = await get_price_history(stock_code, start, end=as_of)
    except Exception as e:
        utils.logger.error(f"[market_context] get_price_history({stock_code}) failed: {e}")
        stock_df = pd.DataFrame(columns=["date", "close"])

    stock: Optional[Dict] = None
    if not stock_df.empty:
        s = stock_df.copy()
        s["date"] = pd.to_datetime(s["date"])
        s = s.set_index("date")["close"].astype(float)
        stock = _summary(s)

        week52 = s[s.index >= (s.index[-1] - pd.Timedelta(days=365))]
        if not week52.empty:
            stock["w52_high"] = round(float(week52.max()), 2)
            stock["w52_high_date"] = str(week52.idxmax().date())
            stock["w52_low"] = round(float(week52.min()), 2)
            stock["w52_low_date"] = str(week52.idxmin().date())
        if not s.empty:
            stock["hist_high"] = round(float(s.max()), 2)
            stock["hist_high_date"] = str(s.idxmax().date())
            stock["hist_low"] = round(float(s.min()), 2)
            stock["hist_low_date"] = str(s.idxmin().date())
            stock["hist_start"] = str(s.index[0].date())

    if not index_series and stock is None:
        return None

    indices = [_summary(s) | {"name": name} for name, s in index_series.items()]
    return {"indices": indices, "stock": stock}
