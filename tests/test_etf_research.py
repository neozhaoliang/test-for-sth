"""Offline ETF tests: source-time, position-vs-valuation and missing evidence."""
from datetime import date, timedelta
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from analysis import etf_data, etf_report
from analysis.research_context import ResearchRequest


def test_spot_premium_uses_iopv_and_never_claims_nav():
    row = etf_data._spot_row({
        "代码": "510300", "名称": "沪深300ETF",
        "最新价": 4.08, "IOPV实时估值": 4,
        "成交额": 35000000, "数据日期": "2026-10-22",
    })
    assert row["stock_code"] == "SH510300"
    assert row["iopv_premium_pct"] == 2.0
    assert row["turnover_yuan"] == 35000000
    assert etf_data._spot_row({"代码": "510300", "最新价": "-", "IOPV实时估值": 0})["iopv_premium_pct"] is None


def test_quarter_parser_does_not_merge_older_portfolio_and_never_invents_weights():
    df = pd.DataFrame([
        {"季度": "2026年1季度", "股票代码": "600519",
         "股票名称": "甲公司", "占净值比例": 9},
        {"季度": "2026年2季度", "股票代码": "600519",
         "股票名称": "甲公司", "占净值比例": 3},
        {"季度": "2026年2季度", "股票代码": "000001",
         "股票名称": "乙公司", "占净值比例": 7},
        {"季度": "2026年3季度", "股票代码": "300750",
         "股票名称": "未来披露", "占净值比例": 31},
    ])
    parsed = etf_data._parse_holdings(df, date(2026, 6, 30))
    assert parsed["as_of"] == "2026-Q2"
    assert parsed["disclosed_count"] == 2
    assert [x["weight_pct"] for x in parsed["holdings"]] == [7, 3]
    assert parsed["holdings"][0]["code"] == "000001"
    assert etf_data._quarter("2025-12-31") == (2025, 4)


def test_price_position_only_with_sufficient_history_and_not_value():
    dates = pd.bdate_range("2025-10-01", periods=220)
    frame = pd.DataFrame({
        "日期": dates.astype(str),
        "收盘": [10 + i * .02 for i in range(220)],
    })
    observation = etf_data._position_from_prices(
        frame, date_column="日期", close_column="收盘"
    )
    assert observation["price_position_pct"] == 100
    assert "pe_ttm" not in observation
    assert etf_data._position_from_prices(
        frame.head(40), date_column="日期", close_column="收盘"
    ) == {}


def test_missing_prices_are_not_treated_as_low_position():
    holdings = [
        {"code": "600519", "name": "甲", "weight_pct": 35,
         "price_position_pct": 92, "net_profit_growth_pct": -7,
         "pe_ttm": 45, "pb": 9},
        {"code": "000001", "name": "乙", "weight_pct": 20,
         "price_position_pct": None},
        {"code": "300750", "name": "丙", "weight_pct": 15,
         "price_position_pct": 8, "net_profit_growth_pct": 12},
    ]
    stats = etf_data._portfolio_stats(holdings)
    assert stats["top5_weight_pct"] == 70
    assert stats["price_observed_weight_pct"] == 50
    assert stats["high_position_weight_pct"] == 35
    assert stats["low_position_weight_pct"] == 15
    assert stats["profit_observed_weight_pct"] == 50
    assert stats["negative_profit_growth_weight_pct"] == 35
    assert stats["pe_pb_observed_weight_pct"] == 35


@pytest.mark.asyncio
async def test_resolve_etf_only_when_catalog_verified(monkeypatch):
    monkeypatch.setattr(etf_data, "_catalog", AsyncMock(return_value=[
        {"code": "510300", "stock_code": "SH510300", "name": "沪深300ETF"},
        {"code": "159915", "stock_code": "SZ159915", "name": "创业板ETF"},
    ]))
    assert (await etf_data.resolve_etf("510300"))["stock_code"] == "SH510300"
    assert (await etf_data.resolve_etf("创业板ETF"))["code"] == "159915"
    assert await etf_data.resolve_etf("510301") is None


