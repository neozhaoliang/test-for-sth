"""Deterministic long-run ROE / retention / dividend valuation.

All monetary outputs require auditable inputs.  Scenarios are explicitly supplied
or derived elsewhere from dated company evidence, never invented here.
The stable-growth PB formula is a sensitivity tool, not a universal DCF.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Optional, Sequence


@dataclass(frozen=True)
class ValuationScenario:
    name: str
    sustainable_roe_pct: float
    payout_pct: float
    retention_conversion_pct: float
    required_return_pct: float


def _valid_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def calculate_scenario(
    scenario: ValuationScenario, *,
    book_value_per_share: Optional[float] = None,
    market_price: Optional[float] = None,
) -> dict:
    """Calculate and verify stable-growth valuation without implicit defaults."""
    r, p, c, k = (
        scenario.sustainable_roe_pct, scenario.payout_pct,
        scenario.retention_conversion_pct, scenario.required_return_pct,
    )
    if not all(_valid_number(x) for x in (r, p, c, k)):
        raise ValueError("scenario parameters must be finite real numbers")
    if not (r > 0 and 0 <= p <= 100 and 0 <= c <= 100 and k > 0):
        raise ValueError("invalid ROE, payout, conversion or required return")
    for label, value in (("book value", book_value_per_share), ("price", market_price)):
        if value is not None and (not _valid_number(value) or value <= 0):
            raise ValueError(f"{label} must be positive when supplied")

    growth = r / 100 * (1 - p / 100) * (c / 100)
    denominator = k / 100 - growth
    result = {
        "scenario": scenario.name,
        "assumptions": {
            "sustainable_roe_pct": r, "payout_pct": p,
            "retention_conversion_pct": c, "required_return_pct": k,
        },
        "earnings_growth_pct": round(growth * 100, 4),
        "model": "steady_state_roe_payout_sensitivity",
        "assumption_status": "scenario_not_observed_fact",
        "status": "ok",
        "fair_pb": None,
        "fair_pe": None,
        "fair_price": None,
        "implied_annual_return_pct": None,
        "dividend_yield_pct": None,
        "reason": "",
    }
    if p == 0:
        result.update(status="inapplicable", reason="零派息下该稳态股息PB公式不能定价")
    elif denominator <= 0:
        result.update(status="inapplicable", reason="要求回报率不高于稳态增长率，禁止推导无限估值")
    else:
        pb = r / 100 * (p / 100) / denominator
        result["fair_pb"] = round(pb, 6)
        result["fair_pe"] = round(pb / (r / 100), 6)
        if book_value_per_share is not None:
            result["fair_price"] = round(pb * book_value_per_share, 4)
    if book_value_per_share is not None and market_price is not None:
        current_pb = market_price / book_value_per_share
        dy = (r / 100) * (p / 100) / current_pb
        result["dividend_yield_pct"] = round(dy * 100, 4)
        # Excludes valuation changes, dilution, buybacks, taxes and transaction costs.
        result["implied_annual_return_pct"] = round((growth + dy) * 100, 4)
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
    rows = [calculate_scenario(s, book_value_per_share=book_value_per_share,
                               market_price=market_price) for s in scenarios]
    return {
        "status": "calculated" if any(x["status"] == "ok" for x in rows) else "inapplicable",
        "results": rows,
        "warning": "非DCF定价结论；需核验ROE、派息率、留存效率、资本结构及估值基准日",
    }


def render_valuation_block(result: dict) -> str:
    lines = ["Python 强制估值校验（不可由模型修改数值）:", result["warning"]]
    if result["status"] == "missing_assumptions":
        lines.append("本次没有可核验的三情景参数，禁止生成确定的合理价或安全边际。")
        return "\n".join(lines)
    for row in result["results"]:
        a = row["assumptions"]
        lines.append(
            f"{row['scenario']}: ROE={a['sustainable_roe_pct']}%，"
            f"派息率={a['payout_pct']}%，转化率={a['retention_conversion_pct']}%，"
            f"要求收益率={a['required_return_pct']}%；增长={row['earnings_growth_pct']}%；"
            f"状态={row['status']}；合理PB={row['fair_pb']}；"
            f"合理PE={row['fair_pe']}；合理价={row['fair_price']}；"
            f"现价稳态回报率={row['implied_annual_return_pct']}%"
            + (f"；失效原因={row['reason']}" if row["reason"] else "")
        )
    lines.append("不得将假设值写成财报事实。若本次日期缺乏可靠价格/净资产，合理价或现价收益率保持缺失。")
    return "\n".join(lines)
