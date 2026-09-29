# -*- coding: utf-8 -*-
"""
Immutable investment-report snapshots.

A snapshot is not merely a cached report.  It records:
- the research request and effective date;
- the exact report JSON;
- the evidence ledger separately;
- prompt version / git SHA / optional model id;
- source vintages (newest as_of per evidence category);
- SHA-256 hashes for reproducibility.

Historical backtests should consume frozen snapshots or rebuild them only from point-in-time
safe adapters.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from analysis.evidence import EvidenceItem, ResearchQuality
from analysis.report_inputs import AnalysisInputs
from analysis.research_context import ResearchRequest
from analysis.research_profile import ResearchProfile
from model.m_analysis import AnalysisReport, CandidateOpinion, KnowledgeExcerpt


class SnapshotManifest(BaseModel):
    snapshot_id: str
    stock_code: str
    stock_name: str = ""
    mode: str
    as_of: str
    captured_at: int
    prompt_version: str = ""
    git_sha: str = ""
    llm_model: str = ""
    report_sha256: str
    evidence_sha256: str
    request_sha256: str = ""
    research_inputs_sha256: str = ""
    file_sha256: Dict[str, str] = Field(default_factory=dict)
    source_vintages: Dict[str, str] = Field(default_factory=dict)
    stale_categories: List[str] = Field(default_factory=list)
    files: Dict[str, str] = Field(default_factory=dict)


def _json_bytes(value) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _sha256(value) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _research_inputs_payload(report: AnalysisReport) -> Dict:
    """
    Freeze the data that existed *before* final synthesis.

    This intentionally excludes summary/review/validation so a future model or prompt can
    be evaluated on the same frozen research inputs without reusing the old conclusion.
    """
    excluded = {
        "summary",
        "review",
        "validation",
        "prompt_version",
        "generated_at",
    }
    payload = report.model_dump(mode="json")
    return {k: v for k, v in payload.items() if k not in excluded}


def _write_json(path: Path, value) -> str:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_vintages(report: AnalysisReport) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for item in report.evidence:
        value = str(item.available_at or item.as_of or "")
        if not value:
            continue
        current = out.get(item.category)
        if current is None or value > current:
            out[item.category] = value
    return out


def save_report_snapshot(
    report: AnalysisReport,
    request: ResearchRequest,
    *,
    root: Optional[str] = None,
) -> Path:
    base = Path(root or request.snapshot_root)
    as_of = str(request.as_of)
    report_payload = report.model_dump(mode="json")
    evidence_payload = [x.model_dump(mode="json") for x in report.evidence]
    request_payload = request.model_dump(mode="json")
    research_inputs_payload = _research_inputs_payload(report)

    report_hash = _sha256(report_payload)
    evidence_hash = _sha256(evidence_payload)
    request_hash = _sha256(request_payload)
    research_inputs_hash = _sha256(research_inputs_payload)
    snapshot_id = f"{report.stock_code}_{as_of}_{report_hash[:12]}"

    target = base / report.stock_code / as_of / snapshot_id
    target.mkdir(parents=True, exist_ok=True)

    report_path = target / "report.json"
    evidence_path = target / "evidence.json"
    request_path = target / "request.json"
    research_inputs_path = target / "research_inputs.json"
    manifest_path = target / "manifest.json"

    file_sha256 = {
        "report": _write_json(report_path, report_payload),
        "evidence": _write_json(evidence_path, evidence_payload),
        "request": _write_json(request_path, request_payload),
        "research_inputs": _write_json(research_inputs_path, research_inputs_payload),
    }

    manifest = SnapshotManifest(
        snapshot_id=snapshot_id,
        stock_code=report.stock_code,
        stock_name=report.stock_name,
        mode=request.mode.value,
        as_of=as_of,
        captured_at=int(time.time()),
        prompt_version=report.prompt_version,
        git_sha=os.getenv("GITHUB_SHA", ""),
        llm_model=(
            os.getenv("LLM_MODEL")
            or os.getenv("ANTHROPIC_MODEL")
            or os.getenv("OPENAI_MODEL")
            or ""
        ),
        report_sha256=report_hash,
        evidence_sha256=evidence_hash,
        request_sha256=request_hash,
        research_inputs_sha256=research_inputs_hash,
        file_sha256=file_sha256,
        source_vintages=_source_vintages(report),
        stale_categories=[
            str(x.get("category"))
            for x in report.research_quality.stale_evidence
            if x.get("category")
        ],
        files={
            "report": report_path.name,
            "evidence": evidence_path.name,
            "request": request_path.name,
            "research_inputs": research_inputs_path.name,
            "manifest": manifest_path.name,
        },
    )
    manifest_path.write_text(
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Convenience pointer for humans/tools. It contains only the immutable snapshot id.
    latest_path = base / report.stock_code / as_of / "latest.json"
    latest_path.write_text(
        json.dumps(
            {
                "snapshot_id": snapshot_id,
                "path": str(target),
                "report_sha256": report_hash,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return target


def load_snapshot_manifest(path: str | Path) -> SnapshotManifest:
    p = Path(path)
    if p.is_dir():
        p = p / "manifest.json"
    return SnapshotManifest.model_validate_json(p.read_text(encoding="utf-8"))



def frozen_research_inputs_to_analysis_inputs(
    payload: Dict,
) -> tuple[AnalysisInputs, List[CandidateOpinion], List[EvidenceItem]]:
    """Rebuild the pure pre-synthesis input contract from research_inputs.json."""
    candidates = [
        CandidateOpinion.model_validate(x)
        for x in payload.get("candidates") or []
    ]
    evidence = [
        EvidenceItem.model_validate(x)
        for x in payload.get("evidence") or []
    ]
    knowledge = [
        KnowledgeExcerpt.model_validate(x)
        for x in payload.get("knowledge_excerpts") or []
    ]

    inputs = AnalysisInputs(
        stock_code=str(payload.get("stock_code") or ""),
        stock_name=str(payload.get("stock_name") or ""),
        research_mode=str(payload.get("research_mode") or "live"),
        as_of=str(payload.get("as_of") or ""),
        quote=payload.get("realtime_quote") or {},
        knowledge_excerpts=knowledge,
        industry_comparison=payload.get("industry_comparison"),
        shareholder_trend=payload.get("shareholder_trend"),
        dividend_history=payload.get("dividend_history") or [],
        buyback_history=payload.get("buyback_history") or [],
        profitability_trend=payload.get("profitability_trend"),
        commodity_signal=payload.get("commodity_signal"),
        rmb_signal=payload.get("rmb_signal"),
        macro_rates=payload.get("macro_rates"),
        policy_events=payload.get("policy_events"),
        fundamentals=payload.get("fundamentals"),
        valuation=payload.get("valuation"),
        valuation_history=payload.get("valuation_history"),
        rd_team=payload.get("rd_team"),
        xueqiu_stock=payload.get("xueqiu_stock"),
        debate=payload.get("debate"),
        sentiment=payload.get("sentiment"),
        margin_signal=payload.get("margin_signal"),
        market_context=payload.get("market_context"),
        freight_signal=payload.get("freight_signal"),
        primary_evidence=payload.get("primary_evidence") or [],
        a_share_structure=payload.get("a_share_structure"),
        management_capital=payload.get("management_capital"),
        filing_calendar=payload.get("filing_calendar") or [],
        research_profile=ResearchProfile.model_validate(
            payload.get("research_profile") or {}
        ),
        research_quality=ResearchQuality.model_validate(
            payload.get("research_quality") or {}
        ),
    )
    return inputs, candidates, evidence


def load_snapshot_report(path: str | Path) -> AnalysisReport:
    p = Path(path)
    manifest = load_snapshot_manifest(p)
    base = p if p.is_dir() else p.parent
    report_name = manifest.files.get("report", "report.json")
    return AnalysisReport.model_validate_json(
        (base / report_name).read_text(encoding="utf-8")
    )


def load_snapshot_research_inputs(path: str | Path) -> Dict:
    p = Path(path)
    manifest = load_snapshot_manifest(p)
    base = p if p.is_dir() else p.parent
    name = manifest.files.get("research_inputs", "research_inputs.json")
    return json.loads((base / name).read_text(encoding="utf-8"))


def verify_snapshot_integrity(path: str | Path) -> Dict[str, object]:
    """
    Verify both semantic hashes (canonical JSON) and stored file hashes.

    Returns a structured result instead of raising so CI/tools can show exactly which
    artifact was corrupted.
    """
    p = Path(path)
    manifest = load_snapshot_manifest(p)
    base = p if p.is_dir() else p.parent

    checks: Dict[str, bool] = {}
    errors: List[str] = []

    for logical, filename in manifest.files.items():
        if logical == "manifest":
            continue
        file_path = base / filename
        if not file_path.exists():
            checks[logical] = False
            errors.append(f"missing file: {filename}")
            continue

        expected_file_hash = manifest.file_sha256.get(logical)
        if expected_file_hash:
            actual_file_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()
            ok = actual_file_hash == expected_file_hash
            checks[f"{logical}_file"] = ok
            if not ok:
                errors.append(f"file hash mismatch: {filename}")

    semantic_specs = {
        "report": (manifest.report_sha256, manifest.files.get("report", "report.json")),
        "evidence": (manifest.evidence_sha256, manifest.files.get("evidence", "evidence.json")),
        "request": (manifest.request_sha256, manifest.files.get("request", "request.json")),
        "research_inputs": (
            manifest.research_inputs_sha256,
            manifest.files.get("research_inputs", "research_inputs.json"),
        ),
    }
    for logical, (expected, filename) in semantic_specs.items():
        if not expected:
            continue
        file_path = base / filename
        if not file_path.exists():
            continue
        try:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
        except Exception as exc:
            checks[f"{logical}_semantic"] = False
            errors.append(f"invalid json: {filename}: {exc}")
            continue
        ok = _sha256(payload) == expected
        checks[f"{logical}_semantic"] = ok
        if not ok:
            errors.append(f"semantic hash mismatch: {filename}")

    return {
        "ok": not errors and all(checks.values()) if checks else False,
        "snapshot_id": manifest.snapshot_id,
        "checks": checks,
        "errors": errors,
    }
