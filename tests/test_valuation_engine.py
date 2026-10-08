import pytest

from analysis.valuation_engine import (
    ValuationScenario,
    calculate_scenario,
    calculate_scenarios,
    render_valuation_block,
)


def case(name="base", roe=15, payout=60, conversion=70, hurdle=10):
    return ValuationScenario(name, roe, payout, conversion, hurdle)


def test_reference_calculation_is_deterministic():
    result = calculate_scenario(case(), book_value_per_share=10, market_price=15)
    assert result["status"] == "ok"
    # g=15%*(1-60%)*70%=4.2%; fair PB=9%/(10%-4.2%)=1.551724...
    assert result["fair_pb"] == pytest.approx(1.551724, abs=1e-6)
    assert result["fair_price"] == pytest.approx(15.5172, abs=1e-4)
    assert result["dividend_yield_pct"] == pytest.approx(6)
    assert result["implied_annual_return_pct"] == pytest.approx(10.2)


def test_zero_dividend_cannot_produce_fair_pb():
    r = calculate_scenario(case(payout=0), book_value_per_share=10)
    assert r["status"] == "inapplicable"
    assert r["fair_pb"] is None
    assert r["fair_price"] is None


def test_growth_at_or_above_required_return_is_inapplicable():
    r = calculate_scenario(case(roe=20, payout=30, conversion=100, hurdle=10))
    assert r["status"] == "inapplicable"
    assert r["fair_pb"] is None


@pytest.mark.parametrize("invalid", [
    case(payout=-1),
    case(payout=101),
    case(roe=0),
    case(conversion=101),
    case(hurdle=0),
    case(roe=float("nan")),
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
