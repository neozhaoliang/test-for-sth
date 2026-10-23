"""Independent HK ETF market sources, actual HK annual/interim profit data.

The tests use representative *provider-native* JSON and Eastmoney long-table
rows, not already-enriched fake holdings.
"""
from datetime import date, datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from analysis import etf_data, etf_hk_market


TODAY = date(2026, 10, 22)


def _timestamps(count=252):
    days = pd.bdate_range(end=TODAY, periods=count)
    return [int(datetime(d.year, d.month, d.day, 16,
                         tzinfo=ZoneInfo("Asia/Hong_Kong")).timestamp())
            for d in days]


def test_tencent_native_hk_kline_schema_with_qfqday_and_day():
    payload = {
        "data": {"hk01919": {
            "qfqday": [["2026-10-20", "12", "12.1", "12.5", "12", "400"],
                       ["2026-10-22", "12.1", "12.4", "12.6", "12.0", "500"]],
        }}
    }
    df = etf_hk_market._tencent_frame(payload, "01919")
    assert df["close"].tolist() == [12.1, 12.4]
    assert df.iloc[-1]["date"] == "2026-10-22"
    assert etf_hk_market._tencent_frame(payload, "01088").empty


def test_yahoo_native_hk_chart_adjusted_close_and_timestamp_conversion():
    stamps = _timestamps(2)
    payload = {"chart": {"result": [{
        "timestamp": stamps,
        "indicators": {
            "quote": [{"close": [10.0, 11.0]}],
            "adjclose": [{"adjclose": [9.5, 10.6]}]
        }
    }]}}
    frame = etf_hk_market._yahoo_frame(payload, "01919")
    assert frame["close"].tolist() == [9.5, 10.6]
    assert frame.iloc[-1]["date"] == TODAY.isoformat()


@pytest.mark.asyncio
async def test_tencent_hk_price_recovers_252_day_window(monkeypatch):
    dates = pd.bdate_range(end=TODAY, periods=252)
    candles = [[d.strftime("%Y-%m-%d"), "10", str(10+i/100), "11", "9", "1000"]
               for i,d in enumerate(dates)]
    calls = []
    async def provider(url, params, timeout=8):
        calls.append((url,params))
        if "ifzq" in url:
            return {"data": {"hk01919": {"qfqday": candles}}}
        return {"chart": {"result": []}}
    monkeypatch.setattr(etf_hk_market, "_request_json", provider)
    found = await etf_hk_market.get_hk_web_price("01919", TODAY)
    assert found["price_as_of"] == TODAY.isoformat()
    assert found["price_position_pct"] == 100.0
    assert found["price_currency"] == "HKD"
    assert found["price_source"].startswith("腾讯")
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_yahoo_hk_fallback_when_tencent_returns_empty(monkeypatch):
    candles = [8 + i/100 for i in range(252)]
    timestamps = _timestamps()
    async def fake(url, params, timeout=8):
        if "ifzq" in url:
            return {"data": {"hk01919": {}}}
        return {"chart": {"result": [{
            "timestamp": timestamps,
            "indicators": {"quote": [{"close": candles}],
                           "adjclose": [{"adjclose": candles}]},
        }]}}
    monkeypatch.setattr(etf_hk_market, "_request_json", fake)
    result = await etf_hk_market.get_hk_web_price("01919", TODAY)
    assert result["price_source"].startswith("Yahoo")
    assert result["price_close"] == round(candles[-1], 4)
    assert result["price_position_pct"] == 100.0


@pytest.mark.asyncio
async def test_stale_data_from_both_independent_feeds_yields_no_price(monkeypatch):
    async def fake(url, params, timeout=8):
        if "ifzq" in url:
            return {"data": {"hk01919": {"day": [
                ["2025-10-20", "5", "5.5", "6", "4", "100"]
            ]}}}
        return {"chart": {"result": []}}
    monkeypatch.setattr(etf_hk_market, "_request_json", fake)
    assert await etf_hk_market.get_hk_web_price("01919", TODAY) == {}


