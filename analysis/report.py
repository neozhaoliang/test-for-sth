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
综合分析报告生成: 候选高可信度用户 (历史) + 实时抓取最新发帖 + 实时行情
+ 同花顺 F10 结构性事实 + 估值 + 雪球个股 -> LLM 结构化摘要。
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from analysis.candidates import find_candidates
from analysis.commodity import get_copper_spread_signal, get_rmb_trend_signal
from analysis.fundamentals import get_ths_fundamentals
from analysis.industry import get_industry_comparison
from analysis.knowledge_base import ensure_loaded as ensure_knowledge_base_loaded
from analysis.profitability import get_profitability_trend
from analysis.realtime_price import get_realtime_quote, get_stock_name
from analysis.session import AnalysisBrowserSession
from analysis.shareholder import get_buyback_history, get_dividend_history, get_shareholder_count_trend
from analysis.xueqiu_stock import get_xueqiu_stock_data
from backtest.llm_client import call_json_ex
from backtest.score import load_records
from model.m_analysis import AnalysisReport, CandidateOpinion, KnowledgeExcerpt, StructuredSummary
from tools.utils import utils

_MAX_LATEST_POSTS = 5
_MAX_HISTORICAL_THESIS = 5
_MIN_CORROBORATING_RECORDS = 2  # 历史验证记录 < 该值时提示"参考价值有限"

_PROMPT_VERSION = "v9-plain-language"

# 实测: 300308 的 v7 prompt 输出 6060 tokens，其中约 5000 花在 thinking 块上。
# 报告类 prompt 的思考预算随输入维度数量增长，上限必须留足余量，否则截断到 len=0。
_SUMMARY_MAX_TOKENS = 16384

_VALID_STANCES = {"bullish", "bearish", "neutral"}
_VALID_LYNCH_CATEGORIES = {
    "fast_grower", "stalwart", "cyclical", "turnaround", "asset_play", "slow_grower", "unclear",
}

