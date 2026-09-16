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
用 LLM 判断一条帖子对其提及的每只股票是否构成"有论据支撑的方向性预测"，
并抽取预测方向、论据、行业看法、市场背景。纯情绪发泄/无理由喊单/单纯转发不计入。
"""

from typing import Any, Dict, List, TypedDict

from backtest.extract import ExtractedPost
from backtest.llm_client import call_json

VALID_DIRECTIONS = {"bullish", "bearish", "topped_out", "bottomed_out"}

_PROMPT_TEMPLATE = """你是一名专业的证券分析助手，任务是判断一段雪球用户发帖内容里，对某只股票是否给出了"有论据支撑的方向性预测"。

判定规则（严格执行）：
1. 纯情绪发泄、无理由的喊单/看多/看空（比如"要涨了""绝了""垃圾股"）不算，必须给出具体理由（如财务数据、行业趋势、估值逻辑、竞争格局、政策影响等）才算。
2. 单纯转发、复述新闻without表达自己观点的不算。
3. 预测方向只能是以下四种之一：bullish(看多/未来上涨)、bearish(看空/未来下跌)、topped_out(认为已见顶，未来将走弱)、bottomed_out(认为已见底，未来将走强)。如果内容含糊无法归类到这四种，则视为不构成有效预测。

帖子发布时间: {created_at}
帖子正文:
{text}

该帖子提及的股票列表: {stock_list}

请针对上面每一只股票，判断该帖子是否包含针对该股票的有效预测。以 JSON 数组格式返回，每个元素对应一只股票，格式如下，不要输出任何其他文字：
[
  {{
    "code": "股票代码",
    "is_reasoned_prediction": true或false,
    "direction": "bullish|bearish|topped_out|bottomed_out",
    "thesis": "预测理由/论据摘要，用原文提炼，不超过200字",
    "industry_view": "帖子中提到的行业看法摘要，没有则为空字符串",
    "market_context": "帖子中提到的当时市场情况摘要，没有则为空字符串"
  }}
]

如果 is_reasoned_prediction 为 false，其余字段可留空字符串。"""


class ClassifiedPrediction(TypedDict):
    post: Dict[str, Any]
    stock_code: str
    stock_name: str
    direction: str
    thesis: str
    industry_view: str
    market_context: str


async def classify_post(extracted: ExtractedPost) -> List[ClassifiedPrediction]:
    """
    对单条已抽取股票代码的帖子调用一次 LLM，批量判断其中每只股票是否构成有效预测。
    返回仅包含 is_reasoned_prediction=true 且方向有效的记录。
    """
    post = extracted["post"]
    stocks = extracted["stocks"]
    stock_by_code = {s["code"]: s["name"] for s in stocks}

    prompt = _PROMPT_TEMPLATE.format(
        created_at=post.get("created_at", ""),
        text=extracted["text"],
        stock_list=", ".join(f"{s['name']}({s['code']})" for s in stocks),
    )

    parsed = await call_json(prompt, max_tokens=2048)
    if not parsed or not isinstance(parsed, list):
        return []

    results: List[ClassifiedPrediction] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        if not item.get("is_reasoned_prediction"):
            continue
        code = str(item.get("code", "")).upper()
        direction = item.get("direction", "")
        if code not in stock_by_code or direction not in VALID_DIRECTIONS:
            continue
        results.append({
            "post": post,
            "stock_code": code,
            "stock_name": stock_by_code[code],
            "direction": direction,
            "thesis": item.get("thesis", "") or "",
            "industry_view": item.get("industry_view", "") or "",
            "market_context": item.get("market_context", "") or "",
        })
    return results
