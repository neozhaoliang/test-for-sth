"""Offline macro/gold/cycle ETF evidence tests: never use fabricated live values."""
from datetime import date, timedelta
from unittest.mock import AsyncMock
import math
import pandas as pd
import pytest

from analysis import etf_macro, etf_data, etf_cycle, etf_report


def test_sector_drivers_are_not_one_size_fits_all():
    gold = etf_macro.sector_for_etf("黄金股ETF永赢")
    assert gold["sector"] == "黄金产业股票"
    assert "实际利率" in " ".join(gold["drivers"])
    assert etf_macro.sector_for_etf("煤炭ETF")["commodity_contract"] == "ZC0"
    assert etf_macro.sector_for_etf("铜产业ETF")["commodity_contract"] == "CU0"
    assert "净息差" in " ".join(etf_macro.sector_for_etf("银行ETF")["drivers"])
    assert "去库存" in " ".join(etf_macro.sector_for_etf("半导体ETF")["drivers"])
    assert etf_macro.sector_for_etf("沪深300ETF")["sector"] == "宽基市场"


def test_observations_are_dated_sorted_and_only_count_sufficient_windows():
    today = date(2026, 10, 22)
    days = pd.bdate_range(today-timedelta(days=440), periods=310)
    df = pd.DataFrame({"date": days, "close": [100+i/10 for i in range(len(days))]})
    latest_day = df.iloc[-1]["date"].date()
    assert (today-latest_day).days <= 12
    p = etf_macro._rows(df, "date", "close", today=today, max_age=12)
    o = etf_macro._observation(p, title="黄金", unit="元/克", source="SGE", market=True)
    assert o["return_1m_pct"] > 0
    assert o["return_3m_pct"] > 0
    assert o["return_1y_pct"] > 0
    assert 0 <= o["position_52w_pct"] <= 100
    assert o["as_of"] == str(latest_day)
    stale = df.copy()
    stale["date"] = pd.to_datetime(stale["date"]) - pd.Timedelta(days=360)
    assert etf_macro._rows(stale, "date", "close", today=today, max_age=12) == []


@pytest.mark.asyncio
async def test_macros_use_gold_and_real_rates_with_distinct_market_dates(monkeypatch):
    now = date(2026, 10, 22)
    days = pd.bdate_range(end=now-timedelta(days=1), periods=260)
    gold = pd.DataFrame({
        "date": days, "close": [500+i for i in range(260)]
    })
    usgold = pd.DataFrame({"date": days, "close": [2100+i*2 for i in range(260)]})
    lpr = pd.DataFrame({"TRADE_DATE": ["2026-08-20","2026-09-20","2026-10-20"],
                        "LPR1Y": [3.1,3.1,3.0]})
    bonds = pd.DataFrame({"日期": ["2026-10-19","2026-10-20"],
                          "美国国债收益率10年":[4.6,4.5],
                          "中国国债收益率10年":[2.0,1.95]})
    macro = {
        "spot_hist_sge": gold,
        "futures_foreign_hist": usgold,
        "macro_china_lpr": lpr,
        "bond_zh_us_rate": bonds,
        "macro_china_money_supply": None,
        "macro_china_pmi_yearly": None,
        "macro_china_fx_gold": None,
    }
    async def fake_ak(key, **kwargs):
        return macro.get(key)
    monkeypatch.setattr(etf_macro, "_ak", fake_ak)
    monkeypatch.setattr(etf_macro, "_fred_real_yield", AsyncMock(return_value={
        "name": "美国10年TIPS实际收益率", "value": 2.6, "unit": "%",
        "as_of": "2026-10-20", "source": "FRED DFII10",
    }))
    d = await etf_macro._collect_uncached(etf_macro.sector_for_etf("黄金股ETF"), now)
    o = d["observations"]
    assert o["gold_cny"]["source"].startswith("上海黄金交易所")
    assert o["gold_cny"]["return_3m_pct"] > 0
    assert o["gold_usd"]["return_1y_pct"] > 0
    assert o["us_real_yield_10y"]["value"] == 2.6
    assert o["us_treasury_10y"]["value"] == 4.5
    assert o["china_lpr1y"]["previous_value"] == 3.1
    assert "M2货币同比" in d["missing"]
    assert "gold_cny" in o and "gold_usd" in o


def test_actual_period_change_not_quarter_on_quarter_profit():
    periods = [
        {"period": "2026-03-31", "net_profit_growth_pct": 50, "roe_pct": 7},
        {"period": "2026-06-30", "net_profit_growth_pct": 35, "roe_pct": 11},
    ]
    observation = etf_data._profit_period_change(periods)
    assert observation["financial_period"] == "2026-06-30"
    assert observation["previous_financial_period"] == "2026-03-31"
    assert observation["growth_change_pp"] == -15
    assert "非单季度环比" in observation["growth_change_definition"]


