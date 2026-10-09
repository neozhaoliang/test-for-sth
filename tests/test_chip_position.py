"""Price location must condition interpretation of retail-holder dispersion."""
import pytest

from analysis.chip_position import assess_chip_price_context, render_chip_price_context


def market(price, latest_date="2026-10-08"):
    return {"stock": {"latest": price, "latest_date": latest_date,
                      "w52_low": 10, "w52_high": 20}}


def holders(change, period="2026-09-30"):
    return {"latest_count": 120000, "change_pct": change,
            "trend": "increasing" if change > 0 else "decreasing",
            "as_of": period}


def evaluate(price, change, period="2026-09-30"):
    return assess_chip_price_context(
        market(price), holders(change, period), as_of="2026-10-08"
    )


def test_bottom_more_holders_is_not_automatically_bearish():
    r = evaluate(11, 20)
    assert r["status"] == "contextualized"
    assert r["position_52w_pct"] == 10
    assert r["holder_context"] == "low_price_more_holders_neutral"
    assert "不应" in r["assessment"]
    assert "不单独扣分" in render_chip_price_context(r)


def test_top_more_holders_flags_hypothesis_not_a_proven_selloff():
    r = evaluate(19, 20)
    assert r["position_band"] == "high"
    assert r["holder_context"] == "high_price_more_holders_watch"
    assert "需" in r["assessment"]
    assert "机构派发" in r["assessment"]


def test_mid_more_holders_is_ambiguous():
    r = evaluate(15, 20)
    assert r["holder_context"] == "middle_price_more_holders_ambiguous"


@pytest.mark.parametrize("price,change", [(19,-12),(11,-12),(19,0),(11,0)])
def test_retail_count_alone_is_not_assigned_unconditional_risk(price, change):
    r = evaluate(price, change)
    assert r["status"] == "contextualized"
    assert r["holder_context"] not in ("high_price_more_holders_watch",
                                       "low_price_more_holders_neutral")


def test_stale_holder_information_does_not_get_combined():
    r = evaluate(19, 20, period="2025-10-01")
    assert r["status"] == "price_only"
    assert r["holder_change_pct"] is None
    assert r["holder_context"] == "unknown"


def test_future_holder_date_or_price_does_not_leak():
    r = evaluate(19, 20, period="2026-10-10")
    assert r["status"] == "price_only"
    r = assess_chip_price_context(market(19, "2026-10-09"), holders(20),
                                  as_of="2026-10-08")
    assert r["status"] == "insufficient_data"


@pytest.mark.parametrize("bad_market", [
    {}, {"stock": {}},
    {"stock": {"latest": 10, "w52_low": 10, "w52_high": 10, "latest_date": "2026-10-08"}},
    {"stock": {"latest": 25, "w52_low": 10, "w52_high": 20, "latest_date": "2026-10-08"}},
])
def test_missing_or_invalid_price_range_does_not_assume_bottom(bad_market):
    r = assess_chip_price_context(bad_market, holders(20))
    assert r["status"] == "insufficient_data"
    assert r["holder_context"] == "unknown"
