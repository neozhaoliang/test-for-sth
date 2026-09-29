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
from typing import Dict, Tuple

from analysis.evidence import EvidenceItem, ResearchQuality
from analysis.report_inputs import AnalysisInputs
from analysis.report_contract import _PROMPT_VERSION
from analysis.report_synthesis import _generate_summary
from analysis.report_validation import validate_report
from analysis.research_profile import ResearchProfile
from analysis.snapshot_store import (
    load_snapshot_manifest,
    load_snapshot_research_inputs,
    verify_snapshot_integrity,
)
from model.m_analysis import AnalysisReport, CandidateOpinion, KnowledgeExcerpt


def _inputs_from_frozen(payload: Dict) -> Tuple[AnalysisInputs, list[CandidateOpinion], list[EvidenceItem]]:
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
    inputs, candidates, evidence = _inputs_from_frozen(frozen)

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
