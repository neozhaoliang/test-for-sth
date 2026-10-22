"""ETF displayed prices and latest disclosed ten holdings; no issuer-only header."""
from datetime import date
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from analysis import etf_data


def _fund():
    return etf_data._catalog_entry("517520", "黄金股ETF永赢", source="fund_name_em")


def test_recent_close_does_not_require_an_entire_trading_year():
    today = date(2026, 10, 22)
    tape = pd.DataFrame({
        "日期": ["2026-10-19", "2026-10-20", "2026-10-21"],
        "收盘": [1.82, 1.75, 1.716],
    })
    assert etf_data._position_from_prices(
        tape, date_column="日期", close_column="收盘"
    ) == {}
    close = etf_data._latest_daily_close(
        tape, date_column="日期", close_column="收盘", today=today,
    )
    assert close["recent_close"] == 1.716
    assert close["recent_close_as_of"] == "2026-10-21"
    result = etf_data._with_recent_close(_fund(), close, today)
    assert result["latest_price"] == 1.716
    assert result["quote_mode"] == "last_verified_daily_close"
    assert result["quote_label"] == "最近交易日收盘价"
    assert "非实时" in result["quote_time"]
    assert result["iopv"] is None


def test_dated_live_quote_takes_priority_over_historical_close():
    today = date(2026, 10, 22)
    row = pd.Series({
        "代码": "517520", "名称": "黄金股ETF永赢",
        "最新价": 1.743, "IOPV实时估值": 1.74, "数据日期": "2026-10-22",
    })
    quote = etf_data._market_quote(
        _fund(), row, source="东方财富ETF实时行情", today=today)
    assert quote["quote_mode"] == "dated_market_snapshot"
    assert quote["latest_price"] == 1.743
    close = etf_data._with_recent_close(
        quote, {"recent_close": 1.716,
                "recent_close_as_of": "2026-10-21"}, today)
    assert close["latest_price"] == 1.743
    assert close["iopv_premium_pct"] is not None


def test_old_or_unknown_quote_never_claims_verified_realtime():
    today = date(2026, 10, 22)
    old = pd.Series({
        "代码": "517520", "名称": "黄金股ETF永赢",
        "最新价": 2.05, "数据日期": "2026-09-01",
    })
    assert etf_data._market_quote(_fund(), old,
        source="东方财富ETF实时行情", today=today) is None
    sina = pd.Series({"代码": "sh517520", "名称": "黄金股ETF永赢",
                      "最新价": 1.77})
    quote = etf_data._market_quote(
        _fund(), sina, source="新浪ETF场内行情", today=today)
    assert quote["quote_label"] == "ETF行情参考价"
    assert quote["quote_mode"] == "undated_market_snapshot"
    close = etf_data._with_recent_close(
        quote, {"recent_close": 1.716,
                "recent_close_as_of": "2026-10-21"}, today)
    assert close["latest_price"] == 1.77
    assert close["quote_mode"] == "undated_market_snapshot"
    assert "来源未提供报价日期" in close["quote_time"]
    assert etf_data._with_recent_close(
        _fund(), {"recent_close": 1.77,
                  "recent_close_as_of": "2026-08-01"}, today
    )["latest_price"] is None


@pytest.mark.asyncio
async def test_quote_falls_back_to_sina_after_eastmoney_outage(monkeypatch):
    def fail():
        raise RuntimeError("eastmoney 502")
    def sina(**kwargs):
        assert kwargs["symbol"] == "ETF基金"
        return pd.DataFrame([{
            "代码": "sh517520", "名称": "黄金股ETF永赢",
            "最新价": 1.716, "成交量": 111,
        }])
    monkeypatch.setattr(etf_data.ak, "fund_etf_spot_em", fail)
    monkeypatch.setattr(etf_data.ak, "fund_etf_category_sina", sina)
    quote = await etf_data._fund_with_live_quote(_fund())
    assert quote["quote_source"] == "新浪ETF场内行情"
    assert quote["quote_mode"] == "undated_market_snapshot"
    assert quote["latest_price"] == 1.716


def _disclosed_holdings():
    # Historical public Q2 2026 disclosure used ONLY as a test fixture.
    names = [
        ("601899", "紫金矿业", 10.84),
        ("600489", "中金黄金", 8.89),
        ("600988", "赤峰黄金", 7.42),
        ("600547", "山东黄金", 7.07),
        ("000975", "山金国际", 4.80),
        ("002155", "湖南黄金", 4.42),
        ("01818", "招金矿业", 4.17),
        ("02899", "紫金矿业", 3.73),
        ("000630", "铜陵有色", 3.50),
        ("02099", "中国黄金国际", 2.93),
    ]
    return pd.DataFrame([
        {"季度": "2026年2季度股票投资明细", "股票代码": code,
         "股票名称": name, "占净值比例": weight}
        for code, name, weight in names
    ])


def test_q2_top_ten_full_cross_market_and_weights():
    rows = etf_data._parse_holdings(_disclosed_holdings(), date(2026, 10, 22))
    assert rows["as_of"] == "2026-Q2"
    assert len(rows["holdings"]) == 10
    assert [x["code"] for x in rows["holdings"]][:2] == ["601899", "600489"]
    assert rows["holdings"][6]["code"] == "01818"
    assert rows["holdings"][7]["code"] == "02899"
    assert round(sum(r["weight_pct"] for r in rows["holdings"]), 2) == 57.77


@pytest.mark.asyncio
async def test_fund_fetches_latest_published_quarter_without_pinning_517520(monkeypatch):
    monkeypatch.setattr(etf_data, "_holdings_cache", {})
    recent = _disclosed_holdings()
    older = recent.copy()
    older["季度"] = "2026年1季度股票投资明细"
    older["占净值比例"] = 1.0
    def provider(*, symbol, date):
        assert symbol == "517520"
        return recent if date == "" else older
    monkeypatch.setattr(etf_data.ak, "fund_portfolio_hold_em", provider)
    result = await etf_data._holdings("517520", date(2026, 10, 22))
    assert result["as_of"] == "2026-Q2"
    assert result["disclosure_as_of"] == "2026-06-30"
    assert result["holding_count"] == 10
    assert result["retrieved_on"] == "2026-10-22"
    assert round(sum(x["weight_pct"] for x in result["holdings"]), 2) == 57.77


@pytest.mark.asyncio
async def test_no_live_holdings_does_not_fabricate_static_samples(monkeypatch):
    monkeypatch.setattr(etf_data, "_holdings_cache", {})
    monkeypatch.setattr(etf_data.ak, "fund_portfolio_hold_em",
                        lambda **kwargs: pd.DataFrame())
    result = await etf_data._holdings("517520", date(2026, 10, 22))
    assert result["holdings"] == []
    assert result["holding_count"] == 0
    assert "error" in result