@pytest.mark.asyncio
async def test_etf_research_is_not_fed_into_company_report(monkeypatch):
    source = {
        "scope": "A股股票ETF", "fund": {"name": "测试ETF"},
        "portfolio": {"holdings": [], "as_of": None},
        "portfolio_stats": {}, "etf_price_position": {},
        "collected_on": "2026-10-22", "limitations": [],
    }
    monkeypatch.setattr(etf_report, "collect_etf_data", AsyncMock(return_value=source))
    monkeypatch.setattr(etf_report, "_write_etf_opinion", AsyncMock(return_value={
        "stance": "neutral", "thesis_summary": "持仓披露暂缺",
        "sections": {}, "risk_notes": "", "invalidation_condition": "",
    }))
    request = ResearchRequest(stock_code="SH510300")
    result = await etf_report.generate_etf_report({
        "code": "510300", "stock_code": "SH510300", "name": "测试ETF",
    }, request)
    assert result["asset_type"] == "etf"
    assert result["etf_data"]["portfolio"]["holdings"] == []
    assert "fundamentals" not in result
    assert "shareholder_trend" not in result


@pytest.mark.asyncio
async def test_missing_llm_does_not_fabricate_direction(monkeypatch):
    monkeypatch.setattr(etf_report, "call_json_ex",
                        AsyncMock(return_value=(None, "refusal")))
    summary = await etf_report._write_etf_opinion({"portfolio": {"holdings": []}})
    assert summary["stance"] == ""
    assert summary["generation_status"] == "unavailable"


@pytest.mark.asyncio
async def test_theme_search_lists_multiple_gold_equity_funds_not_one_pinned_code(monkeypatch):
    rows = [
        etf_data._catalog_entry("517520", "黄金股ETF永赢", source="fund_name_em"),
        etf_data._catalog_entry("159562", "华夏中证沪深港黄金产业股票ETF",
                                source="fund_name_em"),
        etf_data._catalog_entry("517400", "国泰中证沪深港黄金产业股票ETF",
                                source="fund_name_em"),
        etf_data._catalog_entry("518880", "黄金ETF华安", source="fund_name_em"),
    ]
    monkeypatch.setattr(etf_data, "_catalog", AsyncMock(return_value=rows))
    monkeypatch.setattr(etf_data, "_catalog_refresh_task", None)
    monkeypatch.setattr(etf_data, "_scale_refresh_task", None)
    monkeypatch.setattr(etf_data, "_scale_cache", (
        float("inf"), {
            "517520": {"scale_yi": 123.4},
            "159562": {"scale_yi": 64.2},
            "517400": {"scale_yi": 15.1},
            "518880": {"scale_yi": 300},
        }))
    for name in ("黄金股ETF", "黄金股etf"):
        # Ambiguous theme can no longer silently resolve to one company.
        assert await etf_data.resolve_etf(name) is None
        options = await etf_data.search_etfs(name, 40)
        assert [x["code"] for x in options] == [
            "SH517520", "SZ159562", "SH517400"]
        assert options[0]["scale_yi"] == 123.4
    fund = await etf_data.resolve_etf("517520")
    assert fund["stock_code"] == "SH517520"
    assert fund["latest_price"] is None


@pytest.mark.asyncio
async def test_catalog_sources_work_independently_of_spot_quote(monkeypatch):
    ths = pd.DataFrame([
        {"基金代码": "159915", "基金名称": "创业板ETF"},
        {"基金代码": "159562", "基金名称": "华夏中证沪深港黄金产业股票ETF"},
    ])
    fund_names = pd.DataFrame([
        {"基金代码": "517520", "基金简称": "黄金股ETF永赢"},
        {"基金代码": "159001", "基金简称": "某指数ETF联接A"},
        {"基金代码": "999999", "基金简称": "非ETF股票"},
    ])
    monkeypatch.setattr(etf_data.ak, "fund_etf_category_sina",
                        lambda **kwargs: None, raising=False)
    monkeypatch.setattr(etf_data.ak, "fund_etf_category_ths",
                        lambda **kwargs: ths, raising=False)
    monkeypatch.setattr(etf_data.ak, "fund_name_em",
                        lambda **kwargs: fund_names, raising=False)
    monkeypatch.setattr(etf_data.ak, "fund_etf_spot_em",
                        lambda: (_ for _ in ()).throw(RuntimeError("quote 502")))
    monkeypatch.setattr(etf_data.ak, "fund_etf_category_sina",
                        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("Sina off")))
    await etf_data._refresh_catalog()
    codes = {r["code"] for r in etf_data._catalog_cache[1]}
    assert {"517520", "159915", "159562"} <= codes
    assert "159001" not in codes
    assert all(r["latest_price"] is None for r in etf_data._catalog_cache[1])
    fund = await etf_data._fund_with_live_quote(
        etf_data._catalog_entry("517520", "黄金股ETF永赢", source="fund_name_em"))
    assert fund["latest_price"] is None
    assert fund["quote_source"].startswith("行情暂缺")


