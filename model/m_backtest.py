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
    thesis: str = Field(default="", description="预测理由/论据摘要")
    industry_view: str = Field(default="", description="帖子中提到的行业看法")
    market_context: str = Field(default="", description="帖子中提到的当时市场情况")

    verified_at: int = Field(default=0, description="验证时使用的最新价格日期 (Unix 秒)")
    actual_trend: str = Field(default="", description="验证得到的实际走势: bullish|bearish|neutral")
    price_change_pct: float = Field(default=0.0, description="验证窗口内的涨跌幅 (%)")
    verdict: str = Field(default="", description="验证结论: correct|incorrect|inconclusive")
