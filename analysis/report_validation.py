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

from datetime import date, datetime
from math import isfinite, isclose
from typing import Dict, List, Optional, Set

from pydantic import BaseModel, Field

from model.m_analysis import AnalysisReport
from analysis.financial_consistency import coherent_yoy_pct
from analysis.management_capital import build_management_capital_record


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


def _parse_date(value) -> Optional[date]:
    if value in (None, ""):
        return None
    text = str(value).strip()[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _validate_historical_cutoff(report: AnalysisReport) -> List[ValidationIssue]:
    if str(report.research_mode or "") != "historical":
        return []

    cutoff = _parse_date(report.as_of)
    if cutoff is None:
        return [
            _issue(
                "invalid_historical_as_of",
                "error",
                f"historical report 的 as_of={report.as_of!r} 不是有效 YYYY-MM-DD。",
            )
        ]

    issues: List[ValidationIssue] = []
    for item in report.evidence:
        for field_name in ("available_at", "published_at"):
            value = getattr(item, field_name, None)
            dt = _parse_date(value)
            if dt is not None and dt > cutoff:
                issues.append(
                    _issue(
                        "future_evidence",
                        "error",
                        f"{item.label} 的 {field_name}={dt.isoformat()} 晚于历史截止日 "
                        f"{cutoff.isoformat()}。",
                    )
                )
        period = _parse_date(getattr(item, "period", None))
        if period is not None and period > cutoff:
            issues.append(
                _issue(
                    "future_period",
                    "error",
                    f"{item.label} 的 period={period.isoformat()} 晚于历史截止日 "
                    f"{cutoff.isoformat()}。",
                )
            )
    return issues


def _dimension_score_keys(scores: Optional[List[dict]]) -> Set[str]:
    out: Set[str] = set()
    for row in scores or []:
        key = str(row.get("key") or "").strip()
        if not key:
            # Backward compatibility with older reports/tests that stored the English
            # dimension key directly in "dimension".
            raw = str(row.get("dimension") or "").strip()
            key = raw if raw in _DIMENSION_KEYS else ""
        if key:
            out.add(key)
    return out


def _validate_data_consistency(report: AnalysisReport) -> List[ValidationIssue]:
    """Reject the source contradictions observed in the 601919 live acceptance.

    Inspect source values, not the model's interpretation. Historical requests must
    never be compared with a live F10 holder table.
    """
    issues: List[ValidationIssue] = []
    facts = (report.fundamentals or {}).get("facts") or {}
    if report.research_mode == "live":
        f10_dates = [
            _parse_date(facts.get("holder_count_period")),
            *[
                _parse_date(row.get("period"))
                for row in facts.get("holder_count_series") or []
                if row.get("holders")
            ],
        ]
        f10_latest = max((d for d in f10_dates if d), default=None)
        holder = report.shareholder_trend or {}
        selected = _parse_date(holder.get("period") or holder.get("as_of"))
        if f10_latest and (selected is None or selected < f10_latest):
            issues.append(_issue(
                "stale_shareholder_source", "error",
                f"股东户数采用期次 {selected}，早于已取得的F10期次 {f10_latest}。",
            ))

    values = [facts.get(key) for key in (
        "operating_cash_flow", "operating_cash_flow_previous",
        "operating_cash_flow_yoy_pct",
    )]
    if all(isinstance(v, (int, float)) and isfinite(v) for v in values):
        current, previous, reported = values
        checked = coherent_yoy_pct(current, previous, reported)
        if not isclose(checked, reported, abs_tol=0.01):
            issues.append(_issue(
                "cashflow_yoy_sign_conflict", "error",
                f"经营现金流同比 {reported}% 与同口径本期/上期绝对值相矛盾，应为 {checked}%。",
            ))

    if report.management_capital and (report.dividend_history or report.primary_evidence):
        cutoff = _parse_date(report.as_of) or _parse_date(report.management_capital.get("as_of"))
        if cutoff:
            expected = build_management_capital_record(
                dividend_history=report.dividend_history,
                primary_evidence=report.primary_evidence,
                as_of=cutoff,
            )
            for key in ("five_year", "ten_year"):
                actual = report.management_capital.get(key) or {}
                reference = expected[key]
                if actual and actual.get("dividend_years_count") != reference["dividend_years_count"]:
                    issues.append(_issue(
                        "dividend_years_conflict", "error",
                        f"{key}分红年数 {actual.get('dividend_years_count')} 与逐条记录/实施公告"
                        f"汇总结果 {reference['dividend_years_count']} 不一致。",
                    ))
                if reference["cash_dividend_per_10_total"] is None and actual.get("cash_dividend_per_10_total") == 0:
                    issues.append(_issue(
                        "unknown_dividend_amount_as_zero", "error",
                        f"{key}已取得分红实施证据但金额暂缺，不能记为现金分红0元。",
                    ))
    return issues


def validate_report(report: AnalysisReport) -> ReportValidation:
    errors: List[ValidationIssue] = []
    warnings: List[ValidationIssue] = []
    summary = report.summary
    quality = report.research_quality
    review = report.review

    errors.extend(_validate_historical_cutoff(report))
    errors.extend(_validate_data_consistency(report))

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
        min(
            1.0,
            float(quality.coverage or 0.0),
            float(report.research_profile.readiness or 0.0),
        )
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
