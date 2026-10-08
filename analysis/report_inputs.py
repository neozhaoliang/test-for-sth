# -*- coding: utf-8 -*-
"""
Pure data contract for pre-synthesis investment research inputs.

Kept separate from report orchestration so frozen snapshot replay can rebuild the exact
LLM input structure without importing browser/network collection code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from analysis.evidence import ResearchQuality
from analysis.research_profile import ResearchProfile
from model.m_analysis import KnowledgeExcerpt


@dataclass
class AnalysisInputs:
    stock_code: str
    stock_name: str = ""
    research_mode: str = "live"
    as_of: str = ""
    quote: Dict = field(default_factory=dict)
    knowledge_excerpts: List[KnowledgeExcerpt] = field(default_factory=list)
    industry_comparison: Optional[Dict] = None
    shareholder_trend: Optional[Dict] = None
    dividend_history: List[Dict] = field(default_factory=list)
    buyback_history: List[Dict] = field(default_factory=list)
    profitability_trend: Optional[Dict] = None
    commodity_signal: Optional[Dict] = None
    rmb_signal: Optional[Dict] = None
    macro_rates: Optional[Dict] = None
    policy_events: Optional[Dict] = None
    fundamentals: Optional[Dict] = None
    valuation: Optional[Dict] = None
    valuation_history: Optional[Dict] = None
    valuation_scenarios: List[Dict] = field(default_factory=list)  # auditable assumptions, optional
    rd_team: Optional[Dict] = None
    major_events: List[Dict] = field(default_factory=list)
    refinancing_history: List[Dict] = field(default_factory=list)
    executive_profile: Optional[Dict] = None
    governance_alerts: List[Dict] = field(default_factory=list)
    xueqiu_stock: Optional[Dict] = None
    debate: Optional[Dict] = None
    sentiment: Optional[Dict] = None
    margin_signal: Optional[Dict] = None
    market_context: Optional[Dict] = None
    freight_signal: Optional[Dict] = None
    primary_evidence: List[Dict] = field(default_factory=list)
    a_share_structure: Optional[Dict] = None
    management_capital: Optional[Dict] = None
    filing_calendar: List[Dict] = field(default_factory=list)
    research_profile: Optional[ResearchProfile] = None
    research_quality: Optional[ResearchQuality] = None
