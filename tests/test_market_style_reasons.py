"""Actual market rotation driver extraction from private knowledge must be safe.

The tests use invented discussions; no private author text, account or link
appears in snapshots, tool prompts displayed in UI, or API responses.
"""
from datetime import date
from types import SimpleNamespace

import pytest

from analysis.market_style_reasons import (
    _safe_fallback, _select_sources, build_market_style_reasons,
    market_style_reasons_prompt_block,
)
from analysis.public_report import public_stock_report


def entry(body, *, published="2026-10-07", source="bili_laomujiang_transcript", title="市场观察"):
    return SimpleNamespace(
        title=title, distilled=body,
        source=source, author="买股票的老木匠",
        published_at=published, source_url="https://private.example/123",
    )


def data():
    return [
        entry("近期高位成长科技股出现获利盘，部分公募基金可能选择调仓"
              "把仓位转到高股息红利领域，投资者需要检查ETF的真实净申购与持仓变化。",
              source="bili_laomujiang_transcript"),
        entry("国债利率走低会改变险资投资的收益要求，相比债券收益率，"
              "可持续分红资产的股息收益率形成一定相对吸引力。",
              source="xueqiu_source_a", published="2026-08-02"),
        entry("成长股高估值时预期过高，现金流未兑现的融资扩张给原股东带来稀释风险，"
              "资金可能因此重新选择关注真实分红的企业。",
              published="2026-09-20", source="xueqiu_source_a"),
    ]


def test_ranked_sources_keep_live_transcript_and_filter_future_dates():
    rows=data()+[
        entry("红利相对成长变化分析，因为基金调仓而选择新的资产。",
              published="2026-10-20"),
        entry("今天去公园走走，没有投资观点", published="2026-10-05"),
    ]
    selected=_select_sources(rows, date(2026,10,13))
    assert len(selected)==3
    assert any("transcript" in x.source for x in selected)
    assert all(x.published_at <= "2026-10-13" for x in selected)


@pytest.mark.asyncio
async def test_live_distillation_keeps_distinct_actual_causal_arguments(monkeypatch):
    prompts=[]
    fake=[
        {
            "insight":"高估值科技股票出现获利盘之后，资金可能转向派息可见度更高的红利资产。",
            "use_when":"在科技预期已经很高、市场风险偏好下降且指数相对收益扩大的阶段适用。",
            "possible_mistake":"若科技盈利真正超预期且红利派息下降，资金可能再次返回成长板块。",
            "company_question":"核对双方基金份额与主动仓位变化，并区分盈利修正和估值变动。",
        },
        {
            "insight":"当长期债券收益率下降，保险等配置机构可能将稳定股息资产作为替代选择。",
            "use_when":"必须观察到相应利率变化，且派息现金流具有较高可持续性的条件下。",
            "possible_mistake":"股息不是确定利息，周期利润下降可能使高股息预期被误判。",
            "company_question":"核对国债收益率的同期数据及保险资金持仓，测算自由现金流覆盖派息。",
        },
    ]
    async def fake_call(prompt, **kwargs):
        prompts.append(prompt)
        return fake, "end_turn"
    monkeypatch.setattr("backtest.llm_client.call_json_ex",fake_call)
    out=await build_market_style_reasons(data(),as_of="2026-10-13")
    assert out["status"]=="distilled_hypotheses"
    assert len(out["drivers"])==2
    assert out["private_transcript_count"]==1
    assert "获利盘" in out["drivers"][0]["mechanism"]
    assert "债券收益率" in out["drivers"][1]["mechanism"]
    assert "资金" in market_style_reasons_prompt_block(out)
    assert len(prompts)==1
    for banned in ("买股票的老木匠","private.example","bili_laomujiang_transcript"):
        assert banned not in prompts[0]
        assert banned not in str(out)


