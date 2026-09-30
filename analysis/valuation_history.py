# -*- coding: utf-8 -*-
"""
Historical valuation position for A shares.

Uses Eastmoney's valuation-analysis history through AkShare stock_value_em.  Percentiles
are computed in Python so the LLM receives reproducible numbers rather than doing arithmetic
inside the prompt.
"""

from __future__ import annotations

import logging
import asyncio
import re
from datetime import date, datetime
from typing import Dict, List, Optional

import akshare as ak



logger = logging.getLogger("MediaCrawler")

_TIMEOUT_S = 30
_WINDOWS = (3, 5, 10)


def _bare_code(stock_code: str) -> str:
    return re.sub(r"\D", "", stock_code)[-6:]


def _coerce_date(value) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    # pandas Timestamp and similar objects expose .date()
    try:
        d = value.date()
        if isinstance(d, date):
            return d
    except Exception:
        pass
    text = str(value).strip()[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _num(value) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        if value != value:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _percentile(values: List[float], current: Optional[float]) -> Optional[float]:
    if current is None or not values:
        return None
    return round(sum(1 for x in values if x <= current) / len(values) * 100, 1)


def _metric_summary(df, column: str, current: Optional[float], years: int) -> Optional[Dict]:
    if current is None or df is None or df.empty:
        return None
    last_date = df["数据日期"].iloc[-1]
    cutoff = date(last_date.year - years, last_date.month, min(last_date.day, 28))
    sub = df[df["数据日期"] >= cutoff]
    values = [_num(v) for v in sub[column].tolist()]
    values = [v for v in values if v is not None and v > 0]
    if not values:
        return None
    values_sorted = sorted(values)
    return {
        "years": years,
        "percentile": _percentile(values, current),
        "median": round(values_sorted[len(values_sorted) // 2], 3),
        "min": round(values_sorted[0], 3),
        "max": round(values_sorted[-1], 3),
        "observations": len(values_sorted),
    }


def _monthly_history(df, max_months: int = 120) -> List[Dict]:
    if df is None or df.empty:
        return []
    temp = df.copy()
    temp["_month"] = temp["数据日期"].astype(str).str[:7]
    sampled = temp.groupby("_month", sort=True).tail(1).tail(max_months)
    rows: List[Dict] = []
    for _, row in sampled.iterrows():
        rows.append(
            {
                "date": str(row.get("数据日期") or ""),
                "price": _num(row.get("当日收盘价")),
                "pe_ttm": _num(row.get("PE(TTM)")),
                "pb": _num(row.get("市净率")),
            }
        )
    return rows


async def get_valuation_history(
    stock_code: str,
    *,
    as_of: Optional[date] = None,
) -> Optional[Dict]:
    code6 = _bare_code(stock_code)
    if len(code6) != 6:
        return None
    try:
        df = await asyncio.wait_for(
            asyncio.to_thread(ak.stock_value_em, symbol=code6),
            timeout=_TIMEOUT_S,
        )
    except Exception as e:
        logger.warning(
            f"[valuation_history] stock_value_em({code6}) failed: "
            f"{type(e).__name__}: {str(e)[:160]}"
        )
        return None

    if df is None or df.empty or "数据日期" not in df.columns:
        return None
    df = df.dropna(subset=["数据日期"]).copy()
    df["数据日期"] = df["数据日期"].map(_coerce_date)
    df = df.dropna(subset=["数据日期"]).sort_values("数据日期").reset_index(drop=True)
    if as_of is not None:
        df = df[df["数据日期"] <= as_of].reset_index(drop=True)
    if df.empty:
        return None

    last = df.iloc[-1]
    current_pe = _num(last.get("PE(TTM)"))
    current_pb = _num(last.get("市净率"))
    current_price = _num(last.get("当日收盘价"))

    pe_stats = [
        x for x in (_metric_summary(df, "PE(TTM)", current_pe, years) for years in _WINDOWS)
        if x
    ]
    pb_stats = [
        x for x in (_metric_summary(df, "市净率", current_pb, years) for years in _WINDOWS)
        if x
    ]

    valid_pe = sum(1 for v in df.get("PE(TTM)", []) if (_num(v) or 0) > 0)
    notes: List[str] = []
    if valid_pe < len(df) * 0.6:
        notes.append("历史期间较多交易日 PE(TTM) 非正或缺失，PE 分位参考价值受盈利周期影响，应更多结合 PB/中周期利润。")

    return {
        "as_of": str(last.get("数据日期")),
        "current_price": current_price,
        "current_pe_ttm": current_pe,
        "current_pb": current_pb,
        "pe_percentiles": pe_stats,
        "pb_percentiles": pb_stats,
        "history_monthly": _monthly_history(df),
        "observations": int(len(df)),
        "valid_positive_pe_observations": int(valid_pe),
        "notes": notes,
        "source_tier": "B",
        "source": "AkShare stock_value_em / Eastmoney valuation analysis",
    }
