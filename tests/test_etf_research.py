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
