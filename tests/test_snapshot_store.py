import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from analysis import snapshot_replay
from analysis.evidence import EvidenceItem, ResearchQuality
from analysis.research_context import ResearchRequest
from analysis.research_profile import ResearchProfile
from analysis.reviewer import ResearchReview
from analysis.snapshot_store import (
    frozen_research_inputs_to_analysis_inputs,
    load_snapshot_manifest,
    load_snapshot_report,
    load_snapshot_research_inputs,
    save_report_snapshot,
    save_research_input_snapshot,
    verify_snapshot_integrity,
)
from model.m_analysis import AnalysisReport, StructuredSummary


def _report() -> AnalysisReport:
    return AnalysisReport(
        stock_code="600000",
        stock_name="测试股份",
        research_mode="live",
        as_of=str(datetime.now(ZoneInfo("Asia/Shanghai")).date()),
        evidence=[
            EvidenceItem(
                id="v1",
                category="valuation_history",
                label="历史估值",
                source="test",
                source_tier="B",
                kind="derived",
                as_of="2026-09-28",
                available_at="2026-09-28",
                retrieved_at="2026-09-29T00:00:00+00:00",
                value={"pe_ttm": 12.3},
            ),
            EvidenceItem(
                id="a1",
                category="primary",
                label="公告",
                source="巨潮资讯",
                source_tier="S",
                kind="fact",
                as_of="2026-09-20",
                published_at="2026-09-20",
                available_at="2026-09-20",
                retrieved_at="2026-09-29T00:00:00+00:00",
                value={"category": "权益分派"},
            ),
        ],
        research_quality=ResearchQuality(
            coverage=1.0,
            covered_dimensions=12,
            total_dimensions=12,
            high_grade_ratio=1.0,
        ),
        research_profile=ResearchProfile(archetype="general", label="综合型企业", readiness=1.0),
        review=ResearchReview(),
        summary=StructuredSummary(
            stance="neutral",
            company_quality_stance="neutral",
            current_odds_stance="neutral",
            confidence=0.5,
            thesis_summary="测试",
            core_counter_evidence="测试",
            invalidation_condition="测试",
            dimension_analyses={},
            dimension_scores=[],
        ),
        prompt_version="test-prompt",
        generated_at=1,
    )


def test_snapshot_writes_manifest_hashes_and_source_vintages(tmp_path):
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    request = ResearchRequest(
        stock_code="600000",
        save_snapshot=True,
        snapshot_root=str(tmp_path),
    )
    report = _report()
    report.as_of = str(today)

    path = save_report_snapshot(report, request)
    manifest = load_snapshot_manifest(path)

    assert (path / "report.json").exists()
    assert (path / "evidence.json").exists()
    assert (path / "manifest.json").exists()
    assert (path / "request.json").exists()
    assert (path / "research_inputs.json").exists()
    assert len(manifest.report_sha256) == 64
    assert len(manifest.evidence_sha256) == 64
    assert manifest.prompt_version == "test-prompt"
    assert manifest.source_vintages["valuation_history"] == "2026-09-28"
    assert manifest.source_vintages["primary"] == "2026-09-20"

    second = save_report_snapshot(report, request)
    assert second == path
    assert load_snapshot_manifest(second).report_sha256 == manifest.report_sha256



def test_snapshot_research_inputs_exclude_old_conclusion(tmp_path):
    request = ResearchRequest(
        stock_code="600000",
        save_snapshot=True,
        snapshot_root=str(tmp_path),
    )
    report = _report()
    report.as_of = str(request.as_of)

    path = save_report_snapshot(report, request)
    inputs = load_snapshot_research_inputs(path)

    assert "summary" not in inputs
    assert "review" not in inputs
    assert "validation" not in inputs
    assert inputs["stock_code"] == "600000"
    assert inputs["evidence"][0]["category"] == "valuation_history"

    loaded = load_snapshot_report(path)
    assert loaded.stock_code == report.stock_code
    assert loaded.summary.stance == report.summary.stance


