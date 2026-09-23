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


from pydantic import BaseModel, Field


class XueqiuPredictionRecord(BaseModel):
    """
    雪球用户预测回测记录 — 每条构成有效预测且完成验证的记录 (correct/incorrect/inconclusive 均落盘，
    用于统计用户命中率时既需要分子也需要分母)
    """
    user_id: str = Field(default="", description="预测者用户 ID")
    user_nickname: str = Field(default="", description="预测者昵称")
    status_id: str = Field(default="", description="帖子 ID")
    status_url: str = Field(default="", description="帖子链接")
    predicted_at: int = Field(default=0, description="预测发布时间戳 (ms)")

    stock_code: str = Field(default="", description="股票代码 (如 SH600519)")
    stock_name: str = Field(default="", description="股票名称")
    direction: str = Field(default="", description="预测方向: bullish|bearish|topped_out|bottomed_out")
    thesis: str = Field(default="", description="论点: 作者的主张/判断摘要")
    evidence: str = Field(default="", description="论据: 作者引用的具体数据/数字/推理链摘要 (保留原帖关键数字)")
    industry_view: str = Field(default="", description="帖子中提到的行业看法")
    market_context: str = Field(default="", description="帖子中提到的当时市场情况 (背景)")
    schema_version: int = Field(default=2, description="记录结构版本 (升级后重跑可覆盖旧记录)")

    verified_at: int = Field(default=0, description="验证时使用的最新价格日期 (Unix 秒)")
    actual_trend: str = Field(default="", description="验证得到的实际走势: bullish|bearish|neutral")
    price_change_pct: float = Field(default=0.0, description="验证窗口内的涨跌幅 (%)")
    verdict: str = Field(default="", description="验证结论: correct|incorrect|inconclusive|view")


class StockCredibilityScore(BaseModel):
    """用户在单个股票上的可信度评分"""
    stock_code: str = Field(default="", description="股票代码")
    stock_name: str = Field(default="", description="股票名称")
    correct: int = Field(default=0, description="验证为 correct 的预测数")
    incorrect: int = Field(default=0, description="验证为 incorrect 的预测数")
    hit_rate: float = Field(default=0.0, description="原始命中率 correct/(correct+incorrect)")
    wilson_score: float = Field(default=0.0, description="Wilson 区间下界，样本量越少越保守")


class UserCredibilityScore(BaseModel):
    """用户整体可信度评分，含按股票细分"""
    user_id: str = Field(default="", description="用户 ID")
    user_nickname: str = Field(default="", description="用户昵称")
    total_predictions: int = Field(default=0, description="全部有效预测数 (含 inconclusive)")
    correct: int = Field(default=0, description="验证为 correct 的预测数")
    incorrect: int = Field(default=0, description="验证为 incorrect 的预测数")
    inconclusive: int = Field(default=0, description="数据不足无法判定的预测数")
    hit_rate: float = Field(default=0.0, description="原始命中率 correct/(correct+incorrect)")
    wilson_score: float = Field(default=0.0, description="整体 Wilson 区间下界，用于排序")
    by_stock: list[StockCredibilityScore] = Field(default_factory=list, description="按股票代码细分的评分")
