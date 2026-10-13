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



def test_align_disclosed_holder_counts_to_previous_actual_trading_close():
    import pandas as pd
    from analysis.chip_position import align_holder_price_observations
    # 2026-06-30 trading day; 2026-09-30 trading day. A deliberately
    # different close on July 1 must never be backfilled into June.
    prices = pd.DataFrame({
        "date": ["2026-03-30","2026-06-30","2026-07-01","2026-09-30"],
        "close": [10.0, 11.0, 15.0, 13.5],
    })
    shareholders = {"series":[
        {"period":"2026-03-31","holders":10000,"available_at":"2026-04-29"},
        {"period":"2026-06-30","holders":12000,"available_at":"2026-07-20"},
        {"period":"2026-09-30","holders":16000,"available_at":"2026-10-12"},
        {"period":"2026-12-31","holders":20000,"available_at":"2027-01-23"},
    ]}
    series=align_holder_price_observations(
        shareholders, prices, as_of="2026-10-13")
    assert [(x["period"],x["price"],x["price_date"],x["holders"]) for x in series] == [
        ("2026-03-31",10.0,"2026-03-30",10000),
        ("2026-06-30",11.0,"2026-06-30",12000),
        ("2026-09-30",13.5,"2026-09-30",16000),
    ]
    report=assess_chip_price_context(market(13.5,"2026-10-13"),
                                     {"as_of":"2026-09-30","change_pct":33.33},
                                     price_holder_series=series)
    assert report["comparison_start"]=="2026-03-31"
    assert report["comparison_end"]=="2026-09-30"
    assert report["comparison_price_change_pct"]==35.0
    assert report["comparison_holder_change_pct"]==60.0
    assert "配对=3期" in render_chip_price_context(report)


def test_historical_holder_price_join_excludes_unpublished_and_unknown_disclosures():
    import pandas as pd
    from analysis.chip_position import align_holder_price_observations
    df=pd.DataFrame({"date":["2024-03-29","2024-06-28","2024-09-30"],
                     "close":[9.0,10.0,12.0]})
    holder={"series":[
       {"period":"2024-03-31","holders":200,"available_at":"2024-04-20"},
       {"period":"2024-06-30","holders":250,"available_at":"2024-07-25"},
       {"period":"2024-09-30","holders":280,"available_at":None},
    ]}
    pairs=align_holder_price_observations(holder,df,as_of="2024-07-20")
    assert [p["period"] for p in pairs]==["2024-03-31"]
    assert align_holder_price_observations(holder,df,as_of="2024-07-26")[-1]["price"]==10.0


def test_price_gap_and_missing_close_never_generate_fake_points():
    import pandas as pd
    from analysis.chip_position import align_holder_price_observations
    df=pd.DataFrame({"date":["2026-03-12","2026-06-30"],
                     "close":[10.0,float("nan")]})
    holder={"series":[{"period":"2026-03-31","holders":400},
                      {"period":"2026-06-30","holders":500}]}
    assert align_holder_price_observations(holder,df)==[]
