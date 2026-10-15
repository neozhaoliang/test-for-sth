from types import SimpleNamespace

from analysis.resource_macro_transmission import (
    build_resource_macro_transmission, resource_macro_prompt_block,
)
from analysis.valuation_macro import build_macro_valuation_context
from analysis.issuer_business import _topic_snippets


def macro_data(as_of="2026-10-15"):
    return {
        "as_of":as_of,
        "us":{
            "fed_target_lower_pct":3.75, "fed_target_upper_pct":4.0,
            "fed_target_lower_as_of":as_of,"fed_target_upper_as_of":as_of,
            "fed_target_upper_change_180d_pp":.25,
            "fed_target_freshness":{"fresh":True},
            "us10y_yield_pct":4.6, "us10y_change_30d_pp":.18,
            "us10y_as_of":as_of,"us10y_freshness":{"fresh":True},
            "source_urls":{"target_upper":"https://fred.stlouisfed.org/series/DFEDTARU",
                           "target_lower":"https://fred.stlouisfed.org/series/DFEDTARL",
                           "us10y":"https://fred.stlouisfed.org/series/DGS10"},
        },
        "china":{"as_of":"2026-09-20", "lpr_1y_pct":3.0,"lpr_5y_pct":3.5,
                 "freshness":{"fresh":True},"source_url":"https://data.eastmoney.com"},
    }


def observed(**kw):
    p=macro_data(**kw)
    fx={"as_of":"2026-10-15","usdcny_midpoint_cny_per_usd":6.7253,
        "usdcny_change_30obs_pct":-.865,"rmb_trend":"appreciating",
        "source_name":"SAFE fixing"}
    m=build_macro_valuation_context(
        research_profile=SimpleNamespace(archetype="cyclical",industry="贵金属"),
        macro_rates=p, rmb_signal=fx,
        fundamentals={"facts":{"overseas_revenue_pct":None}},
        as_of=p["as_of"],
    )
    return m,p,fx


def test_fed_and_both_lprs_are_dated_first_class_observations():
    m,_,_=observed()
    obs={x["metric"]:x for x in m["observations"]}
    assert obs["fed_target_upper_pct"]["usable"]
    assert obs["fed_target_upper_pct"]["value"]==4.0
    assert obs["fed_target_change_180d_pp"]["value"]==.25
    assert obs["china_lpr_1y_pct"]["value"]==3.0
    assert obs["china_lpr_5y_pct"]["value"]==3.5
    assert obs["usdcny_change_30obs_pct"]["value"]==-.865
    assert m["overseas_revenue_pct"] is None


def test_zijin_receives_unit_price_translation_not_invented_profit_beta():
    m,rates,fx=observed()
    x=build_resource_macro_transmission(
        stock_name="紫金矿业",industry="贵金属",archetype="cyclical",
        macro_context=m,macro_rates=rates,rmb_signal=fx,
        commodity_signal={"route":"gold_precious"},
    )
    assert x["products"]==["黄金","铜"]
    assert x["fx"]["unit_price_scenario_pct"]==-.865
    assert x["fed"]["direction"]=="近180天目标利率上限上调"
    assert "人民币相对美元升值" in x["currency_reading"]
    assert "美元金铜价格涨幅大于" in x["currency_reading"]
    assert "净负债" in x["financing_reading"]
    assert "海外收入占比不能当作净美元敞口" in x["financing_reading"]
    assert "本矿业公司货币与产业周期" in resource_macro_prompt_block(x)
    assert "利润弹性" in resource_macro_prompt_block(x)


def test_not_a_miner_returns_nothing():
    assert build_resource_macro_transmission(
        archetype="consumer_brand",industry="黄金珠宝",stock_name="某珠宝公司",
    ) is None


def test_missing_interest_rates_does_not_fabricate_fed_hike():
    x=build_resource_macro_transmission(
        stock_name="紫金矿业",industry="贵金属",archetype="cyclical",
        macro_context={"observations":[]},
    )
    assert x["fed"]["direction"]=="政策利率方向未核实"
    assert x["fed"]["upper_pct"] is None
    assert x["fx"]["unit_price_scenario_pct"] is None


def test_outdated_interest_is_not_a_current_claim():
    m,rates,fx=observed()
    for obs in m["observations"]:
        if obs["metric"].startswith("fed_"):
            obs["usable"]=False
    x=build_resource_macro_transmission(
        stock_name="紫金矿业",industry="贵金属",archetype="cyclical",
        macro_context=m,macro_rates=rates,rmb_signal=fx,
    )
    assert x["fed"]["upper_pct"] is None
    assert x["fed"]["direction"]=="政策利率方向未核实"


def test_primary_issuer_pdf_excerpts_prioritise_currency_and_interest():
    pages=[
        "经营业务及投资项目情况 " + ("产能进度"*100),
        "外币借款、汇率风险敏感性分析：人民币对美元升值10%时净损益减少。",
        "有息借款中约74%按固定利率计息，其余借款不一定全部为美元借款。",
    ]
    out=_topic_snippets(pages,max_count=6)
    assert out[0]["topic"]=="foreign_exchange_risk"
    assert out[1]["topic"]=="interest_rate_exposure"
    assert "固定利率" in out[1]["excerpt"]
    assert all(1 <= item["page"] <= 3 for item in out)
