"""Dated margin leverage and trading crowding: ETFs and stocks use DIFFERENT denominators.

The turnover baseline is a prior 20-day window, rather than today's share of
a 20-day average that includes today. An unusually busy market is not
necessarily a net inflow, a short squeeze, or a bubble.
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta
import math
from typing import Any

import akshare as ak
import pandas as pd

from tools.utils import utils


def _number(value: Any) -> float | None:
    try:
        value = float(str(value).strip().replace(",", "").replace("%", ""))
        return value if math.isfinite(value) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _date(raw: Any) -> date | None:
    try:
        return pd.Timestamp(raw).date()
    except (TypeError, ValueError, OverflowError):
        return None


def turnover_activity(
    frame: Any, *, cutoff: date, source: str,
    date_column: str = "日期", amount_column: str = "成交额",
    turnover_column: str = "换手率",
) -> dict:
    """Comparable dated *RMB turnover*; never infer inflow from turnover.

    Past-20 baseline EXCLUDES latest 5 days, so the recent-5 ratio can detect
    unusually busy conditions without attenuating itself.
    """
    out: dict = {"source": source, "status": "unavailable"}
    if (frame is None or getattr(frame, "empty", True)
            or date_column not in frame.columns
            or amount_column not in frame.columns):
        return out
    df = frame.copy()
    df["_day"] = pd.to_datetime(df[date_column], errors="coerce")
    df["_amount"] = pd.to_numeric(df[amount_column], errors="coerce")
    if turnover_column in df:
        df["_turnover"] = pd.to_numeric(
            df[turnover_column].astype(str).str.replace("%", "", regex=False),
            errors="coerce",
        )
    else:
        df["_turnover"] = float("nan")
    df = df.dropna(subset=["_day", "_amount"])
    df = df[(df["_day"].dt.date <= cutoff) & (df["_amount"] > 0)]
    df = df.sort_values("_day").drop_duplicates("_day", keep="last").tail(65)
    if df.empty:
        return out
    latest = df.iloc[-1]
    day = latest["_day"].date()
    if (cutoff - day).days > 12:
        return {**out, "status": "stale", "as_of": day.isoformat()}
    result = {
        "source": source, "status": "observed", "as_of": day.isoformat(),
        "latest_turnover_yuan": round(float(latest["_amount"]), 2),
        "latest_turnover_yi": round(float(latest["_amount"])/1e8, 3),
        "samples": len(df),
        "latest_turnover_rate_pct": None,
        "latest_vs_prior20_ratio": None,
        "recent5_vs_prior20_ratio": None,
    }
    latest_rate = _number(latest["_turnover"])
    if latest_rate is not None and 0 <= latest_rate <= 100:
        result["latest_turnover_rate_pct"] = round(latest_rate, 3)
    if len(df) >= 21:
        previous20 = df.iloc[-21:-1]["_amount"].mean()
        if previous20 > 0:
            result["latest_vs_prior20_ratio"] = round(
                float(latest["_amount"])/float(previous20), 2
            )
    if len(df) >= 25:
        previous = df.iloc[-25:-5]["_amount"].mean()
        recent = df.iloc[-5:]["_amount"].mean()
        if previous > 0:
            result["recent5_vs_prior20_ratio"] = round(float(recent/previous), 2)
    return result


def assess_margin_crowding(
    margin: dict | None,
    activity: dict | None, *,
    asset_type: str, price_position_pct: float | None = None,
    float_market_cap_yi: float | None = None,
) -> dict:
    """Dated evidence with no unsupported 'crowding index' or invented zero."""
    margin = margin or {}
    activity = activity or {}
    balance = _number(margin.get("latest_balance_yi"))
    change = _number(margin.get("change_pct"))
    margin_date = str(margin.get("latest_date") or "")
    if len(margin_date) == 8 and margin_date.isdigit():
        margin_date = f"{margin_date[:4]}-{margin_date[4:6]}-{margin_date[6:]}"
    trade_date = str(activity.get("as_of") or "")
    cap = _number(float_market_cap_yi)
    ratio = (round(balance / cap * 100, 2)
             if balance is not None and cap is not None and cap > 0
             and asset_type == "stock" else None)
    gross_buy_yuan = _number(margin.get("latest_buy_yuan"))
    buy_to_trade = None
    amount = _number(activity.get("latest_turnover_yuan"))
    if (gross_buy_yuan is not None and amount and amount > 0
            and margin_date and margin_date == trade_date):
        buy_to_trade = round(gross_buy_yuan / amount * 100, 2)
    activity_factor = _number(activity.get("recent5_vs_prior20_ratio"))
    daily_factor = _number(activity.get("latest_vs_prior20_ratio"))
    px = _number(price_position_pct)
    evidence = []
    if balance is not None:
        evidence.append(
            f"融资余额{balance:.2f}亿元（{margin_date or '日期未核实'}）"
        )
        if change is not None:
            evidence.append(
                f"近{margin.get('days') or '数'}个观察交易日"
                f"{'增加' if change > 0 else '下降' if change < 0 else '基本持平'}"
                f"{abs(change):.1f}%"
            )
    if ratio is not None:
        evidence.append(f"融资余额约占流通市值{ratio:.2f}%")
    if buy_to_trade is not None:
        evidence.append(f"同日融资买入额约占成交额{buy_to_trade:.1f}%")
    if activity_factor is not None:
        evidence.append(f"近5日平均成交额是此前20日的{activity_factor:.2f}倍")
    elif daily_factor is not None:
        evidence.append(f"当日成交额为此前20日平均的{daily_factor:.2f}倍")
    if (activity.get("latest_turnover_rate_pct") is not None
            and asset_type == "stock"):
        evidence.append(f"最新换手率{activity['latest_turnover_rate_pct']:.2f}%")

    elevated_leverage = change is not None and change >= 10
    elevated_activity = (
        (activity_factor is not None and activity_factor >= 1.5)
        or (daily_factor is not None and daily_factor >= 2)
    )
    price_high = px is not None and px >= 80
    # A warning is triggered on corroborated factors, not a single threshold.
    if elevated_leverage and elevated_activity and price_high:
        assessment = "融资余额上升、成交放量且价格已在一年相对高位，杠杆和交易拥挤风险值得警惕。"
        level = "watch"
    elif elevated_leverage and elevated_activity:
        assessment = "融资盘扩张伴随成交活跃度上升，但还不能仅据此认定交易已经过度拥挤。"
        level = "warming"
    elif change is not None and change <= -8:
        assessment = "融资余额在观察期下降，属于去杠杆迹象；不代表卖盘已经出清。"
        level = "deleveraging"
    elif elevated_activity:
        assessment = "近期成交明显活跃，需要结合价格与融资盘，不能把放量直接视为净流入或泡沫。"
        level = "active"
    elif evidence:
        assessment = "现有融资与成交数据没有构成一致的过度拥挤证据，但不代表没有风险。"
        level = "insufficient_confirmation"
    else:
        assessment = "暂未取得可靠的证券自身融资和可比较的成交记录，不据此判断拥挤程度。"
        level = "unknown"
    notes = [
        "融资余额是存量，不是当日净买入额；融资买入额与成交额比较必须是同一交易日。",
        "成交额上升不等于买盘净流入，换手率高不等于参与者已经获利。",
    ]
    if asset_type == "etf":
        notes.append(
            "仅统计这只场内ETF证券自身的两融和成交；不把前十大成分股两融余额加总成基金两融。"
            "交易所没有该ETF明细时不能当作融资余额为零。"
        )
    return {
        "asset_type": asset_type,
        "margin_balance_yi": balance,
        "margin_date": margin_date or None,
        "margin_change_pct": change,
        "margin_samples": margin.get("days"),
        "margin_source": margin.get("source"),
        "margin_to_float_market_cap_pct": ratio,
        "financing_buy_to_same_day_turnover_pct": buy_to_trade,
        "trade_date": trade_date or None,
        "turnover_yi": _number(activity.get("latest_turnover_yi")),
        "turnover_rate_pct": activity.get("latest_turnover_rate_pct"),
        "daily_turnover_ratio": daily_factor,
        "recent5_turnover_ratio": activity_factor,
        "turnover_source": activity.get("source"),
        "assessment": assessment,
        "level": level,
        "evidence": evidence,
        "notes": notes,
        "coverage": {
            "financing": balance is not None,
            "turnover": activity.get("status") == "observed",
            "baseline_20d": activity_factor is not None or daily_factor is not None,
        },
    }


async def get_stock_turnover_activity(stock_code: str, *, as_of: date | None = None) -> dict:
    """35 calendar days of exchange trading; fail closed, not stock price data."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    day = as_of or datetime.now(ZoneInfo("Asia/Shanghai")).date()
    code = stock_code.strip().upper().removeprefix("SH").removeprefix("SZ")
    if len(code) != 6 or not code.isdigit():
        return {"status": "unavailable", "source": "A股历史成交"}
    try:
        df = await asyncio.wait_for(
            asyncio.to_thread(
                ak.stock_zh_a_hist, symbol=code, period="daily",
                start_date=(day - timedelta(days=72)).strftime("%Y%m%d"),
                end_date=day.strftime("%Y%m%d"), adjust="",
            ), timeout=15,
        )
        return turnover_activity(df, cutoff=day, source="东方财富A股日行情")
    except Exception as exc:
        utils.logger.warning("[crowding] stock %s turnover unavailable: %s", code, exc)
        return {"status": "unavailable", "source": "东方财富A股日行情"}
