"""HK ETF holdings: real dated fallback, cross-market earnings, no fake zero coverage."""
from datetime import date
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from analysis import etf_data
from analysis.etf_holding_risk import screen_holdings


TODAY = date(2026, 10, 22)


def _hk_daily():
    days = pd.bdate_range(end=TODAY, periods=252)
    return pd.DataFrame({
        "date": days.date,
        "close": [10 + i / 40 for i in range(252)],
    })


@pytest.mark.asyncio
async def test_hk_quote_uses_plain_http_before_eastmoney_and_never_mini_racer(monkeypatch):
    from analysis import etf_hk_market
    web_observation = etf_data._hk_daily_observation(
        _hk_daily(), TODAY, source="腾讯财经港股复权日K",
        date_column="date", close_column="close",
    )
    web = AsyncMock(return_value=web_observation)
    monkeypatch.setattr(etf_hk_market, "get_hk_web_price", web)
    def forbidden(**kwargs):
        raise AssertionError("V8/MiniRacer or needless Eastmoney call")
    monkeypatch.setattr(etf_data.ak, "stock_hk_daily", forbidden, raising=False)
    monkeypatch.setattr(etf_data.ak, "stock_hk_hist", forbidden, raising=False)
    result = await etf_data._hk_stock_price("01919", TODAY)
    assert result["price_source"].startswith("腾讯财经港股")
    assert result["price_currency"] == "HKD"
    assert result["price_as_of"] == TODAY.isoformat()
    assert result["price_position_pct"] == 100
    assert result["period_return_pct"] > 0
    assert result["history_trading_days"] == 252
    web.assert_awaited_once_with("01919", TODAY)


@pytest.mark.asyncio
async def test_hk_quote_short_history_keeps_close_without_false_52w(monkeypatch):
    from analysis import etf_hk_market
    short = _hk_daily().tail(85).copy()
    observation = etf_data._hk_daily_observation(
        short, TODAY, source="Yahoo Finance港股复权日K",
        date_column="date", close_column="close",
    )
    monkeypatch.setattr(
        etf_hk_market, "get_hk_web_price", AsyncMock(return_value=observation)
    )
    monkeypatch.setattr(
        etf_data.ak, "stock_hk_daily",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("forbidden V8")),
        raising=False,
    )
    result = await etf_data._hk_stock_price("01088", TODAY)
    assert result["price_close"] > 0
    assert result["price_as_of"] == TODAY.isoformat()
    assert "price_position_pct" not in result
    assert "price_window_status" in result


@pytest.mark.asyncio
async def test_stale_hk_market_data_is_rejected_and_not_inflated_to_live(monkeypatch):
    from analysis import etf_hk_market
    monkeypatch.setattr(
        etf_hk_market, "get_hk_web_price", AsyncMock(return_value={})
    )
    monkeypatch.setattr(etf_data.ak, "stock_hk_hist", lambda **kwargs: pd.DataFrame())
    monkeypatch.setattr(
        etf_data.ak, "stock_hk_daily",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("forbidden V8")),
        raising=False,
    )
    result = await etf_data._hk_stock_price("01919", TODAY)
    assert "price_close" not in result
    assert "price_position_pct" not in result
    assert result["price_lookup_status"]
    assert "stock_hk_daily" not in result["price_lookup_sources"]


def test_parse_hk_financial_report_period_and_yoy_without_a_share_substitution():
    df = pd.DataFrame({
        "周期": ["2026/03/31", "2026/06/30"],
        "归母净利润同比增长率(%)": ["23.6%", "11.2%"],
        "净资产收益率ROE(%)": ["8.0", "14.0"],
    })
    parsed = etf_data._hk_financial_from_frame(df, TODAY)
    assert parsed["financial_period"] == "2026-06-30"
    assert parsed["net_profit_growth_pct"] == 11.2
    assert parsed["roe_pct"] == 14
    assert parsed["growth_change_pp"] == -12.4
    assert parsed["previous_financial_period"] == "2026-03-31"


@pytest.mark.asyncio
async def test_hk_financial_provider_calls_report_period_api(monkeypatch):
    sample = pd.DataFrame({
        "周期": ["2025/06/30", "2026/06/30"],
        "归母净利润同比增长率": [20.0, 15.0],
    })
    def provider(**kw):
        assert kw == {"symbol":"01919", "indicator":"报告期"}
        return sample
    monkeypatch.setattr(etf_data.ak, "stock_financial_hk_analysis_indicator_em",
                        provider, raising=False)
    result = await etf_data._hk_stock_financial("01919", TODAY)
    assert result["net_profit_growth_pct"] == 15
    assert "东方财富港股" in result["financial_source"]


