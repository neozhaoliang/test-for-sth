# -*- coding: utf-8 -*-
"""
Historical public-data acceptance probe (no LLM, no browser, no live Xueqiu).

It exercises the strict point-in-time source layer for representative stocks and prints a
machine-readable summary. Public endpoints are inherently fallible, so callers may choose
whether a failed probe should fail CI.

Examples:
    python tools/historical_public_acceptance.py
    python tools/historical_public_acceptance.py 600036@2024-06-30
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

from analysis.filing_calendar import get_financial_filing_calendar
from analysis.macro_rates import get_macro_rate_context
from analysis.margin import get_margin_signal
from analysis.market_context import get_market_context
from analysis.point_in_time_capital_returns import build_point_in_time_capital_returns
from analysis.point_in_time_financials import (
    get_point_in_time_financials,
    to_historical_fundamentals,
    to_historical_profitability,
)
from analysis.point_in_time_ownership import get_point_in_time_ownership
from analysis.point_in_time_shareholder import get_point_in_time_shareholder_trend
from analysis.primary_sources import get_cninfo_primary_evidence
from analysis.realtime_price import get_historical_quote
from analysis.valuation_history import get_valuation_history

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


def _max_available_date(value) -> date | None:
    candidates: List[date] = []

    def walk(obj):
        if isinstance(obj, dict):
            for key, child in obj.items():
                if key in {"available_at", "published_at"} and child:
                    try:
                        candidates.append(date.fromisoformat(str(child)[:10]))
                    except ValueError:
                        pass
                walk(child)
        elif isinstance(obj, list):
            for child in obj:
                walk(child)

    walk(value)
    return max(candidates) if candidates else None


async def _one_case(code: str, as_of: date, label: str) -> Dict:
    (
        quote,
        valuation,
        market,
        margin,
        macro,
        primary,
        calendar,
        pit_financials,
        ownership,
        shareholder,
    ) = await asyncio.gather(
        get_historical_quote(code, as_of),
        get_valuation_history(code, as_of=as_of),
        get_market_context(code, as_of=as_of),
        get_margin_signal(code, as_of=as_of),
        get_macro_rate_context(as_of=as_of),
        get_cninfo_primary_evidence(code, as_of=as_of),
        get_financial_filing_calendar(code, as_of=as_of),
        get_point_in_time_financials(code, as_of),
        get_point_in_time_ownership(code, as_of),
        get_point_in_time_shareholder_trend(code, as_of),
    )

    fundamentals = to_historical_fundamentals(pit_financials)
    profitability = to_historical_profitability(pit_financials)
    dividends, buybacks = build_point_in_time_capital_returns(primary or [])

    payloads = {
        "quote": quote,
        "valuation_history": valuation,
        "market_context": market,
        "margin": margin,
        "macro_rates": macro,
        "primary_evidence": primary,
        "filing_calendar": calendar,
        "financials": pit_financials,
        "fundamentals": fundamentals,
        "profitability": profitability,
        "ownership": ownership,
        "shareholder_count": shareholder,
        "dividends": dividends,
        "buybacks": buybacks,
    }

    failures: List[str] = []
    warnings: List[str] = []

    required = {
        "quote": bool(quote),
        "valuation_history": bool(valuation),
        "market_context": bool(market),
        "primary_evidence": bool(primary),
        "filing_calendar": bool(calendar),
        "financials": bool(pit_financials),
    }
    for name, ok in required.items():
        if not ok:
            failures.append(f"{name} unavailable")

    optional = {
        "margin": bool(margin),
        "macro_rates": bool(macro),
        "ownership": bool(ownership),
        "shareholder_count": bool(shareholder),
        "dividend_events": bool(dividends),
        "buyback_events": bool(buybacks),
    }
    for name, ok in optional.items():
        if not ok:
            warnings.append(f"{name} unavailable/empty")

    future_leaks: List[str] = []
    for name, value in payloads.items():
        latest = _max_available_date(value)
        if latest is not None and latest > as_of:
            future_leaks.append(f"{name}:{latest.isoformat()}")
    if future_leaks:
        failures.append("future availability leak: " + "; ".join(future_leaks))

    latest_financial_period = (
        (pit_financials or {}).get("latest_period")
        if pit_financials else None
    )
    latest_financial_published = (
        (pit_financials or {}).get("latest_published_at")
        if pit_financials else None
    )

    return {
        "stock_code": code,
        "label": label,
        "as_of": as_of.isoformat(),
        "required": required,
        "optional": optional,
        "latest_financial_period": latest_financial_period,
        "latest_financial_published_at": latest_financial_published,
        "primary_evidence_count": len(primary or []),
        "filing_count": len(calendar or []),
        "ownership_holder_count": len((ownership or {}).get("top10_free_holders") or []),
        "shareholder_series_count": len((shareholder or {}).get("series") or []),
        "dividend_event_count": len(dividends),
        "buyback_event_count": len(buybacks),
        "future_leaks": future_leaks,
        "warnings": warnings,
        "failures": failures,
        "ok": not failures,
    }


async def main_async(cases: List[Tuple[str, date, str]], output: Path | None) -> int:
    results: List[Dict] = []
    for code, as_of, label in cases:
        try:
            result = await _one_case(code, as_of, label)
        except Exception as e:
            result = {
                "stock_code": code,
                "label": label,
                "as_of": as_of.isoformat(),
                "ok": False,
                "failures": [f"{type(e).__name__}: {e}"],
                "warnings": [],
            }
        results.append(result)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    summary = {
        "cases": len(results),
        "passed": sum(1 for x in results if x.get("ok")),
        "failed": sum(1 for x in results if not x.get("ok")),
        "failed_cases": [
            f"{x.get('stock_code')}@{x.get('as_of')}"
            for x in results
            if not x.get("ok")
        ],
        "results": results,
    }
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    print("\n=== historical public-data summary ===")
    print(json.dumps({k: v for k, v in summary.items() if k != "results"}, ensure_ascii=False, indent=2))
    return 0 if summary["failed"] == 0 else 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("cases", nargs="*", help="CODE@YYYY-MM-DD")
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    cases = [_parse_case(x) for x in args.cases] if args.cases else DEFAULT_CASES
    output = Path(args.output) if args.output else None
    raise SystemExit(asyncio.run(main_async(cases, output)))


if __name__ == "__main__":
    main()
