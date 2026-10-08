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
盈利能力/成本结构/业绩弹性维度: 近几个报告期的毛利率(或主营业务利润率兜底)、
净利率、ROE、资产负债率环比变化，量化说明利润趋势和对价格/成本波动的弹性，
避免报告用"盈利能力强/成本上涨侵蚀利润"这类无数字支撑的空话。
"""

import asyncio
from typing import Dict, List, Optional

import akshare as ak

from tools.utils import utils

_MAX_PERIODS = 28  # retain enough full fiscal years for 3-5y ROE analysis


def _bare_code(stock_code: str) -> str:
    return stock_code.strip()[-6:]


async def get_profitability_trend(stock_code: str) -> Optional[Dict]:
    """
    最近几个报告期的毛利率/净利率/ROE/资产负债率序列，用于量化盈利弹性和成本压力趋势。
    毛利率字段在部分股票 (如资源类) 上为 NaN，退化用主营业务利润率兜底。
    拉取失败或无可用数据时返回 None。
    """
    try:
        df = await asyncio.to_thread(ak.stock_financial_analysis_indicator, symbol=_bare_code(stock_code))
    except Exception as e:
        utils.logger.error(f"[profitability] stock_financial_analysis_indicator({stock_code}) failed: {e}")
        return None

    if df is None or df.empty:
        return None

    df = df.tail(_MAX_PERIODS)

    def _num(row, col) -> Optional[float]:
        val = row.get(col)
        if val is None or val != val:  # NaN
            return None
        return float(val)

    periods: List[Dict] = []
    for _, row in df.iterrows():
        gross_margin = _num(row, "销售毛利率(%)")
        if gross_margin is None:
            gross_margin = _num(row, "主营业务利润率(%)")
        periods.append(
            {
                "period": str(row.get("日期", "")),
                "gross_margin_pct": gross_margin,
                "net_margin_pct": _num(row, "销售净利率(%)"),
                "roe_pct": _num(row, "净资产收益率(%)"),
                "eps_yuan": _num(row, "摊薄每股收益(元)"),
                "debt_ratio_pct": _num(row, "资产负债率(%)"),
                "net_profit_growth_pct": _num(row, "净利润增长率(%)"),
            }
        )

    if not periods:
        return None

    first, last = periods[0], periods[-1]

    def _trend_note(key: str, label: str) -> str:
        a, b = first.get(key), last.get(key)
        if a is None or b is None:
            return f"{label}数据不足"
        diff = b - a
        direction = "上升" if diff > 0.5 else ("下降" if diff < -0.5 else "基本持平")
        return f"{label}从 {a:.2f}% {direction}至 {b:.2f}% (区间 {first['period']}~{last['period']})"

    return {
        "periods": periods,
        "gross_margin_trend_note": _trend_note("gross_margin_pct", "毛利率/主营业务利润率"),
        "net_margin_trend_note": _trend_note("net_margin_pct", "净利率"),
        "roe_trend_note": _trend_note("roe_pct", "ROE"),
        "debt_ratio_trend_note": _trend_note("debt_ratio_pct", "资产负债率"),
    }
