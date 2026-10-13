"""Turn dated private investor discussions into testable market-style causes.

Private sources are research hypotheses, never proof of ETF orders, actual fund
flows, or issuer operating performance. Source identities, post content, URLs,
and ticker examples never leave this module.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Iterable, Mapping

from analysis.investment_method_distiller import (
    _deidentify_seed, validate_method_output,
)
from analysis.market_research_context import _day, _STYLE_WORDS

# Ranking covers *causal questions*, not only the word 红利.  A discussion of
# why high-valuation technology sells off can be relevant without saying 红利.
_CAUSAL = re.compile(
    r"利率|国债|债券|债市|保险资金|险资|股息|红利|科技|成长|估值|资金|"
    r"调仓|高低切|风格|抱团|机构|股基|基金|ETF|回撤|避险|公募|"
    r"现金流|获利盘|资金成本|收益率|牛市|轮动|赎回|申购", re.I,
)
_EXPLAIN = re.compile(
    r"因为|原因|机制|驱动|所以|导致|流入|流出|切换|再平衡|"
    r"不愿|选择|替代|风险偏好|压力|收益|资金|分流", re.I,
)
# Curated, conditional mechanism fallbacks are emitted ONLY if a matched
# private source contained enough qualifying terms.
_FALLBACKS = (
    (
        "allocation_rebalance",
        re.compile(r"(调仓|高低切|获利盘|轮动|抱团|风格).{0,35}(科技|成长|红利|高股息)|"
                   r"(科技|成长|红利).{0,35}(调仓|轮动|高低切|获利盘|抱团)", re.I),
        {
            "insight": "成长端阶段性涨幅与估值预期较高时，部分配置资金可能再平衡至现金回报更可见的板块，造成红利相对走强。",
            "use_when": "这是资金迁移与风险偏好变化的候选解释，不等于已经证实机构净卖科技、净买红利。",
            "possible_mistake": "成长股下跌和红利跑赢也可能来自盈利预期重新定价，而非同一批资金直接换仓。",
            "company_question": "核对同期指数成交、主动基金持仓数量、ETF份额与申赎，分离估值变化和企业盈利修正。",
        },
    ),
    (
        "required_return",
        re.compile(r"(利率|国债|债券|债市|险资|保险).{0,30}(股息|分红|收益率|红利|资产配置)|"
                   r"(股息|分红|收益率|红利).{0,30}(利率|债券|债市|险资|保险)", re.I),
        {
            "insight": "当可替代的低风险资产收益率或长久期资金的配置要求变化时，可持续股息的相对吸引力也会变化。",
            "use_when": "需要同期国债收益率和保险等长线资金配置证据，不能仅凭红利上涨就断言利率是驱动因素。",
            "possible_mistake": "高股息不等于安全，利率上行、利润下滑或减配也可能使股息资产失去吸引力。",
            "company_question": "核对同期可交易国债收益率、险资与基金持仓、公司真实分红现金流及现价股息率。",
        },
    ),
    (
        "growth_expectations",
        re.compile(r"(科技|成长|AI|估值).{0,45}(泡沫|回撤|透支|增长|盈利|风险|高位|估值|预期)|"
                   r"(高估值|高位|盈利预期).{0,30}(科技|成长)", re.I),
        {
            "insight": "当高预期的科技成长股被重新审视盈利兑现与融资回报时，估值收缩可能使资金转向更重视现金回报的资产。",
            "use_when": "这是相对估值与盈利确定性的假设，不意味着全部科技公司经营恶化或红利公司基本面改善。",
            "possible_mistake": "若科技订单与盈利持续超预期，成长股仍可能重新取得相对收益优势。",
            "company_question": "核对科技股盈利预测修正、实际订单兑现与估值变化，比较红利板块净利润及股息覆盖率。",
        },
    ),
    (
        "dividend_cashflow",
        re.compile(r"(红利|高股息|分红|股息).{0,40}(现金流|利润|派息|确定性|回报|安全边际)|"
                   r"(现金流|利润|确定性).{0,40}(红利|高股息|分红|股息)", re.I),
        {
            "insight": "在盈利前景不确定时，市场可能给可持续、能够由自由现金流支付的现金分红更高估值。",
            "use_when": "这只说明投资者可能重估现金回报的确定性，指数走强本身不能证明企业有更高盈利。",
            "possible_mistake": "若高股息来自一次性收益、举债派息或周期顶部利润，稳定性可能被市场高估。",
            "company_question": "核对已经实施的现金派息、维持性资本支出、净债务变化和至少一个完整周期的利润。",
        },
    ),
)


def _select_sources(excerpts: Iterable, cutoff: date, limit: int = 10) -> list:
    choices = []
    for item in excerpts or []:
        published = _day(getattr(item, "published_at", None))
        if not published or published > cutoff or (cutoff - published).days > 1095:
            continue
        body = str(getattr(item, "distilled", "") or "").strip()
        if len(body) < 20:
            continue
        if not _CAUSAL.search(body):
            continue
        age = (cutoff - published).days
        marker = str(getattr(item, "source", "") or "").lower()
        transcript = "transcript" in marker
        text = str(getattr(item, "title", "") or "") + "\n" + body
        score = (5 if _STYLE_WORDS.search(text) else 0) + (
            3 if _EXPLAIN.search(body) else 0
        ) + (2 if transcript else 0) + (2 if age <= 120 else 0)
        # Never require a specific author name. A transcript and a post are
        # equally eligible, with a modest recall advantage for long transcripts.
        choices.append((score, published.toordinal(), item))
    choices.sort(key=lambda v: (v[0], v[1]), reverse=True)
    selected = []
    seen = set()
    for _, _, item in choices:
        key = (getattr(item, "source", ""), getattr(item, "title", ""),
               getattr(item, "published_at", ""))
        if key not in seen:
            selected.append(item)
            seen.add(key)
        if len(selected) >= limit:
            break
    return selected


def _safe_fallback(selected: list) -> list[dict]:
    # Fallbacks may only express hypotheses related to actual selected text.
    text = "\n".join(str(getattr(e, "distilled", "") or "") for e in selected)
    result = []
    for _, pattern, method in _FALLBACKS:
        if pattern.search(text):
            result.append(dict(method))
    return validate_method_output(result[:4], selected)


async def build_market_style_reasons(
    excerpts: Iterable, *, as_of: str, market_context: Mapping | None = None,
    industry: str = "",
) -> dict:
    cutoff = _day(as_of) or date.today()
    selected = _select_sources(excerpts, cutoff)
    if not selected:
        return {
            "status": "no_matching_distilled_knowledge", "drivers": [],
            "private_excerpt_count": 0, "private_transcript_count": 0,
        }
    transcript_count = sum(
        "transcript" in str(getattr(x, "source", "") or "").lower()
        for x in selected
    )
    baseline = _safe_fallback(selected)
    # Preserves individual causal chains from the author's distilled source.
    # Only vetted, rewritten abstractions enter style_context and the UI.
    snippets = [
        _deidentify_seed(str(getattr(e, "distilled", "") or "")[:950], selected)
        for e in selected[:8]
    ]
    prompt = (
        "请从下列匿名化的既有投资经验中，提炼2至4条有关本轮红利与科技成长"
        "相对表现的【原因机制】，而不只是复述指数涨跌。要保留原材料特有的"
        "条件、资金来源、买卖动机和逻辑分歧；不能自己编造作者未提及的驱动因素。"
        "区别两层：已观测到的指数相对表现不证明资金具体流向；文章/直播观点"
        "是待验证机制而非成交数据或客观事实。"
        "仅输出JSON数组，每条有 insight(一条完整因果链)、"
        "use_when(需要怎样的市场条件才能成立)、"
        "possible_mistake(另一种解释或失效条件)、"
        "company_question(需要核实的指数/基金/行业数据)，四字段分别至少12字。"
        "不能出现姓名、作者、账号、原帖标题、直播原句、URL、"
        "证券名称和代码、个人观点归属、其它公司的例子。"
        "没有具体因果线索时输出空数组，禁止泛泛填空或幻觉。"
        "\n\n匿名化观点（内部研究，不可外传）：\n"
        + "\n".join(f"[{i+1}] {v}" for i, v in enumerate(snippets))
    )
    generated = None
    try:
        from backtest.llm_client import call_json_ex
        generated, _ = await call_json_ex(
            prompt, max_tokens=4096,
            repair_requirements=(
                "仅输出JSON数组，每条insight/use_when/possible_mistake/"
                "company_question四个字段，不能含私人来源或股票案例。"
            ),
        )
    except Exception:
        pass  # A quote API failure must not silently invent a market explanation.
    validated = validate_method_output(generated, selected)
    reasons = validated[:4] if validated else baseline
    return {
        "status": ("distilled_hypotheses" if validated else
                   "keyword_grounded_fallback" if baseline else
                   "knowledge_available_unresolved"),
        "drivers": [
            {
                "mechanism": item["insight"],
                "conditions": item["use_when"],
                "alternative": item["possible_mistake"],
                "verification": item["company_question"],
                "evidence_status": "private_research_hypothesis",
            }
            for item in reasons
        ],
        "private_excerpt_count": len(selected),
        "private_transcript_count": transcript_count,
    }


def market_style_reasons_prompt_block(context: Mapping | None) -> str:
    if not context:
        return "近期红利行情驱动原因尚无经过提炼的适用观点，不应猜测市场资金流向。"
    rows = [
        "## 本轮市场风格为何变化（用于A股资金结构、股价位置及结论，不是单纯指数涨跌复述）",
    ]
    drivers = context.get("drivers") or []
    if not drivers:
        rows.append(
            "本次没有可转化为公司研究的知识库因果机制；"
            "不得假装已阅读缺失的直播/帖子，也不得把指数上涨等同于资金净流入。"
        )
    for item in drivers[:4]:
        rows.append(
            "可能的驱动：" + item["mechanism"] +
            "；成立条件：" + item["conditions"] +
            "；相反解释：" + item["alternative"] +
            "；验证资料：" + item["verification"]
        )
    rows.append(
        "这些是匿名化、已核对不含原帖的机制假设。必须与当期有日期的指数、"
        "基金申赎、公司现金分红和经营业绩区分；没有真实资金流数据，"
        "只能写可能推动而不能写已经确认资金从科技流入红利。"
    )
    return "\n".join(rows)