_PROMPT_TEMPLATE = """你是一名有明确立场的证券分析师，风格类似彼得林奇: 先判断这是哪一类机会，再给出一个明确、可被证伪的结论。禁止给"多空都有可能""需持续观察"这类模糊结论——你必须选边站，哪怕证据不完美。

**第一原则: 这是判断，不是叙述。** 市场上关于一只股票的研报和新闻，绝大多数是股价上涨之后才出现的追认叙事——它们只能解释"它为什么涨了"，回答不了"它在什么情况下会崩"。你的任务是拿可核验的披露数据去找出这家公司的脆弱性，而不是复述市场上的看多故事。一家公司当前涨得好，和它是否脆弱，是两个可以同时成立的判断。

**硬性要求 (违反任何一条都视为不合格输出):**
- 引用任何维度的证据时，必须带出该数据块里的具体数字/期间。数据块标注"暂缺"的维度，只能写"该维度数据暂缺"，**禁止编造，也禁止在该维度上做任何方向的断言——包括反向断言** (数据缺失时不能说"客户集中度低""没有地缘风险")。
- 引用"加息/降息/宏观流动性"类论据时，必须点名央行/经济体、当前政策利率水平或近期变动幅度、预期持续时间窗口。不确定精确数字时，要说明这是基于知识的粗略估计，但仍要给出具体数量级和主体，不能只写"加息预期"四个字。
- **你的判断只能建立在下述数据块给出的数字上。** 数据块里没有的维度一律写"该维度数据暂缺"，禁止凭记忆、市场印象或"这类公司通常……"来补全。读起来通顺但对不上数据块的结论，比写"暂缺"更糟糕。
- 结构性事实块里的比率全部已在 Python 里算好，直接引用，**不要自己重新做算术**。该块里"公司自述的风险"和"公司自己的经营表述"属于利益相关方视角，不能当作客观事实，只能作为"公司自己承认了什么""公司自己想让你相信什么"来引用；公司自述与其披露数字矛盾时以数字为准。严禁把"与头部客户深度绑定""技术领先""行业龙头"这类说法当成护城河证据——除非同一数据块里有可核验的数字支撑。

股票: {stock_code} ({stock_name})
当前行情: {quote_line}

{valuation_block}

{fundamentals_block}

汇率敞口 (判断汇率变动对收入的影响方向):
{fx_block}

{xueqiu_block}

历史验证过的高可信度用户观点 (每人历史命中率越高、验证样本越多，参考价值越大):
{candidates_block}

知识库背景资料 (投资者专栏的长期观点，已去除闲聊，未针对本股票筛选，可能涉及投资哲学、筹码博弈、行情/宏观判断等，请自行判断哪些与当前分析相关):
{knowledge_block}

行业整体涨跌 (判断是不是行业普涨/普跌带来的行情，而非公司自身经营变化):
{industry_block}

股东回报与筹码 (股东户数变化可作散户情绪/筹码集中度的代理指标——户数增加通常意味着原有大户/机构筹码被拆分卖给了更分散的散户，即"机构派发给散户"；户数减少则是筹码集中。分红回购历史反映管理层对股东的回报态度):
{shareholder_block}

盈利能力与成本弹性 (近几个报告期毛利率/主营业务利润率、净利率、ROE、资产负债率的具体走势):
{profitability_block}

大宗商品价差与汇率信号 (仅周期性矿业股适用；只有这里给出了具体数字时，才能在结论里提"囤货/金融属性"，否则不能提):
{commodity_block}

按以下顺序分析。**必须依次走完这七步，每一步都要落到具体数字上；后一步不能跳过前一步直接下结论。**

**一、大势与宏观周期 (先看大势，再看个股)**
1. 点名主要央行的当前位置: 美联储、欧洲央行、日本央行、中国人民银行，各自处于 QE/扩表、缩表、加息、降息、还是观望的哪个阶段，给出政策利率的大致水平或最近一次变动的方向与幅度。
2. 正面回答这个格局问题: 当前是否处在"疫情后美欧日经历 QE 宽松、如今转向新一轮加息/紧缩，而中国既未跟随放水、也未跟随加息"的政策分化中？**如果存在这种分化，必须说明它对本标的的含义** (外资流向、人民币汇率、出口导向型企业的相对竞争力、国内流动性环境)；如果实际情况不是这样，说明真实的格局是什么。
3. 给出这个阶段的预期持续时间窗口 (到什么时候、看什么信号)。
> 不确定精确数字时必须标注为"基于知识的粗略估计"，但仍要给出主体和数量级，不能只写"加息预期"四个字。知识库资料里若有对当前宏观周期的判断，优先采用它并注明来自知识库。

**二、业务本质与外部依赖 (公司靠什么赚钱、命门握在谁手里)**
4. **海外市场依赖**: 引用海外收入占比 / 境外销量占比。占比高时必须评估海外需求波动、关税与贸易政策变化的影响。
5. **供应链自主性**: 是自主可控的国内供应链，还是关键环节依赖国外？从供应商集中度、公司自述风险原文、公司经营表述里找证据。**数据不足以判断时必须写"暂缺"，不得凭印象断言。**
6. **汇率对收入的影响**: 结合"汇率敞口"数据块给出方向性判断 (顺风/逆风) 和传导路径 (折算收入 / 报价竞争力 / 汇兑损益)。该块标注暂缺时，这一项写暂缺。
7. **核心科技与断供/制裁风险**: 用研发强度、发明专利占授权专利比例判断技术壁垒的真实高度；用海外收入占比、公司自述的出口管制/地缘政治风险判断断供与制裁的风险敞口。**"低研发强度 + 高海外依赖"这个组合意味着代工属性与被替代风险，一旦同时出现必须明确指出，不得含糊过去。**

**三、现金流质量 (利润是不是真金白银)**
8. 引用经营现金流/净利润比值、经营现金流同比方向、每股经营现金流。比值显著小于 1、或现金流同比大幅下滑时，必须说明利润含金量问题，并结合应收账款/存货的可能性给出解释或标注为"原因无法从本次数据确认"。

**四、管理层行为与股东回报 (看行为记录，不做人身评价)**
9. 用可核验的行为记录评估管理层是否善待股东: 分红是否长期持续、金额多少；回购是否真实执行、金额与进度；**股权质押比例** (高比例质押是治理风险信号，大股东资金链紧张时可能损害中小股东利益)。
10. 财务上有没有不合理的做法: 结合现金流与利润的背离、质押比例给出判断。**应收账款、存货、商誉、关联交易、大股东减持这几项本次未取到数据，不得凭空评价**——只能写"该维度数据暂缺"。

**五、股价与筹码的联动 (暴涨之后还有没有基本面支撑)**
11. 若结构性事实块给出了"股东户数 vs 同期股价序列"，引用其中的股价与户数对照，判断是否出现**"股价暴涨 + 股东户数同步暴涨"**的派发特征: 股价大涨的同时户数也大增，通常意味着原有大户/机构在高位把筹码分散卖给了散户。引用具体的股价区间和户数变化。
12. 然后回答本步最关键的问题: **后续基本面能否继续支撑这个涨幅？** 用营收增速、净利增速、经营现金流、当前估值四项来判断。**若股价涨幅远高于同期业绩增速，必须明确写出这是估值扩张而非业绩驱动**，并指出它靠什么继续维持。

**六、结构性反证六查 (整份分析的核心，必须逐条做完再下结论)**
13. 对下面六项，逐条写明"命中/未命中/数据暂缺"，命中的必须引用具体数字：
   a. **客户与供应商集中度**: 前五大客户占营业收入 ≥50%，或前五大供应商占总采购额 ≥50% → 命中。集中度高意味着单一客户流失或压价就能重创业绩，这是脆弱性而不是护城河，除非数据块显示该比例在多个报告期持续下降。
   b. **海外收入/销量占比与地缘风险**: 海外收入占比 ≥50%，或公司自述风险段落里明确提到出口、贸易、关税、地缘政治、出口管制风险 → 命中。必须直接引用公司自述的那句话。
   c. **利润含金量**: 经营现金流/净利润 <0.8 → 命中。比值显著小于 1 说明账面利润没有同步变成现金，可能依赖应收账款或存货，需要结合盈利数据判断是季节性还是趋势性。
   d. **研发强度与技术含量**: 研发投入占营业收入 <3%，或发明专利占授权专利比例 <30% → 命中 (银行等本身无研发投入的行业不适用，记为"不适用")。低研发强度意味着低毛利业务或代工属性，技术壁垒有限。
   e. **筹码结构**: 股东户数环比增幅 >20% (或同比大幅增加) → 命中，说明筹码正在从集中走向分散，通常是内部人/机构派发给散户的信号。
   f. **估值**: 市盈率(静态) >50 或 市净率 >8 → 命中。同时必须对比动态与静态市盈率的差距：二者差距巨大说明当前估值高度依赖未来利润继续高速增长，一旦增速回落估值会双杀。

**七、综合结论**
14. 判断这只股票更接近彼得林奇分类中的哪一类: fast_grower(高成长)、stalwart(大盘稳健股)、cyclical(周期股)、turnaround(困境反转)、asset_play(资产价值被低估)、slow_grower(低增长)。证据完全不足以判断时才选 unclear。
15. 判断当前行情是不是行业普涨/普跌驱动，引用行业数据块里的涨跌家数/涨跌幅数字。只有大宗商品数据块给出具体价格数字时，才能据此提"金融属性/套利成分"。
16. 引用盈利能力数据块里的毛利率/净利率/ROE/资产负债率数字和期间，判断盈利趋势是改善还是恶化。
17. **基于以上全部方面** (必须同时包含大势、业务本质、财务质量、管理层行为、筹码与股价联动这五类中的至少四类，不能只依赖其中一两个)，给出一个明确倾向 (bullish/bearish/neutral)。neutral 仅在证据真正相互抵消、没有任何一方占优时才能选，不能用来逃避判断。

**结论约束 (违反即视为不合格输出):**
- 六查 (第 13 步) 中只要有任意一项命中，就**不得**给出 bullish——除非你能用该数据块里的具体数字正面反驳它 (例如：证明集中度在多个报告期持续下降、现金流比值低是明确的季节性且有往期数字佐证)。空泛的辩护不算反驳。
- **禁止用分类豁免风险**: 选 fast_grower/cyclical 等等，不能成为跳过第 13 步已命中项的理由。"高成长所以贵一点合理""周期股现金流本来就波动"这类话，如果拿不出数字，就是不合格。
- **禁止用"行业景气"豁免第二、五步的结论**: 若第 11 步判定为"股价暴涨 + 户数暴涨"且第 12 步判定基本面增速跟不上涨幅，则同样不得给出 bullish。
- 无论最终倾向是什么，`core_counter_evidence` **必须填写**与你的结论相悖的最强证据，并带具体数字。确实一条都没有时才写"未发现"。**不允许留空，不允许写"暂无"。**
- `thesis_summary` 必须引用至少四个不同方面的具体数字，且必须正面回应对结论不利的脆弱性事实——不允许只挑利好数字、把不利事实挪到 risk_notes 里一笔带过。

**输出措辞 (违反即视为不合格输出):**
- 上面的"一/二/…/七"编号、步骤序号、"六查"、"维度"、"数据块"、"命中/未命中"，全部是给你自己推理用的流程语言。`thesis_summary` 等字段是写给投资者看的结论，**一律不得出现这些词**，也不得出现"第 N 步""经检查""多维验证""反证"这类自我描述，更不得出现"六查多项命中"这类句子。
- 要表达某项脆弱性成立，就直接把它作为事实陈述出来: 写"前五大客户占营收 75.98%，单一客户流失即可重创业绩"，而不是写"客户集中度一项命中"。语气要像研究员写给基金经理的段落，不是分析流程的日志。
- **分条列举时必须换行**: 每个要点独占一行，行首用 "1. " / "2. " 或 "· "，行与行之间用 \\n 分隔 (JSON 字符串里的换行必须写成 \\n 这个转义序列)。不要把多个要点挤成一行连续的文字，也不要整段不分行。

18. 明确说明: 如果接下来出现什么具体情况/数据，会证明这个判断是错的 (invalidation_condition)。这一步是强制的，不能写"无法确定"之类的话。
19. 列出其他需要注意的风险/不确定性。

无论证据是否充分、是否有疑点，都必须直接输出下面的 JSON，不要输出任何其他文字，不要提出反问或要求澄清:
{{
  "lynch_category": "fast_grower|stalwart|cyclical|turnaround|asset_play|slow_grower|unclear",
  "stance": "bullish|bearish|neutral",
  "thesis_summary": "关键论据摘要: 须覆盖大势/业务本质/财务质量/管理层行为/筹码与股价联动中的至少四类，引用至少四个不同方面的具体数字，并正面回应对结论不利的脆弱性事实，600字以内。分条书写，每点一行，行间用 \\n。写作时不要提及任何分析步骤、检查项或维度编号",
  "core_counter_evidence": "与上述结论相悖的最强证据，必须带具体数字；一条都没有时写「未发现」；不允许留空。分条书写，每点一行，行间用 \\n。直接陈述事实本身，不要写「六查」「检查项」这类流程用语。200字以内",
  "invalidation_condition": "什么情况出现会证明这个判断错了，150字以内",
  "risk_notes": "其他风险提示/不确定性，200字以内"
}}"""


