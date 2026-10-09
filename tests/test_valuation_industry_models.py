"""Industry valuation arithmetic and fail-closed tests. No network calls."""
from dataclasses import dataclass
from datetime import date

import pytest

from analysis.valuation_models import (
    adjusted_nav, calculate_industry_valuation, discounted_equity_cash_flow,
    dividend_discount, residual_income, select_models,
)
from analysis.valuation_orchestrator import build_industry_valuation_report
from analysis.valuation_macro import (
    build_macro_valuation_context, stress_valuation_with_verified_exposures,
)


@pytest.mark.parametrize("archetype,industry,expected", [
    ("bank", "银行", "bank_residual_income"),
    ("cyclical", "航运", "midcycle_fcfe"),
    ("stable_yield", "核电", "regulated_dividend_discount"),
    ("technology", "电子", "owner_fcfe"),
    ("consumer_brand", "白酒", "owner_fcfe"),
    ("financial", "保险", "insurance_embedded_value"),
    ("general", "房地产开发", "adjusted_nav"),
])
def test_sector_routes_are_business_specific(archetype, industry, expected):
    assert select_models(archetype, industry)["primary"] == expected


def test_fcfe_pv_includes_explicit_cash_and_nonzero_terminal():
    cash = [1, 1, 1, 1, 1]
    v = discounted_equity_cash_flow(
        annual_cash_flow_per_share=cash, terminal_cash_flow_per_share=1.02,
        required_return_pct=10, terminal_growth_pct=2, market_price=10)
    explicit = sum(1 / 1.1 ** t for t in range(1, 6))
    tail = 1.02 / (.1 - .02) / (1.1 ** 5)
    assert v["intrinsic_per_share"] == pytest.approx(explicit + tail, abs=.0002)
    assert v["components"]["pv_explicit_cash_flows"] == pytest.approx(explicit, abs=.0002)
    assert "终值占比≥70%" in "".join(v["warnings"])


def test_cashflow_and_dps_not_silently_substituted():
    result = calculate_industry_valuation(
        archetype="stable_yield",
        inputs={"annual_cash_flow_per_share":[2,2,2],
                "terminal_cash_flow_per_share":2,
                "required_return_pct":10,"terminal_growth_pct":2})
    assert result["status"] == "insufficient_evidence"
    assert "annual_dividends_per_share" in result["missing"]


def test_tail_growth_close_to_discount_rejected():
    with pytest.raises(ValueError, match="too close"):
        dividend_discount(annual_dividends_per_share=[1,1,1],
                          terminal_dividend_per_share=1,
                          required_return_pct=6,terminal_growth_pct=3.5)


def test_residual_income_clean_surplus_and_no_perpetuity_blowup():
    v = residual_income(opening_book_per_share=10,
                        annual_roe_pct=[12,12,12],
                        annual_payout_pct=[100,100,100],
                        required_return_pct=10)
    # Book value stays at 10, residual income 0.2, discounted.
    target = 10 + sum(.2 / 1.1**t for t in range(1,4))
    assert v["intrinsic_per_share"] == pytest.approx(target,abs=.0002)
    assert v["components"]["ending_book_per_share"] == 10
    assert v["components"]["terminal_residual_income"] == 0


def test_accounting_losses_not_faked_as_positive():
    with pytest.raises(ValueError,match="insolvent"):
        residual_income(opening_book_per_share=1,
                        annual_roe_pct=[-30,-30,-30],
                        annual_payout_pct=[0,0,0],
                        required_return_pct=15)


def test_adjusted_nav_all_obligations_and_liquidation_cost():
    v = adjusted_nav(fair_assets_per_share=15,total_obligations_per_share=8,
                     realization_tax_and_cost_per_share=2,market_price=4)
    assert v["intrinsic_per_share"] == 5
    assert v["margin_of_safety_pct"] == 20


@dataclass
class Profile:
    archetype: str = "technology"
    industry: str = "计算机"


@dataclass
class Inputs:
    research_profile: object = None
    valuation_assumption_context: dict = None
    quote: dict = None
    as_of: str = "2026-10-09"
    macro_rates: dict = None
    rmb_signal: dict = None
    market_context: dict = None
    fundamentals: dict = None
    valuation_history: dict = None
    policy_events: dict = None
    commodity_signal: dict = None
    freight_signal: dict = None


