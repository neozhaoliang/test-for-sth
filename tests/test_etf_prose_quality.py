"""ETF reports must be investment opinions, not exposed prompts or null schemas."""
import json
from datetime import date
from unittest.mock import AsyncMock

import pytest

from analysis import etf_data, etf_report


def _sample():
    return {
        "fund":{"code":"513530",
                "name":"华泰柏瑞中证港股通高股息投资ETF(QDII)",
                "latest_price":1.487,
                "iopv":None,
                "iopv_premium_pct":None,
                "turnover_yuan":148040000,
                "quote_time":"时间尚未核实"},
        "portfolio":{"holdings":[
            {"name":"中远海控","code":"01919","weight_pct":5.91}
        ],"as_of":"2026-Q2"},
        "portfolio_stats":{"top10_weight_pct":42.1},
        "macro_market":{"sector":"跨行业高股息","observations":{}},
        "industry_cycle":{"sector":"跨行业高股息","stage":"多行业分化"},
        "holding_risk":{"management_style":{"kind":"index"}},
        "limitations":["基金合同尚未核实，IOPV为null，不要照抄"],
        "scope":"非A股股票ETF或混合资产ETF：数据接口有问题",
        "collected_on":"2026-10-23",
    }


def _opinion(**kwargs):
    return {
        "stance":"neutral",
        "thesis_summary":"港股通高股息策略的核心在于盈利与股息是否可持续。",
        "sections":{
            "macro_liquidity":"利率下行可能提高稳定现金回报资产的吸引力。",
            "commodity_market":"航运盈利受运价影响，能源企业受油价影响。",
            "fund_structure":"这份栏目应由上方数据卡替代。",
            "market_price":"这份报价说明应由现价和流动性卡替代。",
            "holdings":"中远海控01919权重5.91%——已经在上面的表格。",
        },
        "risk_notes":"高景气利润下滑或导致派息减少",
        "invalidation_condition":"连续报告期现金分红被盈利和现金流覆盖",
        **kwargs,
    }


def test_prompt_echo_checker_catches_backend_field_and_conditional_workflow():
    for text in (
        "IOPV为null，latest_shares为null，无法得到净值。",
        "若为指数产品，则需核实基金合同和成分股变动。",
        "基金名称提示可能为指数产品，但仍需看编制规则。",
        "宏观数据方面，sample_count为0、price_observed_weight_pct为0。",
    ):
        assert etf_report._has_prompt_echo({
            "thesis_summary":text, "sections":{},"risk_notes":"",
        }), text
    assert not etf_report._has_prompt_echo(_opinion())
    assert not etf_report._has_prompt_echo(_opinion(
        sections={"macro_liquidity":"IOPV尚未取得同步值，不能计算参考折价。"}
    ))


@pytest.mark.asyncio
async def test_retries_template_echo_and_only_publishes_rewritten_thesis(monkeypatch):
    bad=_opinion(
        sections={"fund_structure":"普通重复", "market_price":"旧重复"},
        thesis_summary="若为指数产品，需要核实合同，若为主动产品，另需调查经理策略。"
    )
    prompts=[]
    async def mock_call(prompt, **kwargs):
        prompts.append(prompt)
        return (bad if len(prompts)==1 else _opinion()), None
    monkeypatch.setattr(etf_report, "call_json_ex", mock_call)
    out=await etf_report._write_etf_opinion(_sample())
    assert len(prompts)==2
    assert out["generation_status"]=="complete"
    assert "若为指数" not in out["thesis_summary"]
    assert out["sections"]["holdings"] == ""
    assert out["sections"]["fund_structure"] == ""
    assert out["sections"]["market_price"] == ""
    assert "latest_shares" not in prompts[0]
    assert "基金合同尚未核实，IOPV为null" not in prompts[0]
    assert "非A股股票ETF或混合资产ETF" not in prompts[0]


@pytest.mark.asyncio
async def test_twice_prompt_echo_never_published_as_analysis(monkeypatch):
    wrong=_opinion(thesis_summary="IOPV为null，latest_shares为null，建议继续核实。")
    model=AsyncMock(return_value=(wrong,None))
    monkeypatch.setattr(etf_report, "call_json_ex", model)
    out=await etf_report._write_etf_opinion(_sample())
    assert model.await_count==2
    assert out["generation_status"]=="unavailable"
    assert "latest_shares" not in json.dumps(out,ensure_ascii=False)
    assert out["stance"]==""


def test_recent_verified_close_cannot_carry_intraday_turnover_or_iopv():
    fund={"code":"513530","latest_price":1.55,"iopv":1.57,
          "iopv_premium_pct":-1.27,"turnover_yuan":148040000,
          "volume":99930000,"price_change_pct":-1.2,
          "quote_mode":"previous_market_snapshot"}
    updated=etf_data._with_recent_close(fund,{
        "recent_close":1.487,"recent_close_as_of":"2026-10-22"
    },date(2026,10,23))
    assert updated["latest_price"]==1.487
    assert updated["quote_mode"]=="last_verified_daily_close"
    for key in ("iopv","iopv_premium_pct","turnover_yuan",
                "volume","price_change_pct"):
        assert updated[key] is None



@pytest.mark.asyncio
async def test_high_dividend_duplicates_removed_before_returning_report(monkeypatch):
    sample=_sample()
    rendered=_opinion()
    rendered["sections"] = {
        "high_positions": "海丰国际价格83%位置，PE估值分位较高。",
        "earnings_cycle": "海丰国际净利增长回落，派息率偏高。",
        "valuation": "海丰国际估值偏高，需要担心股价回撤。",
        "industry_cycle": "海丰国际净利润增长回落，派息可能下降。",
        "commodity_market": "海丰国际派息可能下降。",
        "macro_liquidity": "长端利率回落使股息资产相对更有吸引力。",
    }
    rendered["risk_notes"] = "海丰国际估值偏高和派息可能下降。"
    monkeypatch.setattr(
        etf_report, "call_json_ex", AsyncMock(return_value=(rendered, None)),
    )
    result = await etf_report._write_etf_opinion(sample)
    assert result["generation_status"] == "complete"
    assert result["sections"]["high_positions"]
    assert result["sections"]["earnings_cycle"]
    assert result["sections"]["macro_liquidity"]
    for key in ("valuation", "industry_cycle", "commodity_market"):
        assert result["sections"][key] == ""
    assert result["risk_notes"] == ""


def test_non_dividend_etf_does_not_lose_distinct_narratives():
    original = {
        "high_positions": "某成分股价格高位。",
        "earnings_cycle": "产业利润改善。",
        "valuation": "整体估值收益率的独立测算。",
        "industry_cycle": "库存周期出现拐点。",
        "commodity_market": "独立产品价格上涨。",
    }
    sections = etf_report._compact_etf_sections(
        original, "黄金产业股票"
    )
    for key, value in original.items():
        assert sections[key] == value
