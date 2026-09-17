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
综合分析报告生成: 候选高可信度用户 (历史) + 实时抓取最新发帖 + 实时行情 -> LLM 摘要。
"""

import time
from typing import Dict, List

from analysis.candidates import find_candidates
from analysis.realtime_price import get_realtime_quote
from analysis.session import AnalysisBrowserSession
from backtest.extract import extract_stock_mentions, strip_html
from backtest.llm_client import call_text
from backtest.score import load_records
from model.m_analysis import AnalysisReport, CandidateOpinion
from tools.utils import utils

_MAX_LATEST_POSTS = 5
_MAX_HISTORICAL_THESIS = 5


def _historical_thesis(user_id: str, stock_code: str) -> List[str]:
    records = load_records(user_id)
    thesis = [r.get("thesis", "") for r in records if r.get("stock_code") == stock_code and r.get("thesis")]
    return thesis[-_MAX_HISTORICAL_THESIS:]


async def _latest_relevant_posts(session: AnalysisBrowserSession, user_id: str) -> List[str]:
    posts = await session.get_latest_posts(user_id, page_size=20)
    texts: List[str] = []
    for post in posts:
        if post.get("status_type") != "original":
            continue
        description = post.get("description") or ""
        if not extract_stock_mentions(description):
            continue
        text = strip_html(description)
        if text:
            texts.append(text)
        if len(texts) >= _MAX_LATEST_POSTS:
            break
    return texts


def _build_prompt(stock_code: str, quote: Dict, candidates: List[CandidateOpinion]) -> str:
    lines = [f"请基于以下信息，对股票 {stock_code} 的走势给出一段简明的中文综合分析（不构成投资建议）:"]

    if quote:
        lines.append(
            f"\n当前行情: 最新价 {quote.get('latest_price')}, "
            f"涨跌幅 {quote.get('change_pct')}%, 成交量 {quote.get('volume')}"
        )
    else:
        lines.append("\n当前行情: 暂无实时数据")

    lines.append("\n历史验证过的高可信度用户观点:")
    for c in candidates:
        lines.append(
            f"\n- {c.user_nickname} (历史命中率 {c.hit_rate:.1%}, "
            f"Wilson分 {c.wilson_score:.3f}, {c.correct}/{c.correct + c.incorrect})"
        )
        if c.historical_thesis:
            lines.append("  历史观点摘录:")
            for t in c.historical_thesis:
                lines.append(f"    · {t[:200]}")
        if c.latest_posts:
            lines.append("  最新发言摘录:")
            for t in c.latest_posts:
                lines.append(f"    · {t[:200]}")
        else:
            lines.append("  (未能获取到最新发言)")

    lines.append("\n请综合以上历史可信度、历史观点逻辑、最新发言和当前行情，给出一段分析文字。")
    return "\n".join(lines)


async def generate_report(stock_code: str) -> AnalysisReport:
    candidate_scores = find_candidates(stock_code)
    if not candidate_scores:
        return AnalysisReport(
            stock_code=stock_code,
            stock_name="",
            realtime_quote=None,
            candidates=[],
            llm_summary="暂无历史验证记录，无法生成可信度参考。",
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
                    historical_thesis=_historical_thesis(user.user_id, stock_code),
                    latest_posts=latest_posts,
                )
            )
    finally:
        if started:
            await session.close()

    quote = await get_realtime_quote(stock_code)

    prompt = _build_prompt(stock_code, quote, candidates)
    summary = await call_text(prompt) or "LLM 摘要生成失败，请参考以上原始数据自行判断。"

    return AnalysisReport(
        stock_code=stock_code,
        stock_name=stock_name,
        realtime_quote=quote,
        candidates=candidates,
        llm_summary=summary,
        generated_at=int(time.time()),
    )