@dataclass
class AnalysisInputs:
    """报告的全部输入维度。用 dataclass 收敛是为了避免拆成十几个位置参数——
    加维度时漏传一个参数在位置参数下会静默串位，不会报错。"""
    stock_code: str
    stock_name: str = ""
    quote: Dict = field(default_factory=dict)
    knowledge_excerpts: List[KnowledgeExcerpt] = field(default_factory=list)
    industry_comparison: Optional[Dict] = None
    shareholder_trend: Optional[Dict] = None
    dividend_history: List[Dict] = field(default_factory=list)
    buyback_history: List[Dict] = field(default_factory=list)
    profitability_trend: Optional[Dict] = None
    commodity_signal: Optional[Dict] = None
    rmb_signal: Optional[Dict] = None
    fundamentals: Optional[Dict] = None
    valuation: Optional[Dict] = None
    xueqiu_stock: Optional[Dict] = None


def _credibility_note(hit_rate: float, correct: int, incorrect: int) -> str:
    total = correct + incorrect
    if total < _MIN_CORROBORATING_RECORDS:
        return f"仅 {total} 条历史验证记录，参考价值有限"
    return f"基于 {total} 条历史预测验证，命中 {correct} 次，命中率 {hit_rate:.0%}"


def _historical_thesis(user_id: str, stock_code: str) -> List[str]:
    records = load_records(user_id)
    thesis = [r.get("thesis", "") for r in records if r.get("stock_code") == stock_code and r.get("thesis")]
    return thesis[-_MAX_HISTORICAL_THESIS:]


