from types import SimpleNamespace

from analysis.evidence import build_evidence_ledger, evaluate_research_quality


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
