from analysis.evidence import EvidenceItem, ResearchQuality
from analysis.report_validation import validate_report
from analysis.research_profile import ResearchProfile
from analysis.reviewer import ResearchReview
from model.m_analysis import AnalysisReport, StructuredSummary


DIM_KEYS = [
    "management",
    "fundamentals",
    "rd",
    "chip_flow",
    "price_position",
    "cycle_position",
    "policy_geopolitics",
    "retail_sentiment",
    "shareholder_returns",
    "growth_elasticity",
    "a_share_structure",
    "risk_quality",
]

TOOL_KEYS = [
    "analyze_management",
    "analyze_business_fundamentals",
    "analyze_rd_capability",
    "analyze_chip_flow",
    "analyze_price_position",
    "analyze_cycle_position",
    "analyze_policy_geopolitics",
    "analyze_retail_sentiment",
    "analyze_shareholder_returns",
    "analyze_growth_elasticity",
    "analyze_a_share_structure",
    "analyze_risk_quality",
]


def valid_report() -> AnalysisReport:
    return AnalysisReport(
        stock_code="600000",
        stock_name="测试股份",
        primary_evidence=[
            {
                "source_name": "巨潮资讯",
                "source_tier": "S",
                "kind": "fact",
                "title": "2025年年度报告",
            }
        ],
        evidence=[
            EvidenceItem(
                id="e1",
                category="fundamentals",
                label="基本面",
                source="test",
                source_tier="B",
                kind="fact",
                as_of="2026-06-30",
                value={"revenue": 1},
            )
        ],
        research_quality=ResearchQuality(
            coverage=1.0,
            covered_dimensions=12,
            total_dimensions=12,
            high_grade_ratio=1.0,
        ),
        review=ResearchReview(confidence_penalty=0.05),
        research_profile=ResearchProfile(
            archetype="general",
            label="综合型企业",
            readiness=1.0,
        ),
        summary=StructuredSummary(
            stance="neutral",
            company_quality_stance="bullish",
            current_odds_stance="neutral",
            confidence=0.8,
            thesis_summary="十二个方向均已分析。",
            core_counter_evidence="最强反方证据为估值偏高。",
            invalidation_condition="若利润连续两个报告期下降则判断失效。",
            dimension_analyses={k: f"{k} analysis" for k in TOOL_KEYS},
            dimension_scores=[
                {"dimension": k, "score": 0, "note": "neutral"}
                for k in DIM_KEYS
            ],
        ),
    )


def test_valid_report_passes_contract():
    result = validate_report(valid_report())
    assert result.ok
    assert result.errors == []


def test_score_only_provider_refusal_keeps_complete_research_publishable():
    report = valid_report()
    report.summary.dimension_scores = None
    result = validate_report(report)
    assert result.ok
    assert result.errors == []
    assert any(x.code == "dimension_scores_unavailable" for x in result.warnings)
    # No placeholders: a missing chart is preferable to 12 invented zeros.
    assert report.summary.dimension_scores is None


def test_empty_scores_dont_mask_missing_essential_dimension_analysis():
    report = valid_report()
    report.summary.dimension_scores = []
    del report.summary.dimension_analyses["analyze_management"]
    result = validate_report(report)
    assert not result.ok
    assert any(x.code == "missing_dimension_analyses" for x in result.errors)
    assert any(x.code == "dimension_scores_unavailable" for x in result.warnings)



def test_missing_dimension_and_counter_evidence_fail_contract():
    report = valid_report()
    report.summary.dimension_analyses.pop("analyze_rd_capability")
    report.summary.dimension_scores = report.summary.dimension_scores[:-1]
    report.summary.core_counter_evidence = ""

    result = validate_report(report)

    assert not result.ok
    codes = {x.code for x in result.errors}
    assert "missing_dimension_analyses" in codes
    assert "missing_dimension_scores" in codes
    assert "dimension_score_count" in codes
    assert "missing_counter_evidence" in codes


def test_confidence_cannot_exceed_coverage_after_review_penalty():
    report = valid_report()
    report.research_quality.coverage = 0.7
    report.review.confidence_penalty = 0.1
    report.summary.confidence = 0.61

    result = validate_report(report)

    assert not result.ok
    assert any(x.code == "confidence_exceeds_evidence" for x in result.errors)


def test_stale_evidence_is_warning_not_structural_failure():
    report = valid_report()
    report.research_quality.stale_evidence = [
        {
            "category": "margin",
            "label": "融资盘",
            "as_of": "2025-01-01",
            "age_days": 636,
            "max_age_days": 14,
        }
    ]

    result = validate_report(report)

    assert result.ok
    assert any(x.code == "stale_evidence_present" for x in result.warnings)



def test_historical_report_rejects_evidence_available_after_cutoff():
    report = valid_report()
    report.research_mode = "historical"
    report.as_of = "2024-06-30"
    report.evidence[0].period = "2024-03-31"
    report.evidence[0].published_at = "2024-04-25"
    report.evidence[0].available_at = "2024-07-01"

    result = validate_report(report)

    assert not result.ok
    assert any(x.code == "future_evidence" for x in result.errors)


def test_historical_report_allows_later_retrieval_time():
    report = valid_report()
    report.research_mode = "historical"
    report.as_of = "2024-06-30"
    report.evidence[0].period = "2024-03-31"
    report.evidence[0].published_at = "2024-04-25"
    report.evidence[0].available_at = "2024-04-25"
    report.evidence[0].retrieved_at = "2026-09-29T12:00:00+08:00"

    result = validate_report(report)

    assert result.ok
    assert not any(x.code == "future_evidence" for x in result.errors)
