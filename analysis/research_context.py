# -*- coding: utf-8 -*-
"""
Research-time contract for live vs historical investment reports.

The core rule is simple:
    historical mode may only use sources that can prove their data was available
    on or before request.as_of.

This module intentionally blocks historical reports until every required source path is
declared and implemented as as-of safe.  It is better to reject a historical request than
to silently mix 2026 knowledge into a 2024 backtest.
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
        capability=SourceTemporalCapability.LIVE_ONLY,
        reason="current quote adapter has no historical point-in-time contract yet",
        implemented=False,
    ),
    "fundamentals": TemporalCapability(
        source="fundamentals",
        capability=SourceTemporalCapability.LIVE_ONLY,
        reason="F10 parser currently returns the latest visible report and may include future publications",
        implemented=False,
    ),
    "profitability": TemporalCapability(
        source="profitability",
        capability=SourceTemporalCapability.LIVE_ONLY,
        reason="current financial indicator path is not publication-date filtered",
        implemented=False,
    ),
    "shareholder_count": TemporalCapability(
        source="shareholder_count",
        capability=SourceTemporalCapability.LIVE_ONLY,
        reason="historical rows exist but disclosure availability has not been enforced",
        implemented=False,
    ),
    "dividend_buyback": TemporalCapability(
        source="dividend_buyback",
        capability=SourceTemporalCapability.LIVE_ONLY,
        reason="events are not yet filtered by announcement availability date",
        implemented=False,
    ),
    "a_share_structure": TemporalCapability(
        source="a_share_structure",
        capability=SourceTemporalCapability.LIVE_ONLY,
        reason="quarter holdings must respect disclosure date, not only quarter-end date",
        implemented=False,
    ),
    "market_context": TemporalCapability(
        source="market_context",
        capability=SourceTemporalCapability.LIVE_ONLY,
        reason="current helper anchors calculations on today",
        implemented=False,
    ),
    "margin": TemporalCapability(
        source="margin",
        capability=SourceTemporalCapability.LIVE_ONLY,
        reason="latest financing snapshot is not truncated to as_of",
        implemented=False,
    ),
    "macro_rates": TemporalCapability(
        source="macro_rates",
        capability=SourceTemporalCapability.LIVE_ONLY,
        reason="current macro context returns latest releases and lacks vintage-date filtering",
        implemented=False,
    ),
    "industry_cycle": TemporalCapability(
        source="industry_cycle",
        capability=SourceTemporalCapability.LIVE_ONLY,
        reason="commodity/freight/industry helpers are not uniformly as-of aware",
        implemented=False,
    ),
    "xueqiu_live": TemporalCapability(
        source="xueqiu_live",
        capability=SourceTemporalCapability.LIVE_ONLY,
        reason="current browser path reads today's stock page and discussion stream",
        implemented=False,
    ),
    "kol_knowledge": TemporalCapability(
        source="kol_knowledge",
        capability=SourceTemporalCapability.LIVE_ONLY,
        reason="knowledge retrieval does not yet filter cards by original publication date <= as_of",
        implemented=False,
    ),
    "candidate_credibility": TemporalCapability(
        source="candidate_credibility",
        capability=SourceTemporalCapability.LIVE_ONLY,
        reason="current Wilson/hit-rate score may include predictions resolved after historical as_of",
        implemented=False,
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
        else:
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
