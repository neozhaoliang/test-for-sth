"""No ETF may inherit golden-miner commentary from a different ETF research run."""
import json
from unittest.mock import AsyncMock

import pytest

from analysis import etf_macro, etf_cycle, etf_data, etf_report


NAME = "华泰柏瑞中证港股通高股息投资ETF(QDII)"


def _data(name=NAME):
    market = etf_macro.sector_for_etf(name)
    return {
        "fund": {"code": "513530", "name": name},
        "macro_market": {
            "sector": market["sector"], "key_drivers": market["drivers"],
            "observations": {"china_lpr1y": {
                "name": "中国1年LPR", "value": 3.0,
                "as_of": "2026-10-20", "unit": "%", "source": "公开利率"}},
        },
        "portfolio": {"holdings": [
            {"code":"01919","name":"中远海控","weight_pct":5.91},
            {"code":"00386","name":"中国石油化工股份","weight_pct":5.10},
            {"code":"01088","name":"中国神华","weight_pct":4.20},
        ], "as_of":"2026-Q2"},
        "industry_cycle": etf_cycle.assess_cycle({
            "sector": market["sector"], "observations":{}}, []),
        "limitations": ["重仓来自披露的定期报告，非实时成份"],
        "collected_on": "2026-10-23",
    }


def _success():
    return {
        "stance": "neutral", "thesis_summary":
            "港股通高股息策略重点看现金流、派息率和跨行业盈利周期。",
        "sections": {
            "commodity_market": "需要核对能源、航运等已持有公司的派息覆盖。",
            "industry_cycle": "重仓横跨不同产业，周期并不一致。",
        },
        "risk_notes": "高景气分红可能减少",
        "invalidation_condition": "股息兑现和利润证据出现转折",
    }


def test_513530_is_dividend_not_mining_sector():
    profile = etf_macro.sector_for_etf(NAME)
    assert profile["sector"] == "跨行业高股息"
    assert profile["commodity_contract"] is None
    assert "现金流" in " ".join(profile["drivers"])
    assert etf_report._profile_for_data(_data()) == "跨行业高股息"
    assert etf_report._section_labels_for("跨行业高股息")[
        "commodity_market"] == "股息来源、现金流与分红可持续性"


def test_gold_research_is_not_part_of_base_agent_prompt():
    assert "黄金股ETF" not in etf_report._SYSTEM_RULES
    assert "gold_cny" not in etf_report._SYSTEM_RULES
    assert "COMEX" not in etf_report._SYSTEM_RULES
    assert "黄金" in etf_report._rules_for_profile("黄金产业股票")
    assert "黄金" not in etf_report._rules_for_profile("跨行业高股息")
    assert "黄金" not in etf_report._rules_for_profile("宽基市场")


@pytest.mark.asyncio
async def test_513530_prompt_contains_no_517520_or_gold_macro(monkeypatch):
    prompts = []
    async def generated(prompt, **kwargs):
        prompts.append(prompt)
        return _success(), None
    monkeypatch.setattr(etf_report, "call_json_ex", generated)
    result = await etf_report._write_etf_opinion(_data())
    assert result["stance"] == "neutral"
    assert result["generation_status"] == "complete"
    assert len(prompts) == 1
    for marker in ("黄金股ETF", "国内金价", "美元金价", "gold_cny", "gold_usd",
                   "上海金", "永赢", "黄金期货"):
        assert marker not in prompts[0], f"foreign ETF leakage: {marker}"
    for current in ("513530", "高股息", "分红", "现金流", "中远海控"):
        assert current in prompts[0], f"missing current ETF context: {current}"


@pytest.mark.asyncio
async def test_gold_only_prompt_for_real_gold_etf(monkeypatch):
    prompts = []
    async def generated(prompt, **kwargs):
        prompts.append(prompt)
        return _success(), None
    monkeypatch.setattr(etf_report, "call_json_ex", generated)
    gold = _data("黄金股ETF永赢")
    gold["fund"]["code"] = "517520"
    await etf_report._write_etf_opinion(gold)
    assert len(prompts) == 1
    assert "gold_cny" in prompts[0]
    assert "金矿" in prompts[0]
    assert etf_report._section_labels_for("黄金产业股票")[
        "commodity_market"] == "黄金价格与矿企盈利传导"


@pytest.mark.asyncio
async def test_reject_hardcoded_other_fund_stance_and_regenerate(monkeypatch):
    stale = _success()
    stale["thesis_summary"] = "本基金不是黄金股ETF，未提供gold_cny。"
    prompts = []
    async def generated(prompt, **kwargs):
        prompts.append(prompt)
        return (stale if len(prompts) == 1 else _success()), None
    monkeypatch.setattr(etf_report, "call_json_ex", generated)
    out = await etf_report._write_etf_opinion(_data())
    assert len(prompts) == 2
    assert out["generation_status"] == "complete"
    assert "黄金股ETF" not in json.dumps(out, ensure_ascii=False)


@pytest.mark.asyncio
async def test_reject_twice_instead_of_publishing_wrong_etf(monkeypatch):
    stale = _success()
    stale["sections"]["commodity_market"] = "本基金不是黄金股ETF，未提供gold_usd。"
    stub = AsyncMock(return_value=(stale, None))
    monkeypatch.setattr(etf_report, "call_json_ex", stub)
    out = await etf_report._write_etf_opinion(_data())
    assert stub.await_count == 2
    assert out["generation_status"] == "unavailable"
    assert out["stance"] == ""
    assert "gold_usd" not in json.dumps(out, ensure_ascii=False)


@pytest.mark.asyncio
async def test_513530_market_payload_has_no_gold_warnings_and_dividend_cycle(monkeypatch):
    holdings = [
        {"code": "01919", "name": "中远海控", "weight_pct": 5.91},
        {"code": "00386", "name": "中国石油化工股份", "weight_pct": 5.10},
        {"code": "01088", "name": "中国神华", "weight_pct": 4.2},
    ]
    monkeypatch.setattr(etf_data, "_holdings",
        AsyncMock(return_value={"holdings": holdings, "as_of":"2026-Q2"}))
    monkeypatch.setattr(etf_data, "_etf_price", AsyncMock(return_value={}))
    monkeypatch.setattr(etf_data, "_fund_with_live_quote",
        AsyncMock(return_value={"latest_price": 1.489,
                                "quote_mode":"undated_market_snapshot"}))
    market = etf_macro.sector_for_etf(NAME)
    monkeypatch.setattr(etf_data, "get_etf_market_context",
        AsyncMock(return_value={
            "sector": market["sector"],
            "key_drivers":market["drivers"], "observations":{},
        }))
    monkeypatch.setattr(etf_data, "_hk_stock_financial", AsyncMock(return_value={}))
    monkeypatch.setattr(etf_data, "_hk_stock_valuation", AsyncMock(return_value={}))
    monkeypatch.setattr(etf_data, "_hk_stock_price",
        AsyncMock(return_value={"price_position_pct": 55, "price_currency":"HKD"}))
    result = await etf_data.collect_etf_data({
        "name": NAME, "code": "513530", "stock_code": "SH513530"
    })
    assert result["industry_cycle"]["stage"] == "多行业派息能力与盈利周期分化"
    assert "高股息" in result["scope"]
    assert "分红" in "".join(result["limitations"])
    all_business_context = json.dumps({
        "macro_market": result["macro_market"],
        "industry_cycle": result["industry_cycle"],
        "limitations": result["limitations"]}, ensure_ascii=False)
    assert "黄金股" not in all_business_context
    assert "金价" not in all_business_context
    assert "金矿" not in all_business_context
    assert "gold_cny" not in all_business_context
