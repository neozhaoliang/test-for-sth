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

from analysis.roe_discount_valuation import build_roe_discount_lab
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
                                 as_of=None, valuation_assumption_context=None) -> dict:
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
        "return_decomposition": None,
        "earnings_power": None,
        "roe_discount_lab": None,
        "normalized_earnings": None, "key_assumptions": [],
        "warnings": [
            "历史股息收益率不等于未来总回报，不得使用通用固定股息率倒算内在价值。",
            "已实施分红按公告日所在公历年统计，不等于财年宣告或未来承诺；回购不算现金股息。",
        ],
    }
    # The announcement-year record is only an observed cash-distribution lens.
    # It does NOT establish fiscal-year payout or a universal target dividend yield.
    if len(rows) >= 2 and cutoff.year - rows[-1][0] <= 3:
        recent = rows[-5:]
        baseline = median([d for _, d in recent])
        result["dividend_basis"] = {
            "years": [y for y, _ in recent],
            "payments_by_announcement_year": [
                {"year": y, "dividend_per_share": round(d, 4)} for y, d in recent
            ],
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
            "sampling_note": "按公告日统计已实施派息；缺失年份既不视为已分红，也不擅自补零。",
        }
        result["status"] = "indicative_reference"
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
    result["return_decomposition"] = _build_return_decomposition(
        payout_inputs=valuation_assumption_context or {},
        annual_roes=annual,
        current_price=price,
        book_per_share=book,
        historical_pb_anchor=result["historical_pb_anchor"],
        as_of=cutoff,
    )
    result["roe_discount_lab"] = build_roe_discount_lab(
        valuation=valuation,
        profitability_trend=profitability_trend,
        valuation_assumption_context=valuation_assumption_context,
        as_of=cutoff.isoformat(), archetype=archetype,
    )
    result["earnings_power"] = _company_earnings_power_sensitivity(
        archetype=archetype, profitability_trend=profitability_trend,
        valuation_history=valuation_history, as_of=cutoff, market_price=price,
    )
    return result


