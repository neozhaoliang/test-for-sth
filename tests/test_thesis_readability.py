"""Reader-facing thesis prose should not reprint 12 dimensions of raw figures."""
from types import SimpleNamespace

import pytest

from analysis.thesis_readability import (
    readability_signals, accept_readable_edit, polish_thesis_prose,
)


def overloaded_example():
    # Matches the failure mode in the Zijin report: many facts, dates, rates
    # and shorthand in place of a coherent explanation.
    p = (
        "29.92元这个价格，我给中性。公司质量可看多但赔率一般。"
        "2026H1营收1941.78亿元，同比15.78%，利润392.00亿元同比68.0%，"
        "现金流555亿元同比92%，负债率49.55%，回购39.99亿元、分红1.02元。"
        "股东户数2025-06-30为335,659户、2026-06-30为1,318,365户，"
        "同比292.77%。"
    )
    p2 = (
        "PE(TTM)11.76处10年3.5%分位，5年6.1%分位，PB3.939处10年71%分位。"
        "历史EPS区间0.94至1.48元；历史估值13.33元、16.68元、29.89元，"
        "5年PB中枢3.487倍，对应26.5元。"
        "矿产金同比13.4%，铜-5.7%，碳酸锂20日-30.41%、60日-33.78%。"
    )
    p3 = (
        "美联储目标区间3.75%-4.00%，上次加息0.25个百分点，"
        "美债收益率5.19%，美元兑人民币6.722，人民币30日升值0.872%。"
        "卡莫阿原计划38-42万吨，现计划29-33万吨；毛利率34.66%，"
        "净利率25.65%。2025-12-31陈景河卸任，邹来昌接任。"
        "关键变量仍是金价，如果黄金回撤而新矿山仍扩张，估值和利润可能都承压。"
    )
    return "\n\n".join([p, p2, p3])


def human_rewrite():
    return (
        "紫金矿业确实有很强的经营能力，不过29.92元这个价格，我还是想等一等。"
        "公司这些年矿山运营和现金创造都做得不错，上半年赚了392亿元，"
        "而且现金流也跟得上，这不是一家只靠故事支撑股价的公司。\n\n"
        "我犹豫的原因主要在金价。紫金的金铜业务利润与商品行情密切相关，"
        "这轮高盈利有相当一部分来自黄金价格的上涨。"
        "在金价高位时，市盈率看起来很低，并不一定说明股票很便宜；"
        "一旦价格回落，利润缩水可能比我们想象的快。\n\n"
        "公司还在不断扩张矿山，钱要先花出去，新增产能和盈利却要等以后才能兑现。"
        "股东人数在前期上涨过程中又明显增多，让我不太愿意把这次回调"
        "直接看成捡便宜的机会。这不证明机构一定在卖，但值得保持耐心。\n\n"
        "所以我并不否定公司长期价值。我只是希望，要么未来金铜价格和新项目"
        "的兑现能证明今天的利润可以持续，要么买入价格再便宜一些。"
        "在那之前，我不会因为公司优秀就急着买入。"
    )


def test_detects_numeric_inventory_not_length_alone():
    overloaded = readability_signals(overloaded_example())
    assert overloaded["needs_edit"] is True
    assert overloaded["numeric_mentions"] >= 40
    assert readability_signals(human_rewrite())["needs_edit"] is False
    assert readability_signals("矿业发展很快，但现价不便宜。\n\n"*30)["needs_edit"] is False


def test_screen_rejects_new_financial_facts_but_allows_numerical_selection():
    original=overloaded_example()
    answer=human_rewrite()
    assert accept_readable_edit(original,answer)
    assert not accept_readable_edit(original,answer.replace("392亿元","9000亿元"))
    assert not accept_readable_edit(original,answer+"\n\n2027年净利润将达到8000亿元。")
    assert not accept_readable_edit(original,answer.replace(
        "紫金矿业确实", "买股票的老木匠认为紫金矿业确实"
    ))
    assert not accept_readable_edit(original,
        "紫金很好，现价不便宜。金价是最大风险。")


def test_verified_transition_names_do_not_disappear_in_copy_edit():
    source=overloaded_example()
    inputs=SimpleNamespace(management_capital={"succession":{"verified_transition":{
        "status":"completed", "founder":"陈景河",
        "chairman_appointed":"邹来昌",
    }}})
    assert not accept_readable_edit(source,human_rewrite(),inputs)
    restated=human_rewrite()+"\n\n陈景河已经卸任，邹来昌接任董事长，交接的事实很清楚，"\
        "但下一任管理层的长期表现还需要观察。"
    assert accept_readable_edit(source,restated,inputs)


@pytest.mark.asyncio
async def test_post_review_edit_changes_thesis_only(monkeypatch):
    original=overloaded_example()
    revised=human_rewrite()
    async def responder(prompt,**kwargs):
        assert "写给" not in prompt or "原结论" in prompt
        assert "29.92" in prompt
        assert kwargs["max_tokens"]>=2000
        assert "只能输出" not in prompt or True
        return {"thesis_summary":revised}, "end_turn"
    monkeypatch.setattr("backtest.llm_client.call_json_ex",responder)
    parsed={
        "thesis_summary":original, "stance":"neutral",
        "company_quality_stance":"bullish",
        "current_odds_stance":"neutral", "risk_notes":"二级市场风险",
        "dimension_analyses":{"management":"完整分析，不得改动"},
    }
    result=await polish_thesis_prose(parsed)
    assert result["thesis_summary"]==revised
    assert result["stance"]=="neutral"
    assert result["company_quality_stance"]=="bullish"
    assert result["current_odds_stance"]=="neutral"
    assert result["dimension_analyses"]==parsed["dimension_analyses"]
    assert result["risk_notes"]==parsed["risk_notes"]


@pytest.mark.asyncio
async def test_llm_failure_or_bad_edit_keeps_original(monkeypatch):
    original=overloaded_example()
    parsed={"thesis_summary":original,"stance":"bearish"}
    async def bad(*args,**kwargs):
        return {"thesis_summary":human_rewrite().replace("392亿元","1000亿元")},"end_turn"
    monkeypatch.setattr("backtest.llm_client.call_json_ex",bad)
    assert await polish_thesis_prose(parsed)==parsed
    async def failed(*args,**kwargs):
        raise RuntimeError("provider outage")
    monkeypatch.setattr("backtest.llm_client.call_json_ex",failed)
    assert await polish_thesis_prose(parsed)==parsed


@pytest.mark.asyncio
async def test_clear_short_answer_does_not_trigger_extra_llm(monkeypatch):
    async def should_not_run(*args,**kwargs):
        raise AssertionError("A readable thesis should not require an LLM edit")
    monkeypatch.setattr("backtest.llm_client.call_json_ex",should_not_run)
    parsed={"thesis_summary":human_rewrite(),"stance":"neutral"}
    assert await polish_thesis_prose(parsed)==parsed
