"""Complete missing live cash-conversion metrics from a dated issuer filing.

The F10 'changed financial items' table is intentionally sparse: absence of
operating cash flow there says nothing about whether the issuer published it.
Use one exact CNINFO report only when the live F10 cannot calculate OCF/profit.

No mixed periods, no quoted per-share OCF substituted for net operating cash
flow, and no newer disclosure used for historical reports (caller: live only).
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from analysis.filing_calendar import latest_available_filing_by_period
from analysis.point_in_time_financials import _fetch_and_parse

logger = logging.getLogger("MediaCrawler")


async def complete_live_cash_profit(
    fundamentals: Optional[Dict],
    filing_calendar: List[Dict],
    *,
    as_of: Optional[date] = None,
) -> Optional[Dict]:
    """Return an amended copy only when a latest, dated filing supplies both amounts.

    F10's financial-period tag, if present, MUST equal the filing's period;
    otherwise prefer an explicit missing ratio over silently mixing quarters.
    Do not change a valid F10 ratio or any unrelated financial field.
    """
    if not fundamentals or not isinstance(fundamentals.get("facts"), dict):
        return fundamentals

    facts = fundamentals["facts"]
    if facts.get("cash_to_profit_ratio") is not None:
        return fundamentals

    cutoff = as_of or datetime.now(ZoneInfo("Asia/Shanghai")).date()
    available = latest_available_filing_by_period(filing_calendar or [], cutoff)
    if not available:
        return fundamentals
    period = max(available)
    f10_period = str(facts.get("finance_period") or "")
    if f10_period and f10_period != period:
        logger.info(
            "[live_financial_completion] F10 period=%s vs latest filing=%s; "
            "refusing to combine periods", f10_period, period,
        )
        return fundamentals

    try:
        period_date = date.fromisoformat(period)
        if (cutoff - period_date).days > 380 or period_date > cutoff:
            return fundamentals
    except ValueError:
        return fundamentals

    filing = available[period]
    try:
        parsed = await _fetch_and_parse(filing)
    except Exception as exc:
        logger.warning(
            "[live_financial_completion] filing fetch/parse failure for %s: %s",
            period, exc,
        )
        return fundamentals
    if not parsed or str(parsed.get("period")) != period:
        return fundamentals

    try:
        net_profit = float(parsed["net_profit"])
        ocf = float(parsed["operating_cash_flow"])
    except (KeyError, TypeError, ValueError):
        return fundamentals
    if not (math.isfinite(net_profit) and math.isfinite(ocf)) or net_profit == 0:
        return fundamentals
    # Units must be explicitly established by the PDF parser.  Avoid presenting
    # percentages or an OCR artifact as a billion-yuan cash flow.
    if not str(parsed.get("monetary_unit") or "").startswith("元"):
        return fundamentals

    new_facts = dict(facts)
    new_facts.update(
        net_profit=net_profit,
        net_profit_basis="归属于上市公司股东的净利润",
        operating_cash_flow=ocf,
        cash_to_profit_ratio=round(ocf / net_profit, 3),
        cash_profit_period=period,
        cash_profit_source="巨潮资讯原始定期报告",
    )
    if not f10_period:
        # Do not overwrite the F10 tag of other fields if it is unavailable.
        # The cash/profit KPI uses its own exact period instead.
        new_facts["cash_profit_period"] = period
    new_data = dict(fundamentals)
    new_data["facts"] = new_facts
    source_url = str(filing.get("url") or "")
    if source_url:
        new_map = dict(fundamentals.get("source_map") or {})
        new_map["cash_profit_official_filing"] = source_url
        new_data["source_map"] = new_map
        new_sources = list(fundamentals.get("sources") or [])
        if source_url not in new_sources:
            new_sources.append(source_url)
        new_data["sources"] = new_sources
    logger.info(
        "[live_financial_completion] restored OCF/attributable net-profit "
        "for exact filing period=%s, ratio=%.3f", period,
        new_facts["cash_to_profit_ratio"],
    )
    return new_data