def test_snapshot_integrity_detects_tampering(tmp_path):
    request = ResearchRequest(
        stock_code="600000",
        save_snapshot=True,
        snapshot_root=str(tmp_path),
    )
    report = _report()
    report.as_of = str(request.as_of)
    path = save_report_snapshot(report, request)

    before = verify_snapshot_integrity(path)
    assert before["ok"] is True

    report_path = path / "report.json"
    payload = report_path.read_text(encoding="utf-8")
    report_path.write_text(payload.replace("测试股份", "被篡改股份"), encoding="utf-8")

    after = verify_snapshot_integrity(path)
    assert after["ok"] is False
    assert any("report" in x for x in after["errors"])



def test_frozen_research_inputs_rebuild_analysis_inputs(tmp_path):
    request = ResearchRequest(
        stock_code="600000",
        save_snapshot=True,
        snapshot_root=str(tmp_path),
    )
    report = _report()
    report.as_of = str(request.as_of)
    report.candidates = []
    report.knowledge_excerpts = []

    path = save_report_snapshot(report, request)
    frozen = load_snapshot_research_inputs(path)
    inputs, candidates, evidence = frozen_research_inputs_to_analysis_inputs(frozen)

    assert inputs.stock_code == "600000"
    assert inputs.stock_name == "测试股份"
    assert inputs.research_mode == "live"
    assert inputs.as_of == report.as_of
    assert candidates == []
    assert len(evidence) == 2
    assert evidence[0].category == "valuation_history"


def test_research_input_snapshot_is_replayable_and_has_no_report(tmp_path):
    request = ResearchRequest(
        stock_code="600000",
        save_snapshot=False,
        snapshot_root=str(tmp_path),
    )
    report = _report()
    report.as_of = str(request.as_of)

    path = save_research_input_snapshot(report, request)
    manifest = load_snapshot_manifest(path)

    assert manifest.snapshot_kind == "research_inputs_only"
    assert manifest.report_sha256 == ""
    assert not (path / "report.json").exists()
    assert (path / "research_inputs.json").exists()
    assert (path / "evidence.json").exists()
    assert verify_snapshot_integrity(path)["ok"] is True

    frozen = load_snapshot_research_inputs(path)
    inputs, candidates, evidence = frozen_research_inputs_to_analysis_inputs(frozen)
    assert inputs.stock_code == "600000"
    assert len(evidence) == 2

    second = save_research_input_snapshot(report, request)
    assert second == path
    assert (
        tmp_path / "600000" / str(request.as_of) / "latest-inputs.json"
    ).exists()


def test_replay_accepts_research_input_only_snapshot(tmp_path, monkeypatch):
    request = ResearchRequest(
        stock_code="600000",
        save_snapshot=False,
        snapshot_root=str(tmp_path),
    )
    report = _report()
    report.as_of = str(request.as_of)
    path = save_research_input_snapshot(report, request)

    dimension_keys = [
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

    async def fake_generate_summary(inputs, candidates, evidence):
        return (
            StructuredSummary(
                lynch_category="unclear",
                stance="neutral",
                company_quality_stance="neutral",
                current_odds_stance="neutral",
                confidence=0.5,
                thesis_summary="replay test",
                core_counter_evidence="test counter evidence",
                invalidation_condition="test invalidation",
                risk_notes="",
                dimension_analyses={key: "test" for key in dimension_keys},
                dimension_scores=[
                    {
                        "dimension": key,
                        "key": key,
                        "score": 0,
                        "note": "test",
                    }
                    for key in dimension_keys
                ],
            ),
            ResearchReview(),
        )

    monkeypatch.setattr(snapshot_replay, "_generate_summary", fake_generate_summary)
    replayed = asyncio.run(snapshot_replay.replay_snapshot(path))

    assert replayed.stock_code == "600000"
    assert replayed.summary.stance == "neutral"
    assert replayed.validation["replay"]["snapshot_id"] == load_snapshot_manifest(path).snapshot_id
    assert replayed.validation["replay"]["integrity_verified"] is True
