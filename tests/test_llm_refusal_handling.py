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


@pytest.mark.asyncio
async def test_text_distillation_retries_reasoning_only_token_exhaustion(monkeypatch):
    calls = []

    async def fake_response(prompt, max_tokens, **kwargs):
        calls.append(max_tokens)
        if len(calls) == 1:
            return "", "max_tokens"  # all tokens consumed by thinking
        return "【原则】核对企业现金流。", "end_turn"

    monkeypatch.setattr(llm, "_call_llm_raw_ex", fake_response)
    result = await llm.call_text("提炼投资观点", max_tokens=8192)
    assert result == "【原则】核对企业现金流。"
    assert calls == [8192, 16384]


@pytest.mark.asyncio
async def test_text_distillation_does_not_accept_partial_cutoff(monkeypatch):
    calls = []

    async def fake_response(prompt, max_tokens, **kwargs):
        calls.append(max_tokens)
        return "只有半句话", "max_tokens"

    monkeypatch.setattr(llm, "_call_llm_raw_ex", fake_response)
    result = await llm.call_text("提炼投资观点", max_tokens=8192)
    assert result is None
    assert calls == [8192, 16384]


@pytest.mark.asyncio
async def test_text_distillation_does_not_retry_complete_response(monkeypatch):
    calls = []

    async def fake_response(prompt, max_tokens, **kwargs):
        calls.append(max_tokens)
        return "提炼完成", "end_turn"

    monkeypatch.setattr(llm, "_call_llm_raw_ex", fake_response)
    result = await llm.call_text("提炼投资观点", max_tokens=8192)
    assert result == "提炼完成"
    assert calls == [8192]


@pytest.mark.asyncio
async def test_end_turn_thinking_only_retries_even_when_output_budget_is_not_exhausted(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.deepseek.com/anthropic/v1")
    calls = []

    async def response(prompt, max_tokens, **kwargs):
        calls.append((max_tokens, kwargs.get("disable_thinking")))
        if len(calls) == 1:
            return "", "end_turn"
        return "【原则】投资必须有财报数据支撑。", "end_turn"

    monkeypatch.setattr(llm, "_call_llm_raw_ex", response)
    assert await llm.call_text("提炼投资观点", max_tokens=16384) == "【原则】投资必须有财报数据支撑。"
    assert calls == [(16384, True), (32768, True)]


@pytest.mark.asyncio
async def test_normal_end_turn_empty_twice_yields_no_false_knowledge(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.deepseek.com/anthropic/v1")
    calls = []

    async def response(prompt, max_tokens, **kwargs):
        calls.append(max_tokens)
        return "", "end_turn"

    monkeypatch.setattr(llm, "_call_llm_raw_ex", response)
    assert await llm.call_text("提炼投资观点", max_tokens=16384) is None
    assert calls == [16384, 32768]


@pytest.mark.asyncio
async def test_deepseek_disables_thinking_for_text_extraction_but_not_claude(monkeypatch):
    from types import SimpleNamespace

    seen = []
    class FakeStream:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return False
        async def get_final_message(self):
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="有用观点")],
                stop_reason="end_turn",
            )
    class FakeMessages:
        def stream(self, **params):
            seen.append(params)
            return FakeStream()

    monkeypatch.setattr(llm, "_get_client",
                        lambda: SimpleNamespace(messages=FakeMessages()))
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.deepseek.com/anthropic/v1")
    assert (await llm._call_llm_raw_ex(
        "test", 16384, disable_thinking=True))[0] == "有用观点"
    assert seen[-1]["thinking"] == {"type": "disabled"}
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    assert (await llm._call_llm_raw_ex(
        "test", 16384, disable_thinking=True))[0] == "有用观点"
    assert "thinking" not in seen[-1]


def test_only_exact_deepseek_host_enables_provider_specific_request(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.deepseek.com/anthropic")
    assert llm._is_deepseek_endpoint()
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.deepseek.com.attacker.invalid")
    assert not llm._is_deepseek_endpoint()


def test_deepseek_initial_token_budget_uses_published_384k_max(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.deepseek.com/anthropic")
    monkeypatch.delenv("BACKTEST_LLM_MAX_TOKENS", raising=False)
    assert llm._effective_max_tokens(1024) == 393216
    assert llm._effective_max_tokens(2048) == 393216
    assert llm._effective_max_tokens(16384) == 393216


def test_deepseek_env_override_and_hard_cap(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.deepseek.com/anthropic")
    monkeypatch.setenv("BACKTEST_LLM_MAX_TOKENS", "65536")
    assert llm._effective_max_tokens(1024) == 65536
    assert llm._effective_max_tokens(131072) == 131072
    monkeypatch.setenv("BACKTEST_LLM_MAX_TOKENS", "999999")
    assert llm._effective_max_tokens(2048) == 393216
    monkeypatch.setenv("BACKTEST_LLM_MAX_TOKENS", "broken")
    assert llm._effective_max_tokens(1024) == 393216


def test_native_anthropic_token_budget_is_unchanged(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    monkeypatch.setenv("BACKTEST_LLM_MAX_TOKENS", "393216")
    assert llm._effective_max_tokens(1024) == 1024
    assert llm._effective_max_tokens(8192) == 8192


@pytest.mark.asyncio
async def test_deepseek_json_first_attempt_uses_max_tokens_with_no_redundant_retry(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.deepseek.com/anthropic")
    monkeypatch.delenv("BACKTEST_LLM_MAX_TOKENS", raising=False)
    calls = []

    async def fake_response(prompt, max_tokens):
        calls.append(max_tokens)
        return '{"valid": true}', "end_turn"

    monkeypatch.setattr(llm, "_call_llm_raw_ex", fake_response)
    result, reason = await llm.call_json_ex("return a JSON object", max_tokens=1024)
    assert result == {"valid": True}
    assert reason == "end_turn"
    assert calls == [393216]


@pytest.mark.asyncio
async def test_at_deepseek_max_truncated_json_is_not_retried_at_lower_budget(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.deepseek.com/anthropic")
    monkeypatch.delenv("BACKTEST_LLM_MAX_TOKENS", raising=False)
    calls = []

    async def fake_response(prompt, max_tokens):
        calls.append(max_tokens)
        return "", "max_tokens"

    monkeypatch.setattr(llm, "_call_llm_raw_ex", fake_response)
    parsed, reason = await llm.call_json_ex("return JSON", max_tokens=1024)
    assert parsed is None
    assert reason == "max_tokens"
    assert calls == [393216]


@pytest.mark.asyncio
async def test_tool_messages_use_deepseek_max_budget(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.deepseek.com/anthropic")
    monkeypatch.delenv("BACKTEST_LLM_MAX_TOKENS", raising=False)
    seen = []

    class FakeStream:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return False
        async def get_final_message(self):
            return SimpleNamespace(content=[], stop_reason="end_turn")
    class FakeMessages:
        def stream(self, **kwargs):
            seen.append(kwargs)
            return FakeStream()

    monkeypatch.setattr(llm, "_get_client", lambda: SimpleNamespace(messages=FakeMessages()))
    await llm.call_analysis_with_tools(
        "summary", tools=[], required_tool_names=[], submit_tool_name="submit",
        max_rounds=1, max_tokens=16384)
    assert seen[0]["max_tokens"] == 393216