@pytest.mark.asyncio
async def test_hk_market_main_adapter_uses_web_when_akshare_both_unavailable(monkeypatch):
    monkeypatch.setattr(etf_data.ak, "stock_hk_hist", lambda **kw: pd.DataFrame())
    monkeypatch.setattr(etf_data.ak, "stock_hk_daily",
                        lambda **kw: pd.DataFrame())
    async def web(code, today):
        assert code == "01919"
        return {"price_close": 15.3, "price_position_pct": 71,
                "price_as_of": TODAY.isoformat(), "price_currency": "HKD",
                "price_source": "腾讯财经港股复权日K"}
    monkeypatch.setattr(etf_hk_market, "get_hk_web_price", web)
    result = await etf_data._hk_stock_price("01919", TODAY)
    assert result["price_position_pct"] == 71
    assert result["price_close"] == 15.3
    assert "price_lookup_status" not in result


def _long_hk_income():
    # Actual Eastmoney rows are one accounting metric per report period,
    # not an A-share wide DataFrame with an explicit YOY column.
    data = [
        ("2024-06-30", "004025002", 80),
        ("2025-06-30", "004025002", 100),
        ("2026-06-30", "004025002", 120),
        ("2025-12-31", "004025002", 240),
        ("2024-12-31", "004025002", 200),
        ("2024-06-30", "004012999", 900),  # other aggregate must be ignored
        ("2026-06-30", "004012999", 1500),
    ]
    return pd.DataFrame([{
        "REPORT_DATE": d+" 00:00:00",
        "STD_ITEM_CODE": code, "STD_ITEM_NAME":
            "股东应占溢利" if code == "004025002" else "除税后溢利",
        "AMOUNT": amt,
    } for d,code,amt in data])


def test_hk_income_statement_yoy_matches_prior_year_same_reporting_period():
    result = etf_data._hk_profit_from_statement(_long_hk_income(), TODAY)
    assert result["financial_period"] == "2026-06-30"
    assert result["net_profit_growth_pct"] == 20.0
    assert "财报" not in result.get("growth_change_definition","") or "非单季度" in result["growth_change_definition"]
    assert result["previous_financial_period"] == "2025-12-31"
    assert result["growth_change_pp"] == 0.0  # FY2025 20%; H1 2026 20%


@pytest.mark.asyncio
async def test_hk_financial_falls_back_to_income_statement_with_real_long_columns(monkeypatch):
    monkeypatch.setattr(etf_data.ak, "stock_financial_hk_analysis_indicator_em",
                        lambda **kw: pd.DataFrame(), raising=False)
    def report(**kwargs):
        assert kwargs == {"stock":"01919","symbol":"利润表","indicator":"报告期"}
        return _long_hk_income()
    monkeypatch.setattr(etf_data.ak, "stock_financial_hk_report_em",
                        report, raising=False)
    result = await etf_data._hk_stock_financial("01919", TODAY)
    assert result["net_profit_growth_pct"] == 20
    assert "东方财富港股利润表" in result["financial_source"]


def test_hk_report_bank_profit_code_not_same_as_industrial():
    df = pd.DataFrame([{
        "REPORT_DATE":d, "STD_ITEM_CODE":"001025002",
        "AMOUNT":x,
    } for d,x in [("2025-06-30", 100), ("2026-06-30", 85)]])
    result = etf_data._hk_profit_from_statement(df, TODAY)
    assert result["net_profit_growth_pct"] == -15
    assert result["financial_period"] == "2026-06-30"


@pytest.mark.asyncio
async def test_hk_current_snapshot_parses_roe_dividend_pe_pb_without_inventing_yoy(monkeypatch):
    frame = pd.DataFrame([{
        "股东权益回报率(%)": 12.6,
        "市盈率":8.9,
        "市净率":1.2,
        "股息率TTM(%)":6.8,
        "派息比率(%)":54,
    }])
    monkeypatch.setattr(etf_data.ak, "stock_hk_financial_indicator_em",
                        lambda **kw: frame, raising=False)
    result = await etf_data._hk_stock_snapshot("01919", TODAY)
    assert result["roe_pct"] == 12.6
    assert result["dividend_yield_ttm_pct"] == 6.8
    assert result["payout_ratio_pct"] == 54
    assert result["pe_ttm"] == 8.9
    assert "financial_period" not in result
    assert result["snapshot_verified_report_period"] is False
