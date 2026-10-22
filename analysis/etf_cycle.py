# -*- coding: utf-8 -*-
"""Evidence-driven ETF industry-cycle assessment from disclosed top holdings.

Not a point-score valuation or a magic 'down 20% = cycle trough' rule.
Classifications require identifiable profitability breadth and, for resource
sectors, an independently observed product/commodity signal.
"""
from __future__ import annotations

from typing import Any


def _numeric(v: Any):
    try:
        n = float(v)
        return n if -1e10 < n < 1e10 else None
    except (ValueError, TypeError):
        return None


def earnings_breadth(holdings: list[dict]) -> dict:
    usable = [h for h in holdings if _numeric(h.get("net_profit_growth_pct")) is not None]
    comparable = [h for h in holdings if _numeric(h.get("growth_change_pp")) is not None]
    increasing = [h for h in comparable if h["growth_change_pp"] > 2]
    decreasing = [h for h in comparable if h["growth_change_pp"] < -2]
    positive = [h for h in usable if h["net_profit_growth_pct"] > 0]
    negative = [h for h in usable if h["net_profit_growth_pct"] < 0]
    w = lambda rows: round(sum(float(h.get("weight_pct") or 0) for h in rows), 2)
    out = {
        "sample_count": len(usable), "sample_weight_pct": w(usable),
        "positive_growth_weight_pct": w(positive),
        "negative_growth_weight_pct": w(negative),
        "comparable_count": len(comparable),
        "comparable_weight_pct": w(comparable),
        "accelerating_growth_weight_pct": w(increasing),
        "decelerating_growth_weight_pct": w(decreasing),
        "accelerating_count": len(increasing),
        "decelerating_count": len(decreasing),
        "definition": ("加速/减速为已披露相邻报告期净利同比增长率变动超过±2个百分点；"
                       "Q1与H1为不同累计期间的同比增速，不等于独立单季度利润环比变化。"
                       "权重分母为ETF总净资产，仅前十披露重仓的可观察部分。"),
    }
    return out


_RESOURCE_SECTORS = {"黄金产业股票", "煤炭", "原油石化", "铜产业", "铝产业", "综合有色金属", "钢铁", "甲醇", "基础化工"}


def assess_cycle(market: dict, holdings: list[dict]) -> dict:
    earnings = earnings_breadth(holdings)
    sector = market.get("sector") or ""
    observations = market.get("observations") or {}
    commodity = (observations.get("gold_cny") or observations.get("gold_usd")
                 if sector == "黄金产业股票" else observations.get("sector_commodity"))
    price_trend_3m = _numeric((commodity or {}).get("return_3m_pct"))
    has_cycle_price = price_trend_3m is not None
    sufficient = (earnings["sample_count"] >= 3 and
                  earnings["sample_weight_pct"] >= 15 and
                  earnings["comparable_count"] >= 3)
    negative = earnings["negative_growth_weight_pct"]
    positive = earnings["positive_growth_weight_pct"]
    improving = earnings["accelerating_growth_weight_pct"]
    worsening = earnings["decelerating_growth_weight_pct"]
    reasons: list[str] = []
    if sufficient:
        reasons.append(
            f"可比盈利样本{earnings['comparable_count']}只、覆盖ETF净值"
            f"{earnings['comparable_weight_pct']:.2f}%；"
            f"增速改善权重{improving:.2f}%，增速回落权重{worsening:.2f}%"
        )
    else:
        reasons.append("前十大重仓的可比利润样本不够，不能断言行业周期底/顶")
    if has_cycle_price:
        reasons.append(f"{commodity['name']}近约63交易日变动{price_trend_3m:+.2f}%"
                       f"（截至{commodity['as_of']}）")
    elif sector in _RESOURCE_SECTORS:
        reasons.append("缺少独立于ETF净值的有效产品价格趋势，不能确认资源行业拐点")

    # These are *candidates* for discussion, not deterministic investment stances.
    stage = "周期位置待确认"
    confidence = "不足"
    if sufficient and (sector not in _RESOURCE_SECTORS or has_cycle_price):
        confidence = "中（披露前十样本，并非整个行业）"
        if negative > 0 and improving > worsening and (
                not has_cycle_price or price_trend_3m > 2):
            stage = "低盈利后的修复候选"
            reasons.append("盈利广度尚弱、但样本增速改善并伴随价格回升；"
                           "需再核实库存和供给收缩才能把它当周期底部")
        elif positive > negative and worsening > improving and (
                not has_cycle_price or price_trend_3m < -2):
            stage = "高盈利阶段的回落风险"
            reasons.append("利润仍高但增速扩张面收缩、产品价走弱；"
                           "需核实库存和资本开支是否见顶")
        elif positive > negative and improving > worsening and (
                not has_cycle_price or price_trend_3m > 2):
            stage = "盈利扩张候选"
            reasons.append("盈利正增长权重较多且增速改善；"
                           "仍须检查是否已反映在估值和基金价格中")
        elif negative > positive and worsening >= improving:
            stage = "盈利承压/下行候选"
            reasons.append("负增长样本占优且利润增速继续回落")
        else:
            stage = "产业盈利分化"
            reasons.append("盈利加速、减速和产品价未形成一致证据")
    return {
        "sector": sector, "stage": stage, "confidence": confidence,
        "earnings_breadth": earnings, "commodity_signal": commodity,
        "evidence": reasons,
        "bottom_confirmation": ("确认底部需产品价格/库存/产能/利润同比连续改善，"
                                "低股价或低PE不能独立证明行业见底"),
        "top_confirmation": ("确认顶部风险需高利润、高产能投放、库存累积、"
                             "价格/订单转弱等交叉证据，单次盈利减速不能独立证明见顶"),
    }
