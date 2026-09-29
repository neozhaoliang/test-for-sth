# -*- coding: utf-8 -*-
"""
Replay a frozen research snapshot with the current synthesis stack.

Important:
- no public-data adapter is called;
- no Xueqiu/Bilibili/network research source is refreshed;
- the frozen candidates/KOL/evidence/company data are reused exactly as captured;
- only LLM synthesis/reviewer/scoring and final validation are rerun.

This is for prompt/model A/B evaluation.  It does NOT transform a live snapshot into a
historical point-in-time snapshot.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Dict

from analysis.evidence import ResearchQuality
from analysis.report_contract import _PROMPT_VERSION
from analysis.report_synthesis import _generate_summary
from analysis.report_validation import validate_report
from analysis.research_profile import ResearchProfile
from analysis.snapshot_store import (
    frozen_research_inputs_to_analysis_inputs,
    load_snapshot_manifest,
    load_snapshot_research_inputs,
    verify_snapshot_integrity,
)
from model.m_analysis import AnalysisReport


async def replay_snapshot(
    path: str | Path,
    *,
    require_integrity: bool = True,
) -> AnalysisReport:
    path = Path(path)
    integrity = verify_snapshot_integrity(path)
    if require_integrity and not integrity.get("ok"):
        raise ValueError(
            "snapshot integrity verification failed: "
            + "; ".join(integrity.get("errors") or [])
        )

    manifest = load_snapshot_manifest(path)
    frozen = load_snapshot_research_inputs(path)
    inputs, candidates, evidence = frozen_research_inputs_to_analysis_inputs(frozen)

    summary, review = await _generate_summary(inputs, candidates, evidence)

    quality = inputs.research_quality or ResearchQuality()
    profile = inputs.research_profile or ResearchProfile()
    if summary.confidence:
        summary.confidence = round(
            max(
                0.0,
                min(summary.confidence, quality.coverage, profile.readiness)
                - review.confidence_penalty,
            ),
            3,
        )

    report_payload = dict(frozen)
    report_payload.update(
        {
            "summary": summary.model_dump(mode="json"),
            "review": review.model_dump(mode="json"),
            "validation": None,
            "prompt_version": _PROMPT_VERSION,
            "generated_at": int(time.time()),
        }
    )
    report = AnalysisReport.model_validate(report_payload)

    validation = validate_report(report)
    report.validation = validation.model_dump(mode="json")
    if not validation.ok:
        raise RuntimeError(
            "replayed report contract failed: "
            + "；".join(x.message for x in validation.errors)
        )

    # Keep explicit provenance that this conclusion came from a frozen input pack.
    report.validation.setdefault("replay", {})
    report.validation["replay"] = {
        "snapshot_id": manifest.snapshot_id,
        "snapshot_as_of": manifest.as_of,
        "snapshot_mode": manifest.mode,
        "source_prompt_version": manifest.prompt_version,
        "replay_prompt_version": _PROMPT_VERSION,
        "integrity_verified": bool(integrity.get("ok")),
    }
    return report
