from analysis.knowledge_context import (
    build_knowledge_hypotheses,
    prefilter_knowledge_by_context,
)
from model.m_analysis import KnowledgeExcerpt


def _entry(title: str, text: str, source: str = "xueqiu_3058599833"):
    return KnowledgeExcerpt(
        source=source,
        title=title,
        distilled=text,
        published_at="2026-09-01 10:00:00",
    )


def test_technology_retrieval_keeps_indirect_causal_context_without_stock_name():
    entries = [
        _entry(
            "科技基金抱团",
            "【原则】科技牛市后段要看公募和ETF谁在做边际买家。"
            "【机制】主动基金退出时，被动ETF申购仍可能推高成分股。"
            "【适用条件】高估值科技行情。【失效条件】长期资金同步持续增持。",
        ),
        _entry(
            "AI资本开支",
            "【原则】AI数据中心资本开支如果越来越依赖公司债，需求质量会下降。"
            "【机制】债务融资成本上升会先压制Capex，再传导到算力设备订单。"
            "【适用条件】云厂商大规模扩张期。【失效条件】自由现金流足以覆盖投资。",
        ),
        _entry(
            "技术路线控制",
            "【原则】研发人数多不等于护城河。"
            "【机制】若大客户决定技术路线并扶持二供，供应商主要拥有工程化和认证壁垒。"
            "【适用条件】光模块等大客户集中行业。【失效条件】公司掌握核心IP和标准。",
        ),
        _entry(
            "无关消费",
            "【原则】餐饮翻台率决定门店效率。【机制】客流增加提高收入。",
        ),
    ]

    selected = prefilter_knowledge_by_context(
        entries,
        "300308",
        "中际旭创",
        "通信设备",
        archetype="technology",
        fundamentals={
            "facts": {
                "overseas_revenue_pct": 94.8,
                "top5_customer_pct": 75.9,
                "rd_investment_yuan": 1_000_000_000,
            }
        },
    )

    titles = {x.title for x in selected}
    assert "科技基金抱团" in titles
    assert "AI资本开支" in titles
    assert "技术路线控制" in titles
    assert "无关消费" not in titles


def test_knowledge_hypothesis_is_opinion_plus_verification_not_fact():
    entries = [
        _entry(
            "公司回购与高管减持",
            "【原则】公司回购不能替代内部人真金白银增持。"
            "【机制】回购使用公司资产，而高管减持是个人账户套现。"
            "【适用条件】公司回购与内部人减持同期出现。"
            "【失效条件】回购注销且内部人同步增持。",
        )
    ]

    items = build_knowledge_hypotheses(
        entries,
        "300308",
        "中际旭创",
        "通信设备",
        archetype="technology",
        fundamentals={"facts": {}},
    )

    assert len(items) == 1
    item = items[0]
    assert "内部人利益一致性" in item["topic_labels"]
    assert item["claim"] == "公司回购不能替代内部人真金白银增持。"
    assert "回购使用公司资产" in item["mechanism"]
    assert any("公司回购资金" in q for q in item["verification_questions"])


def test_customer_concentration_activates_dependency_context_for_non_named_post():
    entries = [
        _entry(
            "二供风险",
            "【原则】核心客户扶持第二供应商时，第一供应商历史高增长可能迅速反转。"
            "【机制】客户通过多供降低采购价和单一供应风险。"
            "【适用条件】客户集中度高。【失效条件】替换认证成本极高。",
        )
    ]

    selected = prefilter_knowledge_by_context(
        entries,
        "000001",
        "测试公司",
        "电子",
        archetype="general",
        fundamentals={"facts": {"top5_customer_pct": 65.0}},
    )

    assert [x.title for x in selected] == ["二供风险"]


def test_technology_retrieval_drops_bank_and_commodity_noise():
    entries = [
        _entry(
            "银行不良率",
            "【原则】银行不良率可能低估债权投资风险。"
            "【机制】企业债减值没有全部体现在贷款不良率中。",
        ),
        _entry(
            "铜价关税",
            "【原则】铜价上涨可能来自关税预期而非真实供需。"
            "【机制】投机商囤积库存后遇到政策变化会集中抛售。",
        ),
        _entry(
            "光模块公募抱团",
            "【原则】电子通信和光模块被公募高度集中持有。"
            "【机制】主动基金净流出时，新发科技ETF可能成为边际买家。",
        ),
    ]

    selected = prefilter_knowledge_by_context(
        entries,
        "300308",
        "中际旭创",
        "通信设备",
        archetype="technology",
        fundamentals={
            "facts": {
                "overseas_revenue_pct": 94.8,
                "top5_customer_pct": 75.9,
            }
        },
    )

    titles = [x.title for x in selected]
    assert "光模块公募抱团" in titles
    assert "银行不良率" not in titles
    assert "铜价关税" not in titles


def test_direct_stock_mention_survives_cross_industry_analogy():
    entries = [
        _entry(
            "核心资产轮动",
            "【原则】过去白酒抱团与现在中际旭创等科技核心资产都要警惕估值透支。"
            "【机制】边际资金撤出后高估值核心资产可能经历估值压缩。",
        )
    ]

    selected = prefilter_knowledge_by_context(
        entries,
        "300308",
        "中际旭创",
        "通信设备",
        archetype="technology",
        fundamentals={"facts": {}},
    )

    assert [x.title for x in selected] == ["核心资产轮动"]
