# -*- coding: utf-8 -*-
"""
Cross-dimension research review.

This is intentionally deterministic.  The reviewer does not invent a new investment view;
it checks whether the 12 dimension analyses are leaning on the same factor repeatedly,
appear to contradict each other, or make directional claims without the expected evidence.

The resulting findings are shown to users and also cap report confidence.
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List

from pydantic import BaseModel, Field

from analysis.evidence import EvidenceItem


class ReviewFinding(BaseModel):
    code: str
    factor: str
    severity: str = Field(default="warning", description="info|warning")
    dimensions: List[str] = Field(default_factory=list)
    message: str


class ResearchReview(BaseModel):
    duplicate_factors: List[ReviewFinding] = Field(default_factory=list)
    possible_conflicts: List[ReviewFinding] = Field(default_factory=list)
    weak_links: List[ReviewFinding] = Field(default_factory=list)
    confidence_penalty: float = Field(default=0.0, ge=0.0, le=0.35)


_DIM_LABELS = {
    "analyze_management": "管理层与治理",
    "analyze_business_fundamentals": "经营基本面与护城河",
    "analyze_rd_capability": "研发、人才与技术护城河",
    "analyze_chip_flow": "筹码与散户结构",
    "analyze_price_position": "价格与估值位置",
    "analyze_cycle_position": "行业周期位置",
    "analyze_policy_geopolitics": "政策、利率、汇率与地缘",
    "analyze_retail_sentiment": "雪球散户情绪",
    "analyze_shareholder_returns": "股东回报与资本抽取",
    "analyze_growth_elasticity": "增长空间与股票弹性",
    "analyze_a_share_structure": "A股资金结构与市场风格",
    "analyze_risk_quality": "财务质量与尾部风险",
}

_FACTOR_RULES = {
    "估值/价格": {
        "patterns": (r"\bPE\b", r"\bPB\b", r"市盈率", r"市净率", r"估值", r"历史分位", r"股价位置"),
        "expected": {
            "analyze_price_position",
            "analyze_growth_elasticity",
        },
    },
    "股东户数/筹码": {
        "patterns": (r"股东户数", r"筹码集中", r"筹码分散", r"派发", r"散户"),
        "expected": {
            "analyze_chip_flow",
            "analyze_a_share_structure",
        },
    },
    "融资盘/杠杆": {
        "patterns": (r"融资余额", r"融资盘", r"去杠杆", r"杠杆资金", r"融资买入"),
        "expected": {
            "analyze_chip_flow",
            "analyze_a_share_structure",
            "analyze_risk_quality",
        },
    },
    "盈利/现金流": {
        "patterns": (r"经营现金流", r"现金流", r"ROE", r"净利率", r"毛利率", r"负债率", r"应收", r"存货", r"库存"),
        "expected": {
            "analyze_business_fundamentals",
            "analyze_growth_elasticity",
            "analyze_risk_quality",
        },
    },
    "周期景气": {
        "patterns": (r"周期", r"景气", r"运价", r"铜价", r"产品价格", r"库存", r"开工率"),
        "expected": {
            "analyze_cycle_position",
            "analyze_growth_elasticity",
            "analyze_policy_geopolitics",
        },
    },
    "分红/回购/再融资": {
        "patterns": (r"分红", r"回购", r"定增", r"配股", r"再融资", r"减持"),
        "expected": {
            "analyze_management",
            "analyze_shareholder_returns",
            "analyze_risk_quality",
        },
    },
}

_REQUIRED_CATEGORIES = {
    "analyze_management": {"management_capital", "governance", "shareholder_return", "primary"},
    "analyze_business_fundamentals": {"fundamentals", "profitability"},
    "analyze_rd_capability": {"rd_team", "rd"},
    "analyze_chip_flow": {"shareholder_count", "margin"},
    "analyze_price_position": {"valuation_history", "valuation", "market_context"},
    "analyze_cycle_position": {"cycle_signal", "industry"},
    "analyze_policy_geopolitics": {"macro_rates", "fx", "major_events", "primary", "knowledge"},
    "analyze_retail_sentiment": {"sentiment", "debate"},
    "analyze_shareholder_returns": {"management_capital", "shareholder_return", "governance", "primary"},
    "analyze_growth_elasticity": {"profitability", "valuation_history", "valuation"},
    "analyze_a_share_structure": {"a_share_structure", "market_context", "margin", "institutional", "knowledge"},
    "analyze_risk_quality": {"fundamentals", "profitability", "governance", "primary"},
}

_CONFLICT_RULES = (
    (
        "估值判断",
        (r"高估", r"估值偏高", r"估值昂贵", r"明显透支"),
        (r"低估", r"估值偏低", r"估值便宜", r"明显低估"),
    ),
    (
        "筹码状态",
        (r"筹码集中", r"股东户数减少", r"去杠杆"),
        (r"筹码分散", r"股东户数增加", r"融资拥挤", r"杠杆拥挤"),
    ),
    (
        "周期方向",
        (r"景气上行", r"周期上行", r"景气回升", r"价格上行"),
        (r"景气下行", r"周期下行", r"景气回落", r"价格下行"),
    ),
)


def _match_any(text: str, patterns: Iterable[str]) -> bool:
    return any(re.search(p, text, re.I) for p in patterns)


def _is_missing_analysis(text: str) -> bool:
    return not text.strip() or "数据暂缺" in text or "暂缺" == text.strip()


def review_dimension_analyses(
    analyses: Dict[str, str],
    evidence: List[EvidenceItem],
) -> ResearchReview:
    categories = {e.category for e in evidence}
    duplicates: List[ReviewFinding] = []
    conflicts: List[ReviewFinding] = []
    weak_links: List[ReviewFinding] = []

    # 1) The same economic factor appearing across several dimensions is not automatically
    # wrong, but synthesis must count the underlying fact once rather than as independent votes.
    for factor, rule in _FACTOR_RULES.items():
        hit_dims = [
            dim for dim, text in analyses.items()
            if text and _match_any(text, rule["patterns"])
        ]
        if len(hit_dims) >= 2:
            duplicates.append(
                ReviewFinding(
                    code="duplicate_factor",
                    factor=factor,
                    severity="info",
                    dimensions=[_DIM_LABELS.get(d, d) for d in hit_dims],
                    message=f"{factor}同时出现在多个分析方向；综合判断时应作为同一组底层事实，只计一次影响。",
                )
            )

    # 2) Surface apparent contradictions for reconciliation.  These are flags, not verdicts:
    # different time windows can legitimately produce opposite statements.
    for factor, positive_patterns, negative_patterns in _CONFLICT_RULES:
        pos_dims = [
            dim for dim, text in analyses.items()
            if text and _match_any(text, positive_patterns)
        ]
        neg_dims = [
            dim for dim, text in analyses.items()
            if text and _match_any(text, negative_patterns)
        ]
        if pos_dims and neg_dims and set(pos_dims) != set(neg_dims):
            dims = list(dict.fromkeys(pos_dims + neg_dims))
            conflicts.append(
                ReviewFinding(
                    code="possible_conflict",
                    factor=factor,
                    severity="warning",
                    dimensions=[_DIM_LABELS.get(d, d) for d in dims],
                    message=f"{factor}存在方向相反的表述；需要核对是否来自不同日期/口径，不能直接同时计入综合结论。",
                )
            )

    # 3) Directional analysis without the expected evidence is a weak link.
    for dim, text in analyses.items():
        if _is_missing_analysis(text):
            continue
        required = _REQUIRED_CATEGORIES.get(dim)
        if not required:
            continue
        if not categories.intersection(required):
            weak_links.append(
                ReviewFinding(
                    code="missing_support",
                    factor=_DIM_LABELS.get(dim, dim),
                    severity="warning",
                    dimensions=[_DIM_LABELS.get(dim, dim)],
                    message="该方向给出了实质判断，但本次证据账本没有对应的基础数据；应降置信度或改写为资料不足。",
                )
            )

    # Primary-source-sensitive dimensions get an extra warning when they only rely on
    # third-party/derived data.
    if "primary" not in categories:
        for dim in ("analyze_management", "analyze_shareholder_returns", "analyze_risk_quality"):
            text = analyses.get(dim, "")
            if text and not _is_missing_analysis(text):
                weak_links.append(
                    ReviewFinding(
                        code="no_primary_source",
                        factor=_DIM_LABELS[dim],
                        severity="warning",
                        dimensions=[_DIM_LABELS[dim]],
                        message="本次没有取得巨潮等一手公告证据；治理、资本运作或尾部风险判断应保留来源限制。",
                    )
                )

    penalty = min(
        0.35,
        len(duplicates) * 0.015 + len(conflicts) * 0.05 + len(weak_links) * 0.04,
    )
    return ResearchReview(
        duplicate_factors=duplicates,
        possible_conflicts=conflicts,
        weak_links=weak_links,
        confidence_penalty=round(penalty, 3),
    )