async def _latest_relevant_posts(session: AnalysisBrowserSession, user_id: str) -> List[str]:
    """取该用户最新几条原创帖正文，不要求提及目标股票 (行业/大盘看法也有参考价值)。"""
    from backtest.extract import strip_html

    posts = await session.get_latest_posts(user_id, page_size=20)
    texts: List[str] = []
    for post in posts:
        if post.get("status_type") != "original":
            continue
        text = strip_html(post.get("description") or "")
        if text:
            texts.append(text)
        if len(texts) >= _MAX_LATEST_POSTS:
            break
    return texts


async def _load_knowledge_excerpts() -> List[KnowledgeExcerpt]:
    """知识库不按股票筛选，全量加载 (原文已在知识库加载阶段由 LLM 提炼为投资观点摘要)。"""
    entries = await ensure_knowledge_base_loaded()
    return [
        KnowledgeExcerpt(source=e.source, title=e.title, distilled=e.distilled)
        for e in entries
        if e.distilled
    ]


_RELEVANCE_FILTER_PROMPT = """以下是知识库中若干条投资观点摘要的编号、标题和摘要开头片段。
请判断哪些条目与当前正在分析的股票"{stock_name}"({stock_code})可能相关——包括直接点名该股票、
点名其所属行业({industry_name})、或讨论了适用于该股票的通用宏观/周期/估值方法论观点。
不确定是否相关时倾向保留 (宁可多留通用方法论，不要只保留点名的)。
只有明显完全不相关的条目 (比如只讨论另一个具体行业/概念且没有通用方法论内容) 才排除。

条目列表:
{items_block}

请直接返回 JSON 数组，元素为应保留条目的编号 (整数)，不要输出任何其他文字。例如: [0, 2, 5]"""


async def _filter_relevant_knowledge(
    knowledge_excerpts: List[KnowledgeExcerpt],
    stock_code: str,
    stock_name: str,
    industry_name: Optional[str],
) -> List[KnowledgeExcerpt]:
    """
    知识库全量加载后条目数已较多 (每条摘要都是几百字)，不筛选直接全部塞给最终生成
    summary 的 LLM 调用会稀释真正相关的证据、还容易把输出挤到 max_tokens 上限导致截断。
    用一次单独的轻量 LLM 调用 (只看标题+摘要开头片段，不看全文) 先筛掉明显不相关的条目，
    调用失败时保留全部条目 (不因为筛选环节本身出错而丢失证据)。
    """
    if not knowledge_excerpts:
        return knowledge_excerpts

    items_block = "\n".join(
        f"[{i}] {e.title}: {e.distilled[:80]}"
        for i, e in enumerate(knowledge_excerpts)
    )
    prompt = _RELEVANCE_FILTER_PROMPT.format(
        stock_name=stock_name or stock_code,
        stock_code=stock_code,
        industry_name=industry_name or "未知",
        items_block=items_block,
    )
    parsed, _ = await call_json_ex(prompt, max_tokens=1024)
    if not isinstance(parsed, list):
        utils.logger.warning(f"[analysis.report] 知识库相关性筛选失败，回退为全量 ({stock_code})")
        return knowledge_excerpts

    kept_indices = {i for i in parsed if isinstance(i, int) and 0 <= i < len(knowledge_excerpts)}
    if not kept_indices:
        utils.logger.warning(f"[analysis.report] 知识库相关性筛选返回空结果，回退为全量 ({stock_code})")
        return knowledge_excerpts

    return [e for i, e in enumerate(knowledge_excerpts) if i in kept_indices]


def _build_industry_block(industry_comparison: Optional[Dict]) -> str:
    if not industry_comparison:
        return "暂缺 (行业归属数据本次未能取到)"
    name = industry_comparison.get("industry_name")
    sw_name = industry_comparison.get("sw_industry")
    head = f"所属行业: {name}" if name else f"所属申万行业: {sw_name or '未知'}"
    if "advancing" not in industry_comparison:
        # 只给行业名不给统计时必须说清"统计暂缺"，否则模型容易把"知道行业"当成
        # "知道行业整体在涨"的依据。
        return f"{head} (该行业涨跌家数与涨跌幅统计暂缺, 不得据此判断行业普涨/普跌)"
    return (
        f"{head}, "
        f"上涨家数 {industry_comparison.get('advancing')}, "
        f"下跌家数 {industry_comparison.get('declining')}, "
        f"行业涨跌幅 {industry_comparison.get('industry_change_pct')}%"
    )


