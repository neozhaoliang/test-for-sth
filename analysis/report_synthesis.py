# -*- coding: utf-8 -*-
"""
LLM synthesis stage for the investment report.

Input data has already been collected and normalized before entering this module.
Responsibilities here are limited to:
- run the 12 dimension tool contract;
- recover gracefully if tool calling is incomplete;
- review duplicate factors / contradictions;
- revise the final synthesis without inventing new evidence;
- generate dimension scores for visualization.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List

from analysis.evidence import EvidenceItem
from analysis.report_blocks import _build_prompt
from analysis.report_contract import (
    _ANALYSIS_TOOL_NAMES,
    _ANALYSIS_TOOLS,
    _DIMENSION_LABELS,
    _REPAIR_REQUIREMENTS,
    _REVIEW_SYNTHESIS_PROMPT,
    _SCORE_PROMPT,
    _SCORE_REPAIR,
    _SUBMIT_REPORT_TOOL,
    _SUMMARY_MAX_TOKENS,
    _VALID_LYNCH_CATEGORIES,
    _VALID_STANCES,
)
from analysis.reviewer import ResearchReview, review_dimension_analyses
from backtest.llm_client import call_analysis_with_tools, call_json_ex
from model.m_analysis import CandidateOpinion, StructuredSummary
from tools.utils import utils


_PROCESS_TERM_RE = re.compile(r"六查|第[一二三四五六七八九十\d]+步|检查项\d*")

# 立场枚举值泄漏进中文正文时替换为中文
_ENUM_LABEL_MAP = {"bullish": "看多", "bearish": "看空", "neutral": "中性"}


def _scrub_process_terms(text: str, stock_code: str) -> str:
    """兜底清洗摘要输出: 剔除流程用语行 (如"六查""第3步")，并把泄漏进
    正文的英文立场枚举值换成中文。prompt 已禁用，这里防模型漏网。"""
    if not text:
        return text
    kept = [ln for ln in text.split("\n") if not _PROCESS_TERM_RE.search(ln)]
    if len(kept) != len(text.split("\n")):
        utils.logger.warning(
            f"[analysis.report] {stock_code} 摘要输出包含流程用语，已剔除 "
            f"{len(text.split(chr(10))) - len(kept)} 行"
        )
    text = "\n".join(kept).strip()
    for en, zh in _ENUM_LABEL_MAP.items():
        # 中文与英文之间 \b 不生效 (中文也是 word 字符), 用 ASCII 字母边界
        text = re.sub(rf"(?<![A-Za-z]){en}(?![A-Za-z])", zh, text, flags=re.IGNORECASE)
    return text


async def _apply_review_to_draft(
    parsed: Dict,
    review: ResearchReview,
    stock_code: str,
) -> Dict:
    if not (review.possible_conflicts or review.weak_links):
        return parsed
    try:
        revised, _ = await call_json_ex(
            _REVIEW_SYNTHESIS_PROMPT.format(
                draft_json=json.dumps(parsed, ensure_ascii=False),
                review_json=json.dumps(review.model_dump(), ensure_ascii=False),
            ),
            max_tokens=4096,
            repair_requirements=_REPAIR_REQUIREMENTS,
        )
    except Exception as e:
        utils.logger.warning(f"[analysis.report] {stock_code} 二次审查综合失败: {e}")
        return parsed

    if not isinstance(revised, dict) or revised.get("stance") not in _VALID_STANCES:
        return parsed
    for key in ("company_quality_stance", "current_odds_stance"):
        if revised.get(key) not in _VALID_STANCES:
            revised[key] = parsed.get(key, "")
    return revised


async def _score_dimensions(
    analyses: Dict[str, str], stock_code: str
) -> Optional[List[Dict]]:
    """对十二个维度分析打分 (-10 利空 ~ +10 利多), 供 UI 展示。失败返回 None。"""
    block = "\n\n".join(f"### {name}\n{text}" for name, text in analyses.items() if text)
    if not block:
        return None
    try:
        parsed, _ = await call_json_ex(
            _SCORE_PROMPT.format(analyses_block=block),
            max_tokens=2048,
            repair_requirements=_SCORE_REPAIR,
        )
    except Exception as e:
        utils.logger.warning(f"[analysis.report] {stock_code} 维度打分失败: {e}")
        return None
    if not isinstance(parsed, list):
        return None
    scores: List[Dict] = []
    seen = set()
    for item in parsed:
        if not isinstance(item, dict):
            continue
        dim = str(item.get("dimension", ""))
        if dim not in _DIMENSION_LABELS or dim in seen:
            continue
        try:
            score = max(-10.0, min(10.0, float(item.get("score", 0))))
        except (TypeError, ValueError):
            continue
        seen.add(dim)
        scores.append(
            {
                "dimension": _DIMENSION_LABELS[dim],
                "key": dim,
                "score": round(score, 1),
                "note": str(item.get("note", "") or "")[:30],
            }
        )
    return scores if len(scores) == len(_DIMENSION_LABELS) else None


async def _generate_summary(
    inputs: Any,
    candidates: List[CandidateOpinion],
    evidence: List[EvidenceItem],
) -> tuple[StructuredSummary, ResearchReview]:
    prompt = _build_prompt(inputs, candidates)
    # Valuation is a method layer, never counted as primary factual evidence.
    # Load the locally versioned framework on every run (including fallback path).
    method_file = Path(__file__).resolve().parents[1] / "docs" / "valuation_shuangmulin_chensir_knowledge.md"
    if method_file.is_file():
        prompt += ("\n\n## 估值方法知识库（待以本次数据验证，不构成事实证据）\n"
                   + method_file.read_text(encoding="utf-8"))
    else:
        utils.logger.warning("[analysis.report] 估值方法知识库文件缺失，无法注入长期收益率模型")

    # 工具调用路径: 强制十二维度逐一分析后提交
    analyses, submit_input, tool_reason = await call_analysis_with_tools(
        prompt,
        _ANALYSIS_TOOLS + [_SUBMIT_REPORT_TOOL],
        _ANALYSIS_TOOL_NAMES,
        "submit_report",
    )
    parsed = submit_input if isinstance(submit_input, dict) else None
    if parsed and parsed.get("stance") in _VALID_STANCES:
        utils.logger.info(
            f"[analysis.report] {inputs.stock_code} 工具调用完成: "
            f"{len(analyses)}/12 个维度, 提交正常"
        )
    else:
        # 回退: 网关/模型不支持工具或未走完流程时, 用维度分析拼接后走普通 JSON 调用
        if analyses:
            utils.logger.warning(
                f"[analysis.report] {inputs.stock_code} 工具调用未完成 "
                f"({len(analyses)}/12, reason={tool_reason}), 回退为拼接调用"
            )
            prompt = (
                prompt
                + "\n\n以下是已完成的维度分析:\n"
                + "\n\n".join(f"### {name}\n{text}" for name, text in analyses.items())
                + "\n\n请基于以上维度分析与数据块输出最终结构化报告 JSON。"
            )
        parsed, stop_reason = await call_json_ex(
            prompt, max_tokens=_SUMMARY_MAX_TOKENS, repair_requirements=_REPAIR_REQUIREMENTS
        )
        if not isinstance(parsed, dict):
            parsed = None

    if not parsed or parsed.get("stance") not in _VALID_STANCES:
        utils.logger.error(
            f"[analysis.report] {inputs.stock_code} 摘要生成失败 "
            f"(tool_reason={tool_reason}, parsed={'dict' if isinstance(parsed, dict) else type(parsed).__name__})"
        )
        return (
            StructuredSummary(
                lynch_category="",
                stance="",
                thesis_summary="",
                core_counter_evidence="",
                invalidation_condition="",
                risk_notes="LLM 生成失败，请参考以上原始数据自行判断。",
            ),
            ResearchReview(),
        )
    review = review_dimension_analyses(analyses, evidence)
    parsed = await _apply_review_to_draft(parsed, review, inputs.stock_code)

    lynch_category = parsed.get("lynch_category", "")
    if lynch_category not in _VALID_LYNCH_CATEGORIES:
        lynch_category = "unclear"
    dimension_scores = await _score_dimensions(analyses, inputs.stock_code)
    summary = StructuredSummary(
        lynch_category=lynch_category,
        stance=parsed.get("stance", ""),
        company_quality_stance=parsed.get("company_quality_stance", ""),
        current_odds_stance=parsed.get("current_odds_stance", ""),
        confidence=max(0.0, min(1.0, float(parsed.get("confidence", 0.0) or 0.0))),
        thesis_summary=_scrub_process_terms(parsed.get("thesis_summary", "") or "", inputs.stock_code),
        core_counter_evidence=_scrub_process_terms(
            parsed.get("core_counter_evidence", "") or "", inputs.stock_code
        ),
        invalidation_condition=_scrub_process_terms(
            parsed.get("invalidation_condition", "") or "", inputs.stock_code
        ),
        risk_notes=_scrub_process_terms(parsed.get("risk_notes", "") or "", inputs.stock_code),
        dimension_scores=dimension_scores,
        dimension_analyses=analyses or None,
    )
    return summary, review