@pytest.mark.asyncio
async def test_gold_equity_etf_is_not_treated_as_physical_gold(monkeypatch):
    spot = etf_data._catalog_entry("517520", "黄金股ETF永赢", source="fund_name_em")
    disclosure = {"holdings": [
        {"code": "600519", "name": "A股样本", "weight_pct": 10},
        {"code": "00700", "name": "港股样本", "weight_pct": 8},
    ], "as_of": "2026-Q2"}
    monkeypatch.setattr(etf_data, "_holdings", AsyncMock(return_value=disclosure))
    monkeypatch.setattr(etf_data, "_etf_price", AsyncMock(return_value={}))
    monkeypatch.setattr(etf_data, "get_etf_market_context", AsyncMock(return_value={
        "sector": "黄金产业股票", "key_drivers": ["金价"],
        "observations": {}, "as_of": "2026-10-22",
    }))
    monkeypatch.setattr(etf_data, "_hk_stock_price", AsyncMock(return_value={
        "price_position_pct": 55, "price_currency": "HKD",
    }))
    monkeypatch.setattr(etf_data, "_fund_with_live_quote",
                        AsyncMock(return_value=spot))
    monkeypatch.setattr(etf_data, "_stock_price",
                        AsyncMock(return_value={"price_position_pct": 85}))
    monkeypatch.setattr(etf_data, "_stock_profit",
                        AsyncMock(return_value={"net_profit_growth_pct": 9}))
    monkeypatch.setattr(etf_data, "_stock_valuation",
                        AsyncMock(return_value={"pe_ttm": 22}))
    result = await etf_data.collect_etf_data(spot)
    assert "黄金产业股票ETF" in result["scope"]
    assert result["portfolio"]["holdings"][0]["price_position_pct"] == 85
    assert result["portfolio"]["holdings"][1]["price_currency"] == "HKD"
    assert result["portfolio_stats"]["price_observed_weight_pct"] == 18


def test_five_digit_hong_kong_holding_remains_in_disclosure_not_a_share_pipeline():
    frame = pd.DataFrame([
        {"季度": "2026年2季度", "股票代码": "00700",
         "股票名称": "港股样本", "占净值比例": 13},
        {"季度": "2026年2季度", "股票代码": "600519",
         "股票名称": "A股样本", "占净值比例": 12},
    ])
    parsed = etf_data._parse_holdings(frame, date(2026, 7, 3))
    assert parsed["disclosed_count"] == 2
    assert parsed["holdings"][0]["code"] == "00700"


@pytest.mark.asyncio
async def test_web_etf_suggestion_does_not_block_on_stock_market_loader(monkeypatch):
    from web import app as web_app
    stock_search = AsyncMock(side_effect=AssertionError("ETF search must skip stock quote"))
    monkeypatch.setattr(web_app, "search_stocks", stock_search)
    monkeypatch.setattr(web_app, "search_etfs",
                        AsyncMock(return_value=[
                            {"code": "SH517520", "name": "黄金股ETF永赢",
                             "market": "SH", "asset_type": "etf"}
                        ]))
    for name in ("517520", "黄金股etf"):
        response = await web_app.suggest_stocks(q=name, limit=8)
        assert response["items"][0]["code"] == "SH517520"
    stock_search.assert_not_called()
