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
    # Long prose is allowed, but a paragraph pasted thirty times is now
    # rightly treated as duplication instead of innocuous length.
    assert readability_signals("矿业发展很快，但现价不便宜。\n\n"*30)["needs_edit"] is True
    varied = "\n\n".join(
        f"研究公司未来的{topic}，需要看看经营事实和相应的投入回报。"
        for topic in (
            "矿石品位", "运输费用", "能源成本", "管理效率", "采矿许可",
            "项目建设", "现金分红", "客户信用", "设备折旧", "矿权收购",
            "经营现金流", "资本开支", "铜矿产能", "黄金销售", "税费负担",
            "流动资金", "存货变化", "财务杠杆", "资金成本", "工程进度",
            "汇率变动", "安全生产", "供应链质量", "合同执行", "成本控制",
            "技术改造", "环保投入", "汇兑收益", "利润分配", "风险管理",
        )
    )
    assert len(varied) > 850
    assert readability_signals(varied)["needs_edit"] is False


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
        "这个价格未必有足够安全边际。"
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


def test_reader_voice_contract_is_first_and_has_no_word_quota():
    from analysis.report_contract import _PROMPT_TEMPLATE, _SUBMIT_REPORT_TOOL
    assert _PROMPT_TEMPLATE.startswith("【先遵守：面向用户的中文表达")
    assert "没有固定字数、段落数、数字数量" in _PROMPT_TEMPLATE[:1300]
    assert "不设字数" in _SUBMIT_REPORT_TOOL["description"]
    assert "投资总结不需要交代看多多少条" in _PROMPT_TEMPLATE
    assert "不要求投资总结复述票数" in _PROMPT_TEMPLATE


def long_natural_example():
    return "\n\n".join([
        "29.92元的紫金矿业，我暂时不买。上半年有392亿元利润，经营现金流555亿元，现金回笼情况不错。矿山经营确实赚到了钱，不过矿产金只增长13.4%，矿产铜还下降了5.7%，这些增长背后商品价格贡献有多大，不能只看利润同比就下结论。",
        "金价上涨时，矿山企业的毛利率很容易改善。今年上半年毛利率到了34.66%，利润数字自然比往年漂亮。市场通常会拿最新利润计算市盈率，11.76倍似乎便宜，但这需要金价长期维持高位。如果下一阶段黄金价格回落，利润下降以后，同一股价下的市盈率会重新升高。买股票时需要想的是几年能赚多少钱，而不只是这一年。",
        "这家公司已经不是经营质量需要怀疑的阶段。资产负债率49.55%，现金流还高于账面利润，说明当期赚到的钱并非都停留在应收账款上。财务报表里能确认的经营能力可以认可，但企业质量并不能自动证明现在的股价合适。利润越处在景气高位，越需要认真考虑下一次价格下降会发生什么。",
        "扩建新矿山也有两面性。行情好的时候投资，矿山投产后能增加产量，企业可能继续增长。但项目从建设到产生现金回报往往需要几年，建设花钱时的金属价格与投产时可能完全不同。如果在成本高的时候投入大量资金，等新产能出来恰好遇上商品价格回落，新增利润可能不及当年的设想。具体要看建设支出的进度和未来矿山的实际产量。",
        "资本市场并不一定按当期利润决定股票价格。市场愿意给一个高市净率，隐含了对未来回报率的信心。过去公司的利润和分红一直改善，确实能支撑一部分溢价，不过给得太高就要求后面越来越顺利。投资者买的是以后持续分配给股东的现金，不能只把今年的盈利增速往后延伸。",
        "还有管理层交接。陈景河卸任，邹来昌接任，这件事已经完成，不能把它写成没人知道未来由谁管理。更实际的问题是以后收购矿山、建设大型项目时，新班子能否保持过去的资本配置水平。这个问题需要几年经营记录才能看出来，现阶段没有必要仅凭交接就认定经营会变差。",
        "所以按29.92元，我愿意承认紫金的经营能力和长期资源禀赋，也不着急按照最好年份的利润去买。现在决定投资回报的仍是金价、铜价、项目投入和未来可持续盈利。如果价格出现更充分的回撤，或者正常年份的利润水平有了更可靠的依据，再谈安全边际会更有意义。"
    ])

def test_long_natural_investment_discussion_has_no_850_character_ceiling():
    from analysis.thesis_readability import _numbers
    original = overloaded_example()
    revised = long_natural_example()
    assert len(revised) > 850
    assert len(_numbers(revised)) > 8
    assert accept_readable_edit(
        original, revised, SimpleNamespace(quote={"latest_price": 29.92})
    )


@pytest.mark.asyncio
async def test_rewriter_does_not_force_200_to_500_characters(monkeypatch):
    original = overloaded_example()
    revised = long_natural_example()
    received = []
    async def responder(prompt, **kwargs):
        received.append(prompt)
        assert "通常写200—500字" not in prompt
        assert "最多选约三到六个" not in prompt
        assert "没有固定字数" in prompt
        assert kwargs["max_tokens"] >= 4000
        return {"thesis_summary": revised}, "end_turn"
    monkeypatch.setattr("backtest.llm_client.call_json_ex", responder)
    source = {
        "thesis_summary": original, "stance": "neutral",
        "company_quality_stance": "bullish",
        "current_odds_stance": "neutral",
    }
    edited = await polish_thesis_prose(
        source, SimpleNamespace(quote={"latest_price": 29.92})
    )
    assert len(received) == 1
    assert edited["thesis_summary"] == revised
    assert edited["stance"] == source["stance"]