def _build_return_decomposition(*, payout_inputs, annual_roes, current_price,
                                book_per_share, historical_pb_anchor, as_of):
    """Explicit, finite-horizon ROE/retention/cash/PB scenarios.

    This is an *illustrative* total-return decomposition based on published
    fiscal-year payout matches and user-configured hurdle; it is not an
    automatic intrinsic value, and the final-period PB is only a sensitivity.
    """
    from analysis.valuation_payout import annual_payout_from_eps
    from analysis.valuation_assumptions import estimate_valuation_scenarios

    record = {
        "status": "insufficient_evidence",
        "framework": "five_year_roe_payout_pb_sensitivity_v1",
        "holding_years": 5,
        "payout_evidence": None,
        "scenarios": [],
        "missing_requirements": [],
        "warnings": [
            "总回报分解只是情景近似，ROE×留存×转化率不保证实现EPS增长。",
            "终点PB是条件假设而非预测，不得把条件参考价称为精准买点。",
            "现金股息、回购、增发、资本开支和债务融资不能混为一谈。",
        ],
    }
    paid = annual_payout_from_eps(
        annual_eps=payout_inputs.get("annual_eps"),
        dividends=payout_inputs.get("fiscal_dividends"),
        as_of=as_of.isoformat(),
    )
    record["payout_evidence"] = {
        "status": paid["status"], "payout_pct": paid.get("payout_pct"),
        "fiscal_years": paid.get("years") or [],
        "basis": "同财年EPS与已实施现金分红匹配，含正式公告来源；EPS仍需年报原文核验",
    }
    if paid["status"] != "verified":
        record["missing_requirements"].append("至少三个同财年、带来源的已实施派息/EPS配对")
    if len(annual_roes) < 3:
        record["missing_requirements"].append("至少三个完整年度的ROE")
    if not current_price or current_price <= 0 or not book_per_share or book_per_share <= 0:
        record["missing_requirements"].append("同一研究时点的正股价及每股净资产")
    required_return = _number(payout_inputs.get("required_return_pct"))
    if required_return is None or not 6 <= required_return <= 25:
        record["missing_requirements"].append("用户明确设置的6%—25%年化要求收益率")
    terminal_pb = _number(payout_inputs.get("terminal_pb"))
    terminal_pb_basis = str(payout_inputs.get("terminal_pb_basis") or "").strip()
    if not (terminal_pb and terminal_pb > 0 and terminal_pb_basis):
        terminal_pb = _number((historical_pb_anchor or {}).get("median_pb"))
        if terminal_pb and terminal_pb > 0:
            terminal_pb_basis = "同一截止日之前的历史PB中位数，仅用作终点情景假设"
    if terminal_pb is None or terminal_pb <= 0 or terminal_pb > 30:
        record["missing_requirements"].append("有可追溯依据的终点PB假设")
    if record["missing_requirements"]:
        return record

    profitability = {
        "periods": [
            {"period": f"{yr}-12-31", "roe_pct": roe}
            for yr, roe in annual_roes.items()
        ],
    }
    estimated = estimate_valuation_scenarios(
        profitability_trend=profitability, as_of=as_of.isoformat(),
        payout_pct=paid["payout_pct"], required_return_pct=required_return,
    )
    if estimated["status"] != "estimated":
        record["missing_requirements"].extend(estimated.get("reasons") or ["情景数据不完整"])
        return record

    current_pb = current_price / book_per_share
    horizon = record["holding_years"]
    for item in estimated["scenarios"]:
        roe = item["sustainable_roe_pct"] / 100.0
        payout = item["payout_pct"] / 100.0
        conversion = item["retention_conversion_pct"] / 100.0
        hurdle = item["required_return_pct"] / 100.0
        growth = roe * (1.0 - payout) * conversion
        # Future earnings are assumed to evolve with the stated, imperfect
        # conversion hypothesis. They are NOT accounting forecasts.
        first_dividend = book_per_share * roe * payout
        dividends = [
            first_dividend * (1.0 + growth) ** yr
            for yr in range(horizon)
        ]
        # Earnings-based proxy for future book, rather than claiming audited
        # net assets will equal retained-growth accounting.
        terminal_book_proxy = book_per_share * (1.0 + growth) ** horizon
        terminal_value = terminal_book_proxy * terminal_pb
        discount = 1.0 + hurdle
        discounted_dividends = sum(
            cash / (discount ** (year + 1))
            for year, cash in enumerate(dividends)
        )
        discounted_terminal = terminal_value / discount ** horizon
        expected_pb_change = (terminal_pb / current_pb) ** (1.0 / horizon) - 1.0
        dividend_yield = first_dividend / current_price
        record["scenarios"].append({
            "name": item["name"],
            "sustainable_roe_pct": item["sustainable_roe_pct"],
            "payout_pct": item["payout_pct"],
            "retention_conversion_pct": item["retention_conversion_pct"],
            "growth_contribution_pct": round(growth * 100, 3),
            "cash_dividend_yield_pct": round(dividend_yield * 100, 3),
            "valuation_change_annualized_pct": round(expected_pb_change * 100, 3),
            "illustrative_total_return_pct": round(
                (growth + dividend_yield + expected_pb_change) * 100, 3
            ),
            "required_return_pct": item["required_return_pct"],
            "terminal_pb": round(terminal_pb, 4),
            "discounted_dividends_per_share": round(discounted_dividends, 4),
            "discounted_terminal_per_share": round(discounted_terminal, 4),
            "terminal_share_of_value_pct": round(
                discounted_terminal / (discounted_dividends + discounted_terminal) * 100, 2
            ) if discounted_dividends + discounted_terminal > 0 else None,
            "conditional_reference_price": round(
                discounted_dividends + discounted_terminal, 2
            ),
            "basis": "5年分红现值+终点PB情景价值；仅供敏感性讨论",
        })
    record["status"] = "illustrative_scenarios"
    record["roe_years"] = estimated["observation_years"]
    record["terminal_pb_basis"] = terminal_pb_basis
    record["current_pb_from_quote"] = round(current_pb, 4)
    record["policy"] = estimated["policy"]
    return record


