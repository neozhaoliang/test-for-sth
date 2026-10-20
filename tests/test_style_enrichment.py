"""Observed sector boards must not discard explanations learned from KB."""
from types import SimpleNamespace

import pytest

from analysis import report_synthesis as synth


@pytest.mark.asyncio
async def test_observed_sector_rotation_retains_real_knowledge_drivers(monkeypatch):
    events = []
    async def rotation_reading(rotation, **kwargs):
        events.append(("observed_rotation", len(rotation["sectors"])))
        return {
            "status": "llm_selected",
            "summary":"最近行业的涨跌幅明显分化，资源股相对走弱，防守类表现更平稳。",
            "hypotheses":["利率变化可能影响有色商品价格，也可能改变资金对分红的要求。"],
        }
    async def kb_reasons(excerpts, **kwargs):
        events.append(("private_KB", len(excerpts)))
        assert kwargs["market_context"]["sector_rotation"]["status"]=="observed"
        return {
            "status":"distilled_hypotheses",
            "drivers":[{
                "mechanism":"资金要求更高的短期现金回报时，高估值成长股票的定价可能收缩。",
                "conditions":"需核对相同窗口的基金持仓和长债收益率。",
                "alternative":"盈利预期下降也能造成指数相对变化。",
                "verification":"核对基金份额及实际交易量。",
                "evidence_status":"private_research_hypothesis",
            }],
            "private_excerpt_count":1,"private_transcript_count":1,
        }
    monkeypatch.setattr(synth,"interpret_sector_rotation",rotation_reading)
    monkeypatch.setattr(synth,"build_market_style_reasons",kb_reasons)
    rotation={
        "status":"observed", "as_of":"2026-10-20",
        "sectors":[
            {"name":"有色金属","d20_pct":-8.0},
            {"name":"贵金属","d20_pct":-3.0},
            {"name":"煤炭行业","d20_pct":4.0},
            {"name":"银行","d20_pct":3.0},
            {"name":"半导体","d20_pct":-6.0},
            {"name":"电力行业","d20_pct":2.0},
        ],
        "benchmarks":[],
    }
    style={"sector_rotation":rotation}
    inputs=SimpleNamespace(
        valuation_model_result={"style_context":style},
        research_profile=SimpleNamespace(industry="有色金属"),
        research_mode="live",
        knowledge_excerpts=[SimpleNamespace(distilled="匿名知识机制")],
    )
    block=await synth._enrich_style_context(inputs,"2026-10-20")
    assert events==[("observed_rotation",6),("private_KB",1)]
    assert style["causal_knowledge_status"]=="distilled_hypotheses"
    assert len(style["causal_drivers"])==1
    assert "最近行业的涨跌幅明显分化" in block
    assert "短期现金回报" in block
    assert "资金流向" in block or "资金调仓" in block
    assert "源码" not in block
    assert "private_excerpt_count" not in block


@pytest.mark.asyncio
async def test_old_historical_replay_does_not_use_later_knowledge(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("No future method or source in old backtest")
    monkeypatch.setattr(synth,"interpret_sector_rotation",forbidden)
    monkeypatch.setattr(synth,"build_market_style_reasons",forbidden)
    style={"sector_rotation":{"status":"observed","sectors":[
        {"name":"有色金属","d20_pct":-3},
        {"name":"银行","d20_pct":2},
        {"name":"电力行业","d20_pct":1},
    ]}}
    inp=SimpleNamespace(
        valuation_model_result={"style_context":style},
        research_profile=SimpleNamespace(industry="有色金属"),
        research_mode="historical",
        knowledge_excerpts=[],
    )
    out=await synth._enrich_style_context(inp,"2025-06-30")
    assert style["causal_knowledge_status"]=="historical_method_not_available"
    assert style["causal_drivers"]==[]
    assert "历史" in out
