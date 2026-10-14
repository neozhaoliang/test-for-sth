# -*- coding: utf-8 -*-
"""
LLM synthesis stage for the investment report.

Input data has already been collected and normalized before entering this module.
Responsibilities here are limited to:
- run the 12 dimension tool contract;
- recover gracefully if tool calling is incomplete;
- review duplicate factors / contradictions;
- revise verifiably wrong factual claims without rewriting the manager's thesis;
- generate optional descriptive dimension visuals (never the investment verdict).
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, Dict, List

from analysis.evidence import EvidenceItem
from analysis.valuation_orchestrator import build_industry_valuation_report, industry_model_prompt_block
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
from analysis.public_report import strip_internal_process_commentary
from backtest.llm_client import call_analysis_with_tools, call_json_ex
from model.m_analysis import CandidateOpinion, StructuredSummary, KnowledgeExcerpt
from tools.utils import utils
from analysis.position_risk import position_risk_prompt_block, position_risk_context
from analysis.company_valuation_knowledge import (
    load_company_valuation_views, company_valuation_prompt,
)
from analysis.investment_method_distiller import (
    compile_investment_methods, investment_manager_prompt,
)
from analysis.market_style_reasons import (
    build_market_style_reasons, market_style_reasons_prompt_block,
)


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
    text = strip_internal_process_commentary("\n".join(kept).strip())
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
                # Send *only actionable* flags; info-only duplicate labels and
                # procedural messages are not evidence and invite verbatim
                # copying into the reader's investment conclusion.
                review_json=json.dumps({
                    "possible_conflicts": [
                        {"code": item.code, "factor": item.factor,
                         "dimensions": item.dimensions}
                        for item in review.possible_conflicts
                    ],
                    "weak_links": [
                        {"code": item.code, "factor": item.factor,
                         "dimensions": item.dimensions}
                        for item in review.weak_links
                    ],
                }, ensure_ascii=False),
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
        utils.logger.warning(
            f"[analysis.report] {stock_code} 维度评分未返回12项JSON数组；"
            "该图表是可选附加结果，不影响已完成的文字研究"
        )
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
    if len(scores) != len(_DIMENSION_LABELS):
        utils.logger.warning(
            f"[analysis.report] {stock_code} 维度评分只解析到"
            f" {len(scores)}/{len(_DIMENSION_LABELS)} 项；不补造分数"
        )
        return None
    return scores


async def _generate_summary(
    inputs: Any,
    candidates: List[CandidateOpinion],
    evidence: List[EvidenceItem],
) -> tuple[StructuredSummary, ResearchReview]:
    prompt = _build_prompt(inputs, candidates)
    from analysis.issuer_business import issuer_business_prompt_block
    prompt += "\n\n" + issuer_business_prompt_block(
        getattr(inputs, "issuer_business", None)
    )
    from analysis.issuer_capital import issuer_capital_prompt_block
    prompt += "\n\n" + issuer_capital_prompt_block(
        getattr(inputs, "issuer_capital_context", None)
    )
    # Fintech-equity financing is NOT interchangeable with margin loans.
    # The investor pays for issuer new equity via dilution/required return,
    # while shareholder secondary sales do not fund company projects.

    # Valuation is a method layer, never counted as primary factual evidence.
    # Load the locally versioned framework on every run (including fallback path).
    method_file = Path(__file__).resolve().parents[1] / "docs" / "valuation_shuangmulin_chensir_knowledge.md"
    # Prevent point-in-time backtests from seeing rules distilled after their as-of date.
    as_of = str(getattr(inputs, "as_of", "") or "")[:10]
    if method_file.is_file() and (not as_of or as_of >= "2026-10-08"):
        # Keep formulas and reasoning rules, never source identities or the
        # original-discussion bibliography in the company analysis prompt.
        method = method_file.read_text(encoding="utf-8")
        method = method.split("## 5. 可追溯原始讨论", 1)[0]
        method = method[method.find("## 1. "):] if "## 1. " in method else method
        # The numbered mandatory 'calculate every company in this order' section
        # is useful for offline formula testing, but not for a subjective PM
        # considering companies with radically different economics.
        if "## 3. Agent 强制计算顺序" in method:
            first, latter = method.split("## 3. Agent 强制计算顺序", 1)
            method = first + (
                "\n## 方法适用边界\n" + latter.split("## 4. 不得机械使用的边界", 1)[1]
                if "## 4. 不得机械使用的边界" in latter else ""
            )
        for author in ("陈chensir", "双木林叔", "买股票的老木匠", "军师祭咖啡"):
            method = method.replace(author, "投资方法")
        prompt += (
            "\n\n## 可复用估值方法（不得暴露来源身份或其他公司案例）\n"
            + method
            + "\n请仅使用本公司当期数据独立判断，禁止复述案例、作者、原文。"
        )
    elif not method_file.is_file():
        utils.logger.warning("[analysis.report] 估值方法知识库文件缺失，无法注入长期收益率模型")

    # Model family is selected by operating economics (industry classification),
    # NOT by whichever formula generates a flattering target price.
    # No explicit per-share forecast + dated provenance => no numeric target.
    if (getattr(inputs, "research_mode", "") == "historical"
            and as_of and as_of < "2026-10-09"):
        inputs.valuation_model_result = {
            "status": "historical_method_not_available",
            "valuation": {"status": "historical_method_not_available",
                          "reason": "行业估值引擎于2026-10-09才纳入研究流程"},
            "route": {}, "macro_context": {}, "interactive_inputs": None,
        }
        prompt += ("\\n\\n历史研究基准日早于行业估值引擎上线日；"
                   "不得把后来才形成的研究框架和宏观情景倒灌到历史报告，"
                   "不输出自动目标价。")
    else:
        inputs.valuation_model_result = build_industry_valuation_report(inputs)
        prompt += ("\\n\\n## 行业专用估值与A股宏观传导（不可虚构价值）\\n"
                   + industry_model_prompt_block(inputs.valuation_model_result))

    if (getattr(inputs, "research_mode", "") == "historical"
            and as_of and as_of < "2026-10-09"):
        # A post-hoc research methodology must not be backported into earlier
        # snapshots; actual historical issuer events remain in source archives.
        inputs.position_risk = {
            "status": "historical_method_not_available",
            "regime": "not_assessed",
            "risk_signals": [],
            "risk_asymmetry_triggered": False,
        }
    else:
        inputs.position_risk = position_risk_context(inputs)

    # Source-dated observations enter as inputs; they are NOT a factor-count
    # rule and have zero authority to override the eventual model stance.
    context = getattr(inputs, "position_risk", None)
    if context:
        prompt += "\n\n" + position_risk_prompt_block(context)

    # Integrate the private corpus as a causal hypothesis, not mere index stats.
    style = (getattr(inputs, 'valuation_model_result', None) or {}).get('style_context')
    if isinstance(style, dict):
        historical = getattr(inputs, 'research_mode', '') == 'historical'
        if historical and as_of and as_of < '2026-10-13':
            style['causal_knowledge_status'] = 'historical_method_not_available'
            style['causal_drivers'] = []
        else:
            reasons = await build_market_style_reasons(
                getattr(inputs, 'knowledge_excerpts', None) or [],
                # Exact style returns describe the outcome the hypothesis must explain.
                # General market_context is a stock/index summary and does not contain
                # the red-versus-growth pair; passing it left the model direction-blind.
                as_of=as_of, market_context=style,
                industry=str(getattr(getattr(inputs, 'research_profile', None),
                                     'industry', '') or ''),
            )
            style['causal_knowledge_status'] = reasons['status']
            style['causal_drivers'] = reasons['drivers']
            # Counts are internal diagnostics; never expose sources to the UI.
            style['private_excerpt_count'] = reasons['private_excerpt_count']
            style['private_transcript_count'] = reasons['private_transcript_count']
            utils.logger.info(
                '[analysis.report] 市场风格因果知识覆盖 usable=%d, transcript=%d, drivers=%d, status=%s',
                reasons['private_excerpt_count'], reasons['private_transcript_count'],
                len(reasons['drivers']), reasons['status'],
            )
        prompt += '\n\n' + market_style_reasons_prompt_block(style)

    # Subjective investment methods are synthesized from private knowledge,
    # then anonymized and checked before entering the final report model.
    # The model is free to disagree with any method, including its authors.
    profile = getattr(inputs, "research_profile", None)
    style_ctx = (getattr(inputs, "valuation_model_result", None) or {}).get("style_context") or {}
    if (getattr(inputs, "research_mode", "") == "historical"
            and as_of and as_of < "2026-10-12"):
        # This synthesis design was introduced in 2026-10. Older historical
        # backtests retain the ordinary manager principles but must not borrow
        # hindsight-distilled knowledge strategies.
        methods = []
    else:
        methods = await compile_investment_methods(
            getattr(inputs, "knowledge_excerpts", None) or [],
            as_of=str(getattr(inputs, "as_of", "") or ""),
            archetype=getattr(profile, "archetype", "general"),
            industry=getattr(profile, "industry", ""),
            stock_name=str(getattr(inputs, "stock_name", "") or ""),
            fallback_principles=style_ctx.get("investment_principles") or [],
        )
    prompt += "\n\n" + investment_manager_prompt(
        methods, archetype=getattr(profile, "archetype", "general"),
    )

    # Private issuer-specific historical theses must not disappear in a generic
    # "extract timeless principles" step. They carry testable assumptions such
    # as future profit and P/E, but are NOT certified company financial facts.
    # No live/late-researched thesis may be backfilled into old as-of reports.
    direct_views = []
    if getattr(inputs, "research_mode", "") != "historical":
        direct_views = await asyncio.to_thread(
            load_company_valuation_views,
            stock_name=str(getattr(inputs, "stock_name", "") or ""),
            stock_code=str(getattr(inputs, "stock_code", "") or ""),
            as_of=str(getattr(inputs, "as_of", "") or ""),
        )
    # Preserve the anonymous raw-derived excerpts in the PRIVATE report
    # container so the public projection can detect and remove any near-verbatim
    # phrases accidentally copied by the synthesis model.
    if direct_views:
        original_entries = getattr(inputs, "knowledge_excerpts", None)
        if isinstance(original_entries, list):
            original_entries.extend(
                KnowledgeExcerpt(
                    source="private_company_valuation_view",
                    title="公司历史估值假设",
                    distilled=item["hypothesis"],
                    published_at=item["published_at"],
                )
                for item in direct_views
            )
    model = getattr(inputs, "valuation_model_result", None)
    if isinstance(model, dict):
        model["company_view_coverage"] = {
            "status": "found_dated_hypotheses" if direct_views else "not_found_in_local_archive",
            "count": len(direct_views),
            "latest_published_at": direct_views[0]["published_at"] if direct_views else None,
            "note": ("研究者观点仅作为非事实的估值假设。"
                     "未发现文章不代表作者没有发表；本地归档可能滞后。"),
        }
    prompt += "\n\n" + company_valuation_prompt(
        direct_views, stock_name=str(getattr(inputs, "stock_name", "") or ""),
    )

    # 工具调用路径: 强制十二维度逐一分析后提交
    analyses, submit_input, tool_reason = await call_analysis_with_tools(
        prompt,
        _ANALYSIS_TOOLS + [_SUBMIT_REPORT_TOOL],
        _ANALYSIS_TOOL_NAMES,
        "submit_report",
    )
    parsed = submit_input if isinstance(submit_input, dict) else None
    fallback_stop_reason = None
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
        parsed, fallback_stop_reason = await call_json_ex(
            prompt, max_tokens=_SUMMARY_MAX_TOKENS, repair_requirements=_REPAIR_REQUIREMENTS
        )
        if not isinstance(parsed, dict):
            parsed = None

    if not parsed or parsed.get("stance") not in _VALID_STANCES:
        utils.logger.error(
            f"[analysis.report] {inputs.stock_code} 核心综合研究失败 "
            f"(tool_reason={tool_reason}, json_stop_reason={fallback_stop_reason}, "
            f"parsed={'dict' if isinstance(parsed, dict) else type(parsed).__name__})"
        )
        if fallback_stop_reason == "refusal":
            raise RuntimeError(
                "AI 模型/第三方兼容接口拒绝输出股票研究内容；"
                "这不是该公司财务数据或评分算法错误。"
                "请检查模型供应商过滤策略，或切换可正常输出结构化研究的模型。"
            )
        raise RuntimeError(
            "AI 模型未返回合格的核心结构化研究结果；"
            "请检查模型接口、工具调用兼容性及终端日志。"
            "为避免编造研究结论，本次没有发布报告。"
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
    # Data validity is enforced in report validation. A deterministic screening
    # rule must NEVER overwrite the LLM's portfolio-manager conclusion.
    return summary, review


