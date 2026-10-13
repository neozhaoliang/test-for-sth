"""ROE growth/discount valuation: independent of noisy historical prices."""
from datetime import date

import pytest

from analysis.roe_discount_valuation import calculate_roe_prices, build_roe_discount_lab


def test_chen_sir_equation_matches_20roe_75payout_10hurdle_example():
    # ROE 20%, retention 25%, g=5%, required return 10%.
    # Fair steady PB = 20%*75%/(10%-5%) = 3.
    d = calculate_roe_prices(
        book_per_share=10, roe_pct=20, growth_pct=5, discount_pct=10)
    assert d["status"] == "calculated"
    assert d["steady_growth_price"] == 30
    assert d["implied_payout_pct"] == 75
    assert d["first_year_dividend_yuan"] == 1.5
    assert 10 < d["finite_fade_price"] < 30


def test_finite_residual_income_at_cost_of_equity_equals_book():
    d = calculate_roe_prices(
        book_per_share=25, roe_pct=10, growth_pct=2, discount_pct=10)
    assert d["status"] == "calculated"
    assert d["finite_fade_price"] == 25
    assert d["steady_growth_price"] == 25


def test_impossible_perpetual_growth_is_rejected_not_blown_up():
    for g in (10, 15):
        d = calculate_roe_prices(
            book_per_share=10, roe_pct=15, growth_pct=g, discount_pct=10)
        assert d["status"] == "invalid_growth"
    assert calculate_roe_prices(book_per_share=0, roe_pct=15,
                               growth_pct=2, discount_pct=10)["status"] == "invalid_input"
    assert calculate_roe_prices(book_per_share=10, roe_pct=8,
                               growth_pct=8, discount_pct=10)["status"] == "invalid_growth"


def test_valuation_does_not_accept_or_consult_market_price():
    inputs = dict(book_per_share=10, roe_pct=18, growth_pct=3, discount_pct=11)
    answer = calculate_roe_prices(**inputs)
    assert answer["status"] == "calculated"
    assert "market_price" not in inputs
    assert "market_price" not in answer
    assert "relative_to_market_pct" not in answer


def test_historical_roes_use_complete_periods_only_and_no_auto_hurdle():
    trend = {"periods": [
        {"period": "2022-12-31", "roe_pct": 6, "eps_yuan": 1},
        {"period": "2023-12-31", "roe_pct": 12, "eps_yuan": 1.1},
        {"period": "2024-12-31", "roe_pct": 18, "eps_yuan": 1.3},
        {"period": "2025-12-31", "roe_pct": 80, "eps_yuan": 1.6},
        {"period": "2026-06-30", "roe_pct": 150, "eps_yuan": 8},
    ]}
    lab = build_roe_discount_lab(
        valuation={"nav_per_share": 20, "pb": 1000},
        profitability_trend=trend, as_of="2026-10-13",
        archetype="cyclical")
    assert lab["roe_anchor"]["median_pct"] == 15.0
    assert lab["defaults"]["roe_pct"] == 15.0
    assert lab["defaults"]["growth_pct"] is None
    assert lab["defaults"]["discount_pct"] is None
    assert len(lab["roe_observations"]) == 4


def test_pit_does_not_annualize_roe_or_use_premature_year_end():
    trend = {"periods": [
        {"period": "2023-12-31", "roe_pct": 10},
        {"period": "2024-12-31", "roe_pct": 11},
        {"period": "2025-12-31", "roe_pct": 22},
        {"period": "2025-09-30", "roe_pct": 150},
    ]}
    lab = build_roe_discount_lab(
        valuation={"nav_per_share": 10},
        profitability_trend=trend, as_of="2026-03-15")
    assert [x["year"] for x in lab["roe_observations"]] == [2023, 2024]
    assert lab["defaults"]["roe_pct"] is None
    april = build_roe_discount_lab(
        valuation={"nav_per_share": 10},
        profitability_trend=trend, as_of="2026-04-15")
    assert [x["year"] for x in april["roe_observations"]] == [2023, 2024]
    may = build_roe_discount_lab(
        valuation={"nav_per_share": 10},
        profitability_trend=trend, as_of="2026-05-01")
    assert [x["year"] for x in may["roe_observations"]] == [2023, 2024, 2025]


def test_missing_book_does_not_substitute_quote():
    lab = build_roe_discount_lab(
        valuation={"nav_per_share": None, "pb": 8.8},
        profitability_trend={"periods": [
            {"period": "2023-12-31", "roe_pct": 9},
            {"period": "2024-12-31", "roe_pct": 10},
            {"period": "2025-12-31", "roe_pct": 11}]},
        as_of="2026-10-13")
    assert lab["status"] == "missing_book"
    assert lab["book_per_share"] is None
