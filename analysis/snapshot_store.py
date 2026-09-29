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

from analysis.research_context import ResearchRequest
from model.m_analysis import AnalysisReport


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

    report_hash = _sha256(report_payload)
    evidence_hash = _sha256(evidence_payload)
    snapshot_id = f"{report.stock_code}_{as_of}_{report_hash[:12]}"

    target = base / report.stock_code / as_of / snapshot_id
    target.mkdir(parents=True, exist_ok=True)

    report_path = target / "report.json"
    evidence_path = target / "evidence.json"
    manifest_path = target / "manifest.json"

    report_path.write_text(
        json.dumps(report_payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    evidence_path.write_text(
        json.dumps(evidence_payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

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
        source_vintages=_source_vintages(report),
        stale_categories=[
            str(x.get("category"))
            for x in report.research_quality.stale_evidence
            if x.get("category")
        ],
        files={
            "report": report_path.name,
            "evidence": evidence_path.name,
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
