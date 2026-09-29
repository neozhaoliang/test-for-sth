# -*- coding: utf-8 -*-
"""
Manual full-report acceptance runner.

Unlike tools/live_public_acceptance.py, this exercises the complete investment-agent path:
public data + Xueqiu session + KOL context + LLM dimension analysis + synthesis + deterministic
final-report validation.

It is intentionally NOT part of pull-request CI because it needs live network access,
configured LLM credentials and (for the Xueqiu dimensions) a usable browser/login session.

Examples:
    python tools/live_report_acceptance.py
    python tools/live_report_acceptance.py 600036:financial 601088:cyclical
    python tools/live_report_acceptance.py --out data/acceptance/reports
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from analysis.report import generate_report
from analysis.report_validation import validate_report


DEFAULT_CASES: List[Tuple[str, str, str]] = [
    ("688981", "technology", "中芯国际"),
    ("601088", "cyclical", "中国神华"),
    ("600036", "financial", "招商银行"),
    ("600519", "consumer_brand", "贵州茅台"),
    ("600900", "stable_yield", "长江电力"),
    ("600309", "cyclical", "万华化学"),
]


def _parse_case(value: str) -> Tuple[str, Optional[str], str]:
    parts = value.split(":", 1)
    code = parts[0].strip()
    expected = parts[1].strip() if len(parts) > 1 and parts[1].strip() else None
    return code, expected, code


def _dimension_count(report) -> int:
    analyses = report.summary.dimension_analyses or {}
    return sum(1 for x in analyses.values() if str(x or "").strip())


async def _run_case(
    code: str,
    expected_archetype: Optional[str],
    label: str,
    output_dir: Optional[Path],
    min_coverage: float,
) -> Dict:
    failures: List[str] = []
    warnings: List[str] = []
    try:
        report = await generate_report(code)
    except Exception as e:
        return {
            "stock_code": code,
            "label": label,
            "expected_archetype": expected_archetype,
            "ok": False,
            "failures": [f"generate_report failed: {type(e).__name__}: {e}"],
            "warnings": [],
        }

    validation = validate_report(report)
    profile = report.research_profile
    quality = report.research_quality
    dims = _dimension_count(report)

    if not validation.ok:
        failures.extend(x.message for x in validation.errors)
    if expected_archetype and profile.archetype != expected_archetype:
        failures.append(
            f"profile mismatch: expected={expected_archetype}, actual={profile.archetype}"
        )
    if dims != 12:
        failures.append(f"dimension analyses count={dims}, expected=12")
    if quality.coverage < min_coverage:
        failures.append(
            f"evidence coverage={quality.coverage:.3f} below threshold={min_coverage:.3f}"
        )
    if not report.summary.core_counter_evidence.strip():
        failures.append("core_counter_evidence is empty")
    if not report.summary.invalidation_condition.strip():
        failures.append("invalidation_condition is empty")

    warnings.extend(x.message for x in validation.warnings)
    if report.research_quality.stale_evidence:
        warnings.append(
            "stale categories: "
            + ",".join(
                str(x.get("category"))
                for x in report.research_quality.stale_evidence
            )
        )

    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"{code}.json"
        path.write_text(
            json.dumps(report.model_dump(), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    return {
        "stock_code": code,
        "stock_name": report.stock_name or label,
        "expected_archetype": expected_archetype,
        "actual_archetype": profile.archetype,
        "stance": report.summary.stance,
        "company_quality_stance": report.summary.company_quality_stance,
        "current_odds_stance": report.summary.current_odds_stance,
        "confidence": report.summary.confidence,
        "evidence_coverage": quality.coverage,
        "profile_readiness": profile.readiness,
        "evidence_count": len(report.evidence),
        "primary_evidence_count": len(report.primary_evidence),
        "dimension_count": dims,
        "review_penalty": report.review.confidence_penalty,
        "validation_warnings": len(validation.warnings),
        "failures": failures,
        "warnings": warnings,
        "ok": not failures,
    }


async def main_async(
    cases: List[Tuple[str, Optional[str], str]],
    output_dir: Optional[Path],
    min_coverage: float,
) -> int:
    results = []
    # Serial by design: the full report path is browser/network/LLM heavy and public
    # endpoints should not be hammered concurrently.
    for code, expected, label in cases:
        result = await _run_case(
            code,
            expected,
            label,
            output_dir,
            min_coverage,
        )
        results.append(result)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    summary = {
        "cases": len(results),
        "passed": sum(1 for x in results if x["ok"]),
        "failed": sum(1 for x in results if not x["ok"]),
        "failed_codes": [x["stock_code"] for x in results if not x["ok"]],
        "average_coverage": round(
            sum(float(x.get("evidence_coverage") or 0) for x in results)
            / len(results),
            3,
        )
        if results
        else 0.0,
    }
    print("\n=== full-report acceptance summary ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if output_dir:
        (output_dir / "_summary.json").write_text(
            json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return 0 if summary["failed"] == 0 else 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "cases",
        nargs="*",
        help="CODE[:expected_archetype], e.g. 600036:financial",
    )
    parser.add_argument(
        "--out",
        default="",
        help="optional directory for full report JSON files",
    )
    parser.add_argument(
        "--min-coverage",
        type=float,
        default=0.60,
        help="minimum evidence coverage required for a case to pass (default: 0.60)",
    )
    args = parser.parse_args()
    cases = [_parse_case(x) for x in args.cases] if args.cases else DEFAULT_CASES
    output_dir = Path(args.out) if args.out else None
    raise SystemExit(
        asyncio.run(main_async(cases, output_dir, max(0.0, min(1.0, args.min_coverage))))
    )


if __name__ == "__main__":
    main()