def _empirical_quantile(values: list[float], quantile: float) -> float:
    """Interpolated empirical sample quantile, no imposed P/E target."""
    values = sorted(values)
    offset = (len(values) - 1) * quantile
    low = int(offset)
    high = min(low + 1, len(values) - 1)
    return values[low] + (offset - low) * (values[high] - values[low])


def _company_earnings_power_sensitivity(*, archetype, profitability_trend,
                                        valuation_history, as_of, market_price):
    """A *historical*, stock-specific EPS × P/E stress table, not fair value.

    The company investor can form a forward earnings thesis separately. Historical
    EPS and P/E marginals are not independent scenario probabilities; especially
    with cyclicals, P/E can soar precisely when earnings hit a trough.
    """
    result = {
        "status": "insufficient_history",
        "model": "historical_earnings_power_sensitivity_v1",
        "scenarios": [],
        "earnings_years": [],
        "pe_sample_months": 0,
        "cautions": [
            "这是历史每股盈利×历史市场PE的条件敏感性，不是当前公允价值或预测。",
            "周期股盈利低谷可能推高PE，因此历史EPS分位与PE分位的组合并非联合概率。",
            "财务应收款、净债务、资本开支及潜在股本稀释未进入这个简化计算。",
        ],
    }
    if archetype not in ("cyclical", "general", "consumer_brand", "technology"):
        result["cautions"].append("当前企业类型不适合以此作为主要估值方法。")
        return result
    if not market_price or market_price <= 0:
        return result
    earnings: dict[int, float] = {}
    for row in (profitability_trend or {}).get("periods") or []:
        when = _date(row.get("period"))
        eps = _number(row.get("eps_yuan"))
        if (when and when <= as_of and when.month == 12 and when.day == 31
                and eps is not None):
            earnings[when.year] = eps
    selected_years = sorted(earnings)[-5:]
    earnings_values = [earnings[y] for y in selected_years]
    if len(earnings_values) < 3:
        result["cautions"].append("少于三个完整财年每股盈利，无法进行盈利能力压力情景。")
        return result
    result["earnings_years"] = selected_years
    if any(x <= 0 for x in earnings_values):
        result["cautions"].append("观察期包含亏损年度；PE定价易失真，应改用全周期自由现金流和资产负债表。")
        return result

    pe_values = []
    for row in (valuation_history or {}).get("history_monthly") or []:
        observed = _date(row.get("date"))
        pe = _number(row.get("pe_ttm"))
        if not observed or observed > as_of or pe is None or pe <= 0:
            continue
        # Retain only the last 5 completed years (no hindsight from newer data).
        if observed.year < as_of.year - 5:
            continue
        pe_values.append(pe)
    result["pe_sample_months"] = len(pe_values)
    if len(pe_values) < 24:
        result["cautions"].append("同期正PE月度样本不足24个，不生成机械参考价格。")
        return result
    if _empirical_quantile(pe_values, 0.5) > 60:
        result["cautions"].append("历史PE中位数超过60倍，可能由盈利低谷造成，停止价格外推。")
        return result

    for label, fraction in (("历史较弱", 0.25), ("历史中枢", 0.50),
                            ("历史较强", 0.75)):
        eps = _empirical_quantile(earnings_values, fraction)
        pe = _empirical_quantile(pe_values, fraction)
        result["scenarios"].append({
            "name": label,
            "observed_annual_eps_yuan": round(eps, 3),
            "historical_pe_quantile": round(pe, 2),
            "conditional_price_yuan": round(eps * pe, 2),
            "relative_to_market_pct": round((eps * pe / market_price - 1) * 100, 2),
        })
    result["status"] = "historical_sensitivity_only"
    result["market_price_yuan"] = market_price
    result["pe_as_of"] = str((valuation_history or {}).get("as_of") or "")
    return result