def _build_shareholder_block(
    shareholder_trend: Optional[Dict],
    dividend_history: List[Dict],
    buyback_history: List[Dict],
) -> str:
    lines = []
    if shareholder_trend:
        lines.append(
            f"股东户数: 最新 {shareholder_trend.get('latest_count')} 户 "
            f"(截止 {shareholder_trend.get('as_of')})，环比变化 {shareholder_trend.get('change_pct')}%，"
            f"筹码趋于{'分散' if shareholder_trend.get('trend') == 'increasing' else '集中'}"
        )
    else:
        lines.append("股东户数变化: 暂缺")

    if dividend_history:
        lines.append("历史分红记录:")
        for d in dividend_history:
            lines.append(
                f"  · {d.get('announce_date')}: 每10股派息 {d.get('dividend_per_10_shares')} 元 ({d.get('progress')})"
            )
    else:
        lines.append("历史分红记录: 暂缺")

    if buyback_history:
        lines.append("历史回购记录:")
        for b in buyback_history:
            lines.append(
                f"  · {b.get('announce_date')}: 计划金额区间 {b.get('planned_amount_range')}，"
                f"已回购 {b.get('actual_amount')} ({b.get('progress')})"
            )
    else:
        lines.append("历史回购记录: 暂缺")

    return "\n".join(lines)


def _build_commodity_block(commodity_signal: Optional[Dict]) -> str:
    if not commodity_signal:
        return "暂缺 (非周期性矿业股，或行业归属数据未能取到，本维度不适用/暂缺)"
    return (
        f"沪铜最新价 {commodity_signal.get('sh_copper_price')} {commodity_signal.get('sh_copper_unit')}, "
        f"COMEX铜最新价 {commodity_signal.get('comex_copper_price')} {commodity_signal.get('comex_copper_unit')}, "
        f"人民币汇率趋势: {commodity_signal.get('rmb_trend') or '暂缺'}。"
        f"{commodity_signal.get('note', '')}"
    )


def _build_fx_block(rmb_signal: Optional[Dict], facts: Optional[Dict]) -> str:
    """
    汇率敞口 = 汇率方向 × 海外收入占比。两者缺一都无法判断顺风逆风：
    只知道人民币升值但不知道有没有海外收入，或只知道海外收入高但不知道汇率方向，
    都不足以给出方向性结论——所以缺任一项时必须写明"暂缺"，不要让模型自己补。
    """
    lines = []
    trend_note = (rmb_signal or {}).get("rmb_trend_note")
    lines.append(
        f"人民币汇率: {trend_note}" if trend_note
        else f"人民币汇率: {_MISSING_TAIL}"
    )
    overseas_pct = (facts or {}).get("overseas_revenue_pct")
    if overseas_pct is None:
        lines.append(f"海外收入占比: {_MISSING_TAIL}")
    else:
        lines.append(
            f"海外收入占比: {overseas_pct}% (汇率敞口的主要来源；占比越高，"
            f"汇率变动对收入/毛利的杠杆越大)"
        )
    if trend_note and overseas_pct is not None:
        lines.append(
            "注: 上述两项均已给出，必须据此给出汇率对该公司收入的影响方向 (顺风/逆风)，"
            "并说明大致传导路径 (折算收入 / 报价竞争力 / 汇兑损益)。"
        )
    return "\n".join(lines)


def _build_profitability_block(profitability_trend: Optional[Dict]) -> str:
    if not profitability_trend:
        return "暂缺 (本次未能取到财务指标数据)"
    lines = [
        profitability_trend.get("gross_margin_trend_note", ""),
        profitability_trend.get("net_margin_trend_note", ""),
        profitability_trend.get("roe_trend_note", ""),
        profitability_trend.get("debt_ratio_trend_note", ""),
    ]
    periods = profitability_trend.get("periods") or []
    if periods:
        lines.append("各报告期明细:")
        for p in periods:
            lines.append(
                f"  · {p.get('period')}: 毛利率/主营业务利润率 {p.get('gross_margin_pct')}%, "
                f"净利率 {p.get('net_margin_pct')}%, ROE {p.get('roe_pct')}%, "
                f"资产负债率 {p.get('debt_ratio_pct')}%, 净利润增长率 {p.get('net_profit_growth_pct')}%"
            )
    return "\n".join(l for l in lines if l)


_MISSING_TAIL = "暂缺 (本次未能取到，该维度不得做任何断言，包括反向断言)"


def _yi(yuan: Optional[float]) -> str:
    """元 -> 亿元。缺失一律写"暂缺"，绝不写 0 或 0.0。"""
    if yuan is None:
        return "暂缺"
    return f"{yuan / 1e8:.2f}亿"


def _pct_str(value: Optional[float]) -> str:
    return "暂缺" if value is None else f"{value}%"


def _build_valuation_block(valuation: Optional[Dict], quote: Optional[Dict]) -> str:
    if not valuation:
        return f"估值快照: {_MISSING_TAIL}"

    lines = [f"估值快照 (数据日期 {valuation.get('valuation_as_of') or '未知'}; 价格随行情变动，比率可能有小幅滞后):"]
    lines.append(
        f"  市盈率(动态) {valuation.get('pe_dynamic')}, 市盈率(静态) {valuation.get('pe_static')}, "
        f"市净率 {valuation.get('pb')}"
    )
    lines.append(
        f"  每股收益 {valuation.get('eps')} 元, 每股净资产 {valuation.get('nav_per_share')} 元, "
        f"每股经营现金流 {valuation.get('ocf_per_share')} 元"
    )
    lines.append(
        f"  净资产收益率 {_pct_str(valuation.get('roe_pct'))}, 毛利率 {_pct_str(valuation.get('gross_margin_pct'))}"
    )
    lines.append(f"  股权质押占A股总股本 {_pct_str(valuation.get('pledge_ratio_pct'))}")

    pe_dyn = valuation.get("pe_dynamic")
    pe_sta = valuation.get("pe_static")
    if pe_dyn is not None and pe_sta is not None and pe_dyn > 0 and pe_sta > pe_dyn:
        lines.append(
            f"  注: 静态市盈率({pe_sta}) 高于动态市盈率({pe_dyn})，说明当前估值已把未来利润增长"
            f"计入价格——增速一旦回落会同时杀业绩和杀估值。"
        )

    float_shares = valuation.get("float_shares")
    price = (quote or {}).get("latest_price")
    if float_shares and price:
        lines.append(
            f"  流通市值 约 {float_shares * float(price) / 1e8:.2f}亿 "
            f"(流通A股 {float_shares / 1e8:.2f}亿股 × 最新价 {price})"
        )
    else:
        lines.append("  流通市值: 暂缺")
    return "\n".join(lines)


