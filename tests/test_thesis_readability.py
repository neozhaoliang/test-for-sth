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
        "29.92元的紫金矿业，我暂时不买。上半年有392亿元利润，现金流也很好，"
        "矿山经营的底子没什么可挑剔的。问题是这里面有多少是金价涨出来的。"
        "矿业企业行情好时利润增长很快，按这样的利润去看市盈率，很容易误判价格。\n\n"
        "紫金还在不断建设新矿山，这些投入不是当年花完就有回报。"
        "后面金价、铜价和新增产量怎么走，才是买入时要考虑的账。"
        "现价我不想赌这些事情都顺利，也没必要因为股价从高点回落就急着接。"
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
        "29.92元的紫金矿业", "买股票的老木匠认为29.92元的紫金矿业"
    ))
    assert not accept_readable_edit(original,
        "紫金很好，现价不便宜。金价是最大风险。")


def test_verified_transition_names_may_live_in_management_section_not_thesis():
    source=overloaded_example()
    inputs=SimpleNamespace(management_capital={"succession":{"verified_transition":{
        "status":"completed", "founder":"陈景河",
        "chairman_appointed":"邹来昌",
    }}})
    # The management dimension retains the dated transition; the top-line
    # investment thesis is allowed to focus on prices, profits and capex.
    assert accept_readable_edit(source,human_rewrite(),inputs)
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


def performance_rhetoric_example():
    # A plausible 650-yuan-like thesis, with few numbers but painfully
    # predictable AI stage directions and symmetric rhetorical statements.
    return (
        "29.92元的这只股票，我不怀疑它有利润，我怀疑的是利润能不能变成钱。"
        "上半年392亿元利润，这个数据值得看，但不能就此推断股价便宜。\n\n"
        "我最不满意的是现金流跟利润的关系。管理层有自己的解释，"
        "没有应收和库存细项时这些解释没法全部核验。"
        "这不是经营能力的问题，而是买入价格的问题。\n\n"
        "什么会让我改变看法？未来新项目投产、收入兑现，"
        "再结合价格位置，才可能改变今天的取舍。"
    )


def test_scripted_analyst_performance_triggers_without_numeric_overload():
    report=performance_rhetoric_example()
    score=readability_signals(report)
    assert score["numeric_mentions"] < 13
    assert score["numeric_overload"] is False
    assert score["scripted_tone"] is True
    assert score["scripted_score"] >= 4
    assert score["needs_edit"] is True
    assert readability_signals(human_rewrite())["needs_edit"] is False


def test_edited_prose_must_remove_stilted_rhetoric_without_new_numbers():
    old=performance_rhetoric_example()
    assert accept_readable_edit(old, human_rewrite())
    bad=human_rewrite()+"\n\n我不怀疑这家公司，我怀疑的是估值。"
    assert not accept_readable_edit(old,bad)
    assert not accept_readable_edit(old, human_rewrite().replace(
        "392亿元", "900亿元"
    ))


@pytest.mark.asyncio
async def test_scripted_voice_triggers_polish_without_numeric_dump(monkeypatch):
    old=performance_rhetoric_example()
    captured=[]
    async def edit(prompt,**kwargs):
        captured.append(prompt)
        assert "不演投资大师" in prompt
        assert "不可修改的立场字段" in prompt
        assert "你是投资讨论区里讲道理、说人话的老朋友" not in prompt
        return {"thesis_summary":human_rewrite()}, "end_turn"
    monkeypatch.setattr("backtest.llm_client.call_json_ex",edit)
    before={"thesis_summary":old,"stance":"neutral",
            "company_quality_stance":"bullish",
            "current_odds_stance":"neutral"}
    after=await polish_thesis_prose(before)
    assert len(captured)==1
    assert after["thesis_summary"]==human_rewrite()
    assert after["stance"]==before["stance"]
    assert after["company_quality_stance"]==before["company_quality_stance"]
    assert after["current_odds_stance"]==before["current_odds_stance"]


def test_generic_research_conclusion_is_flagged_for_rewrite():
    scripted = (
        "总体来看，公司经营韧性较强，估值中枢有望改善，"
        "但风险与机遇并存，后续仍需持续关注业绩兑现。"
    )
    score = readability_signals(scripted)
    assert score["scripted_tone"] is True
    assert score["needs_edit"] is True