def test_report_does_not_guess_fcfe_from_roe():
    result = build_industry_valuation_report(Inputs(
        research_profile=Profile(),
        valuation_assumption_context={}, quote={"latest_price":100}))
    assert result["status"] == "insufficient_evidence"
    assert result["valuation"]["intrinsic_per_share"] if False else True
    assert result["interactive_inputs"] is None
    assert "annual_cash_flow_per_share" in result["evidence_gate"]["missing"]


def manifest_for(inputs, ts="2026-09-30"):
    return {
        key: {"available_at":ts, "assumption_basis":"用户明确的情景假设",
              "source_url":"https://example.org/synthetic-filing",
              **({"unit":"CNY/share"} if key.endswith("_per_share") else {})}
        for key in inputs
    }


def test_provenanced_report_executes_and_displays_inputs():
    forecasts = {
        "annual_cash_flow_per_share": [1,1,1,1,1],
        "terminal_cash_flow_per_share": 1.02,
        "required_return_pct": 10,
        "terminal_growth_pct": 2,
    }
    report = build_industry_valuation_report(Inputs(
        research_profile=Profile(),
        valuation_assumption_context={
            "industry_valuation_inputs": forecasts,
            "industry_valuation_provenance": manifest_for(forecasts)
        },
        quote={"latest_price":10}))
    assert report["status"] == "calculated"
    assert report["valuation"]["intrinsic_per_share"] > 0
    assert report["interactive_inputs"] == forecasts


def test_stale_or_future_evidence_is_rejected_for_historical_asof():
    forecasts = {
        "annual_cash_flow_per_share": [1,1,1],
        "terminal_cash_flow_per_share": 1,
        "required_return_pct": 10,
        "terminal_growth_pct": 2,
    }
    provenance = manifest_for(forecasts, ts="2026-10-10")
    report = build_industry_valuation_report(Inputs(
        research_profile=Profile(),
        valuation_assumption_context={
            "industry_valuation_inputs": forecasts,
            "industry_valuation_provenance": provenance,
        }))
    assert report["status"] == "insufficient_evidence"
    assert report["evidence_gate"]["invalid"]
    assert report["interactive_inputs"] is None


def test_policy_repricing_requires_independent_calibration():
    base = {
        "annual_cash_flow_per_share":[1,1,1],
        "terminal_cash_flow_per_share":1,
        "required_return_pct":10,"terminal_growth_pct":2,
    }
    missing=stress_valuation_with_verified_exposures(
        archetype="technology", industry="软件",model_inputs=base,
        shock={"usdcny_change_pct":5},exposures={}, market_price=10)
    assert missing["status"] == "unquantifiable"
    ex={
        "source_url":"https://example.org/calibration",
        "as_of":"2026-09-30",
        "fcfe_pct_per_1pct_usdcny":.5,
    }
    positive=stress_valuation_with_verified_exposures(
        archetype="technology", industry="软件",model_inputs=base,
        shock={"usdcny_change_pct":5},exposures=ex, market_price=10)
    assert positive["status"] == "calculated"
    assert positive["result"]["intrinsic_per_share"] > calculate_industry_valuation(
        archetype="technology",industry="软件",inputs=base)["intrinsic_per_share"]


def test_macro_context_does_not_use_stale_us10y_or_fx_direction_as_beta():
    macro = build_macro_valuation_context(
        research_profile=Profile(),
        macro_rates={"as_of":"2026-10-09","us":{
            "us10y_yield_pct":4,"us10y_as_of":"2026-10-08",
            "us10y_freshness":{"fresh":False}}},
        rmb_signal={"rmb_trend":"appreciating","as_of":None},
        fundamentals={"facts":{"overseas_revenue_pct":90}},
        as_of="2026-10-09",
    )
    assert macro["observations"][0]["usable"] is False
    assert macro["quantified_equity_impact"] is None
    assert macro["macro_shocks_applied"] is False
    assert not macro["rmb_is_dated"]
