# -*- coding: utf-8 -*-
"""ETF ownership incentives and high-valuation exposure, with evidence gates.

Quarterly weights and today's stock prices cannot prove an ETF bought today.
Index reconstitution trades and active-manager return chasing are *different*
hypotheses, not observable intent. Show only evidenced high-price exposure.
"""
from __future__ import annotations

import math
import re
from typing import Any


_INDEX_HINTS = (
    "中证", "国证", "沪深", "深证", "上证", "恒生", "纳斯达克",
    "标普", "创业板指", "科创50", "中证港股通", "指数", "A500", "a500",
)
_ACTIVE_HINTS = ("主动ETF", "主动型ETF", "主动管理ETF", "主动式ETF")


def _float(value: Any) -> float | None:
    try:
        val = float(value)
        return val if math.isfinite(val) else None
    except (ValueError, TypeError, OverflowError):
        return None


def management_style(fund: dict) -> dict:
    """Naming clues only; no claim of verified contract or index methodology."""
    name = str(fund.get("name") or "")
    lowered = name.lower()
    if any(token.lower() in lowered for token in _ACTIVE_HINTS):
        kind = "active"
        label = "可能为主动管理ETF"
    elif any(token.lower() in lowered for token in _INDEX_HINTS):
        kind = "index"
        label = "可能为指数跟踪ETF"
    else:
        kind = "unknown"
        label = "管理方式尚未核实"
    return {
        "kind": kind,
        "label": label,
        "source": "基金名称提示，非基金合同或指数编制方案",
        "verified": False,
        "necessary_verification": "以基金合同、招募说明书、跟踪指数及管理方式为准",
    }


def _valuation_percentile(holding: dict) -> float | None:
    points = [
        _float((holding.get(key) or {}).get("percentile"))
        for key in ("pe_history_position", "pb_history_position")
        if isinstance(holding.get(key), dict)
    ]
    return max([x for x in points if x is not None], default=None)


def screen_holdings(fund: dict, portfolio: dict) -> dict:
    style = management_style(fund)
    holdings = (portfolio.get("holdings") or [])[:10]
    exposed = []
    observed_weight = 0.0
    valuation_observed = 0.0
    high_price_weight = 0.0
    high_valuation_weight = 0.0
    high_both_weight = 0.0
    for h in holdings:
        weight = _float(h.get("weight_pct")) or 0
        pos = _float(h.get("price_position_pct"))
        pct = _valuation_percentile(h)
        if pos is not None:
            observed_weight += weight
        if pct is not None:
            valuation_observed += weight
        high_price = pos is not None and pos >= 80
        high_valuation = pct is not None and pct >= 80
        if high_price:
            high_price_weight += weight
        if high_valuation:
            high_valuation_weight += weight
        if high_price and high_valuation:
            high_both_weight += weight
        if high_price or high_valuation:
            exposed.append({
                "code": h.get("code"), "name": h.get("name"),
                "weight_pct": round(weight, 2),
                "price_position_pct": pos,
                "valuation_percentile_pct": pct,
                "price_observed_on": h.get("price_as_of"),
                "valuation_observed_on": h.get("valuation_observed_on"),
                "flags": [
                    flag for flag, cond in (
                        ("接近52周股价高区间", high_price),
                        ("至少一项PE/PB估值分位较高", high_valuation),
                    ) if cond
                ],
            })
    exposed.sort(key=lambda h: -h["weight_pct"])
    risk = {
        "kind": "管理方式未核实",
        "headline": "先核实指数规则或基金经理的调仓行为，不能凭季度持仓断言买入高位股。",
        "verification": [
            "基金合同、业绩比较基准、指数跟踪规则或主动管理授权",
            "前后季度持仓权重及指数成份变动（价格上涨也会抬高权重）",
            "股票估值与基金申赎/交易日期；基金季报不代表当日实时持仓",
        ],
    }
    if style["kind"] == "index":
        risk = {
            "kind": "指数被动高位承接风险",
            "headline": ("需要检查指数是否在热门股票高位扩容、纳入或加权，以及基金"
                         "是否被动跟随买入；指数ETF不等于有能力择时避开明显泡沫。"),
            "verification": [
                "指数编制方案的选样、权重和定期调样规则",
                "最近两次指数纳入/剔除及调样公告与当时股价/估值分位",
                "最新申购赎回清单、季度持仓变化和基金份额变动",
            ],
        }
    elif style["kind"] == "active":
        risk = {
            "kind": "主动ETF高位追逐热点风险",
            "headline": ("应核查经理是否持续在高估值阶段增配热门股票以追逐短期净值"
                         "表现；不能仅凭高位持仓推断经理动机或存在操纵。"),
            "verification": [
                "连续定期报告的增减仓与成交时点、经理策略和基金合同",
                "增配股票的历史PE/PB与估值分位、盈利兑现与现金流",
                "基金净值、风险暴露、换手率和基准偏离的同期变化",
            ],
        }
    return {
        "management_style": style,
        "risk_type": risk["kind"],
        "risk_summary": risk["headline"],
        "verification_needed": risk["verification"],
        "disclosure_as_of": portfolio.get("disclosure_as_of") or portfolio.get("as_of"),
        "price_observed_weight_pct": round(observed_weight, 2),
        "valuation_observed_weight_pct": round(valuation_observed, 2),
        "high_price_weight_pct": round(high_price_weight, 2),
        "high_valuation_weight_pct": round(high_valuation_weight, 2),
        "high_price_and_valuation_weight_pct": round(high_both_weight, 2),
        "watchlist": exposed,
        "limitations": (
            "价格区间80%是股价高位指标，非泡沫认定；历史PE/PB分位也非独立定价证据。"
            "季报权重变动既可能来自交易，也可能来自股价涨跌，不能据此断定ETF实际在高位买入，更不能推断基金经理动机。"
        ),
    }
