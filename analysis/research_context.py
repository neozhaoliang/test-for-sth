# -*- coding: utf-8 -*-
"""
Research-time contract for live vs historical investment reports.

The core rule is simple:
    historical mode may only use sources that can prove their data was available
    on or before request.as_of.

Historical reports are allowed only when every *required* source path is point-in-time safe.
Some live-only dimensions may be explicitly marked as safe omissions: historical mode must
leave them missing rather than silently mix today's information into a past-date report.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, model_validator


class ResearchMode(str, Enum):
    LIVE = "live"
    HISTORICAL = "historical"


class SourceTemporalCapability(str, Enum):
    LIVE_ONLY = "live_only"
    AS_OF_SAFE = "as_of_safe"
    SNAPSHOT_ONLY = "snapshot_only"


class ResearchRequest(BaseModel):
    stock_code: str
    mode: ResearchMode = ResearchMode.LIVE
    as_of: Optional[date] = None
    save_snapshot: bool = False
    snapshot_root: str = "data/investment_snapshots"

    @model_validator(mode="after")
    def validate_time_contract(self):
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        if self.mode == ResearchMode.HISTORICAL:
            if self.as_of is None:
                raise ValueError("historical mode requires as_of")
            if self.as_of >= today:
                raise ValueError("historical mode as_of must be before today")
        else:
            if self.as_of is None:
                self.as_of = today
            elif self.as_of != today:
                raise ValueError(
                    "live mode always uses today's data; use historical mode for a past as_of"
                )
        return self


class TemporalCapability(BaseModel):
    source: str
    capability: SourceTemporalCapability
    reason: str
    implemented: bool = True
    required_for_historical: bool = True


# This registry is deliberately conservative.  A source only becomes AS_OF_SAFE after its
# adapter filters by *availability date*, not merely report period.
SOURCE_TEMPORAL_CAPABILITIES: Dict[str, TemporalCapability] = {
    "valuation_history": TemporalCapability(
        source="valuation_history",
        capability=SourceTemporalCapability.AS_OF_SAFE,
        reason="historical PE/PB series can be truncated to date <= as_of",
    ),
    "primary_evidence": TemporalCapability(
        source="primary_evidence",
        capability=SourceTemporalCapability.AS_OF_SAFE,
        reason="CNINFO query can cap announcement publication date at as_of",
    ),
    "realtime_quote": TemporalCapability(
        source="realtime_quote",
        capability=SourceTemporalCapability.AS_OF_SAFE,
        reason="historical daily-close quote is capped at as_of and uses the previous trading day for change",
    ),
    "fundamentals": TemporalCapability(
        source="fundamentals",
        capability=SourceTemporalCapability.AS_OF_SAFE,
        reason="historical mode parses the exact CNINFO periodic-report PDF version published by as_of; live mode still uses F10",
        implemented=True,
    ),
    "filing_calendar": TemporalCapability(
        source="filing_calendar",
        capability=SourceTemporalCapability.AS_OF_SAFE,
        reason="official CNINFO periodic filings are queried and versioned by actual publication date <= as_of",
    ),
    "profitability": TemporalCapability(
        source="profitability",
        capability=SourceTemporalCapability.AS_OF_SAFE,
        reason="historical profitability is derived only from exact CNINFO filing versions published by as_of",
        implemented=True,
    ),
    "shareholder_count": TemporalCapability(
        source="shareholder_count",
        capability=SourceTemporalCapability.AS_OF_SAFE,
        reason="historical mode parses shareholder count only from exact CNINFO periodic-report PDF versions published by as_of",
        implemented=True,
    ),
    "dividend_buyback": TemporalCapability(
        source="dividend_buyback",
        capability=SourceTemporalCapability.AS_OF_SAFE,
        reason="historical mode builds dividend/buyback events only from CNINFO announcements published by as_of and never backfills later implementation progress",
        implemented=True,
    ),
    "a_share_structure": TemporalCapability(
        source="a_share_structure",
        capability=SourceTemporalCapability.AS_OF_SAFE,
        reason="historical mode reads identifiable top holders only from the exact CNINFO periodic-report PDF version published by as_of; live mode may use richer reconstructed databases",
        implemented=True,
    ),
    "market_context": TemporalCapability(
        source="market_context",
        capability=SourceTemporalCapability.AS_OF_SAFE,
        reason="stock and index series are truncated to as_of before YTD/52-week/history calculations",
    ),
    "margin": TemporalCapability(
        source="margin",
        capability=SourceTemporalCapability.AS_OF_SAFE,
        reason="exchange margin detail is queried backwards from as_of for the latest six trading days",
    ),
    "macro_rates": TemporalCapability(
        source="macro_rates",
        capability=SourceTemporalCapability.AS_OF_SAFE,
        reason="Fed target, Treasury 10Y and LPR observations are truncated to as_of and freshness is evaluated relative to as_of",
    ),
    "industry_cycle": TemporalCapability(
        source="industry_cycle",
        capability=SourceTemporalCapability.LIVE_ONLY,
        reason="commodity/freight series are as-of truncated, but strict historical industry classification is not yet archived; historical mode safely omits the route when classification is unavailable",
        implemented=False,
        required_for_historical=False,
    ),
    "policy_news": TemporalCapability(
        source="policy_news",
        capability=SourceTemporalCapability.LIVE_ONLY,
        reason="current finance-news feeds are not a historical archive; historical mode deliberately omits them instead of backfilling today's news",
        implemented=False,
        required_for_historical=False,
    ),
    "xueqiu_live": TemporalCapability(
        source="xueqiu_live",
        capability=SourceTemporalCapability.LIVE_ONLY,
        reason="historical mode deliberately omits today's stock page/discussion stream; absence lowers evidence coverage but does not permit a live fallback",
        implemented=False,
        required_for_historical=False,
    ),
    "kol_knowledge": TemporalCapability(
        source="kol_knowledge",
        capability=SourceTemporalCapability.AS_OF_SAFE,
        reason="raw KOL entries are normalized to publication epoch seconds and historical mode excludes unknown/future timestamps before retrieval",
    ),
    "candidate_credibility": TemporalCapability(
        source="candidate_credibility",
        capability=SourceTemporalCapability.AS_OF_SAFE,
        reason="historical Wilson score only uses posts published by as_of and correct/incorrect outcomes verified by as_of",
    ),
}


HISTORICAL_REQUIRED_SOURCES = tuple(SOURCE_TEMPORAL_CAPABILITIES)


class HistoricalReadiness(BaseModel):
    ready: bool
    safe_sources: List[str] = Field(default_factory=list)
    blocking_sources: List[str] = Field(default_factory=list)
    reasons: Dict[str, str] = Field(default_factory=dict)


def historical_readiness() -> HistoricalReadiness:
    safe: List[str] = []
    blocking: List[str] = []
    reasons: Dict[str, str] = {}
    for name in HISTORICAL_REQUIRED_SOURCES:
        item = SOURCE_TEMPORAL_CAPABILITIES[name]
        if item.capability == SourceTemporalCapability.AS_OF_SAFE and item.implemented:
            safe.append(name)
            continue
        if not item.required_for_historical:
            # Explicit safe omission: historical orchestration must not fall back to live
            # data, but the missing optional source does not block the whole report.
            reasons[name] = item.reason
            continue
        blocking.append(name)
        reasons[name] = item.reason
    return HistoricalReadiness(
        ready=not blocking,
        safe_sources=safe,
        blocking_sources=blocking,
        reasons=reasons,
    )


def assert_request_supported(request: ResearchRequest) -> None:
    if request.mode == ResearchMode.LIVE:
        return
    readiness = historical_readiness()
    if readiness.ready:
        return
    detail = "；".join(
        f"{name}: {readiness.reasons.get(name, 'not as-of safe')}"
        for name in readiness.blocking_sources
    )
    raise NotImplementedError(
        "historical/as-of report is intentionally blocked until all required sources "
        "are point-in-time safe. Blocking sources: " + detail
    )