def test_prose_edit_cannot_erase_critical_fund_and_policy_arguments():
    original = (
        "当前价格需要谨慎。2026年上半年经营活动现金流只有18亿元，"
        "公募基金持股高度集中，北美客户依赖和美国1260H清单需要考虑。"
        "机构抱团抬升估值后，如果新增资金不足，股价可能比利润波动得更剧烈。"
    )
    loses_risks = (
        "公司今年盈利不错，股票现价却已经不便宜。"
        "虽然短期有一些不确定性，但从长期经营看仍然具备增长空间。"
        "按照这个买入价格承担波动，缺少足够安全边际。"
    )
    assert not accept_readable_edit(original, loses_risks)


def test_bad_lead_price_triggers_rewrite_and_verified_quote_can_replace_it():
    from analysis.thesis_readability import _lead_price_conflicts
    inputs = SimpleNamespace(quote={"latest_price": 29.72})
    original = (
        "9.85元买的是3.94倍市净率和一份把上半年利润年化后的约10倍市盈率。"
        "2026年上半年净利润大增，矿产金产量却没跟上，利润主要靠价格上涨。"
        "公司仍在扩张，原材料价格高企，当前估值对应的风险没有降低。"
        "扩张的资本成本和现金回报需要持续观察。"
    )
    revised = (
        "按29.72元这个价格看，紫金矿业不能只拿今年上半年的利润算便宜。"
        "公司净利润涨得快，矿山产量增长却跟不上，金铜涨价贡献不小。"
        "公司还在投钱建新矿，项目要等投产才能看到现金回报。"
        "如果金价回落，今年看着好看的利润就未必能长期维持。"
        "现在按高景气的利润给估值，我会更谨慎。"
    )
    assert _lead_price_conflicts(original, inputs)
    assert not _lead_price_conflicts(revised, inputs)
    assert accept_readable_edit(original, revised, inputs)
    assert not accept_readable_edit(original, original, inputs)


@pytest.mark.asyncio
async def test_very_long_report_is_rewritten_not_force_padded(monkeypatch):
    from analysis.thesis_readability import readability_signals
    original = (
        "9.85元买的是3.94倍市净率，2026年上半年营收只增15.78%，"
        "净利润却增68%，矿产金只增13.4%、矿产铜降了5.7%。"
        "跨周期ROE中位22.58%，正常化盈利收益率5.75%，"
        "在建工程尚未核实。"
    ) * 9
    assert readability_signals(original)["too_long"]
    out = (
        "按29.72元看，紫金今年利润涨得快，金铜价格上涨是重要原因。"
        "矿山产量没有同样快地增长，公司还在建设新的项目。"
        "现在按高景气利润计算市盈率，很容易把周期顶部当便宜。"
        "如果商品价格回落，新增产能却迟迟不能贡献现金流，"
        "这个买价未必有足够安全边际。"
    )
    called = []
    async def responder(prompt, **kwargs):
        called.append(prompt)
        assert "29.72元" in prompt
        assert "不必逐个汇报" in prompt
        assert "允许删掉非决定性的事实" in prompt
        return {"thesis_summary": out}, "end_turn"
    monkeypatch.setattr("backtest.llm_client.call_json_ex", responder)
    before = {
        "thesis_summary": original,
        "stance": "bearish",
        "company_quality_stance": "bullish",
        "current_odds_stance": "bearish",
    }
    after = await polish_thesis_prose(
        before, SimpleNamespace(quote={"latest_price": 29.72})
    )
    assert len(called) == 1
    assert after["thesis_summary"] == out
    assert after["stance"] == "bearish"
    assert after["company_quality_stance"] == "bullish"


def test_verifiable_opening_price_is_corrected_without_changing_other_figures():
    from analysis.thesis_readability import align_verified_lead_price
    before = {
        "thesis_summary": "9.85元买的是3.94倍PB，历史价格25.14元。"
                          "矿山利润不错，但金价如果跌下来，风险会显现。",
        "stance": "bearish",
    }
    inputs = SimpleNamespace(quote={"latest_price": 29.72})
    after = align_verified_lead_price(before, inputs)
    assert after["thesis_summary"].startswith("29.72元买的是3.94倍PB")
    assert "历史价格25.14元" in after["thesis_summary"]
    assert after["stance"] == before["stance"]


def test_quote_guard_does_not_erase_correct_price_or_unverified_history():
    from analysis.thesis_readability import align_verified_lead_price
    current = {"thesis_summary": "29.72元买还要考虑矿业周期。"}
    historical = {"thesis_summary": "2024年股价曾到9.85元。"}
    inp = SimpleNamespace(quote={"latest_price": 29.72})
    assert align_verified_lead_price(current, inp) == current
    assert align_verified_lead_price(historical, inp) == historical