def _build_fundamentals_block(fundamentals: Optional[Dict]) -> str:
    if not fundamentals:
        return f"同花顺 F10 结构性事实: {_MISSING_TAIL}"

    facts = fundamentals.get("facts") or {}
    lines = ["同花顺 F10 结构性事实 (来自公司定期报告原文，比率已由程序算好，直接引用即可):"]
    period_line = f"  财务数据期间 {facts.get('finance_period') or '未知'}"
    if fundamentals.get("concentration_period"):
        period_line += (
            f"；客户/供应商集中度期间 {fundamentals['concentration_period']}"
            "(集中度是年报强制披露项，可能滞后最多约 9 个月，引用时要点明期间)"
        )
    lines.append(period_line)

    revenue_yoy = facts.get("revenue_yoy_pct")
    lines.append(
        f"  营业收入 {_yi(facts.get('revenue'))}"
        + (f" (同比 {revenue_yoy:+}%)" if revenue_yoy is not None else "")
    )
    net_profit_yoy = facts.get("net_profit_yoy_pct")
    basis = facts.get("net_profit_basis")
    lines.append(
        f"  净利润 {_yi(facts.get('net_profit'))}"
        + (f" (口径: {basis})" if basis else "")
        + (f" (同比 {net_profit_yoy:+}%)" if net_profit_yoy is not None else "")
    )
    ocf_yoy = facts.get("operating_cash_flow_yoy_pct")
    lines.append(
        f"  经营活动现金流净额 {_yi(facts.get('operating_cash_flow'))}"
        + (f" (同比 {ocf_yoy:+}%)" if ocf_yoy is not None else "")
    )
    lines.append(
        f"  经营现金流/净利润 {facts.get('cash_to_profit_ratio') if facts.get('cash_to_profit_ratio') is not None else '暂缺'}"
        " (显著小于 1 说明账面利润没有同步变成现金)"
    )
    lines.append(
        f"  研发投入 {_yi(facts.get('rd_investment_yuan'))}, 研发强度(研发投入/营业收入) "
        f"{_pct_str(facts.get('rd_intensity_pct'))}"
    )
    lines.append(
        f"  前五大客户占营业收入 {_pct_str(facts.get('top5_customer_pct'))}, "
        f"前五大供应商占总采购额 {_pct_str(facts.get('top5_supplier_pct'))}"
    )
    lines.append(
        f"  海外业务收入 {_yi(facts.get('overseas_revenue_yuan'))}, "
        f"占营业收入 {_pct_str(facts.get('overseas_revenue_pct'))}"
    )
    lines.append(f"  境外销量占比 {_pct_str(facts.get('overseas_sales_pct'))}")
    lines.append(
        f"  授权专利 {facts.get('patents_granted') if facts.get('patents_granted') is not None else '暂缺'} 件, "
        f"其中发明专利 {facts.get('patents_invention') if facts.get('patents_invention') is not None else '暂缺'} 件, "
        f"发明专利占比 {_pct_str(facts.get('invention_ratio_pct'))}"
    )
    lines.append(
        f"  股东户数 {facts.get('holder_count_latest') if facts.get('holder_count_latest') is not None else '暂缺'} 户"
        f" (截止 {facts.get('holder_count_period') or '未知'}), "
        f"环比 {_pct_str(facts.get('holder_count_qoq_pct'))}, 同比 {_pct_str(facts.get('holder_count_yoy_pct'))}"
    )

    # 户数序列带同期股价，是"股价暴涨是否伴随筹码派发"唯一可核验的对照数据，
    # 不渲染出来模型就只能靠猜。
    series = facts.get("holder_count_series") or []
    if len(series) >= 2:
        lines.append("  股东户数 vs 同期股价序列 (用于判断股价涨幅与筹码分散是否同步):")
        for point in series[:8]:
            lines.append(
                f"    · {point.get('period')}: 股东户数 {point.get('holders')} 户, 股价 {point.get('price')}"
            )

    customers = fundamentals.get("top_customers") or []
    if customers:
        lines.append("  主要客户明细:")
        for c in customers[:5]:
            lines.append(f"    · {c.get('name')}: 销售额 {_yi(c.get('amount_yuan'))}, 占比 {_pct_str(c.get('pct'))}")
    suppliers = fundamentals.get("top_suppliers") or []
    if suppliers:
        lines.append("  主要供应商明细:")
        for s in suppliers[:5]:
            lines.append(f"    · {s.get('name')}: 采购额 {_yi(s.get('amount_yuan'))}, 占比 {_pct_str(s.get('pct'))}")

    risks = (fundamentals.get("self_disclosed_risks") or "").strip()
    lines.append("")
    lines.append("公司自述的风险 (原文摘录: 公司在自己的定期报告里承认的风险，引用时请注明来自公司披露):")
    lines.append(risks if risks else "暂缺 (该公司本期报告的董事会经营评述中没有独立的风险小节)")

    narrative = (fundamentals.get("management_narrative") or "").strip()
    lines.append("")
    lines.append(
        "公司自己的经营表述 (公司管理层视角，属于利益相关方自述，不是客观事实；"
        "只能用于了解公司自己想强调什么，不得单独作为看多理由):"
    )
    lines.append(narrative if narrative else "暂缺")

    if fundamentals.get("missing"):
        lines.append("")
        lines.append(f"本块缺失维度: {', '.join(fundamentals['missing'])} (这些维度不得做任何断言)")

    return "\n".join(lines)


