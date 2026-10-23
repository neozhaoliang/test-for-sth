"""Stock and ETF financing crowding uses dated, comparable SECURITY-LEVEL evidence."""
from datetime import date
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from analysis import trading_crowding
from analysis import etf_data
from analysis.report_blocks import _build_trading_crowding_block


TODAY=date(2026,10,23)


def _turnover(*, recent_multiplier=2):
    days=pd.bdate_range(end="2026-10-22",periods=30)
    return pd.DataFrame({
        "日期":days,
        "成交额":[1e8]*25+[1e8*recent_multiplier]*5,
        "换手率":[2]*25+[5]*5,
    })


def _margin(change=15):
    return {"latest_date":"20261022","latest_balance_yi":38.95,
            "change_pct":change,"days":6,
            "source":"沪深交易所融资融券明细"}


def test_real_prior20_baseline_excludes_recent5_and_future():
    activity=trading_crowding.turnover_activity(
        _turnover(),cutoff=TODAY,source="历史日行情",
    )
    assert activity["recent5_vs_prior20_ratio"]==2
    assert activity["latest_vs_prior20_ratio"] > 1
    assert activity["latest_turnover_yi"]==2
    assert activity["latest_turnover_rate_pct"]==5
    assert activity["as_of"]=="2026-10-22"


def test_stock_financing_price_high_and_trading_volume_joint_risk():
    activity=trading_crowding.turnover_activity(
        _turnover(),cutoff=TODAY,source="真实成交"
    )
    result=trading_crowding.assess_margin_crowding(
        _margin(),activity,asset_type="stock",
        price_position_pct=93,float_market_cap_yi=1000,
    )
    assert result["level"]=="watch"
    assert result["margin_to_float_market_cap_pct"]==3.9
    assert result["margin_change_pct"]==15
    assert result["coverage"]["baseline_20d"] is True
    assert "杠杆" in result["assessment"]
    assert "交易拥挤" in result["assessment"]
    assert "融资余额" in _build_trading_crowding_block(result)


def test_stock_not_crowded_from_one_signal_alone():
    activity=trading_crowding.turnover_activity(
        _turnover(recent_multiplier=1),cutoff=TODAY,source="成交"
    )
    result=trading_crowding.assess_margin_crowding(
        _margin(),activity,asset_type="stock",price_position_pct=98
    )
    assert result["level"]!="watch"


def test_etf_own_margin_not_component_stock_margin_and_no_float_denominator():
    result=trading_crowding.assess_margin_crowding(
        _margin(),trading_crowding.turnover_activity(
            _turnover(),cutoff=TODAY,source="ETF交易"
        ),asset_type="etf",price_position_pct=95,float_market_cap_yi=1000,
    )
    assert result["asset_type"]=="etf"
    assert result["margin_to_float_market_cap_pct"] is None
    assert "基金" in "".join(result["notes"])
    assert "成分股" in "".join(result["notes"])


def test_missed_financing_disclosure_is_unknown_not_zero():
    result=trading_crowding.assess_margin_crowding(
        None,{},asset_type="etf",
    )
    assert result["margin_balance_yi"] is None
    assert result["level"]=="unknown"
    assert result["coverage"]["financing"] is False


def test_future_prices_are_not_allowed_in_historical_crowding():
    activity=trading_crowding.turnover_activity(
        _turnover(),cutoff=date(2026,9,1),source="历史",
    )
    assert activity["status"]=="unavailable"


@pytest.mark.asyncio
async def test_etf_crowding_fetcher_uses_etf_daily_amount_not_stock_quotes(monkeypatch):
    seen={}
    def hist(**kwargs):
        seen.update(kwargs)
        return _turnover()
    monkeypatch.setattr(trading_crowding.ak,"fund_etf_hist_em",hist)
    result=await trading_crowding.get_etf_turnover_activity("513530",as_of=TODAY)
    assert seen["symbol"]=="513530"
    assert result["recent5_vs_prior20_ratio"]==2
    assert result["source"]=="东方财富ETF日成交额"


@pytest.mark.asyncio
async def test_etf_crowding_uses_its_own_exchange_margin_signal(monkeypatch):
    holdings=[{"name":"样本港股","code":"01919","weight_pct":8}]
    monkeypatch.setattr(etf_data,"_holdings",AsyncMock(
        return_value={"holdings":holdings,"as_of":"2026-Q2"}))
    monkeypatch.setattr(etf_data,"_etf_price",AsyncMock(
        return_value={"price_position_pct":90}))
    monkeypatch.setattr(etf_data,"_fund_with_live_quote",AsyncMock(
        return_value={"latest_price":1.49,"quote_mode":"dated_market_snapshot"}))
    monkeypatch.setattr(etf_data,"get_etf_market_context",AsyncMock(
        return_value={"sector":"跨行业高股息","observations":{},"key_drivers":[]}))
    for name in ("_hk_stock_price","_hk_stock_financial","_hk_stock_valuation",
                 "_hk_stock_snapshot"):
        monkeypatch.setattr(etf_data,name,AsyncMock(return_value={}))
    signal=AsyncMock(return_value=_margin())
    monkeypatch.setattr(etf_data,"get_margin_signal",signal)
    monkeypatch.setattr(etf_data,"get_etf_turnover_activity",
                        AsyncMock(return_value=trading_crowding.turnover_activity(
                            _turnover(),cutoff=TODAY,source="场内ETF")))
    result=await etf_data.collect_etf_data({
        "code":"513530","stock_code":"SH513530","name":"中证港股通高股息ETF"
    })
    signal.assert_awaited_once_with("SH513530")
    assert result["trading_crowding"]["margin_balance_yi"]==38.95
    assert result["trading_crowding"]["asset_type"]=="etf"


def test_margin_purchase_ratio_requires_exact_same_trade_date():
    activity=trading_crowding.turnover_activity(
        _turnover(),cutoff=TODAY,source="真实成交",
    )
    m=_margin()
    m["latest_buy_yuan"]=50_000_000
    ratio=trading_crowding.assess_margin_crowding(
        m,activity,asset_type="stock",
    )
    assert ratio["financing_buy_to_same_day_turnover_pct"]==25
    m["latest_date"]="20261021"
    mismatched=trading_crowding.assess_margin_crowding(
        m,activity,asset_type="stock",
    )
    assert mismatched["financing_buy_to_same_day_turnover_pct"] is None
