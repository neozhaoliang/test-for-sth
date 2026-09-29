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

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from analysis.evidence import EvidenceItem, ResearchQuality
from analysis.research_profile import ResearchProfile
from analysis.reviewer import ResearchReview


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
    source_url: str = Field(default="", description="原帖/专栏/视频来源链接")


class StructuredSummary(BaseModel):
    """LLM 生成的结构化分析摘要"""
    lynch_category: str = Field(
        default="",
        description="彼得林奇式分类: fast_grower|stalwart|cyclical|turnaround|asset_play|slow_grower|unclear",
    )
    stance: str = Field(default="", description="综合倾向: bullish|bearish|neutral")
    company_quality_stance: str = Field(
        default="", description="企业长期质量倾向: bullish|bearish|neutral"
    )
    current_odds_stance: str = Field(
        default="", description="当前股票赔率倾向: bullish|bearish|neutral"
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="综合置信度")
    thesis_summary: str = Field(default="", description="关键论据摘要")
    core_counter_evidence: str = Field(
        default="",
        description="与结论相悖的最强证据 (必须带具体数字)，没有也要写「未发现」",
    )
    invalidation_condition: str = Field(default="", description="如果出现什么情况，说明这个判断是错的")
    risk_notes: str = Field(default="", description="风险提示/需要注意的不确定性")
    dimension_scores: Optional[List[dict]] = Field(
        default=None,
        description="十二维度评分: [{dimension: 中文名, score: -10..+10, note: 一句理由}]",
    )
    dimension_analyses: Optional[Dict[str, str]] = Field(
        default=None,
        description="十二维度详细分析原文 (工具名 -> 该维度完整分析, 供 UI 展开查看)",
    )


class AnalysisReport(BaseModel):
    """单只股票的综合分析报告"""
    stock_code: str = Field(default="", description="股票代码")
    stock_name: str = Field(default="", description="股票名称")
    realtime_quote: Optional[dict] = Field(default=None, description="实时行情快照 (最新价/涨跌幅/成交量等)")
    candidates: List[CandidateOpinion] = Field(default_factory=list, description="候选高可信度用户列表")
    knowledge_excerpts: List[KnowledgeExcerpt] = Field(default_factory=list, description="知识库背景资料 (全量，未按股票筛选)")
    industry_comparison: Optional[dict] = Field(default=None, description="所属行业涨跌家数对比 (行业普涨/普跌判断)")
    shareholder_trend: Optional[dict] = Field(default=None, description="股东户数环比变化 (散户情绪/筹码集中度代理)")
    dividend_history: List[dict] = Field(default_factory=list, description="历史分红记录")
    buyback_history: List[dict] = Field(default_factory=list, description="历史回购记录")
    dividend_chart: Optional[List[dict]] = Field(
        default=None,
        description="分红+回购柱状图数据 (按年度, 回购折算元/10股并入, 含按现价股息率)",
    )
    commodity_signal: Optional[dict] = Field(default=None, description="与公司产品匹配的周期商品期货代理 (20/60日方向、1年位置；铜产业可含内外盘背景)")
    freight_signal: Optional[dict] = Field(default=None, description="集运运价景气度 (仅航运/港口类公司)")
    margin_signal: Optional[dict] = Field(default=None, description="融资盘与流通盘 (融资余额序列/占流通市值比例/近期增减)")
    market_context: Optional[dict] = Field(default=None, description="大盘与风格: 主要指数与个股的年内/上半年/下半年涨跌幅 + 个股历史区间位置")
    rmb_signal: Optional[dict] = Field(default=None, description="人民币汇率趋势 (全部股票，用于判断汇率对海外收入的影响方向)")
    macro_rates: Optional[dict] = Field(
        default=None, description="中美利率环境：Fed目标区间/美债10Y/中国LPR及新鲜度"
    )
    policy_events: Optional[dict] = Field(
        default=None,
        description="按公司暴露路径筛选的近期政策/地缘财经媒体事件线索；不是一手事实",
    )
    profitability_trend: Optional[dict] = Field(default=None, description="近几个报告期毛利率/净利率/ROE/资产负债率趋势 (盈利能力与成本弹性)")
    fundamentals: Optional[dict] = Field(default=None, description="同花顺 F10 结构性事实 (集中度/海外占比/现金流质量/研发强度/股东人数/公司自述风险)")
    valuation: Optional[dict] = Field(default=None, description="估值快照 (PE/PB/每股指标/股权质押)")
    valuation_history: Optional[dict] = Field(
        default=None, description="历史PE/PB分位与月度估值序列"
    )
    rd_team: Optional[dict] = Field(
        default=None, description="巨潮最新年报中的研发人员数量、占比与学历/年龄结构"
    )
    xueqiu_stock: Optional[dict] = Field(default=None, description="雪球个股维度 (机构持仓/讨论热度，仅聚合数字)")
    a_share_structure: Optional[dict] = Field(
        default=None, description="A股公开机构持股、前十大流通股东与特殊资金类型"
    )
    management_capital: Optional[dict] = Field(
        default=None, description="管理层利益绑定、经营执行与5/10年资本分配长期账本"
    )
    research_profile: ResearchProfile = Field(
        default_factory=ResearchProfile,
        description="按行业/研发/资本回报确定的公司研究画像与重点证据准备度",
    )
    sentiment: Optional[dict] = Field(default=None, description="雪球讨论区情绪聚合 (看多/看空比例, 一致看多预警, 反向指标)")
    debate: Optional[dict] = Field(default=None, description="雪球多空辩论 (双方核心论点 + 历史验证统计 + 哪方更合理)")
    primary_evidence: List[dict] = Field(default_factory=list, description="巨潮等一手公告元数据")
    evidence: List[EvidenceItem] = Field(default_factory=list, description="本次研究实际使用/可用的证据账本")
    research_quality: ResearchQuality = Field(
        default_factory=ResearchQuality, description="证据覆盖率、来源质量与缺口"
    )
    review: ResearchReview = Field(
        default_factory=ResearchReview, description="跨维度重复计分、潜在冲突与弱证据审查"
    )
    summary: StructuredSummary = Field(default_factory=StructuredSummary, description="LLM 生成的结构化分析摘要")
    prompt_version: str = Field(default="", description="生成本报告所用的 prompt 版本号")
    generated_at: int = Field(default=0, description="报告生成时间 (Unix 秒)")
