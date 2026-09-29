from datetime import datetime
from zoneinfo import ZoneInfo

from analysis.evidence import EvidenceItem, ResearchQuality
from analysis.research_context import ResearchRequest
from analysis.research_profile import ResearchProfile
from analysis.reviewer import ResearchReview
from analysis.snapshot_store import (
    load_snapshot_manifest,
    load_snapshot_report,
    load_snapshot_research_inputs,
    save_report_snapshot,
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
