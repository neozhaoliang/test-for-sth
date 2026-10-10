"""Investor-facing valuation reference lenses, NOT audited fair value.

A professional can quantify what today's price assumes before producing an FCFE.
These reference prices answer 'at which repeatable cash dividend would the share
yield X%' and 'where is its historic PB midpoint', NOT 'what is intrinsic value'.

Do not transform preliminary issuer dividends into deterministic dividend forecasts.
No perpetual ROE-growth denominator is used.
"""
from __future__ import annotations

from datetime import date
from math import isfinite
from statistics import median
from typing import Any, Mapping, Optional, Sequence


_IMPLEMENTED = ("实施", "完成", "已派", "除权除息", "paid", "completed", "implemented")
_NOT_IMPLEMENTED = ("预案", "待实施", "未实施", "取消", "不分配", "pending", "proposal", "unpaid")


def _number(raw):
    if isinstance(raw, bool):
        return None
    try:
        v = float(raw)
        return v if isfinite(v) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _date(raw):
    try:
        return date.fromisoformat(str(raw)[:10])
    except (TypeError, ValueError):
        return None


def _completed_dividend_years(records: Sequence[Mapping], cutoff: date):
    """Sum distinct *payment implementation records* by announcement calendar year.

    This gives cash paid/implemented within the calendar year; it does NOT
    establish the corresponding fiscal-year payout ratio. Multiple notices of
    one payment are not to be added twice; without a unique action key,
    duplicated same-day+same-value entries are excluded.
    """
    years = {}
    seen = set()
    for row in records or []:
        day = _date(row.get("announce_date"))
        cash = _number(row.get("dividend_per_10_shares"))
        status = str(row.get("progress") or "").strip().lower()
        if not day or day > cutoff or cash is None or cash <= 0:
            continue
        if any(word in status for word in _NOT_IMPLEMENTED):
            continue
        if not any(word in status for word in _IMPLEMENTED):
            continue
        key = (day.isoformat(), round(cash, 6))
        if key in seen:
            continue
        seen.add(key)
        if day.year == cutoff.year:  # current calendar year is incomplete
            continue
        years[day.year] = years.get(day.year, 0.0) + cash / 10
    return dict(sorted(years.items()))


def investor_reference_valuation(*, archetype: str, dividend_history=None,
                                 valuation=None, profitability_trend=None,
                                 valuation_history=None, quote=None,
                                 as_of=None) -> dict:
    """Produce transparent reference price zones without pretending FCFE was forecast."""
    cutoff = _date(as_of) or date.today()
    v = valuation or {}
    q = quote or {}
    price = _number(q.get("latest_price"))
    if price is not None and price <= 0:
        price = None
    book = _number(v.get("nav_per_share"))
    history = _completed_dividend_years(dividend_history or [], cutoff)
    rows = sorted(history.items())
    result = {
        "status": "partial", "framework": "investor_reference_lenses_v1",
        "archetype": archetype, "price_reference": price,
        "as_of": cutoff.isoformat(), "dividend_basis": None,
        "dividend_scenarios": [], "historical_pb_anchor": None,
        "normalized_earnings": None, "key_assumptions": [],
        "warnings": [
            "收益率定价是参考区间，不是现金流折现的内在价值或买卖指令。",
            "已实施分红按公告日所在公历年统计，不等于财年宣告或未来承诺；回购不算现金股息。",
        ],
    }
    # Dividends matter most for public utilities and other stable-distribution
    # archetypes. For cyclicals they are an extra lens, not the main one.
    if len(rows) >= 2 and cutoff.year - rows[-1][0] <= 3:
        recent = rows[-5:]
        observed_positive = [d for _, d in recent]
        baseline = median(observed_positive)
        # Explicit investor hurdle examples, not issuer WACC or market forecasts.
        assumptions = [
            ("谨慎", .70, 7.0),
            ("中性", 1.00, 5.5),
            ("乐观", 1.15, 4.5),
        ]
        scenarios = []
        for name, multiple, required_yield in assumptions:
            dps = baseline * multiple
            scenarios.append({
                "name": name,
                "annual_cash_dividend_per_share": round(dps, 4),
                "assumed_cash_dividend_yield_pct": required_yield,
                "reference_price": round(dps / (required_yield / 100), 2),
                "basis": "已实施派息年份中位 × 情景分红调整 / 假设收益率",
            })
        result["dividend_basis"] = {
            "years": [y for y, _ in recent],
            "payments_by_announcement_year": [
                {"year": y, "dividend_per_share": round(d, 4)}
                for y, d in recent],
            "observed_median_cash_dividend_per_share": round(baseline, 4),
            "last_implemented_dividend_year": rows[-1][0],
            "last_implemented_dividend_per_share": round(rows[-1][1], 4),
            "latest_implemented_dividend_yield_pct": (
                round(rows[-1][1] / price * 100, 2) if price else None
            ),
            "median_implemented_dividend_yield_pct": (
                round(baseline / price * 100, 2) if price else None
            ),
            "sample_includes_zero_dividend_years": False,
            "sampling_note": "只统计有明确实施记录的派息年份；未见记录的年份不能被假定已派息，也不能假定一定为零。",
        }
        result["dividend_scenarios"] = scenarios
        result["status"] = "indicative_reference"
        result["key_assumptions"].append(
            "参考情景以已实施年度派息中位数为锚；股息分别按0.70/1.00/1.15倍情景化，"
            "投资者假设收益率为7.0%/5.5%/4.5%；这些是固定压力情景，不是预测。"
        )
    else:
        result["warnings"].append("不足两个近年已实施分红年份，不能从缺失记录补造派息估值。")

    if book is not None and book > 0:
        data = (valuation_history or {}).get("pb_percentiles") or []
        usable = sorted(
            [row for row in data
             if row.get("years") in (3, 5, 10)
             and (_number(row.get("observations")) or 0) >= 100
             and _number(row.get("median")) is not None
             and _number(row.get("median")) > 0],
            key=lambda x: abs(int(x["years"]) - 5),
        )
        if usable:
            row = usable[0]
            result["historical_pb_anchor"] = {
                "window_years": int(row["years"]),
                "median_pb": row["median"],
                "current_pb": _number(v.get("pb")),
                "book_value_per_share": book,
                "median_pb_reference_price": round(book * float(row["median"]), 2),
                "label": "历史PB中枢对应价，非内在价值；若盈利或净资产结构变化，历史中枢可能失效",
            }
            if result["status"] == "partial":
                result["status"] = "indicative_reference"

    annual = {}
    for row in (profitability_trend or {}).get("periods") or []:
        day = _date(row.get("period"))
        roe = _number(row.get("roe_pct"))
        if day and day <= cutoff and day.month == 12 and day.day == 31 and roe is not None and -50 < roe < 100:
            annual[day.year] = roe
    if book is not None and book > 0 and price and len(annual) >= 3:
        values = [annual[y] for y in sorted(annual)[-5:]]
        normalized_roe = median(values)
        result["normalized_earnings"] = {
            "annual_roe_years": sorted(annual)[-5:],
            "median_roe_pct": round(normalized_roe, 2),
            "price_to_book": round(price / book, 3),
            "indicative_normalized_earnings_yield_pct": round(
                normalized_roe * book / price, 2
            ),
            "note": "ROE中位数×期末账面净资产 / 当前股价，仅为跨周期盈利能力参照；不等于可分配现金收益率。",
        }
    return result