def _build_xueqiu_block(xueqiu_stock: Optional[Dict]) -> str:
    if not xueqiu_stock:
        return f"雪球个股维度 (机构持仓/讨论热度): {_MISSING_TAIL}"

    lines = ["雪球个股维度 (仅聚合数字，不含用户观点原文):"]
    holding = xueqiu_stock.get("org_holding")
    if holding:
        lines.append("  机构/主要股东持仓 (雪球口径):")
        for h in holding[:10]:
            numbers = ", ".join(f"{k}={v}" for k, v in h.items() if k != "name")
            lines.append(f"    · {h.get('name')}: {numbers}")
    else:
        lines.append("  机构持仓: 暂缺")

    quote_detail = xueqiu_stock.get("quote_detail") or {}
    if quote_detail:
        lines.append("  雪球行情扩展字段: " + ", ".join(f"{k}={v}" for k, v in quote_detail.items()))

    discussion = xueqiu_stock.get("discussion") or {}
    if discussion.get("post_count") is not None:
        lines.append(
            f"  讨论热度: 相关帖子约 {discussion['post_count']} 条 "
            f"(仅作情绪/关注度参考，不构成证据；热度高本身既可能意味着分歧也可能意味着拥挤)"
        )
    else:
        lines.append("  讨论热度: 暂缺")

    return "\n".join(lines)


def _build_candidates_block(candidates: List[CandidateOpinion]) -> str:
    lines = []
    for c in candidates:
        lines.append(f"- {c.user_nickname} ({c.credibility_note})")
        if c.historical_thesis:
            lines.append("  历史观点摘录:")
            for t in c.historical_thesis:
                lines.append(f"    · {t[:200]}")
        if c.latest_posts:
            lines.append("  最新发言摘录:")
            for t in c.latest_posts:
                lines.append(f"    · {t[:200]}")
        else:
            lines.append("  (未能获取到最新发言)")
    if not lines:
        lines.append("(暂无)")
    return "\n".join(lines)


def _build_knowledge_block(knowledge_excerpts: List[KnowledgeExcerpt]) -> str:
    lines = []
    for k in knowledge_excerpts:
        lines.append(f"- 《{k.title}》 ({k.source})")
        lines.append(f"    {k.distilled}")
    if not lines:
        lines.append("(暂无背景资料)")
    return "\n".join(lines)


def _build_prompt(inputs: AnalysisInputs, candidates: List[CandidateOpinion]) -> str:
    quote = inputs.quote
    if quote:
        quote_line = (
            f"最新价 {quote.get('latest_price')}, "
            f"涨跌幅 {quote.get('change_pct')}%, 成交量 {quote.get('volume')}"
        )
    else:
        quote_line = "暂无实时数据"

    return _PROMPT_TEMPLATE.format(
        stock_code=inputs.stock_code,
        stock_name=inputs.stock_name or inputs.stock_code,
        quote_line=quote_line,
        valuation_block=_build_valuation_block(inputs.valuation, quote),
        fundamentals_block=_build_fundamentals_block(inputs.fundamentals),
        fx_block=_build_fx_block(inputs.rmb_signal, (inputs.fundamentals or {}).get("facts")),
        xueqiu_block=_build_xueqiu_block(inputs.xueqiu_stock),
        candidates_block=_build_candidates_block(candidates),
        knowledge_block=_build_knowledge_block(inputs.knowledge_excerpts),
        industry_block=_build_industry_block(inputs.industry_comparison),
        shareholder_block=_build_shareholder_block(
            inputs.shareholder_trend, inputs.dividend_history, inputs.buyback_history
        ),
        profitability_block=_build_profitability_block(inputs.profitability_trend),
        commodity_block=_build_commodity_block(inputs.commodity_signal),
    )


async def _generate_summary(inputs: AnalysisInputs, candidates: List[CandidateOpinion]) -> StructuredSummary:
    prompt = _build_prompt(inputs, candidates)
    parsed, stop_reason = await call_json_ex(prompt, max_tokens=_SUMMARY_MAX_TOKENS)
    if not parsed or not isinstance(parsed, dict) or parsed.get("stance") not in _VALID_STANCES:
        utils.logger.error(
            f"[analysis.report] {inputs.stock_code} 摘要生成失败 "
            f"(stop_reason={stop_reason}, parsed={'dict' if isinstance(parsed, dict) else type(parsed).__name__})"
        )
        return StructuredSummary(
            lynch_category="",
            stance="",
            thesis_summary="",
            core_counter_evidence="",
            invalidation_condition="",
            risk_notes="LLM 生成失败，请参考以上原始数据自行判断。",
        )
    lynch_category = parsed.get("lynch_category", "")
    if lynch_category not in _VALID_LYNCH_CATEGORIES:
        lynch_category = "unclear"
    return StructuredSummary(
        lynch_category=lynch_category,
        stance=parsed.get("stance", ""),
        thesis_summary=parsed.get("thesis_summary", "") or "",
        core_counter_evidence=parsed.get("core_counter_evidence", "") or "",
        invalidation_condition=parsed.get("invalidation_condition", "") or "",
        risk_notes=parsed.get("risk_notes", "") or "",
    )


