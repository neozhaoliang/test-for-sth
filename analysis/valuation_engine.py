"""Finite-horizon, two-stage dividend discount *scenario*, not a price target.

The prior perpetuity PB=ROE*payout/(discount-growth) could diverge when a
temporarily high ROE and retention-implied growth approached the hurdle rate.
This implementation explicitly fades ROE over five years and normalizes the
terminal payout and growth assumption. The retention conversion factor is a
modelled productive-capital fraction, never evidence of cashflow or enterprise value.

The book value below represents *effective productive equity* after assumed
retention losses; it is not necessarily GAAP shareholder equity.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Optional, Sequence


FORECAST_YEARS = 5
MIN_TERMINAL_DISCOUNT_SPREAD_PCT = 4.0
DEFAULT_TERMINAL_GROWTH_CAP_PCT = 2.0
DEFAULT_NORMALIZED_ROE_CAP_PCT = 12.0


@dataclass(frozen=True)
class ValuationScenario:
    name: str
    sustainable_roe_pct: float  # first projected annual ROE, not a perpetuity
    payout_pct: float           # first five years, not necessarily terminal payout
    retention_conversion_pct: float
    required_return_pct: float
    normalized_roe_pct: Optional[float] = None
    terminal_growth_cap_pct: float = DEFAULT_TERMINAL_GROWTH_CAP_PCT


def _valid_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def calculate_scenario(
    scenario: ValuationScenario, *,
    book_value_per_share: Optional[float] = None,
    market_price: Optional[float] = None,
) -> dict:
    """Five discounted dividend years plus a conservatively normalized terminal.

    Earnings_t = effective_book_(t-1) * faded_ROE_t
    dividend_t = earnings_t * forecast_payout
    effective_book_t = effective_book_(t-1) + retained_earnings * efficiency

    For the tail, payout increases if needed to limit retention-implied terminal
    growth to an explicit cap (default 2%). Terminal g is also kept at least
    four percentage points below the required return; no near-zero denominator.
    """
    r, p, c, k, rt, gt = (
        scenario.sustainable_roe_pct, scenario.payout_pct,
        scenario.retention_conversion_pct, scenario.required_return_pct,
        scenario.normalized_roe_pct, scenario.terminal_growth_cap_pct,
    )
    numbers = (r, p, c, k, gt) + ((rt,) if rt is not None else ())
    if not all(_valid_number(x) for x in numbers):
        raise ValueError("scenario parameters must be finite real numbers")
    if not (0 < r <= 100 and 0 <= p <= 100 and 0 <= c <= 100
            and 0 < k <= 100 and 0 <= gt <= 10
            and (rt is None or 0 < rt <= 100)):
        raise ValueError("invalid ROE, payout, conversion, terminal growth or hurdle")
    for label, value in (("book value", book_value_per_share), ("price", market_price)):
        if value is not None and (not _valid_number(value) or value <= 0):
            raise ValueError(f"{label} must be positive when supplied")

    # No observed multi-year ROE supplied? Use an explicit conservative modelling
    # policy rather than assume today's high ROE lasts forever.
    normalized = float(rt if rt is not None else min(r, DEFAULT_NORMALIZED_ROE_CAP_PCT))
    normalized_source = "provided" if rt is not None else "model_cap_at_12_pct"
    result = {
        "scenario": scenario.name,
        "model": "five_year_fade_normalized_terminal_ddm",
        "status": "ok",
        "assumption_status": "scenario_not_observed_fact",
        "assumptions": {
            "sustainable_roe_pct": r,
            "payout_pct": p,
            "retention_conversion_pct": c,
            "required_return_pct": k,
            "normalized_roe_pct": normalized,
            "normalized_roe_source": normalized_source,
            "terminal_growth_cap_pct": gt,
            "forecast_years": FORECAST_YEARS,
        },
        "earnings_growth_pct": None,  # no perpetuity ROE*retention claim
        "terminal_growth_pct": None,
        "terminal_payout_pct": None,
        "terminal_value_share_pct": None,
        "forecast_dividend_pv_pb": None,
        "terminal_value_pv_pb": None,
        "fair_pb": None,
        "fair_pe": None,
        "fair_price": None,
        "dividend_yield_pct": None,
        "implied_annual_return_pct": None,  # not derivable as naive g+y
        "reason": "",
    }
    # Without any shareholder payout there is no evidence of eventual cash
    # distribution. Do not silently invent a future dividend.
    if p == 0:
        result.update(status="inapplicable", reason="当前不分红；缺少未来分红承诺，不能自动假设终值派息")
        return result

    rate = k / 100
    productivity = c / 100
    payout = p / 100
    book = 1.0
    dividend_pv = 0.0
    for year in range(1, FORECAST_YEARS + 1):
        roe = (r + (normalized - r) * (year - 1) / (FORECAST_YEARS - 1)) / 100
        earnings = book * roe
        dividend = earnings * payout
        dividend_pv += dividend / ((1 + rate) ** year)
        book += earnings * (1 - payout) * productivity

    # Assume the terminal firm distributes more if needed to respect a modest
    # perpetual growth cap; retained profits cannot forever grow at peak ROE.
    terminal_roe = normalized / 100
    terminal_growth_cap = min(
        gt / 100, max(0.0, rate - MIN_TERMINAL_DISCOUNT_SPREAD_PCT / 100)
    )
    if terminal_roe * productivity > 0:
        terminal_payout = max(
            payout, 1 - terminal_growth_cap / (terminal_roe * productivity)
        )
    else:
        terminal_payout = payout
    terminal_payout = min(1.0, terminal_payout)
    terminal_growth = terminal_roe * (1 - terminal_payout) * productivity
    spread = rate - terminal_growth
    if spread < MIN_TERMINAL_DISCOUNT_SPREAD_PCT / 100 - 1e-12:
        result.update(status="inapplicable", reason="终值增长率与折现率安全距离不足")
        return result
    next_dividend = book * terminal_roe * terminal_payout
    terminal_pv = next_dividend / spread / ((1 + rate) ** FORECAST_YEARS)
    fair_pb = dividend_pv + terminal_pv
    if not isfinite(fair_pb) or fair_pb <= 0:
        result.update(status="inapplicable", reason="终值计算无效")
        return result
    result.update(
        terminal_growth_pct=round(terminal_growth * 100, 4),
        terminal_payout_pct=round(terminal_payout * 100, 4),
        terminal_value_share_pct=round(100 * terminal_pv / fair_pb, 4),
        forecast_dividend_pv_pb=round(dividend_pv, 6),
        terminal_value_pv_pb=round(terminal_pv, 6),
        fair_pb=round(fair_pb, 6),
        fair_pe=round(fair_pb / (r / 100), 6),
    )
    if book_value_per_share is not None:
        result["fair_price"] = round(fair_pb * book_value_per_share, 4)
        if market_price is not None:
            # Indicative next dividend yield only, NOT expected annual total return.
            result["dividend_yield_pct"] = round(
                (r / 100) * payout * book_value_per_share / market_price * 100, 4
            )
    return result


def calculate_scenarios(
    scenarios: Sequence[ValuationScenario], *,
    book_value_per_share: Optional[float] = None,
    market_price: Optional[float] = None,
) -> dict:
    names = [s.name for s in scenarios]
    if len(names) != len(set(names)):
        raise ValueError("duplicate scenario names")
    if not scenarios:
        return {"status": "missing_assumptions", "results": [],
                "warning": "缺少具备日期、来源和解释的情景假设，不得自动捏造目标价"}
    rows = [
        calculate_scenario(s, book_value_per_share=book_value_per_share,
                           market_price=market_price) for s in scenarios
    ]
    return {
        "status": "calculated" if any(x["status"] == "ok" for x in rows) else "inapplicable",
        "results": rows,
        "warning": (
            "仅为五年有限期股息折现情景，不是可靠目标价；长期ROE与终值分红政策须单独核验。"
            "不能把阶段性高ROE和留存增长永续外推；终值占比高时尤其不稳健。"
        ),
    }


def render_valuation_block(result: dict) -> str:
    lines = ["Python 强制估值校验（不可由模型修改数值）:", result["warning"]]
    if result["status"] == "missing_assumptions":
        lines.append("本次没有可核验的三情景参数，禁止生成确定的合理价或安全边际。")
        return "\n".join(lines)
    for row in result["results"]:
        a = row["assumptions"]
        lines.append(
            f"{row['scenario']}: 首年ROE={a['sustainable_roe_pct']}%，"
            f"前5年分红率={a['payout_pct']}%，有效再投资效率={a['retention_conversion_pct']}%，"
            f"要求收益率={a['required_return_pct']}%，长期ROE={a['normalized_roe_pct']}% "
            f"({a['normalized_roe_source']})；"
            f"终值增长={row['terminal_growth_pct']}%，终值派息率={row['terminal_payout_pct']}%；"
            f"状态={row['status']}；情景PB={row['fair_pb']}；"
            f"情景PE={row['fair_pe']}；情景价={row['fair_price']}；"
            f"终值占比={row['terminal_value_share_pct']}%"
            + (f"；失效原因={row['reason']}" if row["reason"] else "")
        )
    lines.append("严禁把情景价格写成精确目标价；不提供原模型的 g+股息率=预期总回报 伪指标。")
    return "\n".join(lines)
