"""Regression for model/provider refusals seen in 601899 scoring request.

A refusal in *optional* dimension-scoring must never invalidate a complete
twelve-dimension report. Core synthesis failure is still a hard failure.
"""
from types import SimpleNamespace

import pytest

import backtest.llm_client as llm
import analysis.report_synthesis as synthesis


def test_short_provider_refusal_is_detected_without_matching_long_report():
    assert llm._is_model_refusal("很抱歉，我无法回答您的问题")
    assert llm._is_model_refusal("抱歉，我无法回答您的问题")
    assert not llm._is_model_refusal('{"score": 1, "note": "无法回答不等于风险低"}')
    assert not llm._is_model_refusal("")


@pytest.mark.asyncio
async def test_refusal_does_not_trigger_a_second_llm_json_repair(monkeypatch):
    calls = []
    async def fake_response(prompt, max_tokens):
        calls.append((prompt, max_tokens))
        return "很抱歉，我无法回答您的问题", "end_turn"
    monkeypatch.setattr(llm, "_call_llm_raw_ex", fake_response)
    parsed, stop = await llm.call_json_ex("对该股票的维度打分", max_tokens=1024)
    assert parsed is None
    assert stop == "refusal"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_optional_scoring_refusal_does_not_fabricate_neutral_scores(monkeypatch):
    async def refused(prompt, **kwargs):
        return None, "refusal"
    monkeypatch.setattr(synthesis, "call_json_ex", refused)
    result = await synthesis._score_dimensions(
        {"analyze_management": "基于披露的产能、成本与管理层资料，暂无充分证据定性。"},
        "SH601899",
    )
    assert result is None


@pytest.mark.asyncio
async def test_core_research_refusal_returns_explicit_error_not_empty_report(monkeypatch):
    async def tool_refusal(*args, **kwargs):
        return {}, None, "refusal"
    async def json_refusal(*args, **kwargs):
        return None, "refusal"
    monkeypatch.setattr(synthesis, "call_analysis_with_tools", tool_refusal)
    monkeypatch.setattr(synthesis, "call_json_ex", json_refusal)
    monkeypatch.setattr(synthesis, "_build_prompt", lambda *args: "测试研究")
    monkeypatch.setattr(synthesis, "build_industry_valuation_report", lambda *args: {})
    monkeypatch.setattr(synthesis, "industry_model_prompt_block", lambda *args: "no price")
    ctx = SimpleNamespace(stock_code="SH601899", research_mode="live", as_of="2026-10-09")
    with pytest.raises(RuntimeError, match="拒绝输出股票研究"):
        await synthesis._generate_summary(ctx, [], [])