def _resolve_stock_name(stock_code: str, candidate_scores) -> str:
    for user in candidate_scores:
        for stock in user.by_stock:
            if stock.stock_code == stock_code and stock.stock_name:
                return stock.stock_name
    return ""


async def generate_report(stock_code: str) -> AnalysisReport:
    candidate_scores = find_candidates(stock_code)

    stock_name = _resolve_stock_name(stock_code, candidate_scores)
    quote = await get_realtime_quote(stock_code)
    if not stock_name:
        stock_name = await get_stock_name(stock_code) or ""

    (
        knowledge_excerpts,
        shareholder_trend,
        dividend_history,
        buyback_history,
        profitability_trend,
        fundamentals,
    ) = await asyncio.gather(
        _load_knowledge_excerpts(),
        get_shareholder_count_trend(stock_code),
        get_dividend_history(stock_code),
        get_buyback_history(stock_code),
        get_profitability_trend(stock_code),
        get_ths_fundamentals(stock_code),
    )

    # 行业反查的起点是 F10 公司概要页里的申万行业名，所以必须等 fundamentals 回来。
    # 这一步不再有按行业板块的逐次网络请求，只是本地匹配，放在 gather 之后不拖慢。
    industry_comparison = await get_industry_comparison(
        stock_code, (fundamentals or {}).get("facts", {}).get("sw_industry")
    )

    # 铜价信号按行业关键词判断，申万行业名同样可用——匹配不到同花顺板块时
    # industry_name 为空，但不能因此漏掉"有色金属"这类能直接识别的申万名。
    industry_name = None
    if industry_comparison:
        industry_name = industry_comparison.get("industry_name") or industry_comparison.get("sw_industry")
    commodity_signal, rmb_signal = await asyncio.gather(
        get_copper_spread_signal(stock_code, industry_name),
        get_rmb_trend_signal(),
    )
    knowledge_excerpts = await _filter_relevant_knowledge(
        knowledge_excerpts, stock_code, stock_name, industry_name
    )

    # 估值与同花顺 F10 同页解析，拆成独立维度是因为报告前端与 prompt 都要单列。
    # 必须拷一份再 pop: get_ths_fundamentals 返回的是带 6 小时缓存的同一个 dict，
    # 直接 pop 会把缓存里的估值维度永久删掉。
    valuation = None
    if fundamentals:
        fundamentals = dict(fundamentals)
        valuation = fundamentals.pop("valuation", None)

    inputs = AnalysisInputs(
        stock_code=stock_code,
        stock_name=stock_name,
        quote=quote or {},
        knowledge_excerpts=knowledge_excerpts,
        industry_comparison=industry_comparison,
        shareholder_trend=shareholder_trend,
        dividend_history=dividend_history,
        buyback_history=buyback_history,
        profitability_trend=profitability_trend,
        commodity_signal=commodity_signal,
        rmb_signal=rmb_signal,
        fundamentals=fundamentals,
        valuation=valuation,
    )

    # 雪球个股数据必须借道已登录的浏览器会话，且只在会话存活期内可用；
    # 取完数据后再抓候选用户发帖，最后统一走同一个收尾。
    session = AnalysisBrowserSession()
    started = await session.start()
    if not started:
        utils.logger.warning(f"[analysis.report] 浏览器会话启动失败，跳过雪球维度与最新发言抓取 (stock_code={stock_code})")

    candidates: List[CandidateOpinion] = []
    try:
        if started:
            inputs.xueqiu_stock = await get_xueqiu_stock_data(session, stock_code)
        for user in candidate_scores:
            stock_score = next(s for s in user.by_stock if s.stock_code == stock_code)
            latest_posts = await _latest_relevant_posts(session, user.user_id) if started else []
            candidates.append(
                CandidateOpinion(
                    user_id=user.user_id,
                    user_nickname=user.user_nickname,
                    wilson_score=stock_score.wilson_score,
                    hit_rate=stock_score.hit_rate,
                    correct=stock_score.correct,
                    incorrect=stock_score.incorrect,
                    credibility_note=_credibility_note(stock_score.hit_rate, stock_score.correct, stock_score.incorrect),
                    historical_thesis=_historical_thesis(user.user_id, stock_code),
                    latest_posts=latest_posts,
                )
            )
    finally:
        if started:
            await session.close()

    summary = await _generate_summary(inputs, candidates)

    return AnalysisReport(
        stock_code=stock_code,
        stock_name=stock_name,
        realtime_quote=quote,
        candidates=candidates,
        knowledge_excerpts=knowledge_excerpts,
        industry_comparison=industry_comparison,
        shareholder_trend=shareholder_trend,
        dividend_history=dividend_history,
        buyback_history=buyback_history,
        profitability_trend=profitability_trend,
        commodity_signal=commodity_signal,
        rmb_signal=rmb_signal,
        fundamentals=inputs.fundamentals,
        valuation=inputs.valuation,
        xueqiu_stock=inputs.xueqiu_stock,
        summary=summary,
        prompt_version=_PROMPT_VERSION,
        generated_at=int(time.time()),
    )
