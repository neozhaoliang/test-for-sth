"""Focused regression tests for the mining thesis missing from the Zijin report."""
from types import SimpleNamespace

import pytest

from analysis.resource_mining_thesis import (
    _holder_trajectory,
    is_resource_miner,
    repair_resource_summary,
    resource_miner_prompt_block,
    resource_summary_omissions,
    select_resource_monetary_excerpts,
)


def excerpt(text, published="2025-11-05", author="私人观点"):
    return SimpleNamespace(
        title="行业市场思考",
        distilled=text,
        author=author,
        source="private_source_id",
        source_url="https://private.invalid/post/123",
        published_at=published,
    )


def report(*, historical=False, excerpts=None, with_pairs=True):
    pairs = [
        {"period": "2025-06-30", "holders": 335659, "price": 19.50,
         "holder_published_at": "2025-07-31"},
        {"period": "2026-02-28", "holders": 1108965, "price": 39.55,
         "holder_published_at": "2026-03-31"},
        {"period": "2026-06-30", "holders": 1318365, "price": 25.14,
         "holder_published_at": "2026-08-30"},
    ] if with_pairs else []
    return SimpleNamespace(
        as_of="2026-10-15",
        research_mode="historical" if historical else "live",
        stock_name="紫金矿业",
        stock_code="SH601899",
        research_profile=SimpleNamespace(archetype="cyclical", industry="贵金属"),
        knowledge_excerpts=excerpts if excerpts is not None else [
            excerpt("黄金、有色等大宗商品不仅是供需现象，还受货币政策、实际利率和美元流动性影响。")
        ],
        chip_price_context={"price_holder_series": pairs},
        shareholder_trend={"series": pairs, "as_of": "2026-06-30"},
    )


def test_miner_only_applies_to_metal_resource_operations():
    assert is_resource_miner(archetype="cyclical", industry="贵金属", stock_name="紫金矿业")
    assert not is_resource_miner(archetype="technology", industry="电子", stock_name="科技公司")
    assert not is_resource_miner(archetype="cyclical", industry="黄金珠宝", stock_name="首饰商店")
    assert not is_resource_miner(archetype="cyclical", industry="航运", stock_name="运输公司")


def test_old_monetary_theory_kept_but_future_and_unrelated_discarded():
    entries = [
        excerpt("科技研发和融资是重要问题", "2026-10-14"),
        excerpt("大宗商品的货币现象：有色价格受流动性与美元实际利率影响", "2023-08-19"),
        excerpt("黄金走势跟随美国降息", "2026-11-01"),
    ]
    picked = select_resource_monetary_excerpts(entries, as_of="2026-10-15")
    assert len(picked) == 1
    assert picked[0] is entries[1]


def test_holder_historical_rise_not_erased_by_current_52_week_location():
    ctx = report()
    ctx.chip_price_context["position_52w_pct"] = 26.6
    x = _holder_trajectory(ctx)
    assert x["start_holders"] == 335659
    assert x["end_holders"] == 1318365
    assert x["holder_rise_pct"] > 290
    b = resource_miner_prompt_block(ctx)
    assert "335,659户" in b and "1,318,365户" in b
    assert "美元流动性" in b and "接班" in b
    assert "实际资本开支" in b
    assert "不能据此断言机构派发" in b
    assert "private_source_id" not in b
    assert "私人观点" not in b
    assert "private.invalid" not in b


def test_future_unpublished_holder_observations_excluded_from_historical_view():
    ctx = report(historical=True)
    ctx.chip_price_context["price_holder_series"].append({
        "period": "2026-09-30", "holders": 2500000,
        "holder_published_at": "2026-11-20", "price": 10,
    })
    assert _holder_trajectory(ctx)["end_holders"] == 1318365


def test_no_data_no_fake_holders_or_private_KOL_attribution():
    ctx = report(excerpts=[], with_pairs=False)
    block = resource_miner_prompt_block(ctx)
    assert "未保留可用" in block
    assert "可比历史序列不足" in block
    assert "1,318,365" not in block
    assert "全球已进入统一紧缩周期" in block
    assert resource_summary_omissions("无足够数据判断。", ctx) == ["capex", "succession"]


