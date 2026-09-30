# -*- coding: utf-8 -*-
"""
Deterministic company research profile.

The 12 dimensions are always analyzed, but they should not carry equal practical
importance for every business.  This module classifies a company into a broad research
archetype using disclosed industry / R&D / capital-return facts, then declares which
dimensions and evidence categories deserve priority.

It never produces a bullish/bearish investment conclusion.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

from pydantic import BaseModel, Field

from analysis.evidence import EvidenceItem


class ResearchProfile(BaseModel):
    archetype: str = Field(
        default="general",
        description="technology|cyclical|bank|financial|consumer_brand|stable_yield|general",
    )
    label: str = "综合型企业"
    industry: str = ""
    rationale: List[str] = Field(default_factory=list)
    priority_dimensions: List[str] = Field(default_factory=list)
    priority_evidence_categories: List[str] = Field(default_factory=list)
    missing_priority_evidence: List[str] = Field(default_factory=list)
    readiness: float = Field(default=0.0, ge=0.0, le=1.0)
    analysis_rules: List[str] = Field(default_factory=list)


_BANK_RE = re.compile(
    r"银行|城商行|农商行|股份制银行|国有大型银行|商业银行"
)
_FINANCIAL_RE = re.compile(
    r"保险|证券|多元金融|金融服务|信托|期货"
)
_CYCLICAL_RE = re.compile(
    r"煤炭|石油|油气|炼化|有色|贵金属|黄金|铜|铝|钢铁|水泥|玻璃|"
    r"化工|基础化工|航运|港口|机场|造纸|养殖|农产品|锂|稀土|盐湖"
)
_TECH_RE = re.compile(
    r"半导体|电子|通信|计算机|软件|互联网|光学|光电子|电池|光伏|风电|"
    r"电网设备|自动化|机器人|机械设备|通用设备|专用设备|汽车零部件|"
    r"国防军工|航空|航天|医疗器械|生物制品"
)
_CONSUMER_RE = re.compile(
    r"白酒|啤酒|食品|饮料|乳品|调味品|家电|家居|服装|纺织|化妆品|"
    r"珠宝|黄金珠宝|旅游|酒店|餐饮|零售|品牌消费"
)
_STABLE_YIELD_RE = re.compile(
    r"电力|火电|火力发电|水电|水力发电|核电|核力发电|公用事业|水务|燃气|高速公路|铁路|港口运营|"
    r"电信运营|运营商"
)

_PROFILE_CONFIG: Dict[str, Dict[str, Any]] = {
    "technology": {
        "label": "科技/先进制造",
        "priority_dimensions": [
            "研发、人才与技术护城河",
            "经营基本面与护城河",
            "增长空间与股票弹性",
            "价格与估值位置",
            "财务质量与尾部风险",
        ],
        "priority_evidence_categories": [
            "rd",
            "rd_team",
            "fundamentals",
            "profitability",
            "valuation_history",
        ],
        "analysis_rules": [
            "研发投入高、研发人数多都不等于技术护城河；必须把研发人员结构、发明专利/核心IP、产品商业化、客户认证与利润兑现交叉验证。",
            "必须区分工程化/量产/认证壁垒与底层标准/技术路线控制权；若路线由大客户或平台方定义，应显式评估二供替代与议价权风险。",
            "高增长预期必须与历史估值分位、下游资本开支资金来源一起解释，不能只因行业景气就接受高估值。",
            "应收和存货若明显快于营收增长，应检查量产扩张是否正在恶化现金回收。",
        ],
    },
    "cyclical": {
        "label": "周期/资源",
        "priority_dimensions": [
            "行业周期位置",
            "价格与估值位置",
            "政策、利率、汇率与地缘",
            "财务质量与尾部风险",
            "股东回报与资本抽取",
        ],
        "priority_evidence_categories": [
            "cycle_signal",
            "industry",
            "valuation_history",
            "profitability",
            "shareholder_return",
        ],
        "analysis_rules": [
            "周期顶部的低PE可能是假便宜；必须结合产品价格、库存/运价、资本开支和中周期利润。",
            "周期底部的高PE也可能只是利润分母过低，不能机械按静态PE看贵。",
            "现金成本、资产负债率和股东回报决定公司能否安全穿越下行周期。",
        ],
    },
    "bank": {
        "label": "银行",
        "priority_dimensions": [
            "财务质量与尾部风险",
            "经营基本面与护城河",
            "价格与估值位置",
            "股东回报与资本抽取",
            "政策、利率、汇率与地缘",
        ],
        "priority_evidence_categories": [
            "bank_quality",
            "fundamentals",
            "profitability",
            "valuation_history",
            "macro_rates",
        ],
        "analysis_rules": [
            "银行不能用制造业经营现金流、存货和应收账款逻辑机械评价；核心看净息差、不良率、拨备覆盖率、资本充足率、ROE与存贷款结构。",
            "低PB必须与ROE、净息差和资产质量一起解释；如果不良率上升、拨备覆盖下降或核心一级资本承压，低PB可能是风险折价而不是便宜。",
            "利率变化通过资产端收益率、负债端存款成本和净息差传导；只看到LPR变化时不得直接推断利润方向。",
            "银行资产负债率本身缺乏跨行业解释力，不能拿普通企业阈值判断银行杠杆风险。",
        ],
    },
    "financial": {
        "label": "非银金融",
        "priority_dimensions": [
            "财务质量与尾部风险",
            "管理层与治理",
            "股东回报与资本抽取",
            "价格与估值位置",
            "政策、利率、汇率与地缘",
        ],
        "priority_evidence_categories": [
            "fundamentals",
            "profitability",
            "management_capital",
            "shareholder_return",
            "macro_rates",
        ],
        "analysis_rules": [
            "金融企业不能按普通制造业现金流逻辑机械评价，重点转向资产质量、资本充足、盈利能力与资本回报。",
            "低PB必须与ROE、资产质量和资本约束一起解释，不能单独构成低估结论。",
            "利率环境会影响息差、投资收益和估值，但必须注明数据时点。",
        ],
    },
    "consumer_brand": {
        "label": "消费品牌",
        "priority_dimensions": [
            "经营基本面与护城河",
            "管理层与治理",
            "价格与估值位置",
            "增长空间与股票弹性",
            "财务质量与尾部风险",
        ],
        "priority_evidence_categories": [
            "fundamentals",
            "management_capital",
            "valuation_history",
            "profitability",
            "shareholder_return",
        ],
        "analysis_rules": [
            "品牌知名度不是护城河结论，必须看毛利率、渠道/客户、现金流和长期资本回报。",
            "优秀公司也可能因为估值透支而只具备中性赔率。",
            "库存和应收变化要结合渠道压货、促销和真实终端需求解释。",
        ],
    },
    "stable_yield": {
        "label": "成熟高股息/公用事业",
        "priority_dimensions": [
            "股东回报与资本抽取",
            "财务质量与尾部风险",
            "价格与估值位置",
            "管理层与治理",
            "政策、利率、汇率与地缘",
        ],
        "priority_evidence_categories": [
            "management_capital",
            "shareholder_return",
            "profitability",
            "valuation_history",
            "macro_rates",
        ],
        "analysis_rules": [
            "高股息必须检查分红覆盖、自由现金流/负债和资本开支，不能把一次高分红外推为永久收益率。",
            "利率变化会改变高股息资产的相对吸引力，但不替代企业自身现金创造能力。",
            "政策定价、煤价/燃料成本或资本开支周期可能显著改变分红能力。",
        ],
    },
    "general": {
        "label": "综合型企业",
        "priority_dimensions": [
            "经营基本面与护城河",
            "价格与估值位置",
            "管理层与治理",
            "财务质量与尾部风险",
            "增长空间与股票弹性",
        ],
        "priority_evidence_categories": [
            "fundamentals",
            "valuation_history",
            "management_capital",
            "profitability",
        ],
        "analysis_rules": [
            "先区分公司长期质量与当前股票赔率，再综合形成结论。",
            "缺少关键证据时降低置信度，不用其他维度的材料替代。",
        ],
    },
}


def _facts(fundamentals: Optional[Dict]) -> Dict:
    return (fundamentals or {}).get("facts") or {}


def _num(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def classify_research_profile(
    *,
    fundamentals: Optional[Dict],
    management_capital: Optional[Dict] = None,
    evidence: Optional[Iterable[EvidenceItem]] = None,
) -> ResearchProfile:
    facts = _facts(fundamentals)
    sw_industry = str(facts.get("sw_industry") or "").strip()
    industry_hint = str(facts.get("industry_hint") or "").strip()
    financial_subtype = str(facts.get("financial_subtype") or "").strip()
    industry = sw_industry or industry_hint
    rd_intensity = _num(facts.get("rd_intensity_pct"))
    rationale: List[str] = []

    # More specific structural industries win before the generic technology pattern.
    if financial_subtype == "bank" or _BANK_RE.search(industry):
        archetype = "bank"
        if sw_industry:
            rationale.append(f"申万行业“{sw_industry}”属于银行业。")
        else:
            rationale.append("历史原始财报出现银行专用披露特征，按银行框架研究。")
    elif _FINANCIAL_RE.search(industry):
        archetype = "financial"
        rationale.append(
            f"{'申万行业' if sw_industry else '行业线索'}“{industry}”属于非银金融。"
        )
    elif _CYCLICAL_RE.search(industry):
        archetype = "cyclical"
        rationale.append(f"申万行业“{industry}”具有明显周期/资源属性。")
    elif _CONSUMER_RE.search(industry):
        archetype = "consumer_brand"
        rationale.append(f"申万行业“{industry}”更依赖品牌、渠道与终端需求。")
    elif _STABLE_YIELD_RE.search(industry):
        archetype = "stable_yield"
        rationale.append(f"申万行业“{industry}”具有公用事业/成熟现金流特征。")
    elif _TECH_RE.search(industry) or (rd_intensity is not None and rd_intensity >= 4.0):
        archetype = "technology"
        if industry:
            rationale.append(f"申万行业“{industry}”属于科技/先进制造重点范围。")
        if rd_intensity is not None and rd_intensity >= 4.0:
            rationale.append(f"研发强度 {rd_intensity}% 达到高研发画像阈值。")
    else:
        archetype = "general"
        rationale.append(
            f"行业线索“{industry or '暂缺'}”未命中特定研究模板，使用综合型框架。"
        )

    # A utility-like industry with a demonstrated multi-year dividend habit is even more
    # clearly a yield/capital-return case.  We do not classify by dividend alone.
    if archetype == "stable_yield" and management_capital:
        five = management_capital.get("five_year") or {}
        covered = _num(five.get("cash_dividend_years"))
        if covered is not None and covered >= 4:
            rationale.append("近5年现金分红覆盖较稳定，股东回报应作为一等研究变量。")

    cfg = _PROFILE_CONFIG[archetype]
    categories = {e.category for e in (evidence or [])}
    required = list(cfg["priority_evidence_categories"])
    missing = [x for x in required if x not in categories]
    readiness = (
        (len(required) - len(missing)) / len(required)
        if required
        else 1.0
    )

    return ResearchProfile(
        archetype=archetype,
        label=cfg["label"],
        industry=industry,
        rationale=rationale,
        priority_dimensions=list(cfg["priority_dimensions"]),
        priority_evidence_categories=required,
        missing_priority_evidence=missing,
        readiness=round(readiness, 3),
        analysis_rules=list(cfg["analysis_rules"]),
    )


def profile_prompt_block(profile: ResearchProfile) -> str:
    lines = [
        f"公司研究画像: {profile.label} ({profile.archetype})",
        f"行业识别: {profile.industry or '暂缺'}",
        "优先维度: " + "、".join(profile.priority_dimensions),
        f"重点证据准备度: {profile.readiness * 100:.0f}%",
    ]
    if profile.missing_priority_evidence:
        lines.append(
            "重点证据缺口: " + "、".join(profile.missing_priority_evidence)
        )
    if profile.rationale:
        lines.append("画像依据: " + "；".join(profile.rationale))
    if profile.analysis_rules:
        lines.append("本类公司专门规则:")
        lines.extend(f"  · {x}" for x in profile.analysis_rules)
    lines.append(
        "注意: 12个维度仍需全部分析；本画像只改变研究重点和缺证据时的置信度，不自动决定看多/看空。"
    )
    return "\n".join(lines)
