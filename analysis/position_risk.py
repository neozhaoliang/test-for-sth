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
    # Verified inside sales + new equity issuance is a distinct capital
    # allocation/recovery question, not an automatic claim of shareholder loss.
    # It does not duplicate market margin financing or the price trend.
    issuer = getattr(inputs, "issuer_capital_context", None) or {}
    confirmed = issuer.get("source_verified_events") or []
    if archetype == "technology" and issuer.get("status") == "source_verified_events":
        recent_sales = [
            e for e in confirmed if e.get("status") == "completed"
            and e.get("event_type") == "insider_sale"
            and _dt(e.get("disclosed_at"))
            and 0 <= (cutoff - _dt(e["disclosed_at"])).days <= 365
        ]
        funded = [
            e for e in confirmed if e.get("status") == "completed"
            and e.get("event_type") == "equity_issuance"
            and _dt(e.get("disclosed_at"))
            and _dt(e["disclosed_at"]) <= cutoff
        ]
        if price_ok and recent_sales and funded:
            signal(
                "capital_allocation", "内部人已减持且公司完成新增股权融资",
                len(recent_sales),
                max(recent_sales[0]["disclosed_at"], funded[0]["disclosed_at"]),
                "官方公告确认个人/控股股东已减持、公司另行发行新股募集资金；"
                "两者不能混记为公司分红或回购。现有股东承担估值与每股稀释风险，"
                "新增资金能否获得足够回报仍需看项目兑现，而不能推断违法套现。",
            )
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

    # These factors are a research *inventory*, not an equation for a verdict.
    # Their relative materiality is issuer- and market-specific. One severe
    # factor can dominate; five mild correlated observations need not matter.
    independent = {x["family"] for x in signals} - {"margin_context"}
    low_price = any(x["family"] == "price_position" for x in opp)
    low_valuation = any(x["family"] == "valuation" for x in opp)
    regime = ("invalid_quote" if not price_ok else
              "low_price_and_valuation_observed" if low_price and low_valuation else
              "factors_to_interpret" if signals or opp else
              "insufficient_signals")
    return {
        "status": "assessed" if price_ok else "invalid_quote",
        "regime": regime,
        "as_of": cutoff.isoformat(),
        "reference_price": valid_price if price_ok else None,
        "risk_signals": signals,
        "opportunity_signals": opp,
        "independent_downside_families": sorted(independent),
        # Backwards compatible field. It can NEVER drive a directional stance.
        "risk_asymmetry_triggered": False,
        "issuer_policy_events": events,
        "interpretation": (
            "以下仅为客观观察到的市场与经营线索。"
            "综合投资结论要由研究模型根据竞争、资本回报、市场预期、"
            "行业周期和具体价格独立形成，不能根据阈值或信号数量自动决定方向。"
        ),
        "warnings": [
            "52周价格低位不等于估值便宜，市场反转也不等于经营改善。",
            "单个重大事件可能比多个小指标更重要；信号存在并不代表必然下跌。",
            "融资余额变化、价格下跌和市场风格可能共因，不能重复计票。",
        ],
    }


def position_risk_prompt_block(ctx: dict) -> str:
    """Present researchable observations; never command a stance."""
    rows = [
        "## 市场位置与公司风险：供投资经理自由研判的观察线索",
        f"数据状态={ctx.get('status')}；观察类别={ctx.get('regime')}；"
        f"基准价={ctx.get('reference_price')}；截至={ctx.get('as_of')}。",
        "以下不是打分表或决策树。高位可以偏重下行风险，低位可以寻找改善，"
        "但价格本身既不证明贵，也不证明便宜。"
    ]
    for item in ctx.get("risk_signals") or []:
        rows.append(f"需判断重要性[{item['family']}，{item['as_of']}]：{item['reason']}")
    for item in ctx.get("opportunity_signals") or []:
        rows.append(f"可能的机会线索[{item['family']}]：{item['reason']}")
    rows.append(
        "这些观察是研究入口而非最终判断。你可以看多、看空或中性，"
        "可以认为某项数据被市场错误定价，也可以认为它不重要。"
        "从当下公司和买入价格挑出决定性矛盾，解释为什么其影响"
        "大于其他数据；任何方向都不能由信号数目自动推出。"
    )
    if ctx.get("status") == "invalid_quote":
        rows.append("当前价格失效，禁止推断当前赔率或编造实时行情。")
    return "\n".join(rows)


def enforce_asymmetric_odds(summary, ctx: dict):
    """Legacy compatibility only. Never rewrite an investor's authored view.

    Price, dates, event status and report completeness are validated elsewhere;
    a formula must not replace the model's actual investment judgment.
    """
    return summary
