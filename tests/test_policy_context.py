from analysis.policy_context import build_policy_topics, filter_policy_event_clues


def test_domestic_consumer_does_not_inherit_war_or_trade_topics_without_exposure():
    topics = build_policy_topics(
        archetype="consumer_brand",
        industry="白酒",
        overseas_revenue_pct=1.0,
    )
    assert "consumption_policy" in topics
    assert "rates_liquidity" in topics
    assert "war_shipping" not in topics
    assert "trade_sanctions" not in topics
    assert "fx" not in topics


def test_technology_and_overseas_exposure_enable_trade_fx_topics():
    topics = build_policy_topics(
        archetype="technology",
        industry="半导体",
        overseas_revenue_pct=35.0,
    )
    assert "technology_policy" in topics
    assert "trade_sanctions" in topics
    assert "fx" in topics


def test_cyclical_route_enables_geopolitical_and_product_topics():
    topics = build_policy_topics(
        archetype="cyclical",
        industry="油气开采",
        commodity_route="crude_oil",
        overseas_revenue_pct=0.0,
    )
    assert "war_shipping" in topics
    assert "commodity_crude_oil" in topics
    assert "OPEC" in topics["commodity_crude_oil"]


def test_event_filter_keeps_only_company_or_exposure_relevant_items():
    topics = build_policy_topics(
        archetype="technology",
        industry="半导体",
        overseas_revenue_pct=20.0,
    )
    rows = [
        {
            "source_type": "global_flash",
            "title": "美国扩大先进芯片出口管制",
            "summary": "涉及半导体设备和先进制程",
            "published_at": "2026-09-29T09:00:00+08:00",
            "media": "测试媒体",
            "url": "https://example.com/1",
        },
        {
            "source_type": "global_flash",
            "title": "某地旅游景区迎来客流高峰",
            "summary": "与科技产业无关",
            "published_at": "2026-09-29T08:00:00+08:00",
            "media": "测试媒体",
            "url": "https://example.com/2",
        },
        {
            "source_type": "company_news",
            "title": "中芯国际发布业务进展",
            "summary": "公司相关消息",
            "published_at": "2026-09-29T07:00:00+08:00",
            "media": "测试媒体",
            "url": "https://example.com/3",
        },
    ]
    out = filter_policy_event_clues(
        rows,
        topics=topics,
        company_terms=("中芯国际", "688981"),
    )

    titles = [x["title"] for x in out]
    assert "美国扩大先进芯片出口管制" in titles
    assert "中芯国际发布业务进展" in titles
    assert "某地旅游景区迎来客流高峰" not in titles
    export = next(x for x in out if "出口管制" in x["title"])
    assert "trade_sanctions" in export["matched_topics"]