def test_hk_pe_pb_percentile_requires_dated_current_and_90_observations():
    days = pd.bdate_range(end=TODAY, periods=150)
    df = pd.DataFrame({
        "date":days,
        "value":[4 + i * .01 for i in range(150)],
    })
    parsed = etf_data._hk_valuation_history(df, today=TODAY, metric="市净率")
    assert parsed["latest"] > 5
    assert parsed["history_position"]["percentile"] == 100
    assert parsed["history_position"]["observations"] == 150
    short = etf_data._hk_valuation_history(df.tail(8), today=TODAY, metric="市净率")
    assert "history_position" not in short
    stale = df.copy()
    stale["date"] -= pd.Timedelta(days=200)
    assert etf_data._hk_valuation_history(stale, today=TODAY, metric="市净率") == {}


@pytest.mark.asyncio
async def test_hk_valuation_queries_pe_pb_independently(monkeypatch):
    df = pd.DataFrame({
        "date":pd.bdate_range(end=TODAY,periods=110),
        "value":[9 + i/100 for i in range(110)],
    })
    def baidu(**kw):
        assert kw["symbol"] == "01919"
        assert kw["period"] == "近一年"
        assert kw["indicator"] in ("市盈率(TTM)", "市净率")
        return df
    monkeypatch.setattr(etf_data.ak, "stock_hk_valuation_baidu",
                        baidu, raising=False)
    value = await etf_data._hk_stock_valuation("01919", TODAY)
    assert value["pe_ttm"] is not None
    assert value["pb"] is not None
    assert value["pe_history_position"]["percentile"] == 100
    assert value["valuation_observed_on"] == TODAY.isoformat()


def test_hk_missing_is_unknown_not_a_verified_zero_risk():
    portfolio = {"holdings": [
        {"code":str(1900+i).zfill(5),"name":"港股","weight_pct":5}
        for i in range(10)
    ]}
    risk = screen_holdings({"name":"港股通高股息ETF"}, portfolio)
    assert risk["disclosed_holding_count"] == 10
    assert risk["price_observed_count"] == 0
    assert risk["valuation_observed_count"] == 0
    assert risk["price_observed_weight_pct"] == 0  # observed share, not high-position share
    assert risk["high_price_weight_pct"] is None
    assert risk["high_valuation_weight_pct"] is None
    assert risk["high_price_and_valuation_weight_pct"] is None
    assert risk["watchlist"] == []


@pytest.mark.asyncio
async def test_513530_ten_hk_holdings_produce_real_price_coverage_when_feeds_work(monkeypatch):
    codes = ["01919","03808","01308","01088","00005",
             "00883","00857","00939","01898","01988"]
    monkeypatch.setattr(etf_data, "_holdings", AsyncMock(return_value={
        "as_of":"2026-Q2",
        "holdings":[{"name": "样本"+code,"code":code,"weight_pct":4.2}
                    for code in codes],
    }))
    monkeypatch.setattr(etf_data, "_etf_price", AsyncMock(return_value={}))
    monkeypatch.setattr(etf_data, "_fund_with_live_quote",
                        AsyncMock(return_value={"latest_price":1.489,
                           "quote_mode":"undated_market_snapshot"}))
    monkeypatch.setattr(etf_data, "get_etf_market_context",
                        AsyncMock(return_value={
                            "sector":"跨行业高股息","observations":{},
                            "key_drivers":[],
                        }))
    from analysis import etf_hk_market
    observed = etf_data._hk_daily_observation(
        _hk_daily(), TODAY, source="腾讯财经港股复权日K",
        date_column="date", close_column="close",
    )
    monkeypatch.setattr(
        etf_hk_market, "get_hk_web_price", AsyncMock(return_value=observed)
    )
    monkeypatch.setattr(
        etf_data.ak, "stock_hk_daily",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("V8 forbidden")),
        raising=False,
    )
    monkeypatch.setattr(
        etf_data.ak, "stock_hk_hist",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("HTTP suffices")),
        raising=False,
    )
    monkeypatch.setattr(etf_data, "_hk_stock_snapshot", AsyncMock(return_value={}))
    monkeypatch.setattr(etf_data, "_hk_stock_financial",
                        AsyncMock(return_value={
                            "financial_period":"2026-06-30",
                            "net_profit_growth_pct":12,
                        }))
    monkeypatch.setattr(etf_data, "_hk_stock_valuation",
                        AsyncMock(return_value={
                            "pe_ttm":9.2,"pb":1.1,
                            "pe_history_position":{"percentile":22}
                        }))
    result = await etf_data.collect_etf_data({
        "code":"513530","stock_code":"SH513530",
        "name":"华泰柏瑞中证港股通高股息投资ETF(QDII)"
    })
    assert len(result["portfolio"]["holdings"]) == 10
    assert result["holding_risk"]["price_observed_count"] == 10
    assert result["holding_risk"]["valuation_observed_count"] == 10
    assert result["portfolio_stats"]["hk_price_observed_weight_pct"] == 42.0
    assert result["portfolio_stats"]["profit_observed_weight_pct"] == 42.0
    assert result["portfolio"]["holdings"][0]["price_currency"] == "HKD"
    assert result["portfolio"]["holdings"][-1]["financial_period"] == "2026-06-30"
