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
集运景气度维度: 上海国际能源交易中心 集运指数(欧线)期货主力连续 (EC0, 新浪源，
与沪铜同一接口)。

背景: 集运是典型的地缘政治定价行业——红海绕行、港口拥堵、关税抢运都会直接抬高
运价，而运价是集运公司利润的最强领先指标。判断"景气从高点回落还是处于高位"
必须用运价数据，不能只凭公司财务指标的同比增速外推 (同比增速受上一年基数
影响，基数极端高时即使绝对景气仍处历史高位，同比也是负的)。

EC0 反映欧线即期/结算运价的市场定价，是集运景气最常用的风向标；集运公司
收入覆盖全球多条航线，欧线是其中最重要的一条，其余航线运价本维度暂缺。

仅对申万行业名含"航运/港口"的股票触发。取不到数据返回 None，调用方写"暂缺"。
"""

import asyncio
import time
from datetime import date
from typing import Dict, Optional, Tuple

import akshare as ak
import pandas as pd

from tools.utils import utils

_SHIPPING_KEYWORDS = ("航运", "港口")

_TTL_SECONDS = 6 * 3600
_ec_cache: Optional[Tuple[float, pd.Series]] = None


def is_shipping_industry(industry_name: Optional[str]) -> bool:
    if not industry_name:
        return False
    return any(kw in industry_name for kw in _SHIPPING_KEYWORDS)


async def _get_ec_history(
    as_of: Optional[date] = None,
) -> Optional[pd.Series]:
    """集运指数(欧线)主力连续日收盘序列 (index=日期)。"""
    import asyncio

    global _ec_cache
    if _ec_cache and (time.time() - _ec_cache[0]) < _TTL_SECONDS:
        series = _ec_cache[1]
        if as_of is None:
            return series
        return series[series.index.date <= as_of]

    try:
        df = await asyncio.to_thread(ak.futures_main_sina, symbol="EC0")
    except Exception as e:
        utils.logger.error(f"[freight] futures_main_sina(EC0) failed: {e}")
        return None

    if df is None or df.empty:
        return None

    df = df.copy()
    df["日期"] = pd.to_datetime(df["日期"])
    series = df.set_index("日期")["收盘价"].astype(float)
    _ec_cache = (time.time(), series)
    if as_of is not None:
        series = series[series.index.date <= as_of]
    return series


def _pct_rank(series: pd.Series, value: float) -> float:
    """当前值在历史序列中的分位 (0-100)，当前值处于历史最低记为 0，最高记为 100。"""
    if series.empty:
        return float("nan")
    return float((series <= value).mean() * 100)


async def get_container_freight_signal(
    stock_code: str,
    sw_industry: Optional[str] = None,
    *,
    as_of: Optional[date] = None,
) -> Optional[Dict]:
    """
    集运运价景气度快照。行业归属不是航运/港口时返回 None。

    返回字段 (全部在 Python 里算好，供报告直接引用):
      latest / latest_date / ytd_pct / year_high / year_high_date /
      year_low / year_low_date / hist_high / hist_high_date / hist_low /
      hist_pct_rank / milestones (年内关键转折点的文字序列)
    """
    if not is_shipping_industry(sw_industry):
        return None

    series = await _get_ec_history(as_of=as_of)
    if series is None or series.empty:
        return None

    last = float(series.iloc[-1])
    last_date = str(series.index[-1].date())

    this_year = series[series.index.year == series.index[-1].year]
    year_high = float(this_year.max())
    year_high_date = str(this_year.idxmax().date())
    year_low = float(this_year.min())
    year_low_date = str(this_year.idxmin().date())

    first_of_year = series[series.index < f"{series.index[-1].year}-01-01"]
    ytd_pct = None
    if not first_of_year.empty:
        ytd_pct = (last / float(first_of_year.iloc[-1]) - 1) * 100

    hist_high = float(series.max())
    hist_high_date = str(series.idxmax().date())
    hist_low = float(series.min())

    # 年内关键转折点: 取年内最低点、年内最高点、最近 6 个月的最低收盘，形成
    # "探底 -> 冲高 -> 回落/企稳"的走势骨架，不做方向性解读。
    recent_6m = series[series.index >= (series.index[-1] - pd.Timedelta(days=182))]
    recent_6m_low = float(recent_6m.min()) if not recent_6m.empty else year_low
    recent_6m_low_date = str(recent_6m.idxmin().date()) if not recent_6m.empty else year_low_date

    milestones = [
        f"· 年内最低 {year_low:.1f} 点 ({year_low_date})",
        f"· 年内最高 {year_high:.1f} 点 ({year_high_date})",
        f"· 近 6 个月最低 {recent_6m_low:.1f} 点 ({recent_6m_low_date})",
        f"· 历史最高 {hist_high:.1f} 点 ({hist_high_date})，历史最低 {hist_low:.1f} 点",
    ]

    return {
        "instrument": "集运指数(欧线)期货主力连续 (上海国际能源交易中心, 新浪源)",
        "latest": round(last, 1),
        "latest_date": last_date,
        "ytd_pct": round(ytd_pct, 1) if ytd_pct is not None else None,
        "year_high": round(year_high, 1),
        "year_high_date": year_high_date,
        "year_low": round(year_low, 1),
        "year_low_date": year_low_date,
        "hist_high": round(hist_high, 1),
        "hist_high_date": hist_high_date,
        "hist_low": round(hist_low, 1),
        "hist_pct_rank": round(_pct_rank(series, last), 0),
        "milestones": milestones,
        "note": (
            "运价是集运公司利润的最强领先指标；地缘风险 (绕行/拥堵/关税抢运) 直接抬高运价。"
            "对港口类公司，影响路径是吞吐量与费率而非直接运价收入，引用时须区分公司业务类型。"
            "其余航线 (美线/南美线等) 运价本维度暂缺，欧线是行业最常用的风向标。"
        ),
    }
