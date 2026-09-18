# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#
# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

"""
综合分析报告生成: 候选高可信度用户 (历史) + 实时抓取最新发帖 + 实时行情 -> LLM 结构化摘要。
"""

import time
from typing import Dict, List

from analysis.candidates import find_candidates
from analysis.realtime_price import get_realtime_quote
from analysis.session import AnalysisBrowserSession
from backtest.llm_client import call_json
from backtest.score import load_records
from model.m_analysis import AnalysisReport, CandidateOpinion, StructuredSummary
from tools.utils import utils

_MAX_LATEST_POSTS = 5
_MAX_HISTORICAL_THESIS = 5
_MIN_CORROBORATING_RECORDS = 2  # 历史验证记录 < 该值时提示"参考价值有限"

_PROMPT_VERSION = "v2-structured"

_VALID_STANCES = {"bullish", "bearish", "neutral"}

_PROMPT_TEMPLATE = """请基于以下信息，对股票 {stock_code} 的走势给出综合分析（不构成投资建议）。

当前行情: {quote_line}

历史验证过的高可信度用户观点 (每人历史命中率越高、验证样本越多，参考价值越大):
{candidates_block}

注意: "最新发言"是该用户最近发布的原创帖，不一定直接提到 {stock_code}，可能是对相关行业、关联股票或大盘的最新看法，仅作为判断其当前情绪/立场的背景参考。

请综合以上历史可信度、历史观点逻辑、最新发言背景和当前行情，以 JSON 格式返回，不要输出任何其他文字:
{{
  "stance": "bullish|bearish|neutral",
  "thesis_summary": "关键论据摘要，200字以内",
  "risk_notes": "风险提示/不确定性，150字以内"
}}"""


def _credibility_note(hit_rate: float, correct: int, incorrect: int) -> str:
    total = correct + incorrect
    if total < _MIN_CORROBORATING_RECORDS:
        return f"仅 {total} 条历史验证记录，参考价值有限"
    return f"基于 {total} 条历史预测验证，命中 {correct} 次，命中率 {hit_rate:.0%}"


def _historical_thesis(user_id: str, stock_code: str) -> List[str]:
    records = load_records(user_id)
    thesis = [r.get("thesis", "") for r in records if r.get("stock_code") == stock_code and r.get("thesis")]
    return thesis[-_MAX_HISTORICAL_THESIS:]


async def _latest_relevant_posts(session: AnalysisBrowserSession, user_id: str) -> List[str]:
    """取该用户最新几条原创帖正文，不要求提及目标股票 (行业/大盘看法也有参考价值)。"""
    from backtest.extract import strip_html

    posts = await session.get_latest_posts(user_id, page_size=20)
    texts: List[str] = []
    for post in posts:
        if post.get("status_type") != "original":
            continue
        text = strip_html(post.get("description") or "")
        if text:
            texts.append(text)
        if len(texts) >= _MAX_LATEST_POSTS:
            break
    return texts


def _build_prompt(stock_code: str, quote: Dict, candidates: List[CandidateOpinion]) -> str:
    if quote:
        quote_line = (
            f"最新价 {quote.get('latest_price')}, "
            f"涨跌幅 {quote.get('change_pct')}%, 成交量 {quote.get('volume')}"
        )
    else:
        quote_line = "暂无实时数据"

    candidate_lines = []
    for c in candidates:
        candidate_lines.append(f"- {c.user_nickname} ({c.credibility_note})")
        if c.historical_thesis:
            candidate_lines.append("  历史观点摘录:")
            for t in c.historical_thesis:
                candidate_lines.append(f"    · {t[:200]}")
        if c.latest_posts:
            candidate_lines.append("  最新发言摘录:")
            for t in c.latest_posts:
                candidate_lines.append(f"    · {t[:200]}")
        else:
            candidate_lines.append("  (未能获取到最新发言)")

    return _PROMPT_TEMPLATE.format(
        stock_code=stock_code,
        quote_line=quote_line,
        candidates_block="\n".join(candidate_lines),
    )


async def _generate_summary(stock_code: str, quote: Dict, candidates: List[CandidateOpinion]) -> StructuredSummary:
    prompt = _build_prompt(stock_code, quote, candidates)
    parsed = await call_json(prompt, max_tokens=1024)
    if not parsed or not isinstance(parsed, dict) or parsed.get("stance") not in _VALID_STANCES:
        return StructuredSummary(
            stance="",
            thesis_summary="",
            risk_notes="LLM 生成失败，请参考以上原始数据自行判断。",
        )
    return StructuredSummary(
        stance=parsed.get("stance", ""),
        thesis_summary=parsed.get("thesis_summary", "") or "",
        risk_notes=parsed.get("risk_notes", "") or "",
    )


async def generate_report(stock_code: str) -> AnalysisReport:
    candidate_scores = find_candidates(stock_code)
    if not candidate_scores:
        return AnalysisReport(
            stock_code=stock_code,
            stock_name="",
            realtime_quote=None,
            candidates=[],
            summary=StructuredSummary(
                stance="",
                thesis_summary="",
                risk_notes="暂无历史验证记录，无法生成可信度参考。",
            ),
            prompt_version=_PROMPT_VERSION,
            generated_at=int(time.time()),
        )

    stock_name = ""
    for user in candidate_scores:
        for stock in user.by_stock:
            if stock.stock_code == stock_code and stock.stock_name:
                stock_name = stock.stock_name
                break
        if stock_name:
            break

    session = AnalysisBrowserSession()
    started = await session.start()
    if not started:
        utils.logger.warning(f"[analysis.report] 浏览器会话启动失败，跳过最新发言抓取 (stock_code={stock_code})")

    candidates: List[CandidateOpinion] = []
    try:
        for user in candidate_scores:
            stock_score = next(s for s in user.by_stock if s.stock_code == stock_code)
            latest_posts = await _latest_relevant_posts(session, user.user_id) if started else []
            candidates.append(
                CandidateOpinion(
                    user_id=user.user_id,
                    user_nickname=user.user_nickname,
                    wilson_score=stock_score.wilson_score,
                    hit_rate=stock_score.hit_rate,
                    correct=stock_score.correct,
                    incorrect=stock_score.incorrect,
                    credibility_note=_credibility_note(stock_score.hit_rate, stock_score.correct, stock_score.incorrect),
                    historical_thesis=_historical_thesis(user.user_id, stock_code),
                    latest_posts=latest_posts,
                )
            )
    finally:
        if started:
            await session.close()

    quote = await get_realtime_quote(stock_code)

    summary = await _generate_summary(stock_code, quote, candidates)

    return AnalysisReport(
        stock_code=stock_code,
        stock_name=stock_name,
        realtime_quote=quote,
        candidates=candidates,
        summary=summary,
        prompt_version=_PROMPT_VERSION,
        generated_at=int(time.time()),
    )
