import asyncio

from analysis.external_synthesis import ExternalSynthesisEnvelope, make_external_synthesis_fn
from analysis.evidence import EvidenceItem, ResearchQuality
from analysis.research_context import ResearchRequest
from analysis.research_profile import ResearchProfile
from analysis.reviewer import ResearchReview
from analysis.snapshot_replay import replay_snapshot
from analysis.snapshot_store import save_research_input_snapshot
from model.m_analysis import AnalysisReport, StructuredSummary


DIMENSIONS = [
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


def _summary(*, confidence=0.8, analyses=None) -> StructuredSummary:
    analyses = analyses or {key: "该维度数据暂缺" for key in DIMENSIONS}
    return StructuredSummary(
        lynch_category="unclear",
        stance="neutral",
        company_quality_stance="neutral",
        current_odds_stance="neutral",
        confidence=confidence,
        thesis_summary="外部模型回放测试",
        core_counter_evidence="测试反方证据 1.0",
        invalidation_condition="若测试条件失效则结论失效",
        risk_notes="",
        dimension_analyses=analyses,
        dimension_scores=[
            {"dimension": key, "key": key, "score": 0, "note": "test"}
            for key in DIMENSIONS
        ],
    )


def _input_report() -> AnalysisReport:
    return AnalysisReport(
        stock_code="600000",
        stock_name="测试股份",
        research_mode="historical",
        as_of="2024-06-30",
        evidence=[
            EvidenceItem(
                id="v1",
                category="valuation",
                label="估值",
                source="test",
                source_tier="B",
                kind="derived",
                as_of="2024-06-28",
                available_at="2024-06-28",
                value={"pe": 10},
            ),
            EvidenceItem(
                id="p1",
                category="primary",
                label="公告",
                source="test",
                source_tier="S",
                kind="fact",
                as_of="2024-06-20",
                published_at="2024-06-20",
                available_at="2024-06-20",
                value={"event": "test"},
            ),
        ],
        research_quality=ResearchQuality(
            coverage=0.75,
            covered_dimensions=9,
            total_dimensions=12,
            high_grade_ratio=0.5,
        ),
        research_profile=ResearchProfile(
            archetype="general",
            label="综合型企业",
            readiness=0.7,
        ),
        review=ResearchReview(),
        summary=_summary(confidence=0.2),
        prompt_version="seed",
        generated_at=1,
    )


def test_external_synthesis_uses_project_deterministic_reviewer():
    envelope = ExternalSynthesisEnvelope(
        provider="test-provider",
        model="test-model",
        prompt_version="external-prompt",
        summary=_summary(
            analyses={
                **{key: "该维度数据暂缺" for key in DIMENSIONS},
                "price_position": "PE 10倍，估值偏低。",
                "growth_elasticity": "PE 10倍，估值偏低。",
            }
        ),
    )
    evidence = [
        EvidenceItem(
            id="v1",
            category="valuation",
            label="估值",
            source="test",
            source_tier="B",
            kind="derived",
            value={"pe": 10},
        )
    ]

    summary, review = asyncio.run(
        make_external_synthesis_fn(envelope)(None, [], evidence)
    )

    assert summary.stance == "neutral"
    assert len(review.duplicate_factors) == 1
    assert review.duplicate_factors[0].factor == "估值/价格"
    assert review.confidence_penalty == 0.015


def test_external_synthesis_replay_records_provenance_and_caps_confidence(tmp_path):
    report = _input_report()
    request = ResearchRequest(
        stock_code="600000",
        mode="historical",
        as_of="2024-06-30",
        snapshot_root=str(tmp_path),
    )
    snapshot = save_research_input_snapshot(report, request)

    envelope = ExternalSynthesisEnvelope(
        provider="openai-chatgpt",
        model="GPT-5.6 Sol",
        prompt_version="external-test-v1",
        summary=_summary(confidence=0.95),
    )

    replayed = asyncio.run(
        replay_snapshot(
            snapshot,
            synthesis_fn=make_external_synthesis_fn(envelope),
            replay_metadata=envelope.replay_metadata(),
        )
    )

    assert replayed.validation["ok"] is True
    assert replayed.summary.confidence == 0.7
    provenance = replayed.validation["replay"]["external_synthesis"]
    assert provenance["provider"] == "openai-chatgpt"
    assert provenance["model"] == "GPT-5.6 Sol"
    assert provenance["prompt_version"] == "external-test-v1"
    assert replayed.validation["replay"]["integrity_verified"] is True
