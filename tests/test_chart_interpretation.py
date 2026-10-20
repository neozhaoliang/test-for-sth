"""Charts must explain causes and investment consequences, not score raw values.

Fixtures are synthetic and source-privacy tests intentionally include
private-looking bylines and copied clauses.
"""
from types import SimpleNamespace

import pytest

from analysis.chart_interpretation import (
    chart_evidence, build_chart_interpretations,
    validate_chart_interpretations,
)


def _inputs():
    months = [
        {"date": f"2026-0{i}-30", "price": 30+i, "pe_ttm": 12-i/2, "pb": 3.5}
        for i in range(1, 7)
    ]
    return SimpleNamespace(
        stock_code="SH601899",
        stock_name="紫金矿业",
        research_mode="live",
        research_profile=SimpleNamespace(archetype="cyclical", industry="有色金属"),
        profitability_trend={"periods": [
            {"period": "2024-12-31", "roe_pct": 14.0,
             "gross_margin_pct": 21.0, "net_margin_pct": 11.0},
            {"period": "2025-12-31", "roe_pct": 23.0,
             "gross_margin_pct": 32.0, "net_margin_pct": 20.0},
            {"period": "2026-06-30", "roe_pct": 17.0,
             "gross_margin_pct": 35.0, "net_margin_pct": 22.0},
        ]},
        valuation_history={
            "as_of": "2026-10-20", "current_pe_ttm": 11.7,
            "current_pb": 3.9,
            "pe_percentiles": [
                {"years": 5, "percentile": 5.2, "median": 20.0}
            ],
            "pb_percentiles": [
                {"years": 5, "percentile": 63.6, "median": 3.2}
            ],
            "history_monthly": months,
        },
        industry_comparison=None, sentiment=None,
        valuation_model_result={
            "style_context": {
                "causal_drivers": [{
                    "mechanism":"在高估值成长股获利盘增加的阶段，配置需求可能更多关注现金派息。",
                    "alternative":"行业盈利预期调整也可能造成相对涨跌，而不是机构直接换仓。",
                }],
            },
            "macro_context": {
                "observations": [
                    {"metric":"fed_target_upper_pct", "value":4.0,
                     "as_of":"2026-09-16","usable":True}
                ]
            },
        },
        knowledge_excerpts=[
            SimpleNamespace(
                author="买股票的老木匠",
                source="bili_laomujiang",
                distilled="【机制】金价冲顶时利润暴涨可能令滚动PE变低，"
                          "但黄金和矿山资本开支的周期性仍需要重新估值。",
            ),
        ],
    )


def test_chart_evidence_is_exact_and_has_no_fake_missing_metrics():
    data=chart_evidence(_inputs())
    assert set(data)=={"profitability","valuation_history"}
    assert data["valuation_history"]["pe_percentiles"][0]["percentile"] == 5.2
    assert data["profitability"]["periods"][-1]["period"] == "2026-06-30"
    missing=SimpleNamespace(profitability_trend={},valuation_history={},
                            sentiment=None,industry_comparison=None)
    assert chart_evidence(missing)=={}


def test_validation_rejects_low_pe_bargain_and_private_source_copying():
    inp=_inputs()
    available=chart_evidence(inp)
    data={
        "valuation_history": "当前低PE因此说明明显低估，可以放心投资。这就是买入这家矿业公司的充足安全边际。",
        "profitability": "买股票的老木匠说行情总在高位，企业净利润就一定继续暴涨，这显然是个不该直接转述的私人观点。",
    }
    assert validate_chart_interpretations(
        data,available,archetype="cyclical",
        private_excerpts=inp.knowledge_excerpts
    )=={}


@pytest.mark.asyncio
async def test_real_knowledge_methods_reach_graph_explainer_and_numbers_are_not_rewritten(monkeypatch):
    inputs=_inputs()
    prompts=[]
    reply={
        "valuation_history": (
            "这张市盈率走势图正好提醒人别把便宜和安全混为一谈。"
            "矿业价格景气时利润可能跑在前面，市盈率反而显得很低。"
            "如果金价转弱又碰上新矿建设支出，盈利和估值都可能回落。"
            "还需要核对不同金铜价格下的正常利润。"
        ),
        "profitability": (
            "毛利率变好并不等于矿山成本永久下降。"
            "对金铜业务而言，大宗商品涨价也会提高利润率，"
            "要把产量增加与商品价格上涨拆开来看，才能判断利润能保持多久。"
        ),
        "market_breadth": "没有这张图的数据却试图分析全市场机构都已从科技转向红利，这种断言完全没有依据。",
    }

    async def mocked_llm(prompt, **kwargs):
        prompts.append(prompt)
        return reply, "end_turn"

    monkeypatch.setattr("backtest.llm_client.call_json_ex",mocked_llm)
    research={
        "price_position":"虽然估值倍数很低，但同期金价上涨带来高利润，PB比自身历史中位不低。",
        "cycle_position":"矿山还在投资扩产，金铜价格和盈利周期均会影响下半年利润。",
        "fundamentals":"收入增长与利润增长并不同步。",
    }
    methods=[{
        "insight":"大宗商品涨价会先抬高利润分母，导致账面市盈率越涨越低。",
        "use_when":"矿价和利润处在周期上部时检验。",
        "possible_mistake":"新增项目产能足以抵消产品降价冲击。",
    }]
    result=await build_chart_interpretations(inputs,research,methods)
    assert set(result)=={"profitability","valuation_history"}
    assert "利润" in result["valuation_history"]
    assert "毛利率" in result["profitability"]
    assert len(prompts)==1
    assert methods[0]["insight"] in prompts[0]
    assert "5.2" in prompts[0]
    assert "63.6" in prompts[0]
    assert "fed_target_upper_pct" in prompts[0]
    assert "市场风格" in prompts[0]
    assert "买股票的老木匠" not in prompts[0]
    assert "bili_laomujiang" not in prompts[0]
    assert "market_breadth" not in result
    for content in result.values():
        assert not any(digit.isdigit() for digit in content)


@pytest.mark.asyncio
async def test_chart_interpretation_missing_data_never_calls_model(monkeypatch):
    async def should_not_run(*args, **kwargs):
        raise AssertionError("No graph observations")
    monkeypatch.setattr("backtest.llm_client.call_json_ex",should_not_run)
    inputs=SimpleNamespace(profitability_trend={},valuation_history={},
                           industry_comparison=None,sentiment=None)
    assert await build_chart_interpretations(inputs,{})=={}


def test_public_projection_anonymizes_graph_commentary():
    from analysis.public_report import public_stock_report
    raw={
        "stock_code":"SH601899","stock_name":"紫金矿业",
        "knowledge_excerpts":[{
            "author":"某私人讲述者",
            "distilled":"原始资料里的整段观点完全不能原样转给网页上的普通读者。"
        }],
        "summary":{
            "chart_interpretations":{
                "valuation_history":"某私人讲述者说现在可以买入，原来的依据不可以直接复制到公开的研究报告。",
                "profitability":"商品涨价能抬高毛利率，但持续多久还要核对未来产量和成本。"
            },
        },
    }
    public=public_stock_report(raw)
    text=str(public)
    assert "某私人讲述者" not in text
    assert "原始资料里的整段观点" not in text
    assert "商品涨价" in public["summary"]["chart_interpretations"]["profitability"]
