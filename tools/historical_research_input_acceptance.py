# -*- coding: utf-8 -*-
"""
Strict Historical pre-synthesis end-to-end acceptance.

This runner executes the same historical research collection path as generate_report but
replaces only the final LLM synthesis with a deterministic neutral placeholder.  The
placeholder is never persisted in the research-input snapshot.

Purpose:
- prove the complete historical collection/evidence/profile path works without an LLM key;
- verify no live Xueqiu pollution or future evidence leaks;
- freeze replayable research_inputs.json for later model/Prompt evaluation.

It is NOT a substitute for the real LLM Historical E2E.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path
from typing import Dict, List, Tuple

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from analysis import report as report_module
from analysis.research_context import ResearchRequest
from analysis.reviewer import ResearchReview
from analysis.snapshot_store import (
    load_snapshot_manifest,
    load_snapshot_research_inputs,
    save_research_input_snapshot,
    verify_snapshot_integrity,
)
from model.m_analysis import StructuredSummary


DEFAULT_CASES: List[Tuple[str, date, str]] = [
    ("600036", date(2024, 6, 30), "招商银行"),
    ("601088", date(2023, 6, 30), "中国神华"),
    ("600519", date(2022, 12, 30), "贵州茅台"),
]

_DIMENSION_KEYS = [
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


def _parse_case(value: str) -> Tuple[str, date, str]:
    if "@" not in value:
        raise ValueError("historical case 必须是 CODE@YYYY-MM-DD")
    code, raw_date = value.split("@", 1)
    return code.strip(), date.fromisoformat(raw_date.strip()), code.strip()


async def _placeholder_summary(inputs, candidates, evidence):
    analyses = {
        key: "PRE_SYNTHESIS_ACCEPTANCE_PLACEHOLDER：未运行 LLM，不代表投资判断。"
        for key in _DIMENSION_KEYS
    }
    scores = [
        {
            "dimension": key,
            "key": key,
            "score": 0.0,
            "note": "未运行LLM",
        }
        for key in _DIMENSION_KEYS
    ]
    return (
        StructuredSummary(
            lynch_category="unclear",
            stance="neutral",
            company_quality_stance="neutral",
            current_odds_stance="neutral",
            confidence=0.0,
            thesis_summary="仅用于 pre-synthesis 数据链验收；未运行 LLM，不代表投资判断。",
            core_counter_evidence="未运行 LLM；本工具不生成投资结论。",
            invalidation_condition="未运行 LLM；本工具只验证历史输入与证据链。",
            risk_notes="PRE_SYNTHESIS_ACCEPTANCE_PLACEHOLDER",
            dimension_scores=scores,
            dimension_analyses=analyses,
        ),
        ResearchReview(),
    )


def _future_evidence(report) -> List[str]:
    cutoff = date.fromisoformat(report.as_of)
    bad: List[str] = []
    for item in report.evidence:
        for field in ("period", "published_at", "available_at"):
            raw = getattr(item, field, None)
            if not raw:
                continue
            try:
                value = date.fromisoformat(str(raw)[:10])
            except ValueError:
                continue
            if value > cutoff:
                bad.append(f"{item.category}:{field}={value.isoformat()}")
    return bad


async def _run_case(
    code: str,
    as_of: date,
    label: str,
    snapshot_root: Path,
) -> Dict:
    request = ResearchRequest(
        stock_code=code,
        mode="historical",
        as_of=as_of,
        save_snapshot=False,
        snapshot_root=str(snapshot_root),
    )
    failures: List[str] = []
    warnings: List[str] = []

    original = report_module._generate_summary
    report_module._generate_summary = _placeholder_summary
    try:
        report = await report_module.generate_report(code, request=request)
    except Exception as exc:
        return {
            "stock_code": code,
            "label": label,
            "as_of": as_of.isoformat(),
            "ok": False,
            "failures": [
                f"historical pre-synthesis collection failed: "
                f"{type(exc).__name__}: {exc}"
            ],
            "warnings": [],
        }
    finally:
        report_module._generate_summary = original

    if report.research_mode != "historical":
        failures.append(f"research_mode={report.research_mode!r}, expected historical")
    if report.as_of != as_of.isoformat():
        failures.append(f"report.as_of={report.as_of}, expected {as_of.isoformat()}")

    leaked = _future_evidence(report)
    if leaked:
        failures.append("future evidence: " + "; ".join(leaked))

    if report.xueqiu_stock is not None:
        failures.append("historical inputs unexpectedly contain live xueqiu_stock")
    if report.debate is not None:
        failures.append("historical inputs unexpectedly contain live Xueqiu debate")
    if report.sentiment is not None:
        failures.append("historical inputs unexpectedly contain live Xueqiu sentiment")
    if any(row.latest_posts for row in report.candidates):
        failures.append("historical candidates unexpectedly contain current latest_posts")

    try:
        snapshot_path = save_research_input_snapshot(
            report,
            request,
            root=str(snapshot_root),
        )
        integrity = verify_snapshot_integrity(snapshot_path)
        manifest = load_snapshot_manifest(snapshot_path)
        frozen = load_snapshot_research_inputs(snapshot_path)

        if manifest.snapshot_kind != "research_inputs_only":
            failures.append(
                f"snapshot_kind={manifest.snapshot_kind!r}, expected research_inputs_only"
            )
        if manifest.report_sha256:
            failures.append("research-input snapshot unexpectedly has report_sha256")
        if (snapshot_path / "report.json").exists():
            failures.append("research-input snapshot unexpectedly contains report.json")
        if not integrity.get("ok"):
            failures.append(
                "research-input snapshot integrity failed: "
                + "; ".join(integrity.get("errors") or [])
            )
        for forbidden in ("summary", "review", "validation"):
            if forbidden in frozen:
                failures.append(f"frozen research inputs unexpectedly contain {forbidden}")
    except Exception as exc:
        snapshot_path = None
        integrity = None
        manifest = None
        failures.append(
            f"research-input snapshot failed: {type(exc).__name__}: {exc}"
        )

    if report.research_profile.readiness <= 0:
        failures.append(
            "research profile readiness is zero despite completed evidence collection"
        )
    if report.research_quality.coverage < 0.50:
        warnings.append(
            f"low evidence coverage: {report.research_quality.coverage:.0%}"
        )

    return {
        "stock_code": code,
        "stock_name": report.stock_name or label,
        "as_of": report.as_of,
        "evidence_count": len(report.evidence),
        "primary_evidence_count": len(report.primary_evidence),
        "candidate_count": len(report.candidates),
        "knowledge_count": len(report.knowledge_excerpts),
        "evidence_coverage": report.research_quality.coverage,
        "profile": report.research_profile.archetype,
        "profile_readiness": report.research_profile.readiness,
        "missing_dimensions": report.research_quality.missing_dimensions,
        "future_evidence": leaked,
        "snapshot_path": str(snapshot_path) if snapshot_path else "",
        "snapshot_integrity": integrity,
        "snapshot_kind": manifest.snapshot_kind if manifest else "",
        "warnings": warnings,
        "failures": failures,
        "ok": not failures,
    }


async def main_async(
    cases: List[Tuple[str, date, str]],
    snapshot_root: Path,
    output: Path | None,
) -> int:
    results: List[Dict] = []
    for code, as_of, label in cases:
        result = await _run_case(code, as_of, label, snapshot_root)
        results.append(result)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    summary = {
        "cases": len(results),
        "passed": sum(1 for row in results if row.get("ok")),
        "failed": sum(1 for row in results if not row.get("ok")),
        "failed_cases": [
            f"{row.get('stock_code')}@{row.get('as_of')}"
            for row in results
            if not row.get("ok")
        ],
        "results": results,
    }
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    print("\n=== historical research-input acceptance summary ===")
    print(
        json.dumps(
            {k: v for k, v in summary.items() if k != "results"},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if summary["failed"] == 0 else 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("cases", nargs="*", help="CODE@YYYY-MM-DD")
    parser.add_argument(
        "--snapshot-root",
        default="data/investment_input_snapshots",
    )
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    cases = [_parse_case(x) for x in args.cases] if args.cases else DEFAULT_CASES
    output = Path(args.output) if args.output else None
    raise SystemExit(
        asyncio.run(
            main_async(
                cases,
                Path(args.snapshot_root),
                output,
            )
        )
    )


if __name__ == "__main__":
    main()
