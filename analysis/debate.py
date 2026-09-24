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
多空辩论: 抓取该股讨论区最近多条来自不同用户的表态, 复用回测的分类/验证
管线逐条分析——每方核心论点是什么、有时间范围的股价预测是否在历史上
验证过——最后对比哪一方更合理。情绪维度 (sentiment) 由同一批分类结果
派生, 不再重复调用 LLM。
"""

import asyncio
from typing import Any, Dict, List, Optional

from backtest.extract import strip_html
from backtest.llm_client import call_json, call_text
from backtest.verify import verify_prediction
from media_platform.xueqiu.client import XueqiuClient
from tools.utils import utils

from .sentiment import _symbol, fetch_discussion_posts

_TARGET_POSTS = 300
_CLASSIFY_CONCURRENCY = 3
_FETCH_BUDGET_S = 150
_VERIFY_TIMEOUT_S = 20

_CORE_PROMPT = """以下是一位或多位雪球用户关于"{stock_name}"({stock_code})的{side_label}论点 (每条都是用户原话提炼)。请归纳出其中最核心的 {max_n} 条论点, 每条保留: 论点 + 论据 (原话里的关键数字/逻辑)。300字以内, 要点式中文, 直接输出内容本身。

论点列表:
{items_block}"""

_DISCUSS_PROMPT = """以下是雪球用户关于"{stock_name}"({stock_code})的一条近期表态。请判断:
1. stance: 该作者对这只股票的态度
   - bullish: 明确看多 (看好后市/打算买入/认为低估)
   - bearish: 明确看空 (看空/打算卖出/认为高估或基本面恶化)
   - neutral: 讨论但不站队 (摆事实/提问/转述/记录操作)
   - irrelevant: 与该股票无关 (灌水/广告/无关转发)
2. 若 stance 为 bullish/bearish:
   - direction: 对股价方向的预测 (bullish/bearish/topped_out/bottomed_out), 只谈好坏没谈股价方向则留空字符串;
   - time_horizon: 作者明确说的验证时间范围原文 (如"年内""三个月"), 没有则空字符串;
   - thesis: 其核心论点 (不超过150字);
   - evidence: 其论据 (保留原话关键数字, 不超过200字, 没有则空字符串);
   - logic_novelty/depth/consistency: 1-5 整数 (新颖性/推理深度/自洽性)。
只输出 JSON, 不要任何其他文字:
{{"stance": "bullish|bearish|neutral|irrelevant", "direction": "...", "time_horizon": "...", "thesis": "...", "evidence": "...", "logic_novelty": 1, "logic_depth": 1, "logic_consistency": 1}}

表态内容:
{text}"""

_EVAL_PROMPT = """以下是雪球讨论区关于"{stock_name}"({stock_code})多空双方的核心论点与历史验证统计。请判断当前哪一方更合理。

多方核心论点:
{bull_block}

空方核心论点:
{bear_block}

历史验证统计 (讨论区用户的历史方向性股价预测, 需有明确时间范围):
- 多方: 验证 {bull_verified} 条, 正确 {bull_correct}, 错误 {bull_incorrect}; 另有未验证预测 {bull_unverified} 条
- 空方: 验证 {bear_verified} 条, 正确 {bear_correct}, 错误 {bear_incorrect}; 另有未验证预测 {bear_unverified} 条

