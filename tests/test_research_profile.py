from analysis.evidence import EvidenceItem
from analysis.research_profile import classify_research_profile


def ev(category: str) -> EvidenceItem:
    return EvidenceItem(
        id=f"e-{category}",
        category=category,
        label=category,
        source="test",
        source_tier="B",
        kind="fact",
    )


def fundamentals(industry: str, rd_intensity=None):
    facts = {"sw_industry": industry}
    if rd_intensity is not None:
        facts["rd_intensity_pct"] = rd_intensity
    return {"facts": facts}


def test_technology_profile_requires_rd_and_valuation_evidence():
    profile = classify_research_profile(
        fundamentals=fundamentals("半导体", 8.2),
        evidence=[
            ev("rd"),
            ev("rd_team"),
            ev("fundamentals"),
            ev("profitability"),
            ev("valuation_history"),
        ],
    )
    assert profile.archetype == "technology"
    assert profile.readiness == 1.0
    assert "研发、人才与技术护城河" in profile.priority_dimensions


def test_high_rd_intensity_can_trigger_technology_profile_even_for_generic_industry():
    profile = classify_research_profile(
        fundamentals=fundamentals("其他制造", 5.0),
        evidence=[],
    )
    assert profile.archetype == "technology"
    assert profile.readiness == 0.0
    assert "rd_team" in profile.missing_priority_evidence


def test_cyclical_profile_prioritizes_cycle_not_static_pe():
    profile = classify_research_profile(
        fundamentals=fundamentals("煤炭开采"),
        evidence=[ev("cycle_signal"), ev("industry"), ev("valuation_history")],
    )
    assert profile.archetype == "cyclical"
    assert profile.priority_dimensions[0] == "行业周期位置"
    assert any("低PE" in rule for rule in profile.analysis_rules)


def test_financial_profile_prioritizes_risk_and_capital_return():
    profile = classify_research_profile(
        fundamentals=fundamentals("股份制银行"),
        evidence=[ev("fundamentals"), ev("profitability"), ev("macro_rates")],
    )
    assert profile.archetype == "financial"
    assert profile.priority_dimensions[0] == "财务质量与尾部风险"
    assert "management_capital" in profile.missing_priority_evidence


def test_consumer_brand_profile_is_not_misclassified_as_generic():
    profile = classify_research_profile(
        fundamentals=fundamentals("白酒Ⅱ"),
        evidence=[ev("fundamentals"), ev("valuation_history"), ev("profitability")],
    )
    assert profile.archetype == "consumer_brand"
    assert "经营基本面与护城河" in profile.priority_dimensions


def test_stable_yield_profile_uses_long_term_dividend_record_as_rationale():
    profile = classify_research_profile(
        fundamentals=fundamentals("电力"),
        management_capital={"five_year": {"cash_dividend_years": 5}},
        evidence=[
            ev("management_capital"),
            ev("shareholder_return"),
            ev("profitability"),
            ev("valuation_history"),
            ev("macro_rates"),
        ],
    )
    assert profile.archetype == "stable_yield"
    assert profile.readiness == 1.0
    assert any("分红" in x for x in profile.rationale)