def _holding(code, weight, profit, change):
    return {"code": code, "name": code, "weight_pct": weight,
            "net_profit_growth_pct": profit, "growth_change_pp": change}


def test_cycle_bottom_candidate_needs_profit_and_independent_product_price():
    rows = [
        _holding("600111", 9, -10, 15), _holding("601111", 8, -20, 10),
        _holding("603111", 7, -5, 8), _holding("600112", 5, -12, 6),
    ]
    market = {"sector":"黄金产业股票", "observations": {
        "gold_cny":{"name":"上海金Au99.99","return_3m_pct":7.2,"as_of":"2026-10-21"}
    }}
    state = etf_cycle.assess_cycle(market, rows)
    assert state["stage"] == "低盈利后的修复候选"
    assert state["confidence"].startswith("中")
    market["observations"] = {}
    state = etf_cycle.assess_cycle(market, rows)
    assert state["stage"] == "周期位置待确认"
    assert state["confidence"] == "不足"


def test_cycle_high_earnings_and_deceleration_is_top_risk_not_price_trough():
    rows = [
        _holding("600111", 9, 90, -15), _holding("601111", 8, 70, -10),
        _holding("603111", 7, 120, -8), _holding("600112", 5, 35, -6),
    ]
    market = {"sector":"黄金产业股票", "observations": {
        "gold_cny":{"name":"上海金Au99.99","return_3m_pct":-5.2,
                    "as_of":"2026-10-21"}
    }}
    state = etf_cycle.assess_cycle(market, rows)
    assert state["stage"] == "高盈利阶段的回落风险"
    assert "低PE" in state["bottom_confirmation"]
    assert state["earnings_breadth"]["decelerating_growth_weight_pct"] == 29


@pytest.mark.asyncio
async def test_all_top_ten_stocks_get_financial_data_and_hk_gets_prices(monkeypatch):
    code = "517520"
    entries = [{"code":f"600{100+i}", "name":f"公司{i}", "weight_pct":6}
               for i in range(9)]
    entries.append({"code":"00700", "name":"港股", "weight_pct":5})
    monkeypatch.setattr(etf_data, "_holdings", AsyncMock(return_value={
        "holdings": entries, "as_of":"2026-Q2"
    }))
    monkeypatch.setattr(etf_data, "_etf_price", AsyncMock(return_value={}))
    monkeypatch.setattr(etf_data, "_fund_with_live_quote",
                        AsyncMock(return_value={"code": code,"name":"黄金股ETF"}))
    monkeypatch.setattr(etf_data, "get_etf_market_context",
                        AsyncMock(return_value={
                            "sector":"黄金产业股票", "observations":{}, "key_drivers":[]
                        }))
    sp = AsyncMock(return_value={"price_position_pct":50})
    prof = AsyncMock(return_value={"financial_period":"2026-06-30",
                                   "net_profit_growth_pct":10,"growth_change_pp":-4})
    val = AsyncMock(return_value={"pe_ttm":12})
    hk = AsyncMock(return_value={"price_position_pct":80,"price_currency":"HKD"})
    monkeypatch.setattr(etf_data, "_stock_price", sp)
    monkeypatch.setattr(etf_data, "_stock_profit", prof)
    monkeypatch.setattr(etf_data, "_stock_valuation", val)
    monkeypatch.setattr(etf_data, "_hk_stock_financial", AsyncMock(return_value={}))
    monkeypatch.setattr(etf_data, "_hk_stock_snapshot", AsyncMock(return_value={}))
    monkeypatch.setattr(etf_data, "_hk_stock_valuation", AsyncMock(return_value={}))
    monkeypatch.setattr(etf_data, "_hk_stock_price", hk)
    result = await etf_data.collect_etf_data(
        {"code": code,"stock_code":"SH517520","name":"黄金股ETF永赢"})
    assert len(result["portfolio"]["holdings"]) == 10
    assert prof.await_count == 9
    assert val.await_count == 9
    assert sp.await_count == 9
    assert hk.await_count == 1
    assert result["portfolio"]["holdings"][8]["financial_period"] == "2026-06-30"
    assert "pe_ttm" not in result["portfolio"]["holdings"][9]
    assert result["industry_cycle"]["earnings_breadth"]["decelerating_growth_weight_pct"] == 54


def test_etf_report_requires_macro_gold_and_cycle_nine_sections():
    assert "macro_liquidity" in etf_report._SECTION_NAMES
    assert "commodity_market" in etf_report._SECTION_NAMES
    assert "industry_cycle" in etf_report._SECTION_NAMES
    assert "美国10年TIPS" in etf_report._SYSTEM_RULES
    assert "前十大" in etf_report._SYSTEM_RULES
    assert "底部" in etf_report._SYSTEM_RULES and "顶部" in etf_report._SYSTEM_RULES
