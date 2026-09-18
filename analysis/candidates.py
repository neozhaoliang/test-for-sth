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
根据历史可信度评分，筛选某只股票的高可信度候选用户。
"""

from typing import Dict, List

from backtest.score import score_all_users
from model.m_backtest import StockCredibilityScore, UserCredibilityScore


def _stock_score_for(user: UserCredibilityScore, stock_code: str) -> StockCredibilityScore:
    for stock in user.by_stock:
        if stock.stock_code == stock_code:
            return stock
    raise ValueError(f"user {user.user_id} has no score for {stock_code}")


def find_candidates(stock_code: str, min_wilson: float = 0.0, limit: int = 5) -> List[UserCredibilityScore]:
    """
    找出历史上对该股票有过验证记录、且该股票 Wilson 分数达到阈值的用户，按该股票的
    Wilson 分数降序取前 limit 个。用户在该股票上没有任何验证记录时不纳入候选。
    """
    users = [u for u in score_all_users() if any(s.stock_code == stock_code for s in u.by_stock)]
    users = [u for u in users if _stock_score_for(u, stock_code).wilson_score >= min_wilson]
    users.sort(key=lambda u: _stock_score_for(u, stock_code).wilson_score, reverse=True)
    return users[:limit]


def list_supported_stocks() -> List[Dict]:
    """
    列出所有拥有历史验证记录的股票 (即分析报告能给出候选用户参考的股票)，
    按验证记录总数降序排列。
    """
    totals: Dict[str, Dict] = {}
    for user in score_all_users():
        for stock in user.by_stock:
            entry = totals.setdefault(
                stock.stock_code,
                {"stock_code": stock.stock_code, "stock_name": stock.stock_name, "record_count": 0},
            )
            if not entry["stock_name"] and stock.stock_name:
                entry["stock_name"] = stock.stock_name
            entry["record_count"] += stock.correct + stock.incorrect

    stocks = list(totals.values())
    stocks.sort(key=lambda s: s["record_count"], reverse=True)
    return stocks
