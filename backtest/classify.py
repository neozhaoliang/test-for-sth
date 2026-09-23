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
1. 作者必须给出具体理由才算"有论据" (财务数据、行业趋势、估值逻辑、竞争格局、政策影响、供需、宏观、筹码等)；纯情绪发泄、无理由喊单不算。
2. 单纯转发、复述新闻或他人观点而不表达自己的独立判断不算；只是顺带提一嘴股票、没有任何评价也不算。
3. 人云亦云不算：只是重复市场共识、没有任何增量信息或独立视角的表述，不算。
4. prediction_type 按预测对象分类：
   - price: 对股价涨跌方向的预测 (看多/看空/见顶/见底)，direction 填 bullish|bearish|topped_out|bottomed_out；
   - fundamental: 对企业基本面的预测 (业绩、利润率、分红、减值/爆雷、经营变化等)；
   - commodity: 对商品价格的预测 (铜价、铝价、油价等)；
   - macro_market: 对宏观或市场的预测 (利率、通胀、汇率、市场风格、大盘走势等)；
   - industry: 对行业趋势的预测 (行业景气、竞争格局变化等)；
   - 空字符串: 只有观点、没有预测 (如估值评价、买卖操作及理由)。
5. time_horizon 只对 price 类型填写：作者明确说的验证时间范围原文 (如"年内"、"三个月"、"2027年上半年")。作者没有说时间范围时留空字符串——没有时间范围的股价预测不做验证，只记录。
6. 论据必须忠实保留作者原话里的关键数字与推理链 (如"持仓成本 8 元""市净率 0.7""铜库存 80 万吨"这类具体数字)，不能概括成"估值低""基本面好"这类空洞表述。没有具体论据时 evidence 留空字符串。
7. 逻辑评价 (只对 is_reasoned 为 true 的记录)：
   - logic_dimensions: 作者在这条分析里实际考虑的分析维度列表 (从 供需/库存/成本/产能、财务质量、估值、宏观/利率/通胀/汇率、政策、竞争格局、筹码/资金面、公司治理、周期位置 等里面选，也可以补充其他维度名)；
   - logic_novelty: 1-5 整数，5=非共识的独到视角或反直觉发现，1=市场普遍认知的复述；
   - logic_depth: 1-5 整数，5=有完整数据支撑与推理链，1=只有结论；
   - logic_consistency: 1-5 整数，5=论据与结论完全自洽，1=论据与结论脱节。
8. 只讨论 A 股上市公司；提到的股票必须写 6 位代码 (SH/SZ/BJ 开头)。帖子没有明确讨论任何股票时返回空数组 []。"""

_OUTPUT_SCHEMA = """以 JSON 数组格式返回，每个元素对应一只股票，格式如下，不要输出任何其他文字：
[
  {{
    "name": "股票名称",
    "code": "股票代码 (如 SH600519)",
    "is_reasoned": true或false,
    "prediction_type": "price|fundamental|commodity|macro_market|industry|空字符串",
    "direction": "bullish|bearish|topped_out|bottomed_out (仅 price 类型填写, 其余留空字符串)",
    "time_horizon": "仅 price 类型: 作者明确说的验证时间范围原文, 如 年内/三个月/2027年上半年; 没有则空字符串",
    "thesis": "论点: 作者的主张/判断，用原文提炼，不超过200字",
    "evidence": "论据: 作者引用的具体数据与推理链 (保留原帖关键数字)，不超过300字，没有则为空字符串",
    "industry_view": "帖子中提到的行业看法摘要，没有则为空字符串",
    "market_context": "帖子中提到的当时市场情况摘要 (背景)，没有则为空字符串",
    "logic_dimensions": ["作者考虑的分析维度"],
    "logic_novelty": 1到5的整数,
    "logic_depth": 1到5的整数,
    "logic_consistency": 1到5的整数
  }}
]

如果 is_reasoned 为 false，其余字段可留空字符串或空数组。"""

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
    prediction_type: str  # price|fundamental|commodity|macro_market|industry|""(仅观点)
    direction: str
    time_horizon: str
    thesis: str
    evidence: str
    industry_view: str
    market_context: str
    logic_dimensions: List[str]
    logic_novelty: int
    logic_depth: int
    logic_consistency: int


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

    def _score(v) -> int:
        try:
            return max(1, min(5, int(v or 1)))
        except (TypeError, ValueError):
            return 1

    results: List[ClassifiedPrediction] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        if not item.get("is_reasoned"):
            continue
        code = str(item.get("code", "")).upper()
        ptype = (item.get("prediction_type") or "").strip()
        direction = (item.get("direction") or "").strip()
        if ptype == "price":
            if direction not in VALID_DIRECTIONS:
                continue
        elif direction:
            continue  # 非 price 类型不允许带方向
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
            "prediction_type": ptype,
            "direction": direction,
            "time_horizon": (item.get("time_horizon") or "").strip() if ptype == "price" else "",
            "thesis": thesis,
            "evidence": (item.get("evidence", "") or "").strip(),
            "industry_view": item.get("industry_view", "") or "",
            "market_context": item.get("market_context", "") or "",
            "logic_dimensions": [
                str(d).strip() for d in (item.get("logic_dimensions") or [])
                if str(d).strip()
            ][:8],
            "logic_novelty": _score(item.get("logic_novelty")),
            "logic_depth": _score(item.get("logic_depth")),
            "logic_consistency": _score(item.get("logic_consistency")),
        })
    return results
