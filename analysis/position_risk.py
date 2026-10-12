"""Point-in-time, position-aware *equity odds* overlay.

Asymmetry: after a large rally or while valuation is still expensive during a
selloff, interrogate permanent downside, liquidity and crowding. At a low price
AND genuinely depressed valuation, look for independently verified recovery
catalysts. Neither direction is assumed from price position alone.

Four independent evidence families are required for an objective downside
concurrence; a confirmed, material foreign-policy event can substitute for one
family only when company exposure is documented. An LLM may not turn missing
data into neutral *confidence* or invent an issuer-level financing panic.
"""
from __future__ import annotations

import json
import math
import re
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Optional


_EVENTS_DIR = Path(__file__).resolve().parents[1] / "data" / "issuer_risk_events"


def _n(v) -> Optional[float]:
    if isinstance(v, bool):
        return None
    try:
        out = float(v)
        return out if math.isfinite(out) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _dt(value) -> Optional[date]:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _fresh(raw, cutoff: date, days: int = 10) -> bool:
    value = _dt(raw)
    return value is not None and 0 <= (cutoff - value).days <= days


def _issuer_events(stock_code: str, cutoff: date) -> list[dict]:
    code = re.sub(r"\D", "", stock_code)[-6:]
    if len(code) != 6:
        return []
    try:
        payload = json.loads((_EVENTS_DIR / f"{code}.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return []
    if not isinstance(payload, dict) or payload.get("stock_code") != code:
        return []
    result = []
    for e in payload.get("events") or []:
        if (isinstance(e, dict) and _dt(e.get("published_at"))
                and _dt(e["published_at"]) <= cutoff
                and str(e.get("official_source", "")).startswith("https://")
                and e.get("risk_level") == "material_policy_exposure"):
            result.append({
                "id": e.get("id"),
                "published_at": e["published_at"],
                "event_type": e.get("event_type"),
                "headline": e.get("headline"),
                "official_source": e["official_source"],
                "company_response": e.get("company_response"),
                "legal_scope": e.get("legal_scope"),
                "status": "historical_designation_recheck_current_status",
            })
    return result


def validated_quote_or_close(quote: Optional[Mapping], market: Optional[Mapping],
                              *, as_of: Optional[str]) -> Optional[dict]:
    """Never let a quote of zero/NaN/negative appear as a real current price."""
    cutoff = _dt(as_of) or date.today()
    raw = dict(quote or {})
    price = _n(raw.get("latest_price"))
    if price is not None and price > 0:
        return raw
    stock = (market or {}).get("stock") or {}
    close = _n(stock.get("latest"))
    if close is None or close <= 0 or not _fresh(stock.get("latest_date"), cutoff, 7):
        return None
    return {
        "latest_price": close,
        "change_pct": None,
        "volume": None,
        "timestamp": stock["latest_date"],
        "date": stock["latest_date"],
        "quote_mode": "verified_previous_close",
        "quote_note": "实时报价不可用，使用最近7日内有日期的交易日收盘价；非实时盘中价",
    }


def position_risk_context(inputs: Any) -> dict:
    cutoff = _dt(getattr(inputs, "as_of", None)) or date.today()
    code = str(getattr(inputs, "stock_code", "") or "")
    quote = getattr(inputs, "quote", None) or {}
    market = getattr(inputs, "market_context", None) or {}
    stock = market.get("stock") or {}
    valuation_history = getattr(inputs, "valuation_history", None) or {}
    facts = (getattr(inputs, "fundamentals", None) or {}).get("facts") or {}
    margin = getattr(inputs, "margin_signal", None) or {}
    profile = getattr(inputs, "research_profile", None)
    archetype = getattr(profile, "archetype", "general")
    macro = ((getattr(inputs, "valuation_model_result", None) or {})
             .get("macro_context") or {})
    style = macro.get("a_share_style") or {}
    valid_price = _n(quote.get("latest_price"))
    price_ok = valid_price is not None and valid_price > 0
    signals = []
    opp = []
    external = _n(facts.get("overseas_revenue_pct"))
    cash_profit = _n(facts.get("cash_to_profit_ratio"))
    events = _issuer_events(code, cutoff)

    def signal(key, label, value, source_as_of, details, severity="elevated"):
        signals.append({
            "family": key, "label": label, "value": value,
            "as_of": source_as_of, "reason": details, "severity": severity,
        })

    # Never evaluate "current risk/odds" against zero or an undated old tick.
    if price_ok and _fresh(stock.get("latest_date"), cutoff, 7):
        current = _n(stock.get("latest"))
        high = _n(stock.get("w52_high"))
        low = _n(stock.get("w52_low"))
        d20 = _n(stock.get("d20_pct"))
        d60 = _n(stock.get("d60_pct"))
        if (high and high > 0 and current and 0 < current <= high
                and 100 * (high - current) / high >= 30
                and ((d20 is not None and d20 <= -4) or
                     (d60 is not None and d60 <= -8))):
            drawdown = round(100 * (high-current)/high, 1)
            signal("price_trend", "高点回撤后趋势仍弱", drawdown,
                   stock["latest_date"],
                   f"距52周最高收盘价回撤{drawdown}%；近期趋势仍弱。低位不自动等于便宜。")
        if low and high and current and high > low and 0 < current:
            p = 100 * (current - low)/(high-low)
            if p <= 25:
                opp.append({
                    "family": "price_position", "reason": "处于52周较低位置；需进一步核实估值与复苏催化剂",
                    "position_pct": round(p, 1),
                })
    if (price_ok and _fresh(valuation_history.get("as_of"), cutoff, 14)):
        choices = [
            row for row in valuation_history.get("pb_percentiles") or []
            if (_n(row.get("observations")) or 0) >= 100
            and row.get("years") in (3, 5, 10)
            and _n(row.get("percentile")) is not None
        ]
        choices.sort(key=lambda row: abs(row["years"] - 5))
        if choices:
            row = choices[0]
            pct = _n(row["percentile"])
            if pct >= 80:
                signal("valuation", "市净率仍处历史较高分位", pct,
                       valuation_history["as_of"],
                       f"近{row['years']}年PB处{pct:.1f}%分位；价格回撤不代表预期溢价已充分消失。")
            elif pct <= 25:
                opp.append({
                    "family": "valuation", "reason": f"近{row['years']}年PB已处{pct:.1f}%分位，仍需核验净资产质量",
                    "percentile": pct,
                })
    # Bad cash conversion and overseas exposure are different drivers.
    # Fund flow must NOT be counted as "company cashflow".
    if cash_profit is not None and 0 <= cash_profit < .5:
        signal("cash_conversion", "利润现金兑现偏弱", cash_profit,
               str((getattr(inputs,"fundamentals",None) or {}).get("as_of") or cutoff),
               f"经营现金流/净利润约{cash_profit:.2f}；须核查应收存货、扩产及年度季节性，不能认定利润造假。")
    if style.get("status") == "observed" and _fresh(style.get("as_of"), cutoff, 7):
        spread = _n(style.get("dividend_minus_growth_ytd_pp"))
        if (archetype == "technology" and style.get("regime") == "dividend_leading"
                and spread is not None):
            window = style.get("style_window") or "year to date"
            signal("market_style", "红利相对成长走强", spread,
                   style["as_of"], f"{window}红利相对成长领先{spread:.2f}个百分点。只说明风格压力，不等于个股被机构净卖出。")
    if events and external is not None and external >= 50:
        for e in events[:1]:
            signal("foreign_policy", "已发生的海外政策事件与高海外业务敞口",
                   external, e["published_at"],
                   f"官方发布：{e['headline']}。海外营收占比约{external:.1f}%；"
                   "该名单不等同全面商业禁售，公司公告表示当时订单、生产和供应链未受实质影响；后续仍须核验。",
                   severity="material")
    if margin and _fresh(margin.get("latest_date"), cutoff, 10):
        balance = _n(margin.get("latest_balance_yi"))
        change = _n(margin.get("change_pct"))
        if (balance is not None and balance > 0 and change is not None
                and change <= -5 and stock.get("d20_pct") is not None
                and _n(stock.get("d20_pct")) is not None
                and _n(stock["d20_pct"]) < 0):
            # Financing drawdown is contextual, not separately counted in the
            # independent-family trigger; falling balance can reflect repayment.
            signal("margin_context", "融资余额随弱势行情回落", round(change, 1),
                   margin["latest_date"], f"最近{margin.get('days')}个观察日融资余额变化{change:.1f}%；"
                   "去杠杆迹象待核查，不能仅凭绝对余额推断强平。")

    independent = {x["family"] for x in signals} - {"margin_context"}
    severe_policy = any(x["family"] == "foreign_policy" for x in signals)
    # Four independent families, or a verified material policy event + two
    # others (policy + valuation + cash, etc). This is a *decision review
    # condition*, not empirical evidence that a price will fall.
    bearish = price_ok and (
        len(independent) >= 4 or (severe_policy and len(independent) >= 3)
    )
    low_price = any(x["family"] == "price_position" for x in opp)
    low_valuation = any(x["family"] == "valuation" for x in opp)
    regime = ("bearish_concurrence" if bearish else
              "low_price_recovery_watch" if low_price and low_valuation else
              "risk_watch" if len(independent) >= 2 else
              "mixed_or_incomplete")
    return {
        "status": "assessed" if price_ok else "invalid_quote",
        "regime": regime,
        "as_of": cutoff.isoformat(),
        "reference_price": valid_price if price_ok else None,
        "risk_signals": signals,
        "opportunity_signals": opp,
        "independent_downside_families": sorted(independent),
        "risk_asymmetry_triggered": bearish,
        "issuer_policy_events": events,
        "interpretation": (
            "当前多项独立下行驱动叠加，股票赔率评估应以风险为先；"
            "企业高增长不能自动抵消高估值、市场退潮或重大政策风险。"
            if bearish else
            "先区分价格所处位置与估值是否便宜；低位核验复苏证据，"
            "高估值或弱趋势时核验损失幅度，不因数据缺失预设方向。"
        ),
        "warnings": [
            "52周股价低位不等于估值低位；大幅下跌后仍可能处于估值高分位。",
            "风险信号是观察到的条件，不等于公司失去客户或股价必然继续下跌。",
            "同一价量与融资信号可能相关，融资只作辅助解释而不重复计入独立风险家族。",
        ],
    }


def position_risk_prompt_block(ctx: dict) -> str:
    rows = [
        "## 股价位置 × 估值 × 风格 × 政策：不对称投资赔率",
        f"状态={ctx.get('status')}；情景={ctx.get('regime')}；"
        f"基准价={ctx.get('reference_price')}；日期={ctx.get('as_of')}。",
        "高位先找损失路径，低位先找复苏证据；52周位置低并不意味着估值低，"
        "经历大幅回撤后仍高估值的情况尤其要警惕。"
    ]
    for s in ctx.get("risk_signals") or []:
        rows.append(f"下行风险[{s['family']}，{s['as_of']}]：{s['reason']}")
    for s in ctx.get("opportunity_signals") or []:
        rows.append(f"机会核验[{s['family']}]：{s['reason']}")
    if ctx.get("risk_asymmetry_triggered"):
        rows.append(
            "已出现多条彼此独立的下行风险。不能因公司业绩同比增长漂亮，就用"
            "'利好利空相抵'归为中性。应分别给公司质量和当前股票赔率结论，"
            "明确讨论为何看空赔率，除非存在可验证的实质性反证。"
        )
    else:
        rows.append("未触发客观多风险共振；不要为了迎合某种观点强行判看空或看多。")
    if ctx["status"] == "invalid_quote":
        rows.append("价格无效，不得给出任何当前赔率方向或实时股价。")
    return "\n".join(rows)


def enforce_asymmetric_odds(summary, ctx: dict):
    """Final guard against LLM neutralization of independent *documented* risks."""
    if ctx.get("regime") != "bearish_concurrence":
        return summary
    summary.current_odds_stance = "bearish"
    summary.stance = "bearish"
    # Preserve business-quality assessment: bearish equity odds is NOT a
    # conclusion that the product or company has no technology or customers.
    risks = {s["family"]: s for s in ctx.get("risk_signals") or []}
    key_points = []
    if "foreign_policy" in risks:
        key_points.append("已发生的海外政策列名与高海外收入暴露同时存在（不等于全面禁售）")
    if "valuation" in risks:
        key_points.append(f"PB在历史{risks['valuation']['value']:.1f}%分位")
    if "cash_conversion" in risks:
        key_points.append(f"经营现金流/利润约{risks['cash_conversion']['value']:.2f}")
    if "market_style" in risks:
        key_points.append(f"红利相对成长领先{risks['market_style']['value']:.2f}个百分点")
    if "price_trend" in risks:
        key_points.append(f"距52周高点回撤{risks['price_trend']['value']:.1f}%且趋势偏弱")
    summary.thesis_summary = (
        "当前股票赔率看空：" + "；".join(key_points[:5])
        + "。企业真实增长并不能自动抵消估值和政策风险，需跟踪订单、回款与市场风格变化。"
    )
    return summary
