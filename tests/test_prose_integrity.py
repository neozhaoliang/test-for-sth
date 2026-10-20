"""An LLM must not publish a duplicated, cut-off tail of an investment report."""
from types import SimpleNamespace

import pytest

from analysis.prose_integrity import (
    repeated_trailing_excerpt_start,
    remove_repeated_trailing_excerpt,
    has_malformed_prose,
)
from analysis.dimension_readability import accept_dimension_edit, polish_dimension_prose
from analysis.thesis_readability import accept_readable_edit, polish_thesis_prose
from analysis.public_report import public_stock_report


GOOD = (
    "筹码的位置不太好。股东户数从3月末的7.68万户涨到6月末的21.16万户，"
    "同期股价从34.15元涨到53.01元，7月1日见顶55.85元；"
    "到8月末户数回落至16.45万户、股价40.54元，而现在是29.53元。"
    "十几万个账户的成本在40元以上，股价已经跌破他们，8月以后没有新数据，"
    "这批持仓还在头上压着。"
    "融资余额38.95亿元、占流通市值4.89%，近6个交易日降了3.43%，"
    "属于温和去杠杆，不构成拥挤；公募基金占流通股3.93%、"
    "二季度持股数还减了1.28%，跌下来的过程中没有看到增量资金接力。"
)
BROKEN = (
    GOOD + "\n\n"
    "，股价已经跌破他们，8月以后没有新数据，这批持仓还在头上压着。"
    "融资余额38.95亿元、占流通市值4.89%，近6个交易日降了3.43%，属于温和"
)


def test_real_repeated_fragment_is_removed_without_changing_good_prose():
    index = repeated_trailing_excerpt_start(BROKEN)
    assert index == len(GOOD)
    assert remove_repeated_trailing_excerpt(BROKEN) == GOOD
    assert repeated_trailing_excerpt_start(GOOD) is None
    assert not has_malformed_prose(GOOD)
    assert has_malformed_prose(BROKEN)
    assert "跌下来的过程中没有看到增量资金接力。" in (
        remove_repeated_trailing_excerpt(BROKEN)
    )


def test_detects_broken_ending_without_deleting_nonduplicated_facts():
    clipped = (
        "公司上半年项目投产，收入和利润同比增长，但现金回款速度明显放缓。"
        "公司的应收账款可能要等下一季度才能收回，投资者还需要继续观察。"
        "融资余额暂时没有新的公布信息。公司的盈利变化属于温和"
    )
    assert has_malformed_prose(clipped)
    assert remove_repeated_trailing_excerpt(clipped) == clipped


def test_normal_repeated_financial_concepts_are_not_erased():
    text = (
        "公司的现金流较差，但不代表利润造假。利润和现金流需要分别分析。"
        "企业有大量应收账款，未来仍然需要观察现金流和利润的对应关系。"
    )
    assert remove_repeated_trailing_excerpt(text) == text
    assert not has_malformed_prose(text)


def test_dimension_edit_rejects_duplicate_tail_despite_matching_numeric_sets():
    assert not accept_dimension_edit(GOOD, BROKEN)
    assert not accept_dimension_edit(GOOD, GOOD + "融资余额依然")
    assert remove_repeated_trailing_excerpt(BROKEN) == GOOD


@pytest.mark.asyncio
async def test_dimension_pipeline_cleans_proven_duplicate_before_any_optional_llm(monkeypatch):
    import backtest.llm_client
    async def no_llm(*args, **kwargs):
        raise AssertionError("Exact duplicated copy needs no extra LLM call")
    monkeypatch.setattr(backtest.llm_client, "call_json_ex", no_llm)
    fixed = await polish_dimension_prose({"chip_flow": BROKEN})
    assert fixed["chip_flow"] == GOOD


@pytest.mark.asyncio
async def test_dimension_polish_rejects_llm_edit_that_adds_repeated_tail(monkeypatch):
    import backtest.llm_client
    old = ("总体来看，需要强调的是，现金流质量没有跟上收入变化。"
           "公司连续三个季度继续增加存货，未来回款情况需要关注。"
           "这项经营风险没有证据证明会自动消失。")
    calls = []
    edit = ("现金流落后于收入，存货增加会占用资金。"
            "未来回款情况还没有完全确认，公司的实际现金流需要继续核对。"
            "\n\n，存货增加会占用资金。未来回款情况还没有完全确认，公司的实际现金流")
    async def fake_llm(*args, **kwargs):
        calls.append(1)
        return {"chip_flow": edit}, "end_turn"
    monkeypatch.setattr(backtest.llm_client, "call_json_ex", fake_llm)
    result = await polish_dimension_prose({"chip_flow": old})
    assert calls == [1], "the broken LLM edit must actually be evaluated"
    assert result["chip_flow"] == old


def test_public_projection_sanitizes_legacy_cached_report_without_regeneration():
    raw = {
        "stock_code": "SZ000001", "stock_name": "测试公司",
        "summary": {
            "thesis_summary": BROKEN,
            "dimension_analyses": {"chip_flow": BROKEN},
        },
    }
    public = public_stock_report(raw)
    assert public["summary"]["thesis_summary"] == GOOD
    assert public["summary"]["dimension_analyses"]["chip_flow"] == GOOD
    # Never mutate the cached private research in place.
    assert raw["summary"]["thesis_summary"] == BROKEN


def test_thesis_polish_safely_deletes_only_confirmed_duplicate_tail():
    original = (
        "当前股价没有给多少安全边际。公司盈利在商品价格高位阶段可能显得格外好看。"
        + GOOD + "\n\n，股价已经跌破他们，8月以后没有新数据，这批持仓还在头上压着。"
        "融资余额38.95亿元、占流通市值4.89%，近6个交易日降了3.43%，属于温和"
    )
    assert has_malformed_prose(original)
    expected = remove_repeated_trailing_excerpt(original)
    assert not has_malformed_prose(expected)
    assert not accept_readable_edit(expected, original)
