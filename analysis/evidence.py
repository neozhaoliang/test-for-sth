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
import re
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field


class EvidenceItem(BaseModel):
    id: str = Field(description="stable short id")
    category: str = Field(description="research evidence category")
    label: str = Field(description="human-readable evidence label")
    source: str = Field(description="collector/source module")
    source_tier: str = Field(description="S/A/B/C; S is strongest")
    kind: str = Field(description="fact|derived|opinion")
    as_of: Optional[str] = None
    period: Optional[str] = Field(default=None, description="数据描述的报告期/观察期")
    published_at: Optional[str] = Field(default=None, description="原始信息正式发布日期")
    available_at: Optional[str] = Field(default=None, description="投资者最早可合法获得该信息的时间")
    retrieved_at: Optional[str] = Field(default=None, description="本次研究抓取该信息的时间")
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
    stale_evidence: List[Dict[str, Any]] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


_DIMENSION_REQUIREMENTS: Dict[str, tuple[str, ...]] = {
    "management": ("management_capital", "governance", "shareholder_return"),
    "fundamentals": ("fundamentals", "profitability"),
    "rd": ("rd_team", "rd"),
    "chip_flow": ("shareholder_count", "margin"),
    "price_position": ("valuation_history", "valuation", "market_context"),
    "cycle_position": ("industry", "cycle_signal"),
    "policy_geopolitics": ("macro_rates", "fx", "major_events", "policy_events", "knowledge"),
    "retail_sentiment": ("sentiment", "debate"),
    "shareholder_returns": ("management_capital", "shareholder_return", "governance"),
    "growth_elasticity": ("profitability", "valuation_history", "valuation"),
    "a_share_structure": ("a_share_structure", "market_context", "margin", "institutional", "knowledge"),
    "risk_quality": ("fundamentals", "profitability", "governance"),
}

