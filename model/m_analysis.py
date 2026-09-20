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

from typing import List, Optional

from pydantic import BaseModel, Field


class CandidateOpinion(BaseModel):
    """单个候选用户对目标股票的历史可信度 + 最新发言"""
    user_id: str = Field(default="", description="用户 ID")
    user_nickname: str = Field(default="", description="用户昵称")
    wilson_score: float = Field(default=0.0, description="该用户在此股票上的 Wilson 区间下界")
    hit_rate: float = Field(default=0.0, description="该用户在此股票上的历史命中率")
    correct: int = Field(default=0, description="该股票上验证为 correct 的预测数")
    incorrect: int = Field(default=0, description="该股票上验证为 incorrect 的预测数")
    credibility_note: str = Field(default="", description="可信度的人话解释，样本不足时会提示参考价值有限")
    historical_thesis: List[str] = Field(default_factory=list, description="该用户对此股票的历史预测理由摘录")
    latest_posts: List[str] = Field(default_factory=list, description="实时抓取到的该用户最新发言摘录")


class KnowledgeExcerpt(BaseModel):
    """知识库背景资料 (非可信度验证类观点，始终全量加载作为背景参考)"""
    source: str = Field(default="", description="知识库来源标识，如 bili_laomujiang")
    title: str = Field(default="", description="资料标题")
    distilled: str = Field(default="", description="LLM 提炼后的投资观点摘要 (已去除闲聊)")


class StructuredSummary(BaseModel):
    """LLM 生成的结构化分析摘要"""
    lynch_category: str = Field(
        default="",
        description="彼得林奇式分类: fast_grower|stalwart|cyclical|turnaround|asset_play|slow_grower|unclear",
    )
    stance: str = Field(default="", description="综合倾向: bullish|bearish|neutral")
    thesis_summary: str = Field(default="", description="关键论据摘要")
    invalidation_condition: str = Field(default="", description="如果出现什么情况，说明这个判断是错的")
    risk_notes: str = Field(default="", description="风险提示/需要注意的不确定性")


class AnalysisReport(BaseModel):
    """单只股票的综合分析报告"""
    stock_code: str = Field(default="", description="股票代码")
    stock_name: str = Field(default="", description="股票名称")
    realtime_quote: Optional[dict] = Field(default=None, description="实时行情快照 (最新价/涨跌幅/成交量等)")
    candidates: List[CandidateOpinion] = Field(default_factory=list, description="候选高可信度用户列表")
    knowledge_excerpts: List[KnowledgeExcerpt] = Field(default_factory=list, description="知识库背景资料 (全量，未按股票筛选)")
    summary: StructuredSummary = Field(default_factory=StructuredSummary, description="LLM 生成的结构化分析摘要")
    prompt_version: str = Field(default="", description="生成本报告所用的 prompt 版本号")
    generated_at: int = Field(default=0, description="报告生成时间 (Unix 秒)")
