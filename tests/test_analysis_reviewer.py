from analysis.evidence import EvidenceItem
from analysis.reviewer import review_dimension_analyses


def ev(category: str, tier: str = "B") -> EvidenceItem:
    return EvidenceItem(
        id=f"e-{category}",
        category=category,
        label=category,
        source="test",
        source_tier=tier,
        kind="fact",
    )


def test_duplicate_factor_is_flagged_but_not_treated_as_independent_vote():
    analyses = {
        "analyze_price_position": "当前 PE 35 倍，估值偏高。",
        "analyze_growth_elasticity": "动态 PE 已包含较高增长预期，估值存在透支。",
        "analyze_business_fundamentals": "经营现金流稳定。",
    }
    review = review_dimension_analyses(
        analyses,
        [ev("valuation"), ev("market_context"), ev("profitability")],
    )

    assert any(x.factor == "估值/价格" for x in review.duplicate_factors)
    assert review.confidence_penalty >= 0


def test_opposite_valuation_language_is_surfaced_as_possible_conflict():
    analyses = {
        "analyze_price_position": "静态 PE 位于高位，估值偏高。",
        "analyze_growth_elasticity": "若用明年利润测算，估值偏低。",
    }
    review = review_dimension_analyses(
        analyses,
        [ev("valuation"), ev("market_context"), ev("profitability")],
    )

    assert any(x.factor == "估值判断" for x in review.possible_conflicts)
    assert review.confidence_penalty >= 0.05


def test_directional_claim_without_expected_evidence_is_weak_link():
    analyses = {
        "analyze_rd_capability": "研发强度较高，技术壁垒较强。",
    }
    review = review_dimension_analyses(analyses, [ev("valuation")])

    assert any(x.code == "missing_support" for x in review.weak_links)


def test_primary_evidence_removes_no_primary_source_warning():
    analyses = {
        "analyze_management": "公告显示近年没有异常再融资，治理记录稳定。",
        "analyze_shareholder_returns": "持续现金分红。",
        "analyze_risk_quality": "风险提示公告未出现重大新增事项。",
    }

    without_primary = review_dimension_analyses(
        analyses,
        [ev("governance"), ev("shareholder_return"), ev("fundamentals")],
    )
    with_primary = review_dimension_analyses(
        analyses,
        [ev("governance"), ev("shareholder_return"), ev("fundamentals"), ev("primary", "S")],
    )

    assert any(x.code == "no_primary_source" for x in without_primary.weak_links)
    assert not any(x.code == "no_primary_source" for x in with_primary.weak_links)