# Maximum acceptable age for evidence whose usefulness is strongly time-sensitive.
# Categories absent from this map are either historical by nature (e.g. announcements,
# dividends) or already carry their own freshness logic.
_FRESHNESS_DAYS: Dict[str, int] = {
    "quote": 3,
    "valuation_history": 10,
    "market_context": 10,
    "margin": 14,
    "shareholder_count": 190,
    "a_share_structure": 190,
    "cycle_signal": 10,
    "policy_events": 7,
    "profitability": 220,
    "fundamentals": 220,
    "rd_team": 550,
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


def _parse_as_of_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    text = str(value).strip()
    m = re.search(r"(20\d{2}|19\d{2})[-/]?(\d{2})[-/]?(\d{2})", text)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _latest_period_date(value: Any) -> Optional[str]:
    """Extract the latest YYYY-MM-DD-like date from nested evidence payloads."""
    candidates: List[date] = []

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            for v in obj.values():
                walk(v)
        elif isinstance(obj, (list, tuple)):
            for v in obj:
                walk(v)
        elif isinstance(obj, (str, date, datetime)):
            d = _parse_as_of_date(str(obj))
            if d:
                candidates.append(d)

    walk(value)
    return max(candidates).isoformat() if candidates else None


def _stale_evidence(
    evidence: List[EvidenceItem],
    *,
    today: Optional[date] = None,
) -> List[Dict[str, Any]]:
    today = today or datetime.now(ZoneInfo("Asia/Shanghai")).date()
    by_category: Dict[str, List[tuple[EvidenceItem, date]]] = {}
    for item in evidence:
        if item.category not in _FRESHNESS_DAYS:
            continue
        as_of = _parse_as_of_date(item.as_of)
        if as_of is not None:
            by_category.setdefault(item.category, []).append((item, as_of))

    rows: List[Dict[str, Any]] = []
    for category, dated_items in by_category.items():
        max_age = _FRESHNESS_DAYS[category]
        newest_item, newest_date = max(dated_items, key=lambda pair: pair[1])
        age = (today - newest_date).days
        # A current item in the same category rescues older snapshots from being treated as
        # the category's active evidence vintage.
        if age <= max_age:
            continue
        rows.append(
            {
                "category": category,
                "label": newest_item.label,
                "as_of": newest_date.isoformat(),
                "age_days": age,
                "max_age_days": max_age,
            }
        )
    rows.sort(key=lambda x: x["age_days"], reverse=True)
    return rows


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
    period: Optional[str] = None,
    published_at: Optional[str] = None,
    available_at: Optional[str] = None,
    retrieved_at: Optional[str] = None,
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
            period=period,
            published_at=published_at,
            available_at=available_at,
            retrieved_at=retrieved_at or datetime.now(ZoneInfo("UTC")).isoformat(),
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
        as_of=str(facts.get("finance_period") or facts.get("operate_period") or "") or None,
        tags=("financials", "customers", "suppliers", "cashflow", "working_capital", "receivables", "inventory"),
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
        as_of=str(facts.get("operate_period") or facts.get("finance_period") or "") or None,
        tags=("rd", "patent", "technology"),
        url=source_map.get("operate"),
    )

    rd_team = _get(inputs, "rd_team")
    if rd_team and rd_team.get("parse_status") == "ok":
        _add(
            out,
            category="rd_team",
            label="官方年报研发人员结构",
            source=rd_team.get("source_name") or "巨潮资讯年报",
            tier=rd_team.get("source_tier") or "S",
            kind="fact",
            value={
                "rd_headcount": rd_team.get("rd_headcount"),
                "rd_staff_ratio_pct": rd_team.get("rd_staff_ratio_pct"),
                "education": rd_team.get("education") or {},
                "age": rd_team.get("age") or {},
                "hit_pages": rd_team.get("hit_pages") or [],
                "report_title": rd_team.get("title"),
            },
            as_of=rd_team.get("published_at") or None,
            tags=("rd", "people", "education", "annual_report"),
            url=rd_team.get("pdf_url"),
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
        period=str((valuation_history or {}).get("as_of") or "") or None,
        available_at=str((valuation_history or {}).get("as_of") or "") or None,
        tags=("valuation", "percentile", "history"),
    )

    filing_calendar = _get(inputs, "filing_calendar") or []
    if filing_calendar:
        latest_filing = max(
            filing_calendar,
            key=lambda x: str(x.get("published_at") or ""),
        )
        _add(
            out,
            category="filing_calendar",
            label="周期财报实际发布日期日历",
            source="analysis.filing_calendar",
            tier="S",
            kind="fact",
            value=filing_calendar,
            as_of=str(latest_filing.get("published_at") or "") or None,
            period=str(latest_filing.get("period") or "") or None,
            published_at=str(latest_filing.get("published_at") or "") or None,
            available_at=str(latest_filing.get("published_at") or "") or None,
            tags=("financials", "filing", "availability", "as_of"),
            url=latest_filing.get("url"),
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
        as_of=_latest_period_date((profitability or {}).get("periods") or []),
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
        as_of=_latest_period_date(market),
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
        as_of=_latest_period_date(cycle_signal),
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

    management_capital = _get(inputs, "management_capital")
    _add(
        out,
        category="management_capital",
        label="管理层与长期资本分配账本",
        source="analysis.management_capital",
        tier=(management_capital or {}).get("source_tier") or "B",
        kind="derived",
        value=management_capital,
        as_of=str((management_capital or {}).get("as_of") or "") or None,
        tags=("management", "capital_allocation", "dividend", "buyback", "refinancing", "governance"),
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
        as_of=str((margin or {}).get("latest_date") or "") or None,
        tags=("margin_financing", "leverage", "chip"),
    )

    macro_rates = _get(inputs, "macro_rates")
    _add(
        out,
        category="macro_rates",
        label="中美利率环境",
        source="analysis.macro_rates",
        tier=(macro_rates or {}).get("source_tier") or "B",
        kind="fact",
        value=macro_rates,
        as_of=str((macro_rates or {}).get("as_of") or "") or None,
        tags=("rates", "fed", "treasury", "lpr", "macro"),
    )

    policy_events = _get(inputs, "policy_events")
    _add(
        out,
        category="policy_events",
        label="近期政策/地缘事件线索",
        source="analysis.policy_context",
        tier=(policy_events or {}).get("source_tier") or "B",
        kind=(policy_events or {}).get("kind") or "reported_event",
        value=policy_events,
        as_of=str((policy_events or {}).get("as_of") or "") or None,
        tags=("policy", "geopolitics", "news_clue", "trade", "rates"),
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
            published_at=item.get("published_at") or None,
            available_at=item.get("published_at") or None,
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


def evaluate_research_quality(
    evidence: List[EvidenceItem],
    *,
    today: Optional[date] = None,
) -> ResearchQuality:
    stale = _stale_evidence(evidence, today=today)
    stale_categories = {x["category"] for x in stale}
    categories = {e.category for e in evidence}
    effective_categories = categories - stale_categories
    covered: List[str] = []
    missing: List[str] = []

    for dim, needs in _DIMENSION_REQUIREMENTS.items():
        if any(category in effective_categories for category in needs):
            covered.append(dim)
        else:
            missing.append(dim)

    coverage = len(covered) / len(_DIMENSION_REQUIREMENTS)
    # 按证据类别而不是逐条记录计算来源质量；否则几十条巨潮公告会把比例虚高。
    category_quality: Dict[str, bool] = {}
    for e in evidence:
        is_high = (
            e.source_tier in {"S", "A", "B"}
            and e.kind in {"fact", "derived"}
        )
        category_quality[e.category] = category_quality.get(e.category, False) or is_high
    ratio = (
        sum(1 for ok in category_quality.values() if ok) / len(category_quality)
        if category_quality
        else 0.0
    )

    warnings: List[str] = []
    if stale:
        detail = "；".join(
            f"{x['label']}截止{x['as_of']}（{x['age_days']}天前）"
            for x in stale[:4]
        )
        warnings.append(
            "存在已过新鲜度阈值的时点型证据：" + detail
            + "。这些数据只能作历史背景，不能直接代表当前状态。"
        )
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
        stale_evidence=stale,
        warnings=warnings,
    )
