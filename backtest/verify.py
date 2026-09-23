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

验证哲学 (2026-09 起): 只有 prediction_type=price 且作者给出了明确时间
范围的预测才做价格验证; 其余预测 (基本面/商品/宏观/行业、或无时间范围的
股价预测) 不做验证, 原样记录 (verdict=no_horizon)。时间穿越保护: 只使用
predicted_at 之后、时间范围终点之前的价格数据。
"""

import re
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

# 时间范围解析: 数字/中文数字+单位 / 年份 / 年内年底 / 上下半年
_CN_NUM = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "半": 0.5,
}

_HORIZON_RES = [
    (re.compile(r"([\d一二三四五六七八九十两半]+)\s*个?\s*月"), "months"),
    (re.compile(r"([\d一二三四五六七八九十两半]+)\s*个?\s*季度"), "quarters"),
    (re.compile(r"(20\d{2})\s*年\s*上半年"), "year_h1"),
    (re.compile(r"(20\d{2})\s*年"), "year"),
    (re.compile(r"([\d一二三四五六七八九十两半]+)\s*个?\s*年"), "years"),
    (re.compile(r"([\d一二三四五六七八九十两半]+)\s*周"), "weeks"),
    (re.compile(r"([\d一二三四五六七八九十两半]+)\s*天"), "days"),
]


def _cn_to_num(s: str) -> Optional[float]:
    """'3'->3, '三'->3, '十二'->12, '二十'->20, '半'->0.5。"""
    if s.isdigit():
        return int(s)
    if len(s) == 1:
        return _CN_NUM.get(s)
    if s.startswith("十"):
        return 10 + (_CN_NUM.get(s[1], 0) if len(s) > 1 else 0)
    if "十" in s:
        a, b = s.split("十", 1)
        return _CN_NUM.get(a, 0) * 10 + (_CN_NUM.get(b, 0) if b else 0)
    return _CN_NUM.get(s)


def parse_horizon_end(predicted_date: date, horizon_text: str) -> Optional[date]:
    """把作者的时间范围原文解析为验证截止日。解析不了返回 None (不验证)。"""
    text = (horizon_text or "").strip()
    if not text:
        return None
    for pat, kind in _HORIZON_RES:
        m = pat.search(text)
        if not m:
            continue
        n = _cn_to_num(m.group(1))
        if n is None:
            continue
        if kind == "months":
            y, mo = predicted_date.year, predicted_date.month - 1 + n
            return date(y + int(mo // 12), int(mo % 12) + 1, 1) - timedelta(days=1)
        if kind == "quarters":
            return parse_horizon_end(predicted_date, f"{int(n * 3)}个月")
        if kind == "year_h1":
            return date(int(n), 6, 30)
        if kind == "year":
            return date(int(n), 12, 31)
        if kind == "years":
            return date(predicted_date.year + int(n), 12, 31)
        if kind == "weeks":
            return predicted_date + timedelta(weeks=n)
        if kind == "days":
            return predicted_date + timedelta(days=n)
    if "明年" in text:
        return date(predicted_date.year + 1, 12, 31)
    if "今年" in text or "年内" in text or "年底" in text or "年末" in text:
        return date(predicted_date.year, 12, 31)
    if "上半年" in text:
        return date(predicted_date.year if predicted_date.month <= 6 else predicted_date.year + 1, 6, 30)
    if "下半年" in text:
        return date(predicted_date.year, 12, 31)
    return None


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
    验证单条预测。返回 None 表示"不可验证" (非 price 类型 / 没有明确时间
    范围 / 范围解析不了 / 帖子没有发布时间)——调用方应按 no_horizon 记录;
    数据不足时返回 verdict=inconclusive，调用方跳过存储。
    可验证时: 只使用 predicted_at 之后、时间范围终点之前的价格数据。
    """
    if (prediction.get("prediction_type") or "") != "price":
        return None
    horizon = (prediction.get("time_horizon") or "").strip()
    if not horizon:
        return None

    post = prediction["post"]
    created_at_raw = int(post.get("created_at") or 0)
    if not created_at_raw:
        return None

    predicted_date = _ms_to_date(created_at_raw)
    end_date = parse_horizon_end(predicted_date, horizon)
    if end_date is None or end_date <= predicted_date:
        return None  # 时间范围无法解析, 不验证

    start = predicted_date + timedelta(days=1)  # 严格晚于发帖日，满足"发表在前验证在后"
    df = await price_source.get_price_history(prediction["stock_code"], start=start)
    if len(df) == 0:
        return {
            "verdict": "inconclusive",
            "actual_trend": "",
            "price_change_pct": 0.0,
            "verified_at": 0,
        }
    # 截断到作者说的时间范围终点
    df = df[df["date"] <= end_date.strftime("%Y-%m-%d")]

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
