# -*- coding: utf-8 -*-
"""
Manual live-data acceptance check for representative A-share archetypes.

This is intentionally NOT part of pull-request CI because public finance endpoints can be
rate-limited or temporarily unavailable.  It verifies the public-data plumbing without
requiring Xueqiu login or an LLM.

Examples:
    python tools/live_public_acceptance.py
    python tools/live_public_acceptance.py 688981:technology 601088:cyclical
"""

from __future__ import annotations

import argparse
import asyncio
import json
from types import SimpleNamespace
from typing import Dict, List, Optional, Tuple

from analysis.a_share_structure import get_a_share_structure
from analysis.evidence import build_evidence_ledger, evaluate_research_quality
from analysis.fundamentals import get_ths_fundamentals
from analysis.macro_rates import get_macro_rate_context
from analysis.management_capital import build_management_capital_record
from analysis.primary_sources import get_cninfo_primary_evidence
from analysis.profitability import get_profitability_trend
from analysis.rd_team import get_rd_team_composition
from analysis.research_profile import classify_research_profile
from analysis.shareholder import get_buyback_history, get_dividend_history
from analysis.valuation_history import get_valuation_history


DEFAULT_CASES: List[Tuple[str, str, str]] = [
    ("688981", "technology", "中芯国际"),
    ("601088", "cyclical", "中国神华"),
    ("600036", "financial", "招商银行"),
    ("600519", "consumer_brand", "贵州茅台"),
    ("600900", "stable_yield", "长江电力"),
]


def _parse_case(value: str) -> Tuple[str, Optional[str], str]:
    parts = value.split(":", 1)
    code = parts[0].strip()
    expected = parts[1].strip() if len(parts) > 1 and parts[1].strip() else None
    return code, expected, code


def _blank_inputs(**kwargs):
    base = dict(
        quote={},
        fundamentals=None,
        valuation=None,
        valuation_history=None,
        rd_team=None,
        profitability_trend=None,
        market_context=None,
        industry_comparison=None,
        commodity_signal=None,
        freight_signal=None,
        shareholder_trend=None,
        dividend_history=[],
        buyback_history=[],
        refinancing_history=[],
        executive_profile=None,
        governance_alerts=[],
        major_events=[],
        margin_signal=None,
        macro_rates=None,
        rmb_signal=None,
        a_share_structure=None,
        xueqiu_stock=None,
        sentiment=None,
        debate=None,
        knowledge_excerpts=[],
        primary_evidence=[],
        management_capital=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


async def _one_case(
    code: str,
    expected: Optional[str],
    label: str,
    macro_rates: Optional[Dict],
) -> Dict:
    (
        fundamentals,
        profitability,
        valuation_history,
        primary,
        a_share_structure,
        dividends,
        buybacks,
    ) = await asyncio.gather(
        get_ths_fundamentals(code),
        get_profitability_trend(code),
        get_valuation_history(code),
        get_cninfo_primary_evidence(code),
        get_a_share_structure(code),
        get_dividend_history(code),
        get_buyback_history(code),
    )

    facts = (fundamentals or {}).get("facts") or {}
    prelim = classify_research_profile(
        fundamentals=fundamentals,
        evidence=[],
    )
    rd_team = None
    if prelim.archetype == "technology":
        rd_team = await get_rd_team_composition(code)

    valuation = None
    major_events = []
    refinancing = []
    executive = None
    governance = []
    stripped_fundamentals = fundamentals
    if fundamentals:
        stripped_fundamentals = dict(fundamentals)
        valuation = stripped_fundamentals.pop("valuation", None)
        major_events = stripped_fundamentals.pop("major_events", None) or []
        refinancing = stripped_fundamentals.pop("refinancing_history", None) or []
        executive = stripped_fundamentals.pop("executive_profile", None)
        governance = stripped_fundamentals.pop("governance_alerts", None) or []

    management_capital = build_management_capital_record(
        dividend_history=dividends,
        buyback_history=buybacks,
        refinancing_history=refinancing,
        primary_evidence=primary,
        executive_profile=executive,
        profitability_trend=profitability,
    )

    inputs = _blank_inputs(
        fundamentals=stripped_fundamentals,
        valuation=valuation,
        valuation_history=valuation_history,
        rd_team=rd_team,
        profitability_trend=profitability,
        dividend_history=dividends,
        buyback_history=buybacks,
        refinancing_history=refinancing,
        executive_profile=executive,
        governance_alerts=governance,
        major_events=major_events,
        macro_rates=macro_rates,
        a_share_structure=a_share_structure,
        primary_evidence=primary,
        management_capital=management_capital,
    )
    evidence = build_evidence_ledger(inputs)
    quality = evaluate_research_quality(evidence)
    profile = classify_research_profile(
        fundamentals=stripped_fundamentals,
        management_capital=management_capital,
        evidence=evidence,
    )

    essentials = {
        "fundamentals": bool(stripped_fundamentals),
        "industry": bool(facts.get("sw_industry")),
        "valuation_history": bool(valuation_history),
        "primary_evidence": bool(primary),
        "a_share_structure": bool(a_share_structure),
        "profitability": bool(profitability),
    }
    failures: List[str] = []
    if not essentials["fundamentals"]:
        failures.append("fundamentals unavailable")
    if not essentials["industry"]:
        failures.append("SW industry unavailable")
    if expected and profile.archetype != expected:
        failures.append(
            f"profile mismatch: expected={expected}, actual={profile.archetype}"
        )

    # These are useful but public endpoints can legitimately be empty for a period.
    warnings = [
        name + " unavailable"
        for name in ("valuation_history", "primary_evidence", "a_share_structure", "profitability")
        if not essentials[name]
    ]
    if profile.missing_priority_evidence:
        warnings.append(
            "priority evidence missing: "
            + ",".join(profile.missing_priority_evidence)
        )

    return {
        "stock_code": code,
        "label": label,
        "expected_archetype": expected,
        "actual_archetype": profile.archetype,
        "industry": profile.industry,
        "profile_readiness": profile.readiness,
        "evidence_coverage": quality.coverage,
        "high_grade_ratio": quality.high_grade_ratio,
        "evidence_count": len(evidence),
        "essentials": essentials,
        "stale_evidence": quality.stale_evidence,
        "warnings": warnings,
        "failures": failures,
        "ok": not failures,
    }


async def main_async(cases: List[Tuple[str, Optional[str], str]]) -> int:
    try:
        macro_rates = await get_macro_rate_context()
    except Exception:
        macro_rates = None

    # Run serially to be polite to public data providers and avoid triggering rate limits.
    results = []
    for code, expected, label in cases:
        result = await _one_case(code, expected, label, macro_rates)
        results.append(result)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    summary = {
        "cases": len(results),
        "passed": sum(1 for x in results if x["ok"]),
        "failed": sum(1 for x in results if not x["ok"]),
        "failed_codes": [x["stock_code"] for x in results if not x["ok"]],
    }
    print("\n=== acceptance summary ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["failed"] == 0 else 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "cases",
        nargs="*",
        help="CODE[:expected_archetype], e.g. 600036:financial",
    )
    args = parser.parse_args()
    cases = [_parse_case(x) for x in args.cases] if args.cases else DEFAULT_CASES
    raise SystemExit(asyncio.run(main_async(cases)))


if __name__ == "__main__":
    main()
