"""Asymmetric investing observations do not dictate portfolio-manager stance.

Fixed thresholds may suggest questions but never overrule a reasoned view.
"""
from dataclasses import dataclass, field
from types import SimpleNamespace

from analysis.position_risk import (
    enforce_asymmetric_odds, position_risk_context, position_risk_prompt_block,
    validated_quote_or_close,
    _issuer_events,
)
from model.m_analysis import StructuredSummary
from datetime import date


@dataclass
class Profile:
    archetype: str = "technology"


@dataclass
class Inputs:
    stock_code: str = "SZ300308"
    as_of: str = "2026-10-12"
    quote: dict = field(default_factory=lambda: {"latest_price":775.29})
    market_context: dict = field(default_factory=lambda: {
        "stock":{
            "latest":775.29,"latest_date":"2026-10-09",
            "w52_low":600,"w52_high":1400,
            "d20_pct":-12,"d60_pct":-23,
        }
    })
    valuation_history: dict = field(default_factory=lambda: {
        "as_of":"2026-10-09",
        "pb_percentiles":[{"years":5,"observations":1100,"percentile":93}],
    })
    fundamentals: dict = field(default_factory=lambda: {
        "facts":{"cash_to_profit_ratio":.132,"overseas_revenue_pct":94.82}
    })
    margin_signal: dict = field(default_factory=lambda: {
        "latest_balance_yi":319.82,
        "change_pct":-6.2,
        "latest_date":"2026-10-09","days":20,
    })
    research_profile: Profile = field(default_factory=Profile)
    valuation_model_result: dict = field(default_factory=lambda: {
        "macro_context":{
            "a_share_style":{
                "status":"observed", "regime":"dividend_leading",
                "dividend_minus_growth_ytd_pp":8.54,
                "style_window":"20 trading days", "as_of":"2026-10-09",
            }
        }
    })


def test_innolight_2026_has_distinct_risks_but_no_automatic_bearish_verdict():
    data=position_risk_context(Inputs())
    fam=set(data["independent_downside_families"])
    assert {"foreign_policy","market_style","valuation","cash_conversion","price_trend"} <= fam
    assert data["regime"]=="factors_to_interpret"
    assert not data["risk_asymmetry_triggered"]
    assert len(data["issuer_policy_events"])==1
    assert "2026-06-10" in data["issuer_policy_events"][0]["published_at"]
    prompt=position_risk_prompt_block(data)
    assert "不等于" in prompt or "不能" in prompt
    assert "不是打分表或决策树" in prompt
    # With EXACTLY THE SAME material risk data, the stock manager can
    # disagree with a prescribed bearish stance and keep either conviction.
    for original in ("neutral","bullish","bearish"):
        draft=StructuredSummary(
            stance=original, company_quality_stance="bullish",
            current_odds_stance=original,
            confidence=.4,
            thesis_summary="这是模型的独立判断，需要结合当前买入价。",
        )
        updated=enforce_asymmetric_odds(draft,data)
        assert updated.stance==original
        assert updated.current_odds_stance==original
        assert updated.company_quality_stance=="bullish"
        assert updated.thesis_summary==draft.thesis_summary


def test_policy_event_available_only_after_publication():
    assert _issuer_events("SZ300308", date(2026,6,9))==[]
    late=_issuer_events("SZ300308",date(2026,10,12))
    assert late and late[0]["event_type"]=="us_dod_1260h_listing"
    assert "NOT an automatic BIS Entity List" in late[0]["legal_scope"]
    assert _issuer_events("SH600023",date(2026,10,12))==[]


def test_three_downside_families_without_verified_policy_stay_risk_watch():
    x=Inputs(stock_code="SZ000001")
    x.valuation_model_result={"macro_context":{}}
    ctx=position_risk_context(x)
    assert not ctx["risk_asymmetry_triggered"]
    assert ctx["regime"]=="factors_to_interpret"
    assert not ctx["issuer_policy_events"]


def test_bearish_not_triggered_from_unverified_giant_overseas_business_alone():
    inputs=Inputs()
    inputs.valuation_history={}  # no confirmed high valuation
    inputs.fundamentals={"facts":{"overseas_revenue_pct":94.82}} # missing cash evidence
    inputs.market_context={"stock":{"latest":775.29,"latest_date":"2026-10-09",
                                     "w52_high":800,"w52_low":400,
                                     "d20_pct":3,"d60_pct":4}}
    inputs.valuation_model_result={"macro_context":{"a_share_style":{"status":"insufficient_data"}}}
    ctx=position_risk_context(inputs)
    assert ctx["regime"]!="bearish_concurrence"
    assert "foreign_policy" in ctx["independent_downside_families"]


def test_no_lookahead_or_stale_valuation_and_style():
    x=Inputs(as_of="2026-06-09")
    ctx=position_risk_context(x)
    assert not ctx["risk_asymmetry_triggered"]
    assert not ctx["issuer_policy_events"]
    # A stale price reference must not be used as if it were current.
    assert not any(v["family"]=="valuation" for v in ctx["risk_signals"])


def test_zero_nan_missing_quote_no_unearned_neutral_or_price():
    market={"stock":{"latest":775.29,"latest_date":"2026-10-09"}}
    v=validated_quote_or_close(
        {"latest_price":0,"change_pct":0,"volume":0},market,
        as_of="2026-10-12")
    assert v["latest_price"]==775.29
    assert v["quote_mode"]=="verified_previous_close"
    assert v["volume"] is None and v["change_pct"] is None
    assert validated_quote_or_close({"latest_price":float("nan")}, {},as_of="2026-10-12") is None
    assert validated_quote_or_close({}, {"stock":{"latest":100,"latest_date":"2026-09-15"}},
                                     as_of="2026-10-12") is None


def test_low_position_only_no_automatic_bullish_recommendation():
    x=Inputs(stock_code="SZ000001")
    x.market_context={"stock":{
        "latest":40,"latest_date":"2026-10-09",
        "w52_high":100,"w52_low":38,"d20_pct":2,"d60_pct":-10}}
    x.quote={"latest_price":40}
    x.valuation_history={"as_of":"2026-10-09","pb_percentiles":[
        {"years":5,"observations":400,"percentile":15}]}
    x.fundamentals={"facts":{"cash_to_profit_ratio":1.2,
                            "overseas_revenue_pct":10}}
    x.valuation_model_result={"macro_context":{}}
    result=position_risk_context(x)
    assert result["regime"]=="low_price_and_valuation_observed"
    assert not result["risk_asymmetry_triggered"]


def test_distinct_margin_and_trend_not_double_counted():
    ctx=position_risk_context(Inputs())
    margin=next(s for s in ctx["risk_signals"] if s["family"]=="margin_context")
    assert margin["value"] < 0
    assert "margin_context" not in ctx["independent_downside_families"]
