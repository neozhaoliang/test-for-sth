import pytest

from analysis.valuation_engine import (
    ValuationScenario, calculate_scenario, calculate_scenarios,
    render_valuation_block,
)


def case(name="base", roe=15, payout=60, conversion=70, hurdle=10,
         normalized=None, terminal_growth=2):
    return ValuationScenario(name, roe, payout, conversion, hurdle,
                             normalized_roe_pct=normalized,
                             terminal_growth_cap_pct=terminal_growth)


def test_finite_two_stage_reference_matches_independent_oracle():
    result = calculate_scenario(case(), book_value_per_share=10, market_price=15)
    assert result["status"] == "ok"
    # 5-year ROE fades 15% -> 12%, not a perpetual 4.2% growth extrapolation.
    assert result["model"] == "five_year_fade_normalized_terminal_ddm"
    assert result["assumptions"]["normalized_roe_pct"] == 12
    assert result["terminal_growth_pct"] <= 2
    assert result["terminal_value_share_pct"] < 100
    assert 0 < result["fair_pb"] < 5
    assert result["fair_price"] == pytest.approx(result["fair_pb"] * 10, abs=0.001)
    assert result["implied_annual_return_pct"] is None
    assert result["dividend_yield_pct"] == pytest.approx(6)


def test_regression_near_singular_old_perpetuity_1366_yuan_is_rejected():
    """Screenshot: r=27.9%, p=28.7%, c=50%, hurdle=10%, nav=7.60.

    Prior implementation returned 179.74x PB and 1366 yuan despite no
    reason to believe 27.9% ROE and 9.95% growth were perpetual.
    """
    r = calculate_scenario(
        case(roe=27.9, payout=28.7, conversion=50, hurdle=10),
        book_value_per_share=7.60,
        market_price=30.81,
    )
    assert r["status"] == "ok"
    assert r["assumptions"]["normalized_roe_source"] == "model_cap_at_12_pct"
    assert r["fair_pb"] == pytest.approx(1.130111, abs=1e-5)
    assert r["fair_price"] == pytest.approx(8.5888, abs=1e-3)
    assert r["fair_price"] < 30
    assert r["terminal_growth_pct"] == pytest.approx(2)
    assert r["terminal_payout_pct"] == pytest.approx(66.6667, abs=0.001)
    assert r["terminal_value_share_pct"] > 70


def test_lowering_payout_does_not_create_infinite_value():
    lower = calculate_scenario(
        case(roe=27.9, payout=20, conversion=50, hurdle=10),
        book_value_per_share=7.60)
    standard = calculate_scenario(
        case(roe=27.9, payout=28.7, conversion=50, hurdle=10),
        book_value_per_share=7.60)
    assert lower["fair_price"] == pytest.approx(8.2935, abs=.001)
    assert lower["fair_price"] < standard["fair_price"]
    assert lower["fair_price"] < 30


def test_near_hurdle_temporary_growth_not_singular():
    # An initial retention implied growth ~k does not define terminal growth.
    r = calculate_scenario(case(roe=20, payout=0.1, conversion=100, hurdle=10))
    assert r["status"] == "ok"
    assert r["fair_pb"] < 5
    assert r["terminal_growth_pct"] <= 2


def test_zero_dividend_without_future_commitment_is_inapplicable():
    r = calculate_scenario(case(payout=0), book_value_per_share=10)
    assert r["status"] == "inapplicable"
    assert r["fair_pb"] is None
    assert r["fair_price"] is None


@pytest.mark.parametrize("invalid", [
    case(payout=-1), case(payout=101),
    case(roe=0), case(conversion=101), case(hurdle=0),
    case(roe=float("nan")), case(normalized=-1), case(terminal_growth=12),
])
def test_invalid_assumptions_are_rejected(invalid):
    with pytest.raises(ValueError):
        calculate_scenario(invalid)


def test_missing_inputs_never_invent_price():
    result = calculate_scenarios([])
    assert result["status"] == "missing_assumptions"
    assert "禁止生成确定的合理价" in render_valuation_block(result)
    r = calculate_scenarios([case()])["results"][0]
    assert r["fair_price"] is None
    assert r["implied_annual_return_pct"] is None


def test_duplicate_scenarios_are_rejected():
    with pytest.raises(ValueError):
        calculate_scenarios([case(), case()])


def test_price_book_inputs_must_be_positive():
    with pytest.raises(ValueError):
        calculate_scenario(case(), market_price=0)


def test_normalized_roe_and_terminal_growth_are_explicit():
    a = calculate_scenario(case(roe=30, normalized=9, terminal_growth=1))
    b = calculate_scenario(case(roe=30, normalized=18, terminal_growth=2))
    assert a["assumptions"]["normalized_roe_source"] == "provided"
    assert b["fair_pb"] > a["fair_pb"]