@pytest.mark.asyncio
async def test_invalid_generated_quote_falls_back_to_evidence_linked_methods(monkeypatch):
    async def unsafe(prompt, **kwargs):
        return [{
            "insight":"买股票的老木匠说某个账号一定正在卖出科技买入红利",
            "use_when":"发生科技到红利的风格切换和资金调仓的时候使用。",
            "possible_mistake":"原帖作者的话可能不对需要结合市场客观数据。",
            "company_question":"核验基金与ETF份额以及公司具体财务数据。",
        }], "end_turn"
    monkeypatch.setattr("backtest.llm_client.call_json_ex",unsafe)
    out=await build_market_style_reasons(data(),as_of="2026-10-13")
    assert out["status"]=="keyword_grounded_fallback"
    assert out["drivers"]
    assert all("买股票的老木匠" not in str(x) for x in out["drivers"])
    assert any("科技" in x["mechanism"] for x in out["drivers"])
    assert any("国债" in x["mechanism"] for x in out["drivers"])


@pytest.mark.asyncio
async def test_no_matching_knowledge_never_creates_unsupported_explanation(monkeypatch):
    async def never(*args, **kwargs):
        raise AssertionError("No source should not call LLM")
    monkeypatch.setattr("backtest.llm_client.call_json_ex",never)
    out=await build_market_style_reasons(
        [entry("买菜和做饭，今天的天气还不错并且跑步了。")],
        as_of="2026-10-13")
    assert out["status"]=="no_matching_distilled_knowledge"
    assert out["drivers"]==[]
    assert "不能" in market_style_reasons_prompt_block(out)


@pytest.mark.asyncio
async def test_future_private_knowledge_never_leaks_into_history(monkeypatch):
    async def never(*args, **kwargs):
        raise AssertionError("Future source was not filtered")
    monkeypatch.setattr("backtest.llm_client.call_json_ex",never)
    out=await build_market_style_reasons(
        [entry("未来才知道的红利基金资金切换与股价波动原因。",
               published="2026-10-14")],
        as_of="2026-10-12",
    )
    assert out["drivers"]==[]


def test_public_api_preserves_abstract_why_but_discards_source_material():
    raw={
        "stock_code":"SH600938","stock_name":"中国海油",
        "knowledge_excerpts":[{"author":"买股票的老木匠",
                               "distilled":"投资观点研究私人原文不能作为公开报告内容",
                               "source_url":"https://private.example/no"}],
        "valuation_model":{"style_context":{
            "status":"observed","style_regime":"dividend_leading",
            "causal_knowledge_status":"distilled_hypotheses",
            "causal_drivers":[{
                "mechanism":"当高估值成长股票重新定价时，部分配置资金可能转向现金回报较稳定的红利板块。",
                "conditions":"当成长股票已经经历高涨幅、红利企业的派息现金流仍可持续时考虑。",
                "alternative":"科技行业订单超预期也可能令成长资产重新上涨，并非直接换仓。",
                "verification":"核对有日期的基金份额、价格回报与公司真正现金分红的覆盖情况。",
                "source_url":"https://private.example/some-post",
                "author":"买股票的老木匠",
            }],
            "private_excerpt_count":5,"private_transcript_count":2,
            "investment_principles":[{"id":"private"}],
        }},
    }
    public=public_stock_report(raw)
    style=public["valuation_model"]["style_context"]
    assert len(style["causal_drivers"])==1
    assert "成长股票" in style["causal_drivers"][0]["mechanism"]
    assert style["causal_drivers"][0]["evidence_status"]=="private_research_hypothesis"
    assert set(style["causal_drivers"][0])=={
        "mechanism","conditions","alternative","verification","evidence_status"
    }
    for secret in ("private.example","买股票的老木匠","private_excerpt_count",
                   "private_transcript_count","investment_principles"):
        assert secret not in str(public)


def test_public_api_rejects_named_source_in_market_driver():
    data={
        "stock_code":"SH600938","stock_name":"中国海油",
        "knowledge_excerpts":[{"author":"买股票的老木匠",
                               "distilled":"老木匠本人会说这段话是内部资料"}],
        "valuation_model":{"style_context":{
            "causal_drivers":[{
                "mechanism":"买股票的老木匠认为全部机构已经转向红利股票。",
                "conditions":"红利股价格较高的时候考虑其持股风险。",
                "alternative":"长期业绩改善也可以使红利上涨。",
                "verification":"查询全市场成交和保险资金持股。",
            }]
        }},
    }
    result=public_stock_report(data)
    assert result["valuation_model"]["style_context"]["causal_drivers"]==[]
