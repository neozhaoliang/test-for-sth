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
行业整体涨跌对比: 判断目标股票所在行业是不是普涨/普跌 (同花顺行业板块汇总)。

股票 -> 所属行业的反查**不能靠遍历行业成分股**。上一版对全部约 89 个板块逐个调
stock_board_industry_cons_em，该接口在实测环境下对每个板块都以 RemoteDisconnected
失败，一次报告要空转十几分钟且一次都没成功过。现改为直接读同花顺 F10 公司概要页
标注的"所属申万行业"(该页本就为估值维度而抓取，不产生额外请求)，再在本地把它
匹配到同花顺行业板块名。

两套分类体系并非一一对应 (申万"白酒Ⅱ" vs 同花顺"白酒")，匹配失败时不下结论，
调用方在报告里标注涨跌家数暂缺——宁可缺，不能猜。
"""

import re
import time
from typing import Dict, List, Optional, Tuple

import akshare as ak

from tools.utils import utils

_CACHE_TTL_SECONDS = 3600
_summary_cache: Optional[Tuple[float, "object"]] = None

# 申万行业名带分级罗马数字后缀 (白酒Ⅱ/证券Ⅱ/中药Ⅲ)，同花顺板块名不带。
# 全角罗马数字无歧义直接去；半角只去 II/III/IV——单个 I 或 V 可能本身就是名字的一部分。
_FULLWIDTH_ROMAN_RE = re.compile(r"[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+$")
_ASCII_ROMAN_RE = re.compile(r"(?:II|III|IV)$")


async def _get_industry_summary_df():
    import asyncio

    global _summary_cache
    if _summary_cache and (time.time() - _summary_cache[0]) < _CACHE_TTL_SECONDS:
        return _summary_cache[1]

    try:
        df = await asyncio.to_thread(ak.stock_board_industry_summary_ths)
    except Exception as e:
        utils.logger.error(f"[industry] stock_board_industry_summary_ths failed: {e}")
        return None

    _summary_cache = (time.time(), df)
    return df


def _normalize_industry(name: Optional[str]) -> str:
    text = (name or "").strip()
    text = _FULLWIDTH_ROMAN_RE.sub("", text)
    text = _ASCII_ROMAN_RE.sub("", text)
    return text.strip()


def _match_ths_board(sw_industry: str, boards: List[str]) -> Optional[str]:
    """
    把申万行业名匹配到同花顺行业板块名: 先归一化后取完全相等，退一步取互为子串
    (如申万"股份制银行" ⊃ 同花顺"银行")。

    子串匹配必须**唯一命中**。命中多个说明无从判断该引哪个板块的涨跌家数，
    此时返回 None——报告写"暂缺"是安全的，猜错板块则会给出方向完全相反的结论。
    """
    target = _normalize_industry(sw_industry)
    if not target:
        return None

    normalized = [(_normalize_industry(b), b) for b in boards]
    for norm, raw in normalized:
        if norm == target:
            return raw

    hits = [raw for norm, raw in normalized if norm and (norm in target or target in norm)]
    if len(hits) == 1:
        return hits[0]
    if hits:
        utils.logger.warning(
            f"[industry] 申万行业 '{sw_industry}' 同时命中多个同花顺板块 {hits}，无法判定，记为暂缺"
        )
    return None


async def get_industry_comparison(
    stock_code: str, sw_industry: Optional[str] = None
) -> Optional[Dict]:
    """
    返回该股票所属行业的涨跌家数/整体涨跌幅，用于判断是否处于行业普涨/普跌行情。

    sw_industry 由 analysis.fundamentals 从同花顺 F10 公司概要页解析后传入。
    只认出申万行业、匹配不到同花顺板块时，仍返回行业名 (industry_name 为 None)——
    行业归属本身是有用信息，涨跌家数则在块里写明暂缺，不会被编造。
    行业归属与板块统计都拿不到时返回 None。
    """
    if not sw_industry:
        return None

    summary_df = await _get_industry_summary_df()
    industry_name = None
    if summary_df is not None:
        industry_name = _match_ths_board(sw_industry, summary_df["板块"].tolist())
        if industry_name is None:
            utils.logger.warning(
                f"[industry] {stock_code} 申万行业 '{sw_industry}' 未能对应到同花顺行业板块，"
                f"行业涨跌家数记为暂缺"
            )

    result: Dict = {"industry_name": industry_name, "sw_industry": sw_industry}
    if industry_name is not None and summary_df is not None:
        row = summary_df[summary_df["板块"] == industry_name]
        if not row.empty:
            row = row.iloc[0]
            result["advancing"] = int(row["上涨家数"])
            result["declining"] = int(row["下跌家数"])
            result["industry_change_pct"] = float(row["涨跌幅"])

    if "advancing" not in result:
        result["note"] = "该股所属行业的涨跌家数与行业涨跌幅本次未能取到"
    return result
