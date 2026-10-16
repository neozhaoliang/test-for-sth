"""Live macro-provider outages must not turn public verified decisions into ignorance.

All fixtures are dated, official-source records; no future information is
introduced into a historical replay, and snapshots expire before meetings.
"""
from datetime import date

import pytest

from analysis import macro_rates, macro_verified_fallback
from analysis.report_blocks import _build_macro_rates_block
from analysis.valuation_macro import build_macro_valuation_context
from analysis.resource_macro_transmission import build_resource_macro_transmission


def test_official_backup_values_as_of_oct_16_and_expiry():
    day=date(2026,10,16)
    us=macro_verified_fallback.us_reference(today=day)
    china=macro_verified_fallback.china_reference(today=day)
    japan=macro_verified_fallback.international_reference("japan",today=day)
    euro=macro_verified_fallback.international_reference("euro",today=day)
    assert us["fed_target_lower_pct"]==3.75
    assert us["fed_target_upper_pct"]==4.0
    assert us["fed_target_last_decision_change_pp"]==.25
    assert us["us10y_yield_pct"]==5.19
    assert us["us10y_as_of"]=="2026-10-14"
    assert china["lpr_1y_pct"]==3.0
    assert china["lpr_5y_pct"]==3.5
    assert china["as_of"]=="2026-09-20"
    assert japan["policy_rate_pct"]==1.25
    assert japan["decision_as_of"]=="2026-09-18"
    assert euro["policy_rate_pct"]==2.50
    assert euro["decision_as_of"]=="2026-09-10"
    for x in (us,china,japan,euro):
        assert x["source_mode"]=="dated_verified_fallback"
        assert "不是" in x["fallback_note"] or "非本次" in x["fallback_note"]
    assert not macro_verified_fallback.china_reference(today=date(2026,10,20))
    assert not macro_verified_fallback.us_reference(today=date(2026,11,1))
    assert not macro_verified_fallback.international_reference("euro",today=date(2026,10,27))
    assert not macro_verified_fallback.international_reference("japan",today=date(2026,10,29))
    assert not macro_verified_fallback.us_reference(today=date(2026,9,1))


@pytest.mark.asyncio
async def test_empty_live_fred_and_akshare_still_supply_dated_macro(monkeypatch):
    async def no_us(as_of=None):return None
    async def no_lpr(as_of=None):return None
    monkeypatch.setattr(macro_rates,"_fetch_us_rates",no_us)
    monkeypatch.setattr(macro_rates,"_fetch_china_lpr",no_lpr)
    report=await macro_rates.get_macro_rate_context(
        as_of=date(2026,10,16),allow_verified_live_fallback=True)
    assert report is not None
    assert set(report["fallback_used"])=={"Fed","US10Y","China LPR"}
    assert report["us"]["fed_target_upper_pct"]==4.0
    assert report["us"]["us10y_yield_pct"]==5.19
    assert report["china"]["lpr_5y_pct"]==3.5
    assert "官方日期快照" in str(report["warnings"])
    prompt=_build_macro_rates_block(report)
    for expected in (
        "3.75%~4.0%", "5.19%", "1年期 3.0%", "5年期 3.5%",
        "最近已核验会议利率变化 +0.25", "不是本次实时抓取"
    ):
        assert expected in prompt
    assert "Nonepct" not in prompt
    assert "本次未取得中美利率" not in prompt
    assert "全部资料缺失" in prompt  # As a strict prohibition, not a finding.
    # The industry monetary reasoning must receive the actual policy
    # decision too, even without 180 days of rate history.
    macro=build_macro_valuation_context(
        macro_rates=report, as_of="2026-10-16")
    reading=build_resource_macro_transmission(
        stock_name="紫金矿业", archetype="cyclical", industry="贵金属",
        macro_context=macro,macro_rates=report)
    assert reading["fed"]["upper_pct"]==4.0
    assert reading["fed"]["direction"]=="最近一次已核验会议加息"


@pytest.mark.asyncio
async def test_historical_report_cannot_borrow_verified_live_backup(monkeypatch):
    async def no_us(as_of=None):return None
    async def no_lpr(as_of=None):return None
    monkeypatch.setattr(macro_rates,"_fetch_us_rates",no_us)
    monkeypatch.setattr(macro_rates,"_fetch_china_lpr",no_lpr)
    assert await macro_rates.get_macro_rate_context(
        as_of=date(2026,10,16),allow_verified_live_fallback=False) is None
    assert await macro_rates.get_macro_rate_context(
        as_of=date(2024,6,30),allow_verified_live_fallback=False) is None
    assert await macro_rates.get_macro_rate_context(
        as_of=date(2026,11,1),allow_verified_live_fallback=True) is None


@pytest.mark.asyncio
async def test_live_fallback_adds_only_missing_fred_dimensions(monkeypatch):
    async def partial_us(as_of=None):
        return {
          "fed_target_lower_pct":3.50,"fed_target_upper_pct":3.75,
          "fed_target_lower_as_of":"2026-10-16",
          "fed_target_upper_as_of":"2026-10-16",
          "fed_target_freshness":{"fresh":True,"age_days":0},
          "source_urls":{"target_lower":"live-fred-lower","target_upper":"live-fred-upper"},
          "us10y_freshness":{"fresh":False,"age_days":20},
        }
    async def no_lpr(as_of=None):return None
    monkeypatch.setattr(macro_rates,"_fetch_us_rates",partial_us)
    monkeypatch.setattr(macro_rates,"_fetch_china_lpr",no_lpr)
    out=await macro_rates.get_macro_rate_context(
        as_of=date(2026,10,16),allow_verified_live_fallback=True)
    # Test resilience, NOT the real-world policy figure. Live fresh data
    # overrides the previously verified lower-priority fallback.
    assert out["us"]["fed_target_upper_pct"]==3.75
    assert out["us"]["source_urls"]["target_upper"]=="live-fred-upper"
    assert out["us"]["us10y_yield_pct"]==5.19
    assert "Fed" not in out["fallback_used"]
    assert "US10Y" in out["fallback_used"]


@pytest.mark.asyncio
async def test_japan_ecb_fallback_without_akshare_data(monkeypatch):
    import akshare as ak
    def no_policy():
        raise TimeoutError("upstream blocked")
    monkeypatch.setattr(ak,"macro_bank_japan_interest_rate",no_policy,raising=False)
    monkeypatch.setattr(ak,"macro_bank_euro_interest_rate",no_policy,raising=False)
    monkeypatch.setattr(macro_verified_fallback,"_today",lambda:date(2026,10,16))
    found=await macro_rates.get_international_policy_rates()
    assert found["japan"]["policy_rate_pct"]==1.25
    assert found["euro"]["policy_rate_pct"]==2.50
    assert found["japan"]["source_mode"]=="dated_verified_fallback"
    assert found["euro"]["source_tier"]=="A"
    block=_build_macro_rates_block(found)
    assert "日本央行决议利率 1.25%" in block
    assert "欧洲央行决议利率 2.5%" in block
    assert "官方决议后备记录" in block


@pytest.mark.asyncio
async def test_historical_international_source_does_not_use_live_backup(monkeypatch):
    import akshare as ak
    def no_policy():raise TimeoutError("network")
    monkeypatch.setattr(ak,"macro_bank_japan_interest_rate",no_policy,raising=False)
    monkeypatch.setattr(ak,"macro_bank_euro_interest_rate",no_policy,raising=False)
    monkeypatch.setattr(macro_verified_fallback,"_today",lambda:date(2026,10,16))
    result=await macro_rates.get_international_policy_rates(as_of=date(2026,10,15))
    assert result == {"japan":None, "euro":None}
