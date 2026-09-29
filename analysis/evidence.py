# -*- coding: utf-8 -*-
"""
Investment research evidence ledger.

The existing analysis pipeline already collects many useful data blocks, but historically
those blocks only lived inside one large prompt.  This module turns the same inputs into a
stable, machine-readable evidence ledger so UI/evaluation code can answer:
- what evidence was actually available;
- whether it is fact/derived/opinion;
- how much of the 12-dimension research rubric was covered;
- which important areas are still missing.

It deliberately does not decide whether a stock is attractive.  It only describes the
quality and coverage of the research inputs.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, List, Optional

from pydantic import BaseModel, Field


class EvidenceItem(BaseModel):
    id: str = Field(description="stable short id")
    category: str = Field(description="research evidence category")
    label: str = Field(description="human-readable evidence label")
    source: str = Field(description="collector/source module")
    source_tier: str = Field(description="S/A/B/C; S is strongest")
    kind: str = Field(description="fact|derived|opinion")
    as_of: Optional[str] = None
    summary: str = ""
    value: Any = None
    url: Optional[str] = None
    tags: List[str] = Field(default_factory=list)


class ResearchQuality(BaseModel):
    coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    covered_dimensions: int = 0
    total_dimensions: int = 12
    high_grade_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    missing_dimensions: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


_DIMENSION_REQUIREMENTS: Dict[str, tuple[str, ...]] = {
    "management": ("governance", "shareholder_return"),
    "fundamentals": ("fundamentals", "profitability"),
    "rd": ("rd",),
    "chip_flow": ("shareholder_count", "margin"),
    "price_position": ("valuation_history", "valuation", "market_context"),
    "cycle_position": ("industry", "cycle_signal"),
    "policy_geopolitics": ("fx", "major_events", "knowledge"),
    "retail_sentiment": ("sentiment", "debate"),
    "shareholder_returns": ("shareholder_return", "governance"),
    "growth_elasticity": ("profitability", "valuation_history", "valuation"),
    "a_share_structure": ("a_share_structure", "market_context", "margin", "institutional", "knowledge"),
    "risk_quality": ("fundamentals", "profitability", "governance"),
}

_DIMENSION_LABELS = {
    "management": "管理层与治理",
    "fundamentals": "经营基本面与护城河",
    "rd": "研发、人才与技术护城河",
    "chip_flow": "筹码与散户结构",
    "price_position": "价格与估值位置",
    "cycle_position": "行业周期位置",
    "policy_geopolitics": "政策、利率、汇率与地缘",
    "retail_sentiment": "雪球散户情绪",
    "shareholder_returns": "股东回报与资本抽取",
    "growth_elasticity": "增长空间与股票弹性",
    "a_share_structure": "A股资金结构与市场风格",
    "risk_quality": "财务质量与尾部风险",
}


def _safe_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return repr(value)


def _evidence_id(category: str, source: str, value: Any) -> str:
    raw = f"{category}|{source}|{_safe_json(value)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:14]


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _add(
    out: List[EvidenceItem],
    *,
    category: str,
    label: str,
    source: str,
    tier: str,
    kind: str,
    value: Any,
    as_of: Optional[str] = None,
    summary: str = "",
    tags: Optional[Iterable[str]] = None,
    url: Optional[str] = None,
) -> None:
    if value in (None, "", [], {}):
        return
    out.append(
        EvidenceItem(
            id=_evidence_id(category, source, value),
            category=category,
            label=label,
            source=source,
            source_tier=tier,
            kind=kind,
            as_of=as_of,
            summary=summary,
            value=value,
            url=url,
            tags=list(tags or ()),
        )
    )


def build_evidence_ledger(inputs: Any, candidates: Optional[List[Any]] = None) -> List[EvidenceItem]:
    """
    Convert AnalysisInputs into a compact evidence ledger.

    Source tiers here describe *this project's current acquisition path*, not the intrinsic
    authority of the underlying fact.  For example, data parsed from a third-party F10 page
    remains B-tier until an exchange/company-announcement adapter is added.
    """
    out: List[EvidenceItem] = []

    quote = _get(inputs, "quote") or {}
    _add(
        out,
        category="quote",
        label="实时行情快照",
        source="analysis.realtime_price",
        tier="B",
        kind="fact",
        value=quote,
        as_of=str(quote.get("date") or quote.get("time") or "") or None,
        tags=("price", "liquidity"),
    )

    fundamentals = _get(inputs, "fundamentals") or {}
    facts = fundamentals.get("facts") or {}
    source_map = fundamentals.get("source_map") or {}
    _add(
        out,
        category="fundamentals",
        label="结构性经营事实",
        source="analysis.fundamentals",
        tier="B",
        kind="fact",
        value=fundamentals,
        tags=("financials", "customers", "suppliers", "cashflow"),
        url=source_map.get("finance") or source_map.get("operate"),
    )
    rd_payload = {
        k: facts.get(k)
        for k in (
            "rd_investment_yuan",
            "rd_intensity_pct",
            "patents_granted",
            "patents_invention",
            "invention_ratio_pct",
            "employee_count",
        )
        if facts.get(k) is not None
    }
    _add(
        out,
        category="rd",
        label="研发与专利事实",
        source="analysis.fundamentals",
        tier="B",
        kind="fact",
        value=rd_payload,
        tags=("rd", "patent", "technology"),
        url=source_map.get("operate"),
    )

    valuation = _get(inputs, "valuation")
    _add(
        out,
        category="valuation",
        label="估值与股本快照",
        source="analysis.fundamentals",
        tier="B",
        kind="derived",
        value=valuation,
        as_of=str((valuation or {}).get("valuation_as_of") or "") or None,
        tags=("valuation", "shares"),
        url=source_map.get("profile"),
    )

    valuation_history = _get(inputs, "valuation_history")
    _add(
        out,
        category="valuation_history",
        label="历史PE/PB估值分位",
        source="analysis.valuation_history",
        tier="B",
        kind="derived",
        value=valuation_history,
        as_of=str((valuation_history or {}).get("as_of") or "") or None,
        tags=("valuation", "percentile", "history"),
    )

    profitability = _get(inputs, "profitability_trend")
    _add(
        out,
        category="profitability",
        label="盈利能力与杠杆趋势",
        source="analysis.profitability",
        tier="B",
        kind="derived",
        value=profitability,
        tags=("margin", "roe", "debt"),
    )

    market = _get(inputs, "market_context")
    _add(
        out,
        category="market_context",
        label="大盘、风格与历史价格位置",
        source="analysis.market_context",
        tier="B",
        kind="derived",
        value=market,
        tags=("style", "position", "index"),
    )

    industry = _get(inputs, "industry_comparison")
    _add(
        out,
        category="industry",
        label="行业横向涨跌",
        source="analysis.industry",
        tier="B",
        kind="derived",
        value=industry,
        tags=("industry",),
    )

    cycle_signal: Dict[str, Any] = {}
    if _get(inputs, "commodity_signal"):
        cycle_signal["commodity"] = _get(inputs, "commodity_signal")
    if _get(inputs, "freight_signal"):
        cycle_signal["freight"] = _get(inputs, "freight_signal")
    _add(
        out,
        category="cycle_signal",
        label="行业周期领先/同步指标",
        source="analysis.commodity+analysis.freight",
        tier="B",
        kind="derived",
        value=cycle_signal,
        tags=("cycle", "commodity", "freight"),
    )

    shareholder_count = _get(inputs, "shareholder_trend")
    _add(
        out,
        category="shareholder_count",
        label="股东户数变化",
        source="analysis.shareholder",
        tier="B",
        kind="fact",
        value=shareholder_count,
        as_of=str((shareholder_count or {}).get("as_of") or "") or None,
        tags=("holders", "chip"),
        url=source_map.get("holder"),
    )

    shareholder_return = {
        "dividend_history": _get(inputs, "dividend_history") or [],
        "buyback_history": _get(inputs, "buyback_history") or [],
    }
    _add(
        out,
        category="shareholder_return",
        label="分红与回购记录",
        source="analysis.shareholder",
        tier="B",
        kind="fact",
        value=shareholder_return
        if shareholder_return["dividend_history"] or shareholder_return["buyback_history"]
        else None,
        tags=("dividend", "buyback", "capital_allocation"),
    )

    governance = {
        "refinancing_history": _get(inputs, "refinancing_history") or [],
        "executive_profile": _get(inputs, "executive_profile"),
        "governance_alerts": _get(inputs, "governance_alerts") or [],
        "major_events": _get(inputs, "major_events") or [],
    }
    _add(
        out,
        category="governance",
        label="治理、再融资与重大事项",
        source="analysis.fundamentals",
        tier="B",
        kind="fact",
        value=governance
        if any(v not in (None, [], {}) for v in governance.values())
        else None,
        tags=("governance", "refinancing", "insider", "events"),
        url=source_map.get("event") or source_map.get("capital") or source_map.get("company"),
    )
    _add(
        out,
        category="major_events",
        label="近期重大事项",
        source="analysis.fundamentals",
        tier="B",
        kind="fact",
        value=_get(inputs, "major_events"),
        tags=("events", "policy"),
        url=source_map.get("event"),
    )

    margin = _get(inputs, "margin_signal")
    _add(
        out,
        category="margin",
        label="融资盘与流通盘",
        source="analysis.margin",
        tier="B",
        kind="derived",
        value=margin,
        tags=("margin_financing", "leverage", "chip"),
    )

    rmb = _get(inputs, "rmb_signal")
    _add(
        out,
        category="fx",
        label="人民币汇率趋势",
        source="analysis.commodity",
        tier="B",
        kind="derived",
        value=rmb,
        tags=("fx", "macro"),
    )

    a_share_structure = _get(inputs, "a_share_structure")
    _add(
        out,
        category="a_share_structure",
        label="A股机构持股与特殊股东结构",
        source="analysis.a_share_structure",
        tier="B",
        kind="derived",
        value=a_share_structure,
        as_of=str((a_share_structure or {}).get("report_period") or "") or None,
        tags=("institution", "fund", "social_security", "insurance", "qfii", "national_team"),
    )

    xq_stock = _get(inputs, "xueqiu_stock") or {}
    institutional = xq_stock.get("org_holding")
    _add(
        out,
        category="institutional",
        label="雪球页面可见机构持仓聚合",
        source="analysis.xueqiu_stock",
        tier="C",
        kind="fact",
        value=institutional,
        tags=("institution", "holding"),
    )

    sentiment = _get(inputs, "sentiment")
    _add(
        out,
        category="sentiment",
        label="雪球讨论区情绪聚合",
        source="analysis.debate",
        tier="C",
        kind="derived",
        value=sentiment,
        tags=("sentiment", "retail"),
    )
    debate = _get(inputs, "debate")
    _add(
        out,
        category="debate",
        label="雪球多空论点与历史验证",
        source="analysis.debate",
        tier="C",
        kind="derived",
        value=debate,
        tags=("debate", "retail"),
    )

    knowledge = _get(inputs, "knowledge_excerpts") or []
    if knowledge:
        normalized = [
            k.model_dump() if hasattr(k, "model_dump") else dict(k) if isinstance(k, dict) else str(k)
            for k in knowledge
        ]
        _add(
            out,
            category="knowledge",
            label="老木匠/军师祭咖啡等经验知识",
            source="analysis.knowledge_base",
            tier="C",
            kind="opinion",
            value=normalized,
            tags=("kol", "experience", "market_lore"),
        )

    primary = _get(inputs, "primary_evidence") or []
    for item in primary:
        _add(
            out,
            category="primary",
            label=item.get("title") or "一手公告",
            source=item.get("source_name") or "巨潮资讯",
            tier=item.get("source_tier") or "S",
            kind=item.get("kind") or "fact",
            value={
                "category": item.get("category"),
                "stock_name": item.get("stock_name"),
            },
            as_of=item.get("published_at") or None,
            summary=item.get("category") or "",
            tags=("primary", "announcement", item.get("category") or ""),
            url=item.get("url"),
        )

    if candidates:
        normalized_candidates = [
            c.model_dump() if hasattr(c, "model_dump") else dict(c) if isinstance(c, dict) else str(c)
            for c in candidates
        ]
        _add(
            out,
            category="verified_social",
            label="历史回测过的雪球用户观点",
            source="analysis.candidates+backtest",
            tier="C",
            kind="opinion",
            value=normalized_candidates,
            tags=("social", "backtest"),
        )

    return out


def evaluate_research_quality(evidence: List[EvidenceItem]) -> ResearchQuality:
    categories = {e.category for e in evidence}
    covered: List[str] = []
    missing: List[str] = []

    for dim, needs in _DIMENSION_REQUIREMENTS.items():
        if any(category in categories for category in needs):
            covered.append(dim)
        else:
            missing.append(dim)

    coverage = len(covered) / len(_DIMENSION_REQUIREMENTS)
    # 按证据类别而不是逐条记录计算来源质量；否则几十条巨潮公告会把比例虚高。
    category_quality: Dict[str, bool] = {}
    for e in evidence:
        is_high = e.source_tier in {"S", "A", "B"} and e.kind != "opinion"
        category_quality[e.category] = category_quality.get(e.category, False) or is_high
    ratio = (
        sum(1 for ok in category_quality.values() if ok) / len(category_quality)
        if category_quality
        else 0.0
    )

    warnings: List[str] = []
    if coverage < 0.75:
        warnings.append("研究证据覆盖不足 75%，综合结论应降低置信度。")
    if "governance" not in categories and "primary" not in categories:
        warnings.append("缺少治理/再融资/处罚等客观记录，不宜评价管理层可信度。")
    if "valuation" not in categories or "market_context" not in categories:
        warnings.append("价格与估值位置证据不完整，不能只凭公司质量给出股票结论。")
    if "cycle_signal" not in categories and "industry" in categories:
        warnings.append("行业归属已知但缺少周期领先指标，周期位置只能保守表述。")
    if evidence and all(e.source_tier == "C" for e in evidence):
        warnings.append("当前证据全部来自社交/观点来源，不足以形成高置信度结论。")

    return ResearchQuality(
        coverage=round(coverage, 3),
        covered_dimensions=len(covered),
        total_dimensions=len(_DIMENSION_REQUIREMENTS),
        high_grade_ratio=round(ratio, 3),
        missing_dimensions=[_DIMENSION_LABELS[d] for d in missing],
        warnings=warnings,
    )
