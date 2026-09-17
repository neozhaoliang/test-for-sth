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
用预测发布之后的真实股价走势验证预测方向是否大致应验。
时间穿越保护：只使用 predicted_at 之后的价格数据。
"""

from datetime import date, datetime, timedelta
from typing import Optional, Tuple, TypedDict

import numpy as np

from backtest import price_source
from backtest.classify import ClassifiedPrediction
from tools.time_util import get_date_str_from_unix_time

MIN_TRADING_DAYS = 10  # 验证窗口内至少需要这么多交易日的数据才判定，否则视为数据不足
FLAT_THRESHOLD_PCT = 5.0  # 涨跌幅在此阈值内视为走势平盘 (neutral)

_DIRECTION_TO_EXPECTED_TREND = {
    "bullish": "bullish",
    "bottomed_out": "bullish",
    "bearish": "bearish",
    "topped_out": "bearish",
}


class VerifyResult(TypedDict):
    verdict: str  # correct | incorrect | inconclusive
    actual_trend: str  # bullish | bearish | neutral
    price_change_pct: float
    verified_at: int  # Unix 秒


def _ms_to_date(created_at_raw: int) -> date:
    """兼容毫秒/秒两种时间戳单位 (不同抓取批次可能不一致)。"""
    return datetime.strptime(get_date_str_from_unix_time(created_at_raw), "%Y-%m-%d").date()


def _judge_trend(closes: np.ndarray) -> Tuple[str, float]:
    p0 = closes[0]
    tail_mean = closes[-max(1, len(closes) // 5):].mean()
    change_pct = (tail_mean - p0) / p0 * 100
    if abs(change_pct) < FLAT_THRESHOLD_PCT:
        return "neutral", change_pct
    return ("bullish" if change_pct > 0 else "bearish"), change_pct


async def verify_prediction(prediction: ClassifiedPrediction) -> Optional[VerifyResult]:
    """
    验证单条预测。predicted_at 之后（严格晚于）到今天的价格序列用于判断真实走势。
    数据不足时返回 verdict=inconclusive；调用方对 inconclusive 应跳过存储。
    """
    post = prediction["post"]
    created_at_raw = int(post.get("created_at") or 0)
    if not created_at_raw:
        return None

    predicted_date = _ms_to_date(created_at_raw)
    start = predicted_date + timedelta(days=1)  # 严格晚于发帖日，满足"发表在前验证在后"
    df = await price_source.get_price_history(prediction["stock_code"], start=start)

    if len(df) < MIN_TRADING_DAYS:
        return {
            "verdict": "inconclusive",
            "actual_trend": "",
            "price_change_pct": 0.0,
            "verified_at": 0,
        }

    closes = df["close"].to_numpy(dtype=float)
    actual_trend, change_pct = _judge_trend(closes)
    expected_trend = _DIRECTION_TO_EXPECTED_TREND.get(prediction["direction"])
    verdict = "correct" if actual_trend == expected_trend else "incorrect"

    verified_at_str = df["date"].iloc[-1]
    verified_at = int(datetime.strptime(verified_at_str, "%Y-%m-%d").timestamp())

    return {
        "verdict": verdict,
        "actual_trend": actual_trend,
        "price_change_pct": round(float(change_pct), 2),
        "verified_at": verified_at,
    }