def zhongji_dense_sample():
    # Regression adapted from an actual investor-facing report. It was
    # technically data-rich but painful to read, and contained a wrong total.
    return "\n\n".join([
        "660.7元的中际旭创，最该先回答的不是收入还能跑多快，而是两件事："
        "赚到的利润能不能变成现金，以及45.84%的毛利率是不是这一轮扩产周期的高点。"
        "1.6T硅光规模放量，在多家头部云厂商保持供应位置；"
        "毛利率从2019年的27.91%抬到2026年上半年的45.84%，"
        "净利率35.27%，ROE34.25%。但这些优势更像工程化交付和短缺期的定价权，"
        "不是底层标准制定权；供应商又在扩产，毛利率往哪走比收入增长更重要。",
        "现金这一关更刺眼。2026年上半年净利润136.51亿元，"
        "经营现金只收回18.00亿元，利润只有13.2%变成现金；"
        "同期总资产增52.23%、总负债增82.00%，7月底通过H股"
        "募到约528.91亿港元净额。具体是应收账款还是存货占了大头没有资料拆清；"
        "下半年现金能不能回来，是判断这批利润质量的关键。"
        "TTM市盈率38倍、按上半年年化约29倍，股价从1382.33元回撤51.5%，"
        "PB募资后约8.5倍、ROE30%以上。",
        "还有两条会直接改变买入决定。控股股东以521.73元均价减持550万股，"
        "常务副总裁以414.24元减持70.86万股，苏州益兴福减持463.41万股，"
        "三笔已实施合计620.86万股、卖方拿到约31.63亿元，进的是个人账户；"
        "公司9月回购49.97亿元用于激励并不注销。"
        "2026年6月10日被列入美国国防部1260H名单，"
        "94.82%的海外收入和前五大客户75.98%的集中度带来采购风险；"
        "融资余额6天从313.6亿元降至261.47亿元，"
        "6月末股东户数205678户、环比增长33.2%，当时股价1270元。"
    ])


def zhongji_readable_sample():
    return "\n\n".join([
        "中际旭创的产品现在卖得很好，新一代光模块也已经开始批量交付。"
        "公司在生产、交货和客户认证上有积累。不过，行业内的竞争对手也在扩产，"
        "目前45.84%的毛利率能保持多久，暂时不能只靠今年的订单来判断。",
        "财报里还有一项需要弄清楚。上半年公司赚了136.51亿元利润，"
        "经营现金流却只有18亿元。这种差距有可能跟备货、客户账期或扩产有关，"
        "但单凭这两个数还不能知道钱具体压在哪里。"
        "想用现在的利润给股票估值，最好先把应收、存货和后续回款查明白。",
        "在660.7元这个价格上，投资者实际上需要相信利润还能维持在较高水平。"
        "如果行业供应增加、产品售价下降，估值和盈利都可能受到影响。"
        "它是有竞争力的供应商，但现在买入也承担了较高的盈利预期。",
        "海外客户是另一件需要注意的事。公司已进入美国国防部1260H名单，"
        "这不代表已经遭到全面商业禁售。"
        "可是海外收入占比高，如果今后采购政策进一步收紧，订单确实会受到影响。"
        "控股股东已经有减持记录，具体减持数量和股价需要看公告逐笔核对，"
        "不能靠未经核实的合计金额作判断。"
    ])


def test_zhongji_evidence_dump_triggers_rewrite():
    from analysis.thesis_readability import inconsistent_share_sale_totals
    raw = zhongji_dense_sample()
    signals = readability_signals(raw)
    assert signals["needs_edit"]
    assert signals["dense_sentences"] >= 2
    assert signals["packed_paragraphs"] >= 1
    errors = inconsistent_share_sale_totals(raw)
    assert len(errors) == 1
    assert str(errors[0]["stated"]) == "620.86"
    assert str(errors[0]["expected"]) == "1084.27"
    # A correct explicit three-trade subtotal must not be flagged.
    valid = raw.replace("三笔已实施合计620.86万股", "三笔已实施合计1084.27万股")
    assert inconsistent_share_sale_totals(valid) == []


def test_zhongji_natural_rewrite_is_accepted_and_preserves_key_risks():
    original = zhongji_dense_sample()
    revised = zhongji_readable_sample()
    assert accept_readable_edit(
        original, revised, SimpleNamespace(quote={"latest_price": 660.7})
    )
    assert not accept_readable_edit(original, original)


def test_inconsistent_share_sale_total_does_not_reach_readers():
    from analysis.thesis_readability import suppress_invalid_share_sale_totals
    original = zhongji_dense_sample()
    result = suppress_invalid_share_sale_totals({"thesis_summary": original})
    fixed = result["thesis_summary"]
    assert "620.86万股" not in fixed
    assert "卖方拿到约31.63亿元" not in fixed
    assert "减持550万股" in fixed
    assert "减持70.86万股" in fixed
    assert "减持463.41万股" in fixed


@pytest.mark.asyncio
async def test_second_edit_attempt_can_repair_first_dense_rewrite(monkeypatch):
    original = zhongji_dense_sample()
    revised = zhongji_readable_sample()
    prompts = []
    async def fake_llm(prompt, **kwargs):
        prompts.append(prompt)
        return {"thesis_summary": original if len(prompts) == 1 else revised}, "end_turn"
    monkeypatch.setattr("backtest.llm_client.call_json_ex", fake_llm)
    result = await polish_thesis_prose(
        {"thesis_summary": original, "stance": "bearish"},
        SimpleNamespace(quote={"latest_price": 660.7}),
    )
    assert len(prompts) == 2
    assert "刚才的改写未通过" in prompts[1]
    assert result["thesis_summary"] == revised
