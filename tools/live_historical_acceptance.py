# -*- coding: utf-8 -*-
"""
Manual historical end-to-end acceptance runner.

This exercises the strict point-in-time path:
- historical quote / valuation / market / margin / rates;
- exact CNINFO filing versions for financials, ownership and shareholder counts;
- as-of-filtered announcements and KOL/candidate records;
- NO live Xueqiu discussion/current latest-post fallback;
- 12-dimension synthesis + deterministic report validation;
- optional frozen snapshot persisted for replay.

This is intentionally manual because it requires live public network access and configured
LLM credentials. It does not require a live Xueqiu browser session in historical mode.

Examples:
    python tools/live_historical_acceptance.py
    python tools/live_historical_acceptance.py 600036@2024-06-30 601088@2023-06-30
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date
from pathlib import Path
from typing import Dict, List, Tuple

from analysis.report import generate_report
from analysis.research_context import ResearchRequest
from analysis.report_validation import validate_report
from analysis.snapshot_store import verify_snapshot_integrity


DEFAULT_CASES: List[Tuple[str, date, str]] = [
    ("600036", date(2024, 6, 30), "招商银行"),
    ("601088", date(2023, 6, 30), "中国神华"),
    ("600519", date(2022, 12, 30), "贵州茅台"),
]


def _parse_case(value: str) -> Tuple[str, date, str]:
    if "@" not in value:
        raise ValueError("historical case 必须是 CODE@YYYY-MM-DD")
    code, raw_date = value.split("@", 1)
    return code.strip(), date.fromisoformat(raw_date.strip()), code.strip()


def _future_evidence(report) -> List[str]:
    cutoff = date.fromisoformat(report.as_of)
    bad: List[str] = []
    for e in report.evidence:
        for field in ("period", "published_at", "available_at"):
            raw = getattr(e, field, None)
            if not raw:
                continue
            try:
                d = date.fromisoformat(str(raw)[:10])
            except ValueError:
                continue
            if d > cutoff:
                bad.append(f"{e.category}:{field}={d.isoformat()}")
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
        save_snapshot=True,
        snapshot_root=str(snapshot_root),
    )
    failures: List[str] = []
    warnings: List[str] = []

    try:
        report = await generate_report(code, request=request)
    except Exception as e:
        return {
            "stock_code": code,
            "label": label,
            "as_of": as_of.isoformat(),
            "ok": False,
            "failures": [f"generate_report failed: {type(e).__name__}: {e}"],
            "warnings": [],
        }

    validation = validate_report(report)
    if not validation.ok:
        failures.extend(x.message for x in validation.errors)
    warnings.extend(x.message for x in validation.warnings)

    if report.research_mode != "historical":
        failures.append(f"research_mode={report.research_mode!r}, expected historical")
    if report.as_of != as_of.isoformat():
        failures.append(f"report.as_of={report.as_of}, expected {as_of.isoformat()}")

    leaked = _future_evidence(report)
    if leaked:
        failures.append("future evidence: " + "; ".join(leaked))

    if report.xueqiu_stock is not None:
        failures.append("historical report unexpectedly contains live xueqiu_stock")
    if report.debate is not None:
        failures.append("historical report unexpectedly contains live Xueqiu debate")
    if report.sentiment is not None:
        failures.append("historical report unexpectedly contains live Xueqiu sentiment")
    if any(c.latest_posts for c in report.candidates):
        failures.append("historical candidates unexpectedly contain current latest_posts")

    analyses = report.summary.dimension_analyses or {}
    if len(analyses) != 12:
        failures.append(f"dimension analyses={len(analyses)}, expected 12")
    if len(report.summary.dimension_scores or []) != 12:
        failures.append(
            f"dimension scores={len(report.summary.dimension_scores or [])}, expected 12"
        )

    # generate_report saves snapshot when requested. Locate the newest matching directory
    # only for an integrity sanity check; do not mutate it.
    snapshot_candidates = sorted(
        snapshot_root.glob(f"{code}/{as_of.isoformat()}/*"),
        key=lambda p: p.stat().st_mtime if p.exists() else 0,
        reverse=True,
    )
    snapshot_integrity = None
    if snapshot_candidates:
        snapshot_integrity = verify_snapshot_integrity(snapshot_candidates[0])
        if not snapshot_integrity.get("ok"):
            failures.append(
                "snapshot integrity failed: "
                + "; ".join(snapshot_integrity.get("errors") or [])
            )
    else:
        warnings.append("snapshot directory not found after save_snapshot=True")

    return {
        "stock_code": code,
        "stock_name": report.stock_name or label,
        "as_of": report.as_of,
        "stance": report.summary.stance,
        "company_quality_stance": report.summary.company_quality_stance,
        "current_odds_stance": report.summary.current_odds_stance,
        "confidence": report.summary.confidence,
        "evidence_coverage": report.research_quality.coverage,
        "missing_dimensions": report.research_quality.missing_dimensions,
        "evidence_count": len(report.evidence),
        "primary_evidence_count": len(report.primary_evidence),
        "candidate_count": len(report.candidates),
        "snapshot_integrity": snapshot_integrity,
        "failures": failures,
        "warnings": warnings,
        "ok": not failures,
    }


async def main_async(
    cases: List[Tuple[str, date, str]],
    snapshot_root: Path,
) -> int:
    results: List[Dict] = []
    for code, as_of, label in cases:
        result = await _run_case(code, as_of, label, snapshot_root)
        results.append(result)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    summary = {
        "cases": len(results),
        "passed": sum(1 for x in results if x["ok"]),
        "failed": sum(1 for x in results if not x["ok"]),
        "failed_cases": [
            f"{x['stock_code']}@{x['as_of']}" for x in results if not x["ok"]
        ],
    }
    print("\n=== historical acceptance summary ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["failed"] == 0 else 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "cases",
        nargs="*",
        help="CODE@YYYY-MM-DD, e.g. 600036@2024-06-30",
    )
    parser.add_argument(
        "--snapshot-root",
        default="data/investment_snapshots",
    )
    args = parser.parse_args()
    cases = [_parse_case(x) for x in args.cases] if args.cases else DEFAULT_CASES
    raise SystemExit(
        asyncio.run(main_async(cases, Path(args.snapshot_root)))
    )


if __name__ == "__main__":
    main()
