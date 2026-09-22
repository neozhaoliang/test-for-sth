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

有 $标签$ 的帖子按标签给的股票列表判断；无标签的帖子 (老派大 V 常见，直接用
文字讨论股票) 由 LLM 先自行识别正文讨论的股票，再对每只做同样判断——识别与
判定在同一次调用里完成。
"""

import re
from typing import Any, Dict, List, TypedDict

from backtest.extract import ExtractedPost
from backtest.llm_client import call_json

VALID_DIRECTIONS = {"bullish", "bearish", "topped_out", "bottomed_out"}

_CODE_RE = re.compile(r"^(SH|SZ|BJ)\d{6}$")

_RULES = """判定规则（严格执行）：
1. 纯情绪发泄、无理由的喊单/看多/看空（比如"要涨了""绝了""垃圾股"）不算，必须给出具体理由（如财务数据、行业趋势、估值逻辑、竞争格局、政策影响等）才算。
2. 单纯转发、复述新闻without表达自己观点的不算；只是顺带提一嘴股票、没有任何评价的也不算。
3. 预测方向只能是以下四种之一：bullish(看多/未来上涨)、bearish(看空/未来下跌)、topped_out(认为已见顶，未来将走弱)、bottomed_out(认为已见底，未来将走强)。如果内容含糊无法归类到这四种，则视为不构成有效预测。
4. 观点型内容单独归类：作者对某只股票表达了明确看法但没有方向性预测时（如宏观判断、行业判断、估值判断、买卖操作及其理由、筹码结构分析），is_reasoned_prediction 为 true、direction 留空字符串、thesis 写该观点的摘要。这类观点同样有价值，不能丢弃。
5. 只讨论 A 股上市公司；提到的股票必须写 6 位代码 (SH/SZ/BJ 开头)。帖子没有明确讨论任何股票时返回空数组 []。"""

_OUTPUT_SCHEMA = """以 JSON 数组格式返回，每个元素对应一只股票，格式如下，不要输出任何其他文字：
[
  {{
    "name": "股票名称",
    "code": "股票代码 (如 SH600519)",
    "is_reasoned_prediction": true或false,
    "direction": "bullish|bearish|topped_out|bottomed_out (观点型内容没有方向，留空字符串)",
    "thesis": "预测理由或观点摘要，用原文提炼，不超过200字",
    "industry_view": "帖子中提到的行业看法摘要，没有则为空字符串",
    "market_context": "帖子中提到的当时市场情况摘要，没有则为空字符串"
  }}
]

如果 is_reasoned_prediction 为 false，其余字段可留空字符串。"""

_PROMPT_TAGGED = """你是一名专业的证券分析助手，任务是判断一段雪球用户发帖内容里，对某只股票是否给出了"有论据支撑的方向性预测"。

{RULES}

帖子发布时间: {created_at}
帖子正文:
{text}

该帖子用 $标签$ 提到的股票列表: {stock_list}

请针对上面每一只股票，判断该帖子是否包含针对该股票的有效预测。
{OUTPUT_SCHEMA}"""

_PROMPT_UNTAGGED = """你是一名专业的证券分析助手，任务是判断一段雪球用户发帖内容里，对某只股票是否给出了"有论据支撑的方向性预测"。

{RULES}

帖子发布时间: {created_at}
帖子正文:
{text}

这条帖子没有 $标签$ 标注股票。请先根据正文识别作者**明确讨论并有观点**的股票
(仅限 A 股上市公司；只是顺带提一嘴、没有评价的不算)，然后对每一只判断是否为
有效预测。
{OUTPUT_SCHEMA}"""


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
    对单条帖子调用一次 LLM：有标签时按标签股票列表判断；无标签时先由 LLM
    自行识别正文讨论的股票再判断。返回仅包含 is_reasoned_prediction=true
    且方向/代码有效的记录。
    """
    post = extracted["post"]
    stocks = extracted["stocks"]
    stock_by_code = {s["code"]: s["name"] for s in stocks}

    common = {
        "created_at": post.get("created_at", ""),
        "text": extracted["text"],
        "RULES": _RULES,
        "OUTPUT_SCHEMA": _OUTPUT_SCHEMA,
    }
    if stocks:
        prompt = _PROMPT_TAGGED.format(
            stock_list=", ".join(f"{s['name']}({s['code']})" for s in stocks), **common
        )
    else:
        prompt = _PROMPT_UNTAGGED.format(**common)

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
        direction = item.get("direction", "") or ""
        # 方向为空 = 观点型 (无方向性预测, 如清仓理由/宏观判断), 合法;
        # 非空则必须是四种有效方向之一。
        if direction and direction not in VALID_DIRECTIONS:
            continue
        thesis = (item.get("thesis", "") or "").strip()
        if not thesis:
            continue
        if code in stock_by_code:
            name = stock_by_code[code]
        elif _CODE_RE.match(code):
            name = str(item.get("name", "") or code)
        else:
            continue  # LLM 无标签识别时给出的代码不可信 (格式不对或不存在)
        results.append({
            "post": post,
            "stock_code": code,
            "stock_name": name,
            "direction": direction,
            "thesis": thesis,
            "industry_view": item.get("industry_view", "") or "",
            "market_context": item.get("market_context", "") or "",
        })
    return results
