# -*- coding: utf-8 -*-
"""Turn long-horizon KOL knowledge into explicit, testable research hypotheses.

Knowledge from experienced investors is useful for discovering mechanisms that public
filings and sell-side reports often do not frame explicitly.  It is still opinion, not
fact.  This module therefore never upgrades KOL text into factual evidence; it converts
it into hypotheses, applicability conditions and verification questions that the final
synthesis must test against the hard evidence ledger.
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Sequence, Set

from model.m_analysis import KnowledgeExcerpt


_ISSUE_RULES: Dict[str, Dict[str, object]] = {
    "fund_flow": {
        "label": "资金抱团/风格与边际买家",
        "patterns": (
            r"公募|基金|ETF|抱团|风格切换|资金切换|调仓|机构|散户|派发|接盘|边际买家|被动资金",
        ),
        "questions": (
            "持仓市值上升来自净买入还是股价上涨？",
            "主动基金、被动ETF、北向、保险、社保/养老金、产业资本的持股变化是否同向？",
            "股东户数、融资余额和指数/ETF申赎是否与股价上涨同步扩张？",
        ),
    },
    "moat_control": {
        "label": "技术护城河与路线控制权",
        "patterns": (
            r"护城河|研发|专利|技术路线|标准|架构|代工|组装|工程化|良率|认证|供应商|客户指定|英伟达|NVIDIA|CPO|NPO|LPO|硅光|光模块",
        ),
        "questions": (
            "公司掌握的是底层IP/标准制定权，还是工程化、量产与客户认证能力？",
            "关键产品路线由公司决定，还是由核心客户/上游芯片平台决定？",
            "替换供应商的认证成本、切换周期和客户多源策略能否量化？",
            "研发人员学历结构、发明专利、核心器件自研比例和新产品商业化是否相互印证？",
        ),
    },
    "customer_dependency": {
        "label": "大客户依赖与议价权",
        "patterns": (
            r"大客户|客户集中|单一客户|供应商|议价权|订单|认证|二供|多供|供应链|饭碗|依赖",
        ),
        "questions": (
            "前五大/第一大客户占比是多少，且过去数年是在上升还是下降？",
            "核心客户若导入第二供应商，公司收入/毛利率的敏感度多大？",
            "公司能否通过产品、IP或切换成本反向约束客户，而不仅是被客户认证？",
        ),
    },
    "demand_financing": {
        "label": "终端需求质量与融资来源",
        "patterns": (
            r"资本开支|Capex|算力|AI|数据中心|基础设施|企业债|公司债|发债|债务融资|私募信贷|SPV|自由现金流|寒冬|泡沫",
        ),
        "questions": (
            "下游资本开支由自由现金流还是新增债务/表外融资支撑？",
            "终端客户Capex增速、自由现金流、净负债和信用利差是否同时恶化？",
            "若Capex增速回落，公司订单、ASP、毛利率和库存会以什么顺序受影响？",
        ),
    },
    "valuation_reflexivity": {
        "label": "估值泡沫与叙事反身性",
        "patterns": (
            r"估值|泡沫|牛市|科技牛|戴维斯|杀估值|双杀|情绪|叙事|透支|高估|低估",
        ),
        "questions": (
            "估值扩张有多少来自盈利上修，有多少来自风险偏好/主题资金？",
            "若盈利预测下修，当前估值分位是否会同时压缩形成戴维斯双杀？",
            "公司自身历史估值是否因商业模式/资本结构变化而需要口径重置？",
        ),
    },
    "insider_alignment": {
        "label": "内部人利益一致性",
        "patterns": (
            r"高管|实控人|大股东|减持|增持|回购|股权激励|员工持股|套现|利益一致",
        ),
        "questions": (
            "公司回购资金来自全体股东资产，是否用于注销还是员工激励？",
            "同一时期大股东/高管是在用个人资金增持，还是出售个人持股？",
            "内部人交易发生时的估值、股价位置和后续锁定承诺是什么？",
        ),
    },
    "accounting_quality": {
        "label": "利润质量与现金兑现",
        "patterns": (
            r"现金流|应收|存货|库存|利润质量|回款|现金转换|资本化|减值|账面利润",
        ),
        "questions": (
            "经营现金流/净利润、应收和存货相对营收的变化是否支持利润质量？",
            "高速增长是否依赖更宽松账期、提前备货或资本化支出？",
            "利润率改善来自产品价值量、价格、成本下降还是会计/一次性因素？",
        ),
    },
    "policy_geopolitics": {
        "label": "政策、海外与地缘依赖",
        "patterns": (
            r"美国|海外|出口|关税|制裁|地缘|政策|人民币|美元|汇率|国产替代|出口管制",
        ),
        "questions": (
            "海外收入和单一区域收入占比是多少？",
            "汇率、关税、出口管制或客户所在地政策通过哪条链影响收入/毛利？",
            "公司是否具备区域客户多元化与供应链替代能力？",
        ),
    },
}


_SECTION_RE = re.compile(
    r"【(?P<label>原则|主张|机制|适用条件|失效条件|核验指标|利益相关方)】"
)


def _sections(text: str) -> Dict[str, str]:
    """Parse the structured labels already produced by the knowledge distiller."""
    matches = list(_SECTION_RE.finditer(text or ""))
    if not matches:
        return {"主张": (text or "").strip()}
    out: Dict[str, str] = {}
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        value = text[start:end].strip(" \n\t；;")
        if value:
            label = match.group("label")
            if label == "原则":
                label = "主张"
            out[label] = value
    return out


def _tags(text: str) -> List[str]:
    out: List[str] = []
    for key, rule in _ISSUE_RULES.items():
        if any(re.search(p, text, re.I) for p in rule["patterns"]):
            out.append(key)
    return out


def active_knowledge_topics(
    *,
    archetype: str = "",
    fundamentals: Optional[Dict] = None,
) -> Set[str]:
    """Infer which causal issues deserve retrieval for this company."""
    facts = (fundamentals or {}).get("facts") or {}
    active: Set[str] = {
        "fund_flow",
        "valuation_reflexivity",
        "insider_alignment",
        "accounting_quality",
    }
    if archetype == "technology":
        active.update(
            {
                "moat_control",
                "customer_dependency",
                "demand_financing",
                "policy_geopolitics",
            }
        )
    try:
        overseas = float(facts.get("overseas_revenue_pct"))
    except (TypeError, ValueError):
        overseas = 0.0
    try:
        top5_customer = float(facts.get("top5_customer_pct"))
    except (TypeError, ValueError):
        top5_customer = 0.0
    if overseas >= 30:
        active.add("policy_geopolitics")
        active.add("demand_financing")
    if top5_customer >= 30:
        active.add("customer_dependency")
    if facts.get("rd_investment_yuan") is not None or facts.get("rd_intensity_pct") is not None:
        active.add("moat_control")
    return active


def prefilter_knowledge_by_context(
    excerpts: Sequence[KnowledgeExcerpt],
    stock_code: str,
    stock_name: str,
    industry_name: Optional[str],
    *,
    archetype: str = "",
    fundamentals: Optional[Dict] = None,
    limit: int = 160,
) -> List[KnowledgeExcerpt]:
    """Broad deterministic recall before any optional LLM relevance rerank.

    Direct company/industry mentions win.  Indirect entries survive when they discuss a
    causal issue active for the company (e.g. fund crowding, customer dependency or AI
    capex financing), even if they never name the stock.
    """
    if not excerpts:
        return []
    terms = [
        str(x or "").strip()
        for x in (stock_code, stock_name, industry_name or "")
        if len(str(x or "").strip()) >= 2
    ]
    active = active_knowledge_topics(archetype=archetype, fundamentals=fundamentals)
    ranked = []
    for idx, entry in enumerate(excerpts):
        text = f"{entry.title}\n{entry.distilled}"
        direct = any(term in text for term in terms)
        item_tags = set(_tags(text))
        overlap = item_tags.intersection(active)
        if not direct and not overlap:
            continue
        score = (10 if direct else 0) + min(9, len(overlap) * 3)
        if "【机制】" in entry.distilled:
            score += 1
        if "【适用条件】" in entry.distilled:
            score += 1
        # Original order is newest-first, so idx is a deterministic recency tie-breaker.
        ranked.append((score, -idx, entry))
    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return [row[2] for row in ranked[:limit]]


def build_knowledge_hypotheses(
    excerpts: Sequence[KnowledgeExcerpt],
    stock_code: str,
    stock_name: str,
    industry_name: Optional[str],
    *,
    archetype: str = "",
    fundamentals: Optional[Dict] = None,
    limit: int = 24,
) -> List[Dict]:
    """Convert relevant KOL summaries into auditable hypotheses for the final model."""
    selected = prefilter_knowledge_by_context(
        excerpts,
        stock_code,
        stock_name,
        industry_name,
        archetype=archetype,
        fundamentals=fundamentals,
        limit=max(limit * 3, limit),
    )
    active = active_knowledge_topics(archetype=archetype, fundamentals=fundamentals)
    out: List[Dict] = []
    seen = set()
    for entry in selected:
        parsed = _sections(entry.distilled)
        text = f"{entry.title}\n{entry.distilled}"
        tags = [x for x in _tags(text) if x in active]
        if not tags:
            continue
        fingerprint = (parsed.get("主张") or entry.distilled[:240]).strip()
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        questions: List[str] = []
        for tag in tags:
            for q in _ISSUE_RULES[tag]["questions"]:
                if q not in questions:
                    questions.append(q)
        out.append(
            {
                "source": entry.source,
                "author": entry.author,
                "title": entry.title,
                "published_at": entry.published_at,
                "source_url": entry.source_url,
                "topics": tags,
                "topic_labels": [_ISSUE_RULES[x]["label"] for x in tags],
                "claim": parsed.get("主张") or entry.distilled,
                "mechanism": parsed.get("机制", ""),
                "applicability": parsed.get("适用条件", ""),
                "failure_conditions": parsed.get("失效条件", ""),
                "verification_questions": questions[:6],
            }
        )
        if len(out) >= limit:
            break
    return out


def format_knowledge_hypotheses(items: Iterable[Dict]) -> str:
    rows = list(items)
    if not rows:
        return "(没有检索到与当前公司核心因果风险相关的知识库假设)"
    lines = [
        "以下内容来自老木匠、军师祭咖啡等长期观察者的观点库。它们是【待核验假设】，不是事实，",
        "用途是提醒你公开数据背后可能存在的资金、利益、产业链或周期机制。不得因为作者说过就直接加减分；",
        "必须用本次硬数据/一手公告交叉验证。无法验证时写“假设成立与否暂缺”，而不是忽略。",
    ]
    for i, item in enumerate(rows, 1):
        src = item.get("author") or item.get("source") or "knowledge"
        dt = item.get("published_at") or "日期未知"
        labels = "、".join(item.get("topic_labels") or [])
        lines.append(f"{i}. [{src} {dt}] 主题={labels}")
        lines.append(f"   主张: {item.get('claim') or '未说明'}")
        if item.get("mechanism"):
            lines.append(f"   机制: {item['mechanism']}")
        if item.get("applicability"):
            lines.append(f"   适用条件: {item['applicability']}")
        if item.get("failure_conditions"):
            lines.append(f"   失效条件: {item['failure_conditions']}")
        questions = item.get("verification_questions") or []
        if questions:
            lines.append("   本次必须核验: " + "；".join(questions[:4]))
    return "\n".join(lines)
