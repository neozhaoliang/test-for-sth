# -*- coding: utf-8 -*-
"""
Deterministic validation for a finished investment report.

This module does not judge whether the investment conclusion is right.  It checks whether
the report obeys the research contract:
- all 12 dimensions are present;
- stance fields use the allowed enum;
- confidence respects evidence quality / review penalties;
- counter-evidence and invalidation conditions are not silently omitted;
- stale time-sensitive evidence is not counted as current coverage;
- dimension scores have one entry per dimension and remain in range.

The validator is safe for CI because it performs no network or LLM calls.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Set

from pydantic import BaseModel, Field

from model.m_analysis import AnalysisReport


_VALID_STANCES = {"bullish", "bearish", "neutral"}

_DIMENSION_KEYS = [
    "management",
    "fundamentals",
    "rd",
    "chip_flow",
    "price_position",
    "cycle_position",
    "policy_geopolitics",
    "retail_sentiment",
    "shareholder_returns",
    "growth_elasticity",
    "a_share_structure",
    "risk_quality",
]

_TOOL_TO_DIMENSION = {
    "analyze_management": "management",
    "analyze_business_fundamentals": "fundamentals",
    "analyze_rd_capability": "rd",
    "analyze_chip_flow": "chip_flow",
    "analyze_price_position": "price_position",
    "analyze_cycle_position": "cycle_position",
    "analyze_policy_geopolitics": "policy_geopolitics",
    "analyze_retail_sentiment": "retail_sentiment",
    "analyze_shareholder_returns": "shareholder_returns",
    "analyze_growth_elasticity": "growth_elasticity",
    "analyze_a_share_structure": "a_share_structure",
    "analyze_risk_quality": "risk_quality",
}


class ValidationIssue(BaseModel):
    code: str
    severity: str = Field(description="error|warning")
    message: str


class ReportValidation(BaseModel):
    ok: bool = True
    errors: List[ValidationIssue] = Field(default_factory=list)
    warnings: List[ValidationIssue] = Field(default_factory=list)

    @property
    def issue_count(self) -> int:
        return len(self.errors) + len(self.warnings)


def _issue(code: str, severity: str, message: str) -> ValidationIssue:
    return ValidationIssue(code=code, severity=severity, message=message)


def _dimension_score_keys(scores: Optional[List[dict]]) -> Set[str]:
    out: Set[str] = set()
    for row in scores or []:
        key = str(row.get("dimension") or "").strip()
        if key:
            out.add(key)
    return out


def validate_report(report: AnalysisReport) -> ReportValidation:
    errors: List[ValidationIssue] = []
    warnings: List[ValidationIssue] = []
    summary = report.summary
    quality = report.research_quality
    review = report.review

    for field_name in ("stance", "company_quality_stance", "current_odds_stance"):
        value = str(getattr(summary, field_name, "") or "")
        if value not in _VALID_STANCES:
            errors.append(
                _issue(
                    "invalid_stance",
                    "error",
                    f"{field_name}={value!r} 不是 bullish/bearish/neutral 之一。",
                )
            )

    analyses = summary.dimension_analyses or {}
    present_dims = {
        _TOOL_TO_DIMENSION.get(k, k)
        for k, value in analyses.items()
        if str(value or "").strip()
    }
    missing_analyses = [x for x in _DIMENSION_KEYS if x not in present_dims]
    if missing_analyses:
        errors.append(
            _issue(
                "missing_dimension_analyses",
                "error",
                "缺少维度分析: " + ", ".join(missing_analyses),
            )
        )

    scores = summary.dimension_scores or []
    score_keys = _dimension_score_keys(scores)
    missing_scores = [x for x in _DIMENSION_KEYS if x not in score_keys]
    if missing_scores:
        errors.append(
            _issue(
                "missing_dimension_scores",
                "error",
                "缺少维度评分: " + ", ".join(missing_scores),
            )
        )
    if len(scores) != len(_DIMENSION_KEYS):
        errors.append(
            _issue(
                "dimension_score_count",
                "error",
                f"维度评分应恰有 {len(_DIMENSION_KEYS)} 项，实际 {len(scores)} 项。",
            )
        )
    for row in scores:
        try:
            score = float(row.get("score"))
        except (TypeError, ValueError):
            errors.append(
                _issue(
                    "invalid_dimension_score",
                    "error",
                    f"维度 {row.get('dimension')} 的 score 不是数字。",
                )
            )
            continue
        if not -10 <= score <= 10:
            errors.append(
                _issue(
                    "dimension_score_out_of_range",
                    "error",
                    f"维度 {row.get('dimension')} 的 score={score} 超出 [-10, 10]。",
                )
            )

    if not str(summary.thesis_summary or "").strip():
        errors.append(_issue("missing_thesis", "error", "thesis_summary 为空。"))
    if not str(summary.core_counter_evidence or "").strip():
        errors.append(
            _issue(
                "missing_counter_evidence",
                "error",
                "core_counter_evidence 为空；必须写最强反方证据或明确“未发现”。",
            )
        )
    if not str(summary.invalidation_condition or "").strip():
        errors.append(
            _issue(
                "missing_invalidation",
                "error",
                "invalidation_condition 为空；结论必须可证伪。",
            )
        )

    # Final confidence is capped by coverage and then reduced by deterministic review penalty.
    max_confidence = max(
        0.0,
        min(1.0, float(quality.coverage or 0.0))
        - float(review.confidence_penalty or 0.0),
    )
    if float(summary.confidence or 0.0) > max_confidence + 1e-6:
        errors.append(
            _issue(
                "confidence_exceeds_evidence",
                "error",
                f"confidence={summary.confidence:.3f} 高于证据允许上限 {max_confidence:.3f}。",
            )
        )

    stale_categories = {x.get("category") for x in quality.stale_evidence or []}
    if stale_categories:
        warnings.append(
            _issue(
                "stale_evidence_present",
                "warning",
                "存在过期时点证据: " + ", ".join(sorted(x for x in stale_categories if x)),
            )
        )

    if quality.coverage < 0.75:
        warnings.append(
            _issue(
                "low_evidence_coverage",
                "warning",
                f"证据覆盖率只有 {quality.coverage:.0%}。",
            )
        )

    if report.research_profile.missing_priority_evidence:
        warnings.append(
            _issue(
                "priority_evidence_missing",
                "warning",
                "公司画像重点证据缺失: "
                + ", ".join(report.research_profile.missing_priority_evidence),
            )
        )

    if not report.primary_evidence:
        warnings.append(
            _issue(
                "no_primary_evidence",
                "warning",
                "本次报告没有取得巨潮等一手公告证据。",
            )
        )

    return ReportValidation(
        ok=not errors,
        errors=errors,
        warnings=warnings,
    )
