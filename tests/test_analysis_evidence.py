from types import SimpleNamespace

from analysis.evidence import EvidenceItem, build_evidence_ledger, evaluate_research_quality


def _inputs(**kwargs):
    base = dict(
        quote={},
        fundamentals=None,
        valuation=None,
        profitability_trend=None,
        market_context=None,
        industry_comparison=None,
        commodity_signal=None,
        freight_signal=None,
        shareholder_trend=None,
        dividend_history=[],
        buyback_history=[],
        refinancing_history=[],
        executive_profile=None,
        governance_alerts=[],
        major_events=[],
        margin_signal=None,
        rmb_signal=None,
        xueqiu_stock=None,
        sentiment=None,
        debate=None,
        knowledge_excerpts=[],
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_sparse_inputs_report_missing_dimensions():
    evidence = build_evidence_ledger(
        _inputs(
            quote={"latest_price": 10.0},
            sentiment={"bullish": 4, "bearish": 2},
        )
    )
    quality = evaluate_research_quality(evidence)

    assert quality.coverage < 0.5
    assert "管理层与治理" in quality.missing_dimensions
    assert quality.warnings


def test_rich_inputs_have_high_coverage_and_keep_social_as_c_tier():
    evidence = build_evidence_ledger(
        _inputs(
            quote={"latest_price": 10.0},
            fundamentals={
                "facts": {
                    "rd_investment_yuan": 1_000_000_000,
                    "rd_intensity_pct": 5.2,
                    "patents_granted": 100,
                    "patents_invention": 60,
                }
            },
            valuation={"pe_ttm": 18.0, "valuation_as_of": "2026-09-29"},
            profitability_trend={"periods": [{"period": "2026H1", "roe_pct": 12.0}]},
            market_context={"stock": {"ytd_pct": 8.0}},
            industry_comparison={"industry_name": "测试行业", "advancing": 10, "declining": 5},
            freight_signal={"latest": 2000},
            shareholder_trend={"latest_count": 10000, "as_of": "2026-09-20"},
            dividend_history=[{"announce_date": "2026-05-01", "dividend_per_10_shares": 5}],
            refinancing_history=[{"announce_date": "2025-01-01", "kind": "定增"}],
            executive_profile={"chairman": "张三"},
            governance_alerts=[{"date": "2025-03-01", "kind": "问询"}],
            margin_signal={"latest_balance_yi": 10.0},
            rmb_signal={"rmb_trend": "stable"},
            xueqiu_stock={"org_holding": [{"name": "某机构", "ratio": 1.2}]},
            sentiment={"bullish": 20, "bearish": 10},
            debate={"bull": {"count": 20}, "bear": {"count": 10}},
            knowledge_excerpts=[
                {"source": "xueqiu_4780688814", "title": "测试", "distilled": "【原则】测试"}
            ],
        )
    )
    quality = evaluate_research_quality(evidence)

    assert quality.coverage >= 0.9
    social = [e for e in evidence if e.category in {"sentiment", "debate", "knowledge", "institutional"}]
    assert social
    assert all(e.source_tier == "C" for e in social)
    assert any(e.category == "rd" for e in evidence)


def test_evidence_ids_are_stable_for_same_payload():
    inputs = _inputs(quote={"latest_price": 12.34, "change_pct": 1.2})
    first = build_evidence_ledger(inputs)
    second = build_evidence_ledger(inputs)
    assert [e.id for e in first] == [e.id for e in second]


def test_stale_time_sensitive_evidence_no_longer_counts_as_current_coverage():
    from datetime import date

    evidence = [
        EvidenceItem(
            id="old-val",
            category="valuation_history",
            label="历史估值",
            source="test",
            source_tier="B",
            kind="derived",
            as_of="2025-01-01",
            value={"pe": 10},
        ),
        EvidenceItem(
            id="fund",
            category="fundamentals",
            label="基本面",
            source="test",
            source_tier="B",
            kind="fact",
            as_of="2026-06-30",
            value={"revenue": 1},
        ),
    ]
    q = evaluate_research_quality(evidence, today=date(2026, 9, 29))

    assert any(x["category"] == "valuation_history" for x in q.stale_evidence)
    assert "价格与估值位置" in q.missing_dimensions
    assert any("新鲜度" in x for x in q.warnings)


def test_newer_same_category_snapshot_prevents_false_stale_flag():
    from datetime import date

    evidence = [
        EvidenceItem(
            id="old-margin",
            category="margin",
            label="融资盘旧快照",
            source="test",
            source_tier="B",
            kind="derived",
            as_of="2026-01-01",
            value={"balance": 1},
        ),
        EvidenceItem(
            id="new-margin",
            category="margin",
            label="融资盘新快照",
            source="test",
            source_tier="B",
            kind="derived",
            as_of="2026-09-25",
            value={"balance": 2},
        ),
    ]
    q = evaluate_research_quality(evidence, today=date(2026, 9, 29))

    assert not any(x["category"] == "margin" for x in q.stale_evidence)
