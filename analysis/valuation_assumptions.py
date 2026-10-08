"""Evidence-gated, conservative scenario construction for the ROE valuation engine.

Scenario spreads and retention efficiency are policy assumptions, not historical
measurements. Never backfill future observations into an as-of analysis.
"""
from __future__ import annotations

from datetime import date
from math import isfinite
from statistics import median


def _number(value):
    try:
        x = float(value)
        return x if isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _date(value):
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def estimate_valuation_scenarios(*, profitability_trend=None, fundamentals=None,
                                 dividend_history=None, as_of=None,
                                 payout_pct=None, required_return_pct=None):
    """Require >=3 distinct audited-calendar-year ROEs and a sourced payout ratio.

    payout_pct must be independently computed from matching-period cash dividends
    and attributable earnings, with supporting provenance supplied by caller.
    For now no payout is fabricated from an incomplete dividend history.
    """
    cutoff = _date(as_of) or date.today()
    periods = (profitability_trend or {}).get("periods") or []
    by_year = {}
    for row in periods:
        d = _date(row.get("period"))
        roe = _number(row.get("roe_pct"))
        # A Q1/Q2/Q3 YTD ROE is not comparable with a full-year ROE.
        if d and d <= cutoff and d.month == 12 and d.day == 31 and roe is not None and 0 < roe < 100:
            by_year[d.year] = roe
    years = sorted(by_year)[-5:]
    if len(years) < 3:
        return {"status": "insufficient_history", "scenarios": [],
                "reasons": ["少于三个完整财年ROE，季度和半年ROE不得简单年化"]}
    payout = _number(payout_pct)
    if payout is None or not 0 < payout <= 100:
        return {"status": "missing_payout", "scenarios": [],
                "reasons": ["缺少同口径且有来源的现金分红/归母利润比例"]}
    hurdle = _number(required_return_pct)
    if hurdle is None or not 6 <= hurdle <= 25:
        return {"status": "missing_hurdle", "scenarios": [],
                "reasons": ["要求收益率须有明确、审慎的使用者配置，不能默认为公司的真实资本成本"]}
    observed = [by_year[y] for y in years]
    central = median(observed)
    # Conservative deterministic scenario policies, not forecasts.
    hypotheses = (
        ("悲观", max(1, min(observed) * 0.85), max(5, payout - 10), 25, hurdle + 2),
        ("中性", central, payout, 50, hurdle),
        ("乐观", min(35, max(observed) * 1.05), min(100, payout + 5), 65, max(6, hurdle - 1)),
    )
    scenarios = [{
        "name": name,
        "sustainable_roe_pct": round(roe, 3),
        "payout_pct": round(pay, 3),
        "retention_conversion_pct": efficiency,
        "required_return_pct": round(k, 3),
    } for name, roe, pay, efficiency, k in hypotheses]
    return {"status": "estimated", "scenarios": scenarios,
            "observation_years": years, "historical_roe_pct": observed,
            "policy": "bear=min_roe*0.85; base=median_roe; bull=max_roe*1.05; retention_conversion=25/50/65",
            "warnings": ["情景参数是规则化假设而非事实预测",
                         "未核验再投资效率，估值必须做敏感性分析",
                         "周期股/银行/研发驱动企业仍须行业专用估值交叉核验"]}