判断要求:
1. 逐条比较双方论点的逻辑质量 (论据是否具体、是否可证伪、是否有数据支撑);
2. 结合历史验证命中率 (样本不足时说明样本不足, 不得外推);
3. 给出结论: bull_more_reasonable / bear_more_reasonable / undecided。
只输出 JSON, 不要任何其他文字:
{{"verdict": "bull_more_reasonable|bear_more_reasonable|undecided", "reason": "150字以内理由", "bull_core": "多方核心论点一句话", "bear_core": "空方核心论点一句话"}}"""


async def _classify_posts(posts: List[Dict], sym: str, stock_name: str) -> List[Dict]:
    """
    逐条用讨论表态专用 prompt 分类 (回测分类要求"有论据的方向性预测",
    对讨论区太严格): 先判立场 (bullish/bearish/neutral/irrelevant),
    有方向且有明确时间范围的主张再做股价验证。
    """
    semaphore = asyncio.Semaphore(_CLASSIFY_CONCURRENCY)

    async def _one(post: Dict) -> Optional[Dict]:
        text = strip_html(post.get("description") or post.get("text") or "")
        if not text:
            return None
        async with semaphore:
            try:
                parsed = await call_json(
                    _DISCUSS_PROMPT.format(
                        stock_name=stock_name, stock_code=sym, text=text[:300]
                    ),
                    max_tokens=1024,
                )
            except Exception as e:
                utils.logger.warning(f"[debate] 分类失败 {post.get('status_id')}: {e}")
                return None
        if not isinstance(parsed, dict):
            return None
        stance = parsed.get("stance", "")
        if stance not in ("bullish", "bearish", "neutral", "irrelevant"):
            return None
        item = {
            "status_id": post.get("status_id", ""),
            "user_id": post.get("user_id", ""),
            "created_at": int(post.get("created_at") or 0),
            "stance": stance,
            "direction": parsed.get("direction", "") or "",
            "time_horizon": parsed.get("time_horizon", "") or "",
            "thesis": (parsed.get("thesis", "") or "").strip(),
            "evidence": (parsed.get("evidence", "") or "").strip(),
            "logic_novelty": _score(parsed.get("logic_novelty")),
            "logic_depth": _score(parsed.get("logic_depth")),
            "logic_consistency": _score(parsed.get("logic_consistency")),
            "verdict": "no_horizon",
        }
        # 有方向且有时间范围的股价主张做验证 (与回测同一口径)
        if item["direction"] in ("bullish", "bearish", "topped_out", "bottomed_out") and item["time_horizon"]:
            if item["created_at"]:
                try:
                    result = await asyncio.wait_for(
                        verify_prediction(
                            {
                                "post": {"created_at": item["created_at"]},
                                "stock_code": sym,
                                "prediction_type": "price",
                                "direction": item["direction"],
                                "time_horizon": item["time_horizon"],
                            }
                        ),
                        timeout=_VERIFY_TIMEOUT_S,
                    )
                except Exception as e:
                    utils.logger.warning(f"[debate] 验证失败 {post.get('status_id')}: {e}")
                    result = None
                if result is not None and result["verdict"] != "inconclusive":
                    item["verdict"] = result["verdict"]
        return item

    results = await asyncio.gather(*(_one(p) for p in posts))
    return [r for r in results if r is not None]


def _score(v) -> int:
    try:
        return max(1, min(5, int(v or 1)))
    except (TypeError, ValueError):
        return 1


async def _distill_core(
    stock_code: str, stock_name: str, items: List[Dict], max_n: int, side_label: str
) -> str:
    if not items:
        return "(无)"
    block = "\n".join(
        f"- {p.get('thesis', '')} | 论据: {p.get('evidence', '')[:150]}"
        for p in items
    )
    prompt = _CORE_PROMPT.format(
        stock_name=stock_name, stock_code=stock_code,
        side_label=side_label, max_n=max_n, items_block=block,
    )
    return (await call_text(prompt, max_tokens=768) or "").strip() or "(无)"


async def get_debate(session, stock_code: str, max_posts: int = _TARGET_POSTS) -> Optional[Dict]:
    """
    抓取讨论区表态并产出多空辩论: 双方核心论点 + 历史验证统计 + 哪方更合理。
    失败返回 None (不影响报告其他维度)。情绪维度由本结果派生 (derive_sentiment)。
    """
    page = getattr(session, "context_page", None)
    if page is None:
        return None
    sym = _symbol(stock_code)
    client = XueqiuClient(playwright_page=page)
    try:
        posts = await asyncio.wait_for(
            fetch_discussion_posts(client, sym, target=max_posts),
            timeout=_FETCH_BUDGET_S,
        )
    except asyncio.TimeoutError:
        utils.logger.warning(f"[debate] {stock_code} 讨论收集超时, 跳过该维度")
        return None
    if len(posts) < 20:
        utils.logger.warning(f"[debate] {stock_code} 讨论样本不足 ({len(posts)} 条), 跳过该维度")
        return None

    classified = await _classify_posts(posts, sym, stock_code)

    bull = [c for c in classified if c.get("stance") == "bullish"]
    bear = [c for c in classified if c.get("stance") == "bearish"]
    neutral_count = sum(1 for c in classified if c.get("stance") == "neutral")
    irrelevant_count = sum(1 for c in classified if c.get("stance") == "irrelevant")

    def _stats(items: List[Dict]) -> Dict:
        verified = [c for c in items if c.get("verdict") in ("correct", "incorrect")]
        return {
            "count": len(items),
            "verified": len(verified),
            "correct": sum(1 for c in verified if c["verdict"] == "correct"),
            "incorrect": sum(1 for c in verified if c["verdict"] == "incorrect"),
            "unverified": len(items) - len(verified),
        }

    bull_stats = _stats(bull)
    bear_stats = _stats(bear)

    # 核心论点: 按逻辑质量排序取前 25 条提炼
    def _top(items: List[Dict]) -> List[Dict]:
        scored = sorted(
            items,
            key=lambda c: (
                (c.get("logic_depth") or 0) + (c.get("logic_novelty") or 0),
                c.get("logic_consistency") or 0,
            ),
            reverse=True,
        )
        return scored[:25]

    bull_core = await _distill_core(stock_code, stock_code, _top(bull), 5, "多方")
    bear_core = await _distill_core(stock_code, stock_code, _top(bear), 5, "空方")

    # 哪一方更合理 (结合逻辑质量与历史验证)
    verdict, reason, bull_one, bear_one = "undecided", "", "", ""
    try:
        eval_prompt = _EVAL_PROMPT.format(
            stock_name=stock_code,
            stock_code=stock_code,
            bull_block=bull_core,
            bear_block=bear_core,
            bull_verified=bull_stats["verified"],
            bull_correct=bull_stats["correct"],
            bull_incorrect=bull_stats["incorrect"],
            bull_unverified=bull_stats["unverified"],
            bear_verified=bear_stats["verified"],
            bear_correct=bear_stats["correct"],
            bear_incorrect=bear_stats["incorrect"],
            bear_unverified=bear_stats["unverified"],
        )
        parsed = await call_json(eval_prompt, max_tokens=1024)
        if isinstance(parsed, dict):
            verdict = parsed.get("verdict", "undecided")
            reason = parsed.get("reason", "") or ""
            bull_one = parsed.get("bull_core", "") or ""
            bear_one = parsed.get("bear_core", "") or ""
    except Exception as e:
        utils.logger.warning(f"[debate] {stock_code} 合理性评估失败: {e}")

    result = {
        "posts_collected": len(posts),
        "users": len({p.get("user_id") for p in posts if p.get("user_id")}),
        "classified": len(classified),
        "neutral": neutral_count,
        "irrelevant": irrelevant_count,
        "bull": bull_stats,
        "bear": bear_stats,
        "bull_core": bull_core,
        "bear_core": bear_core,
        "verdict": verdict,
        "reason": reason,
        "bull_one_line": bull_one,
        "bear_one_line": bear_one,
    }
    utils.logger.info(
        f"[debate] {stock_code} 多空辩论: 多方 {bull_stats['count']} 条 (验证正确 {bull_stats['correct']}), "
        f"空方 {bear_stats['count']} 条 (验证正确 {bear_stats['correct']}), 结论 {verdict}"
    )
    return result


def derive_sentiment(debate: Optional[Dict]) -> Optional[Dict]:
    """从辩论结果派生情绪聚合 (同一批分类, 不再调用 LLM)。"""
    if not debate:
        return None
    bull = debate.get("bull") or {}
    bear = debate.get("bear") or {}
    classified = debate.get("classified") or 0
    directional = bull.get("count", 0) + bear.get("count", 0)
    bullish_ratio = bull.get("count", 0) / directional if directional else 0.0
    consensus = "none"
    if directional >= 10:
        if bullish_ratio >= 0.8:
            consensus = "bullish"
        elif bullish_ratio <= 0.2:
            consensus = "bearish"
    note = ""
    if consensus == "bullish":
        note = "讨论区一致看多: 情绪拥挤, 作为反向指标提示回调风险"
    elif consensus == "bearish":
        note = "讨论区一致看空: 悲观情绪极端, 作为反向指标提示见底可能"
    return {
        "posts_collected": debate.get("posts_collected", 0),
        "users": debate.get("users", 0),
        "classified": classified,
        "bullish": bull.get("count", 0),
        "bearish": bear.get("count", 0),
        "neutral": debate.get("neutral", 0),
        "irrelevant": debate.get("irrelevant", 0),
        "reasoned_bullish": bull.get("count", 0),
        "reasoned_bearish": bear.get("count", 0),
        "bullish_ratio": round(bullish_ratio, 3),
        "consensus": consensus,
        "note": note,
    }
