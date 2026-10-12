"""Regression: long-run return attribution needs fiscal-year payout evidence.

Historical announcement-year dividend medians cannot substitute for a matched
cash payout / earnings ratio. The optional conditional price is a sensitivity,
never an issuer's verified intrinsic value.
"""
from analysis.investor_valuation import investor_reference_valuation


def case(*, cutoff="2026-10-12", required=11, dividend_years=(2022, 2023, 2024)):
    annual_eps = [
        {"fiscal_year": y, "eps_yuan": 2, "published_at": f"{y+1}-03-31",
         "source_url": f"https://filings.example/{y}"}
        for y in (2022, 2023, 2024)
    ]
    dividends = [
        {"fiscal_year": y, "dividend_per_10_shares": 10,
         "progress": "实施", "announce_date": f"{y+1}-06-15",
         "source_url": f"https://filings.example/dividend/{y}"}
        for y in dividend_years
    ]
    settings = {
        "annual_eps": annual_eps,
        "fiscal_dividends": dividends,
    }
    if required is not None:
        settings["required_return_pct"] = required
    return investor_reference_valuation(
        archetype="stable_yield",
        valuation={"nav_per_share": 5, "pb": 1.1},
        quote={"latest_price": 5.5},
        profitability_trend={"periods": [
            {"period": "2022-12-31", "roe_pct": 8},
            {"period": "2023-12-31", "roe_pct": 10},
            {"period": "2024-12-31", "roe_pct": 12},
        ]},
        valuation_history={"pb_percentiles": [
            {"years": 5, "median": 1.15, "observations": 800},
        ]},
        valuation_assumption_context=settings, as_of=cutoff,
    )


def test_matched_dividends_and_roe_produce_three_conditional_scenarios():
    ref = case()
    assert ref["dividend_scenarios"] == []
    result = ref["return_decomposition"]
    assert result["status"] == "illustrative_scenarios"
    assert len(result["scenarios"]) == 3
    assert result["payout_evidence"]["fiscal_years"] == [2022, 2023, 2024]
    names = [item["name"] for item in result["scenarios"]]
    assert names == ["悲观", "中性", "乐观"]
    base = result["scenarios"][1]
    assert base["sustainable_roe_pct"] == 10
    assert base["payout_pct"] == 50
    assert base["retention_conversion_pct"] == 50
    assert base["growth_contribution_pct"] == 2.5
    assert base["cash_dividend_yield_pct"] == 4.545
    assert base["required_return_pct"] == 11
    assert base["terminal_pb"] == 1.15
    assert base["conditional_reference_price"] > 0
    assert base["discounted_terminal_per_share"] > 0
    assert 0 < base["terminal_share_of_value_pct"] < 100


def test_no_configured_hurdle_never_generates_conditional_prices():
    result = case(required=None)["return_decomposition"]
    assert result["status"] == "insufficient_evidence"
    assert result["scenarios"] == []
    assert any("要求收益率" in x for x in result["missing_requirements"])


def test_missing_fiscal_dividend_is_not_replaced_with_announcement_year_median():
    result = case(dividend_years=(2022, 2023))["return_decomposition"]
    assert result["payout_evidence"]["status"] == "insufficient_matched_fiscal_years"
    assert result["scenarios"] == []


def test_strict_cutoff_disallows_future_fiscal_dividend_evidence():
    result = case(cutoff="2025-01-01")["return_decomposition"]
    assert result["payout_evidence"]["status"] == "insufficient_matched_fiscal_years"
    assert result["scenarios"] == []


def test_extreme_or_unproven_terminal_pb_is_not_accepted():
    # An unsourced terminal-PB override must not silently replace the
    # qualified historical price-to-book reference.
    ref = case()
    assert ref["return_decomposition"]["terminal_pb_basis"].startswith("同一截止日")