def test_summary_omission_detects_all_four_missing_risks_without_forcing_stance():
    ctx = report()
    missing = resource_summary_omissions(
        "股价29.38元，经营现金流和分红不错，但估值已经不低。", ctx)
    assert set(missing) == {"monetary", "holders", "capex", "succession"}
    assert resource_summary_omissions(
        "美元与利率变化影响金价。股东户数大增且筹码分散，"
        "矿山扩张的投资回报存疑，管理层接班尚待观察。", ctx) == []


@pytest.mark.asyncio
async def test_repair_revision_preserves_model_stance_and_does_not_inject_people(monkeypatch):
    async def fake_llm(prompt, **kwargs):
        assert "1,318,365户" in prompt
        assert "货币与商品" in prompt
        return {"thesis_summary": (
            "当前价我不愿追，但公司利润和现金流仍有真实基础。"
            "黄金与铜价还受美元流动性及实际利率驱动，不能把商品上涨等同于公司产量成长。"
            "股东户数持续增至131.84万，历史上涨阶段筹码分散明显，"
            "回撤后的低位表象不能抹掉高位交易拥挤。"
            "矿山扩张的资本开支回报需要穿越低金价情景检验；"
            "创始人接班与下一代管理层的独立经营能力仍待证实。"
            "我认可企业质量，但这个价位上亏损风险与上涨空间并不对称。"
        )}, "end_turn"
    monkeypatch.setattr("backtest.llm_client.call_json_ex", fake_llm)
    draft = {"thesis_summary": "财务好，赔率一般", "stance": "neutral"}
    new = await repair_resource_summary(draft, report())
    assert new["stance"] == "neutral"
    assert new["thesis_summary"] != draft["thesis_summary"]
    assert "private_source_id" not in new["thesis_summary"]


@pytest.mark.asyncio
async def test_repair_rejects_leaking_original_author(monkeypatch):
    async def fake_llm(prompt, **kwargs):
        return {"thesis_summary": (
            "买股票的老木匠指出：美元利率决定商品价格，股东户数多，"
            "矿山扩张成本高，创始人接班风险增加，"
            "我们应该谨慎对待这些判断并在财报里检验事实。"
        )}, "end_turn"
    monkeypatch.setattr("backtest.llm_client.call_json_ex", fake_llm)
    draft = {"thesis_summary": "维持原判断", "stance": "bearish"}
    assert await repair_resource_summary(draft, report()) == draft


@pytest.mark.asyncio
async def test_resource_repair_accepts_long_explanatory_prose_above_old_500_char_limit(monkeypatch):
    paragraphs = [
        "我认可紫金矿业的矿山经营能力，但这个价格对应的买入赔率不够有吸引力。"
        "公司质量和买入价格是两个问题，利润增长也需要结合所处的金铜周期评价。",
        "黄金与铜属于美元报价的大宗商品，美元实际利率和流动性变化可能影响价格。"
        "人民币升值还会改变美元价格折成人民币后的金额，但美元标价上涨可能抵消这一影响。"
        "因此，不能简单把美国利率变动直接等同于公司的净利润变动。",
        "在前期股价高位时，股东户数明显增加，随后回撤仍然维持较高水平。"
        "这提示市场承接者可能更加分散，但不是机构已经集中派发的证据。",
        "矿山扩张需要资本开支，现金流会先流出，回报可能经过多年才体现。"
        "还需要观察新矿山的投产时间、成本和投资回报率能否经受商品下跌。",
        "创始人的接班与团队独立经营能力尚待正式资料验证，这是需要持续跟踪的风险假设。"
        "如果新增项目的现金流兑现改善，并且估值留出更大的安全边际，我会重新评价。",
    ]
    long_form = "\n\n".join(paragraphs) + "\n\n" + "\n\n".join(paragraphs)
    assert len(long_form) > 500

    async def fake_llm(prompt, **kwargs):
        assert "自然段" in prompt
        assert kwargs["max_tokens"] >= 2500
        return {"thesis_summary": long_form}, "end_turn"

    monkeypatch.setattr("backtest.llm_client.call_json_ex", fake_llm)
    previous = {"thesis_summary": "财务不错，但赔率一般。", "stance": "neutral",
                "company_quality_stance": "bullish", "current_odds_stance": "neutral"}
    repaired = await repair_resource_summary(previous, report())
    assert repaired["thesis_summary"] == long_form
    assert repaired["stance"] == "neutral"
    assert repaired["company_quality_stance"] == "bullish"
    assert repaired["current_odds_stance"] == "neutral"
