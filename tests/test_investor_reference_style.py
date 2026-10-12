"""Regression: investor price anchors + dated red-fund crowding and historical isolation."""
from datetime import date
from types import SimpleNamespace

from analysis.investor_valuation import investor_reference_valuation
from analysis.market_research_context import (
    build_style_investment_context, select_recent_style_excerpts,
)
from analysis.valuation_macro import build_macro_valuation_context
from analysis.report_blocks import _build_dividend_chart


def paid(yr, value, status="实施完成", day="09-02"):
    return {"announce_date": f"{yr}-{day}", "dividend_per_10_shares": value,
            "progress": status}


def fixture():
    return [
        paid(2021,2.25), paid(2024,2.5), paid(2025,3.4),
        paid(2025,3.4),  # duplicate announcement should count once
        paid(2026,.2,"预案"), paid(2026,.5,"待实施"),
    ]


def reference(**kwargs):
    return investor_reference_valuation(
        archetype="stable_yield", dividend_history=fixture(),
        valuation={"nav_per_share":5.49,"pb":.97},
        profitability_trend={"periods":[
            {"period":"2022-12-31","roe_pct":-1},
            {"period":"2023-12-31","roe_pct":5},
            {"period":"2024-12-31","roe_pct":9},
            {"period":"2025-12-31","roe_pct":8},
            {"period":"2026-06-30","roe_pct":1.97},
        ]},
        valuation_history={"pb_percentiles":[
            {"years":5,"median":1.15,"observations":1245}
        ]},
        quote={"latest_price":5.32},as_of="2026-10-10",**kwargs)


def test_reference_values_reproducible_and_never_called_intrinsic_price():
    got=reference()
    assert got["status"] == "indicative_reference"
    assert got["dividend_basis"]["years"] == [2021,2024,2025]
    assert got["dividend_basis"]["observed_median_cash_dividend_per_share"] == .25
    assert got["dividend_basis"]["last_implemented_dividend_per_share"] == .34
    assert got["dividend_basis"]["latest_implemented_dividend_yield_pct"] == 6.39
    assert [v["reference_price"] for v in got["dividend_scenarios"]] == [2.5,4.55,6.39]
    assert got["historical_pb_anchor"]["median_pb_reference_price"] == 6.31
    assert got["normalized_earnings"]["median_roe_pct"] == 6.5


def test_missing_dividend_data_does_not_create_valuation():
    x=investor_reference_valuation(archetype="stable_yield",as_of="2026-10-10")
    assert x["dividend_scenarios"] == []
    assert x["status"] == "partial"


def test_dividend_chart_excludes_proposal_and_duplicated_notice():
    chart=_build_dividend_chart(fixture(), [], None, {"latest_price":5.32})
    assert [r["year"] for r in chart] == ["2021","2024","2025"]
    assert chart[-1]["dividend_per_10"] == 3.4
    assert chart[-1]["yield_pct"] == 6.39


def test_dated_fund_limit_event_and_expiry_and_no_future_peeking():
    knowledge=[
        SimpleNamespace(
            source="bili_laomujiang",author="买股票的老木匠",
            title="资金调仓",distilled="红利ETF被动资金和科技高低切值得观察",
            published_at="2026-10-07 10:00:00",source_url="https://example.com/post",
        ),
        SimpleNamespace(
            source="xueqiu_unknown",author="其他投资者",
            title="后来的帖子",distilled="红利基金限购",
            published_at="2026-10-12 10:00:00",source_url="",
        ),
    ]
    before=build_style_investment_context(
        as_of="2026-10-08",archetype="stable_yield",industry="电力",
        knowledge_excerpts=knowledge)
    assert before["dated_market_events"] == []
    assert before["investment_principles"]
    assert "kol_style_hypotheses" not in before
    assert "买股票的老木匠" not in str(before)
    live=build_style_investment_context(
        as_of="2026-10-10",archetype="stable_yield",industry="电力",
        knowledge_excerpts=knowledge)
    assert any("dividend_fund" in e["event_type"] for e in live["dated_market_events"])
    assert live["investment_principles"]
    assert "kol_style_hypotheses" not in live
    assert "买股票的老木匠" not in str(live)
    long_after=build_style_investment_context(
        as_of="2026-11-08",archetype="stable_yield",industry="电力")
    assert long_after["dated_market_events"] == []


def test_protect_real_style_posts_from_model_reranker():
    rows=[
        SimpleNamespace(
            source="bili_laomujiang",author="买股票的老木匠",
            title="红利高低切",distilled="科技转红利行情的边际资金动向",
            published_at="2026-10-09 12:00:00",
        ),
        SimpleNamespace(
            source="older",author="其他人",title="不相关",
            distilled="风景旅行",published_at="2026-10-09",
        ),
    ]
    assert select_recent_style_excerpts(rows,as_of="2026-10-10") == rows[:1]


def test_latest_20day_style_beats_old_ytd_labels():
    p=SimpleNamespace(archetype="stable_yield",industry="电力")
    market={"indices":[
      {"name":"上证红利","ytd_pct":-5,"d20_pct":5,"latest_date":"2026-10-09"},
      {"name":"科创50","ytd_pct":30,"d20_pct":-4,"latest_date":"2026-10-09"},
      {"name":"创业板指","ytd_pct":25,"d20_pct":-1,"latest_date":"2026-10-09"},
    ]}
    macro=build_macro_valuation_context(
        research_profile=p,market_context=market,as_of="2026-10-10")
    assert macro["a_share_style"]["style_window"]=="20 trading days"
    assert macro["a_share_style"]["regime"]=="dividend_leading"
    assert macro["a_share_style"]["dividend_minus_growth_ytd_pp"]==7.5


def test_knowledge_is_abstracted_without_other_stock_examples():
    posts=[
        SimpleNamespace(
            source="xueqiu_4780688814", author="军师祭咖啡",
            title="齐鲁银行(SH601665)交易思考",
            distilled="红利股阶段涨幅大时要谨慎，短线回撤可能侵蚀收益。"
                      "齐鲁银行(SH601665)的例子不能直接迁移。",
            published_at="2026-09-22", source_url="https://xueqiu.com/example",
        ),
        SimpleNamespace(
            source="xueqiu_4780688814", author="军师祭咖啡",
            title="银行和科技估值", distilled="腾讯的成长回报未必胜过低估值高股息银行，"
                    "买入价格及长期回报要同时衡量。",
            published_at="2026-09-14", source_url="https://xueqiu.com/example2",
        ),
    ]
    result=build_style_investment_context(
        as_of="2026-10-10",archetype="stable_yield",industry="电力",
        knowledge_excerpts=posts,
        market_context={"stock":{"latest_date":"2026-10-09","d20_pct":4.7}},
    )
    assert result["stock_20d_pct"] == 4.7
    assert result["investment_principles"]
    for prohibited in ("军师祭咖啡", "齐鲁银行", "腾讯", "601665",
                       "xueqiu.com", "author", "claim", "kol_style_hypotheses"):
        assert prohibited not in str(result)
    from analysis.market_research_context import style_investment_prompt_block
    prompt=style_investment_prompt_block(result)
    for prohibited in ("军师祭咖啡", "齐鲁银行", "腾讯", "601665"):
        assert prohibited not in prompt
