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
from analysis.knowledge_base import ensure_loaded as ensure_knowledge_base_loaded
from analysis.realtime_price import get_realtime_quote, get_stock_name
from analysis.session import AnalysisBrowserSession
from backtest.llm_client import call_json
from backtest.score import load_records
from model.m_analysis import AnalysisReport, CandidateOpinion, KnowledgeExcerpt, StructuredSummary
from tools.utils import utils

_MAX_LATEST_POSTS = 5
_MAX_HISTORICAL_THESIS = 5
_MIN_CORROBORATING_RECORDS = 2  # 历史验证记录 < 该值时提示"参考价值有限"

_PROMPT_VERSION = "v4-knowledge-base"

_VALID_STANCES = {"bullish", "bearish", "neutral"}
_VALID_LYNCH_CATEGORIES = {
    "fast_grower", "stalwart", "cyclical", "turnaround", "asset_play", "slow_grower", "unclear",
}

_PROMPT_TEMPLATE = """你是一名有明确立场的证券分析师，风格类似彼得林奇: 会先判断这是哪一类机会，再给出一个明确、可被证伪的结论。禁止给"多空都有可能""需持续观察"这类模糊结论——你必须选边站，哪怕证据不完美。

股票: {stock_code}
当前行情: {quote_line}

历史验证过的高可信度用户观点 (每人历史命中率越高、验证样本越多，参考价值越大):
{candidates_block}

知识库背景资料 (投资者专栏的长期观点，已去除闲聊，未针对本股票筛选，可能涉及投资哲学、筹码博弈、行情/宏观判断等，请自行判断哪些与当前分析相关):
{knowledge_block}

注意: "最新发言"是该用户最近发布的原创帖，不一定直接提到 {stock_code}，可能是对相关行业、关联股票或大盘的最新看法，仅作为判断其当前情绪/立场的背景参考。如果上述历史观点摘录中提到的股票代码/名称与 {stock_code} 不一致，以 {stock_code} 为准继续分析，不要就此提出疑问或中断输出——不确定的地方直接写入 risk_notes。

按以下步骤分析:
1. 先判断这只股票当前更接近彼得林奇分类中的哪一类: fast_grower(高成长)、stalwart(大盘稳健股)、cyclical(周期股)、turnaround(困境反转)、asset_play(资产价值被低估)、slow_grower(低增长)。如果证据完全不足以判断，才选 unclear。
2. 基于这个分类和上面的证据，给出一个明确倾向 (bullish/bearish/neutral)，neutral 仅在证据真正相互抵消、没有任何一方占优时才能选，不能用来逃避判断。
3. 给出支撑这个判断的关键论据。
4. 明确说明: 如果接下来出现什么具体情况/数据，会证明这个判断是错的 (invalidation_condition)。这一步是强制的，不能写"无法确定"之类的话。
5. 列出其他需要注意的风险/不确定性。

无论证据是否充分、是否有疑点，都必须直接输出下面的 JSON，不要输出任何其他文字，不要提出反问或要求澄清:
{{
  "lynch_category": "fast_grower|stalwart|cyclical|turnaround|asset_play|slow_grower|unclear",
  "stance": "bullish|bearish|neutral",
  "thesis_summary": "关键论据摘要，200字以内",
  "invalidation_condition": "什么情况出现会证明这个判断错了，100字以内",
  "risk_notes": "其他风险提示/不确定性，150字以内"
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


async def _load_knowledge_excerpts() -> List[KnowledgeExcerpt]:
    """知识库不按股票筛选，全量加载 (原文已在知识库加载阶段由 LLM 提炼为投资观点摘要)。"""
    entries = await ensure_knowledge_base_loaded()
    return [
        KnowledgeExcerpt(source=e.source, title=e.title, distilled=e.distilled)
        for e in entries
        if e.distilled
    ]


def _build_prompt(
    stock_code: str,
    quote: Dict,
    candidates: List[CandidateOpinion],
    knowledge_excerpts: List[KnowledgeExcerpt],
) -> str:
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
    if not candidate_lines:
        candidate_lines.append("(暂无)")

    knowledge_lines = []
    for k in knowledge_excerpts:
        knowledge_lines.append(f"- 《{k.title}》 ({k.source})")
        knowledge_lines.append(f"    {k.distilled}")
    if not knowledge_lines:
        knowledge_lines.append("(暂无背景资料)")

    return _PROMPT_TEMPLATE.format(
        stock_code=stock_code,
        quote_line=quote_line,
        candidates_block="\n".join(candidate_lines),
        knowledge_block="\n".join(knowledge_lines),
    )


async def _generate_summary(
    stock_code: str,
    quote: Dict,
    candidates: List[CandidateOpinion],
    knowledge_excerpts: List[KnowledgeExcerpt],
) -> StructuredSummary:
    prompt = _build_prompt(stock_code, quote, candidates, knowledge_excerpts)
    parsed = await call_json(prompt, max_tokens=2048)
    if not parsed or not isinstance(parsed, dict) or parsed.get("stance") not in _VALID_STANCES:
        return StructuredSummary(
            lynch_category="",
            stance="",
            thesis_summary="",
            invalidation_condition="",
            risk_notes="LLM 生成失败，请参考以上原始数据自行判断。",
        )
    lynch_category = parsed.get("lynch_category", "")
    if lynch_category not in _VALID_LYNCH_CATEGORIES:
        lynch_category = "unclear"
    return StructuredSummary(
        lynch_category=lynch_category,
        stance=parsed.get("stance", ""),
        thesis_summary=parsed.get("thesis_summary", "") or "",
        invalidation_condition=parsed.get("invalidation_condition", "") or "",
        risk_notes=parsed.get("risk_notes", "") or "",
    )


async def generate_report(stock_code: str) -> AnalysisReport:
    candidate_scores = find_candidates(stock_code)

    stock_name = ""
    for user in candidate_scores:
        for stock in user.by_stock:
            if stock.stock_code == stock_code and stock.stock_name:
                stock_name = stock.stock_name
                break
        if stock_name:
            break

    quote = await get_realtime_quote(stock_code)
    if not stock_name:
        stock_name = await get_stock_name(stock_code) or ""

    knowledge_excerpts = await _load_knowledge_excerpts()

    if not candidate_scores:
        summary = await _generate_summary(stock_code, quote, [], knowledge_excerpts)
        return AnalysisReport(
            stock_code=stock_code,
            stock_name=stock_name,
            realtime_quote=quote,
            candidates=[],
            knowledge_excerpts=knowledge_excerpts,
            summary=summary,
            prompt_version=_PROMPT_VERSION,
            generated_at=int(time.time()),
        )

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

    summary = await _generate_summary(stock_code, quote, candidates, knowledge_excerpts)

    return AnalysisReport(
        stock_code=stock_code,
        stock_name=stock_name,
        realtime_quote=quote,
        candidates=candidates,
        knowledge_excerpts=knowledge_excerpts,
        summary=summary,
        prompt_version=_PROMPT_VERSION,
        generated_at=int(time.time()),
    )
