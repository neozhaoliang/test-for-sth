"""Copyediting should remove canned tone without rewriting stock facts."""
from types import SimpleNamespace

import pytest

from analysis.dimension_readability import (
    accept_dimension_edit, polish_dimension_prose,
)


def source():
    return (
        "总体来看，公司营收417.78亿元，归母利润136.51亿元，"
        "海外收入占比94.82%，经营韧性仍需关注。"
        "美国国防部1260H名单事件需要重点跟踪。"
        "企业业务表现亮眼，但仍需关注订单和现金流兑现。"
    )


def natural():
    return (
        "公司营收417.78亿元，归母利润136.51亿元，"
        "但海外收入占了94.82%。美国国防部1260H名单已经是具体事件，"
        "订单会不会受影响，要看客户后面的采购安排。"
        "利润和现金流之间的差距也要继续核对。"
    )


def test_dimension_edit_preserves_facts_and_improves_tone():
    assert accept_dimension_edit(source(), natural())
    assert not accept_dimension_edit(
        source(), natural().replace("94.82%", "94.82亿元")
    )
    assert not accept_dimension_edit(
        source(), natural().replace("1260H", "某")
    )
    assert not accept_dimension_edit(
        source(), source()
    )


def test_dimension_edit_rejects_loss_of_crowding_and_cash_collection():
    original = (
        "总体来看，公募基金抱团比较明显，经营活动现金流没有跟上利润。"
        "需要强调的是，这两件事会影响当前的投资赔率。"
    )
    assert not accept_dimension_edit(
        original,
        "市场资金比较集中，后续还需跟踪。"
    )


@pytest.mark.asyncio
async def test_dimension_polish_only_updates_safe_sections(monkeypatch):
    original = source()
    edits = []
    async def fake_llm(prompt, **kwargs):
        edits.append(prompt)
        assert "只输出" in kwargs["repair_requirements"]
        assert "正常投资者讨论" in prompt
        return {"risk_quality": natural()}, "end_turn"
    monkeypatch.setattr("backtest.llm_client.call_json_ex", fake_llm)
    analyses = {
        "risk_quality": original,
        "management": "董事会已完成换届。人员任免在公告中写得很清楚。",
    }
    revised = await polish_dimension_prose(analyses)
    assert revised["risk_quality"] == natural()
    assert revised["management"] == analyses["management"]
    assert analyses["risk_quality"] == original
    assert len(edits) == 1


@pytest.mark.asyncio
async def test_dimension_polish_skips_already_plain_text(monkeypatch):
    async def must_not_call(*args, **kwargs):
        raise AssertionError("No edit needed")
    monkeypatch.setattr("backtest.llm_client.call_json_ex", must_not_call)
    text = "这家公司的钱主要来自已签订的长期合同，合同结算按月进行。"
    assert await polish_dimension_prose({"fundamentals": text}) == {
        "fundamentals": text
    }


def test_numeric_density_can_be_split_without_dropping_data():
    old = (
        "财务报告显示营业收入100亿元、净利润20亿元、经营现金流15亿元、"
        "应收账款30亿元、存货25亿元、总负债40亿元、营业成本60亿元、"
        "资本开支8亿元、研发支出3亿元、现金分红2亿元，"
        "企业已连续多个季度维持较高的收入和净利润增长率，"
        "但经营现金流仍显著落后于净利润，"
        "在这种情况下只用利润增速作为投资依据会忽略回款与扩产占款。"
    )
    edited = (
        "公司收入100亿元，净利润20亿元，经营现金流15亿元。"
        "应收账款30亿元、存货25亿元，总负债40亿元。"
        "营业成本60亿元，资本开支8亿元、研发支出3亿元，现金分红2亿元。"
        "报表上的收入和利润涨得快，可经营现金流没跟上。"
        "要看应收账款、存货和扩产占用的资金怎样回流，"
        "只看利润增长还不够。"
    )
    assert accept_dimension_edit(old, edited)
