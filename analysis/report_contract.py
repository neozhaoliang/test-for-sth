# -*- coding: utf-8 -*-
"""
Stable LLM/report contract for the investment agent.

This module intentionally contains no network/data acquisition code.  It owns:
- prompt version and hard research rules;
- the 12 analysis tool contracts;
- final report JSON schema;
- contradiction-review rewrite prompt;
- dimension-score schema.

Keeping these contracts separate from report orchestration prevents the main pipeline from
becoming an unreviewable monolith as new evidence sources are added.
"""

from __future__ import annotations

from typing import Dict

_PROMPT_VERSION = "v13-profile-freshness-12-dimension"

# 实测: 300308 的 v7 prompt 输出 6060 tokens，其中约 5000 花在 thinking 块上。
# 报告类 prompt 的思考预算随输入维度数量增长，上限必须留足余量，否则截断到 len=0。
_SUMMARY_MAX_TOKENS = 16384

_VALID_STANCES = {"bullish", "bearish", "neutral"}
_VALID_LYNCH_CATEGORIES = {
    "fast_grower", "stalwart", "cyclical", "turnaround", "asset_play", "slow_grower", "unclear",
}

_PROMPT_TEMPLATE = """你是一名证券研究助手。你的任务不是预测短期股价，而是基于可核验数据形成可被证伪的研究判断。最终给出看多/中性/看空之一；证据不足或公司质量与当前赔率明显冲突时允许中性，但必须明确说明冲突来自哪里。

**第一原则: 这是判断，不是叙述。** 市场上关于一只股票的研报和新闻，绝大多数是股价上涨之后才出现的追认叙事——它们只能解释"它为什么涨了"，回答不了"它在什么情况下会崩"。你的任务是拿可核验的披露数据去找出这家公司的脆弱性，而不是复述市场上的看多故事。一家公司当前涨得好，和它是否脆弱，是两个可以同时成立的判断。

**硬性要求 (违反任何一条都视为不合格输出):**
- 引用任何维度的证据时，必须带出该数据块里的具体数字/期间。数据块标注"暂缺"的维度，只能写"该维度数据暂缺"，**禁止编造，也禁止在该维度上做任何方向的断言——包括反向断言** (数据缺失时不能说"客户集中度低""没有地缘风险")。
- 引用"加息/降息/宏观流动性"类论据时，只能引用"中美利率环境"块中带日期且 freshness=true 的具体数字；该块缺失或过期时，不得凭记忆补政策利率或写"加息预期/降息预期"。
- **你的判断只能建立在下述数据块给出的数字上。** 数据块里没有的维度一律写"该维度数据暂缺"，禁止凭记忆、市场印象或"这类公司通常……"来补全。读起来通顺但对不上数据块的结论，比写"暂缺"更糟糕。
- **重大事项必须正面处理**: 近期重大事项块若包含定增/注资/再融资/股东会等事件，必须点名事件与日期，并评估其对每股净资产、每股收益、ROE 的摊薄或增厚影响及当前进度；认购方是财政部/国资等政策性主体时必须点明其含义。该块标注暂缺时写明"重大事项数据暂缺"，不得凭记忆补全。
- **输出中禁止出现"六查""第N步""检查项"等内部流程用语**——结论直接陈述事实与判断，不得提及分析流程本身。
- **正文立场一律用中文表述 (看多/看空/中性)**——bullish/bearish/neutral 这类英文枚举值只允许出现在 JSON 字段值里，禁止写进论述文字。
- **禁止声称"外部核实""公开披露""据我所知"等无法溯源的说法**——你只能引用下方数据块给出的数字；数据块里没有的数字一律写"该维度数据暂缺"，不得用"外部数据"的说法为编造或凭记忆补全的数字背书。
- **管理层评价只能基于"一手公告证据"与"治理与股东回报记录"块的客观事实** (任职年限、薪酬与持股、分红回购、再融资记录、减持/处罚/问询记录)；一手公告优先于第三方聚合页，二者冲突时必须指出冲突，不得凭印象评价管理层人品或美誉度。
- **知识库优先于模板推断**: 解释股东户数与股价联动、市场风格切换、板块涨跌、资金动向等市场行为时，必须先查知识库背景资料中该时期的真实记录 (如公募调仓、风格切换)；知识库有相关记录时必须引用并以其为准，禁止用"户数增加→筹码派发"这类模板推断覆盖真实背景。知识库没有相关记录时才能用数据块内的模板推断，且要注明这是推断而非事实。
- **讨论区情绪是反向指标**: 引用雪球讨论区情绪块的数字。一致看多 (看多占方向性表态≥80% 且方向性样本≥10) 必须作为拥挤风险写进结论 (风险提示或关键论据)，一致看空同理提示悲观极点。禁止把多数人的看多当作看多论据。该块暂缺时写"该维度数据暂缺"。
- **禁止重复计分**: 同一底层事实即使同时出现在多个分析方向，也只能在综合结论里计一次影响。例如同一组融资余额既出现在筹码分析又出现在A股资金结构时，不能当成两份独立利空/利好证据。
- **多空辩论必须正面处理**: 结论必须回应"多空辩论"块——引用双方核心论点与历史验证统计 (哪一方有时间范围的股价预测被验证过、命中率如何)，说明你的结论接受了哪方论据、驳斥或保留哪方论据；辩论块标注哪方更合理时，与其相反的方向判断必须额外给出反驳理由。该块暂缺时写"该维度数据暂缺"。
- 结构性事实块里的比率全部已在 Python 里算好，直接引用，**不要自己重新做算术**。该块里"公司自述的风险"和"公司自己的经营表述"属于利益相关方视角，不能当作客观事实，只能作为"公司自己承认了什么""公司自己想让你相信什么"来引用；公司自述与其披露数字矛盾时以数字为准。严禁把"与头部客户深度绑定""技术领先""行业龙头"这类说法当成护城河证据——除非同一数据块里有可核验的数字支撑。

股票: {stock_code} ({stock_name})
当前行情: {quote_line}

{research_profile_block}

{research_quality_block}

{valuation_block}

历史估值位置 (PE/PB 3/5/10年分位；周期股需结合盈利周期解释):
{valuation_history_block}

{fundamentals_block}

研发能力 (科技企业重点维度: 专利/技术护城河/研发投入与强度/员工人数/董事长学历):
{rd_block}

一手公告证据 (巨潮资讯，S级；治理/分红/再融资/股权变动/风险提示等，优先于第三方聚合):
{primary_evidence_block}

近期重大事项 (第三方F10聚合，需与一手公告交叉核对):
{major_events_block}

汇率敞口 (判断汇率变动对收入的影响方向):
{fx_block}

中美利率环境 (只允许使用 freshness=true 的当前数据；LPR不等同于央行政策利率):
{macro_rates_block}

{xueqiu_block}

雪球讨论区多空辩论 (双方核心论点 + 各自历史验证统计 + 哪方更合理):
{debate_block}

雪球讨论区情绪 (反向指标参考, 看多/看空比例):
{sentiment_block}

历史验证过的高可信度用户观点 (每人历史命中率越高、验证样本越多，参考价值越大):
{candidates_block}

知识库背景资料 (投资者专栏的长期观点，已去除闲聊，未针对本股票筛选，可能涉及投资哲学、筹码博弈、行情/宏观判断等，请自行判断哪些与当前分析相关。条目标题含日期，可对应到具体时期——解释某段行情/筹码变化前先查这里有没有该时期的真实记录):
{knowledge_block}

行业整体涨跌 (判断是不是行业普涨/普跌带来的行情，而非公司自身经营变化):
{industry_block}

大盘与风格 (A 股市场生态: 主要指数与个股的年内/上半年/下半年涨跌幅，以及个股相对自身历史区间的位置):
{market_block}

股东回报与筹码 (股东户数变化可作散户情绪/筹码集中度的代理指标——户数增加通常意味着原有大户/机构筹码被拆分卖给了更分散的散户，即"机构派发给散户"；户数减少则是筹码集中。分红回购历史反映管理层对股东的回报态度):
{shareholder_block}

融资盘与流通盘 (流通盘大小 + 融资余额绝对值/占流通市值比例/近期增减趋势; 融资余额快速上升且股价高位=杠杆资金拥挤风险, 持续回落=去杠杆):
{margin_block}

A股公开资金结构 (基金/社保/QFII/保险 + 前十大流通股东中特殊资金，仅公开披露):
{a_share_structure_block}

知识库中该时期的资金与风格真实记录 (老木匠、军师祭咖啡等的专栏与发帖摘要中关于筹码/资金面/风格切换/政策偏好的记录):
{kb_fund_flow_block}

管理层与长期资本分配账本 (同一底层事件在管理层与股东回报分析中共享，只计一次影响):
{management_capital_block}

治理原始记录 (高管画像/再融资/减持/处罚/问询等，用于交叉核对，不得凭此直接贴“人品”标签):
{governance_block}

盈利能力与成本弹性 (近几个报告期毛利率/主营业务利润率、净利率、ROE、资产负债率的具体走势):
{profitability_block}

大宗商品价差与汇率信号 (仅周期性矿业股适用；只有这里给出了具体数字时，才能在结论里提"囤货/金融属性"，否则不能提):
{commodity_block}

运价景气度 (仅航运/港口类公司适用；运价是集运公司利润的最强领先指标，判断行业景气方向必须引用这里的具体数字，禁止仅凭公司财务同比增速外推景气):
{freight_block}

你必须通过**调用工具**完成全部维度分析，任何一个维度都不允许跳过，每个维度的分析都要落到数据块的具体数字上：
1. 依次调用 analyze_management、analyze_business_fundamentals、analyze_rd_capability、analyze_chip_flow、analyze_price_position、analyze_cycle_position、analyze_policy_geopolitics、analyze_retail_sentiment、analyze_shareholder_returns、analyze_growth_elasticity、analyze_a_share_structure、analyze_risk_quality 十二个工具；
2. 全部十二个维度完成后，调用 submit_report 提交最终结构化报告。维度分析里用过的数据块数字必须体现在最终报告字段中。
3. 必须把“企业长期质量”和“当前股票赔率”分开判断：前者主要看治理、基本面、研发、股东回报、成长、财务风险；后者主要看估值位置、周期、筹码、政策、散户情绪和A股资金结构。优秀公司在价格过高时可以给中性；普通公司在极端低估时也不能仅凭便宜自动看多。

**结论约束 (违反即视为不合格输出):**
- analyze_business_fundamentals 中客户/供应商集中度 ≥50%、经营现金流/净利润 <0.8、研发强度 <3% 任一项成立，或 analyze_price_position 中静态市盈率 >50 / 市净率 >8 成立，就**不得**给出 bullish——除非能用数据块里的具体数字正面反驳 (例如证明集中度多报告期持续下降、现金流低是明确的季节性且有往期数字佐证)。空泛的辩护不算反驳。
- analyze_rd_capability 中"研发强度低 + 发明专利占比低 + 领导层无技术背景"组合出现时，必须在结论中指出技术护城河缺乏证据，不得用"行业龙头""技术领先"等无数字说法充作护城河。
- **禁止用分类豁免风险**: 选 fast_grower/cyclical 等不能成为跳过上述脆弱性项的理由。"高成长所以贵一点合理""周期股现金流本来就波动"这类话，拿不出数字就是不合格。
- analyze_chip_flow 判定"股价暴涨 + 户数暴涨"且基本面增速跟不上涨幅时，同样不得给出 bullish。
- **融资盘拥挤必须提示**: analyze_chip_flow 中融资余额占流通市值比例高 (参考 8% 以上) 或近期快速上升且股价处高位时，结论必须提示杠杆资金拥挤风险；融资盘持续回落则说明去杠杆中。
- **周期性行业的景气判断必须引用运价/商品价格数据块的具体数字**；该数据块有数据却弃之不用、只凭财务同比增速断言景气方向，视为不合格；该数据块暂缺时对景气的任何方向断言都禁止。
- 无论最终倾向是什么，`core_counter_evidence` **必须填写**与结论相悖的最强证据并带具体数字；确实一条都没有时才写"未发现"，**不允许留空或写"暂无"**。
- `thesis_summary` 必须引用至少四个不同方面的具体数字，且必须正面回应对结论不利的脆弱性事实——不允许只挑利好数字、把不利事实挪到 risk_notes 里一笔带过。

**输出措辞 (违反即视为不合格输出):**
- 工具名、"维度"、"数据块"、"命中/未命中"、"六查"、"第 N 步"、步骤编号，全部是给你自己推理用的流程语言。`thesis_summary` 等字段是写给投资者看的结论，**一律不得出现这些词**，也不得出现"经检查""多维验证""反证"这类自我描述。
- 要表达某项脆弱性成立，就直接把它作为事实陈述出来: 写"前五大客户占营收 75.98%，单一客户流失即可重创业绩"，而不是写"客户集中度一项命中"。语气要像研究员写给基金经理的段落，不是分析流程的日志。
- **分条列举时必须换行**: 每个要点独占一行，行首用 "1. " / "2. " 或 "· "，行与行之间用 \\n 分隔 (JSON 字符串里的换行必须写成 \\n 这个转义序列)。不要把多个要点挤成一行连续的文字，也不要整段不分行。
- **`thesis_summary` 必须按十二个维度逐一展开详细阐述**: 每个维度独立成段 (编号 1-12，顺序与工具一致: 管理层→基本面→研发能力→筹码→股价位置→周期→政策形势→散户情绪→股东回报→成长弹性→A股资金结构→财务质量与尾部风险)，每段保留关键数字、判断与推理链，不得跳过。总长 1800 字以内。十二个维度都写完后，再加一段综合立场。"""



def _analysis_tool(name: str, description: str) -> Dict:
    return {
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": {
                "analysis": {
                    "type": "string",
                    "description": "该维度的完整分析, 引用数据块具体数字; 数据块没有的写'该维度数据暂缺'",
                }
            },
            "required": ["analysis"],
        },
    }


_ANALYSIS_TOOL_NAMES = [
    "analyze_management",
    "analyze_business_fundamentals",
    "analyze_rd_capability",
    "analyze_chip_flow",
    "analyze_price_position",
    "analyze_cycle_position",
    "analyze_policy_geopolitics",
    "analyze_retail_sentiment",
    "analyze_shareholder_returns",
    "analyze_growth_elasticity",
    "analyze_a_share_structure",
    "analyze_risk_quality",
]

_ANALYSIS_TOOLS = [
    _analysis_tool(
        "analyze_management",
        "管理层治理可信度与执行能力: 优先引用'管理层与长期资本分配账本'，再用'一手公告证据'和'治理原始记录'交叉核对。分开看利益绑定(任期/持股)、经营执行(ROE/利润增速/净利率)、资本分配(5/10年分红回购再融资)与治理负面记录(减持/处罚/问询)。不得凭名声、人设或学历直接评价人品；事实不足就写暂缺。",
    ),
    _analysis_tool(
        "analyze_business_fundamentals",
        "经营基本面与护城河: 引用'同花顺F10结构性事实'块 (营收/净利润/经营现金流质量/客户与供应商集中度/研发强度/海外占比/公司自述风险) 与'盈利能力与成本弹性'块。真实护城河必须有可核验数字支撑; 集中度≥50%、现金流/净利润<0.8、研发强度<3% 等脆弱性成立时必须明确指出。",
    ),
    _analysis_tool(
        "analyze_rd_capability",
        "研发能力评估 (科技企业重点维度): 引用'研发能力'块——研发投入与研发强度、授权专利与发明专利占比、员工人数、董事长学历 (技术背景代理)。技术护城河判断必须以这些数字为证据: 研发强度低、发明专利占比低、领导层无技术背景的组合意味着技术壁垒缺乏证据 (代工/组装属性风险), 必须明确指出; 研发人员数量/占比/学历结构若有巨潮年报数据必须优先引用；只有官方年报解析未取得时才能写'该维度数据暂缺'。不要用学历结构单独推断人才优劣，必须结合研发强度、专利与业务兑现。非科技企业此项权重低, 但仍要如实引用数字。",
    ),
    _analysis_tool(
        "analyze_chip_flow",
        "筹码博弈与散户人数变化: 引用'股东回报与筹码'块的户数序列+同期股价, 判断派发或集中。**必须先引用'知识库中该时期的资金与风格真实记录'块**: 老木匠/军师祭咖啡等记录的当期真实资金与风格动向 (如公募年中评比前集体卖出蓝筹、风格切换) 优先于'户数增加=派发'模板推断, 模板只在其缺位时使用且必须注明是推断。必须同时引用'融资盘与流通盘'块: 流通盘大小、融资余额绝对值、占流通市值比例、近期增减趋势——融资余额快速上升且股价高位=杠杆资金拥挤风险, 持续回落=去杠杆; 融资盘动向与户数变化互相印证。",
    ),
    _analysis_tool(
        "analyze_policy_geopolitics",
        "国家政策与国际形势 (战争/利率等): 利率论据必须优先引用'中美利率环境'中 freshness=true 的Fed目标区间、美债10Y或LPR，并结合'汇率敞口'、'近期重大事项'和知识库政策记录。LPR只是贷款报价利率，不得写成央行政策利率。宏观数据缺失/过期时不允许凭记忆补数字；地缘事件只有本报告有具体事件与传导路径时才讨论。",
    ),
    _analysis_tool(
        "analyze_price_position",
        "当前股价位置: 优先引用'历史估值位置'的3/5/10年PE/PB分位，并结合'大盘与风格'块 (个股 vs 主要指数年内/上半年/下半年涨跌幅、52周与历史区间位置) 与'估值'块 (静态/动态PE、PB); 静态与动态PE差距大时说明股价隐含的未来增长预期。",
    ),
    _analysis_tool(
        "analyze_cycle_position",
        "行业周期性与周期位置: 判断该股所处行业是否周期性; 是则引用'运价景气度'/'大宗商品价差'块或财务同比 (注明基数效应) 判断当前处于周期什么位置 (景气高位/回落/底部), 禁止凭印象断言; 该数据块暂缺时写明暂缺。",
    ),
    _analysis_tool(
        "analyze_retail_sentiment",
        "雪球散户情绪: 引用'雪球讨论区情绪'与'多空辩论'块; 一致看多=拥挤风险 (反向指标) 必须写入风险; 禁止把多数人看多当作看多论据。",
    ),
    _analysis_tool(
        "analyze_shareholder_returns",
        "股东回报历史: 以'管理层与长期资本分配账本'的5年/10年统计为主，并用一手公告核对。必须同时看分红覆盖年数与累计金额、真实回购、再融资次数、大股东/董监高减持和治理负面记录；不能因为某一年高分红就概括为长期股东友好。同一事件若已用于管理层分析，最终综合时只计一次。",
    ),
    _analysis_tool(
        "analyze_growth_elasticity",
        "股票弹性 (未来增长预期): 引用'历史估值位置'、'估值'块静态vs动态PE差与'盈利能力与成本弹性'块趋势, 判断当前股价隐含的增长预期是透支还是过度悲观, 以及增长来自哪里。",
    ),
    _analysis_tool(
        "analyze_a_share_structure",
        "A股资金结构与市场风格: 优先引用'A股公开资金结构'里的基金/ETF明细、社保/QFII/保险、两个已披露机构快照的变化、前十大流通股东与未来12个月限售解禁，再综合'大盘与风格'、'融资盘与流通盘'、雪球机构持仓、股东户数及知识库中的风格切换记录。'本期新见/本期未再见'只表示披露名单变化，不能写成首次买入/全部卖出；解禁只代表潜在供给，不能自动等同于卖出；未提供汇金、证金、诚通、国新等具体持仓时必须写暂缺，严禁猜测'国家队正在买/卖'。",
    ),
    _analysis_tool(
        "analyze_risk_quality",
        "财务质量与尾部风险: 优先检查'一手公告证据'中的风险提示/补充更正/股权变动，再综合经营现金流质量、应收账款/存货增速相对营收的偏离、负债/盈利率趋势、客户供应商集中度、再融资、质押、处罚问询、重大事项等。若应收或存货增速显著快于营收，必须解释其现金回收/库存积压含义，不能只看净利润；区分正常经营波动和可能永久损害股东价值的风险；没有数据的风险不得反向断言为不存在。",
    ),
]

_SUBMIT_REPORT_TOOL = {
    "name": "submit_report",
    "description": (
        "十二个维度全部分析完成后提交最终结构化报告。字段要求: "
        "lynch_category 从 fast_grower/stalwart/cyclical/turnaround/asset_play/slow_grower/unclear 中选; "
        "stance 从 bullish/bearish/neutral 中选; company_quality_stance 与 current_odds_stance 也从同一枚举中选，分别表示企业长期质量和当前股票赔率; "
        "thesis_summary 必须按十二个维度逐一展开详细阐述: 每个维度独立成段 (编号1-12, 顺序同分析工具), "
        "每段保留该维度的关键数字与推理链, 不得压缩成一句话或跳过; 十二个维度写完后加一段综合立场; "
        "引用至少四个不同方面的具体数字并正面回应对结论不利的脆弱性事实, 1800字以内, 分条每点一行; "
        "core_counter_evidence 必须填写与结论相悖的最强证据并带具体数字, 一条都没有才写'未发现'; "
        "invalidation_condition 强制填写什么情况会证明判断错误; risk_notes 列其他风险。"
        "输出正文禁止出现工具名/'维度'/'数据块'/'六查'/'第N步'等流程用语, 立场用中文(看多/看空/中性)表述。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "lynch_category": {
                "type": "string",
                "enum": list(_VALID_LYNCH_CATEGORIES),
            },
            "stance": {"type": "string", "enum": list(_VALID_STANCES)},
            "company_quality_stance": {"type": "string", "enum": list(_VALID_STANCES)},
            "current_odds_stance": {"type": "string", "enum": list(_VALID_STANCES)},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "thesis_summary": {"type": "string"},
            "core_counter_evidence": {"type": "string"},
            "invalidation_condition": {"type": "string"},
            "risk_notes": {"type": "string"},
        },
        "required": [
            "lynch_category",
            "stance",
            "company_quality_stance",
            "current_odds_stance",
            "confidence",
            "thesis_summary",
            "core_counter_evidence",
            "invalidation_condition",
            "risk_notes",
        ],
    },
}

_REVIEW_SYNTHESIS_PROMPT = """下面是一份股票研究初稿 JSON，以及确定性审查器发现的问题。
你的任务不是重新研究股票，也不是新增事实，而是修正最终综合判断：

1. 同一底层事实被多个分析方向重复引用时，只能计一次影响。
2. 若审查器指出方向冲突且初稿无法用日期/口径明确化解，应降低置信度或把综合立场收缩为中性；不得自行编造解释。
3. 若某个方向缺少对应证据，不能让它成为推动最终立场的核心理由。
4. 保留企业长期质量与当前股票赔率的区分。
5. 只能使用初稿中已经出现的事实和数字；禁止添加任何新事实。
6. 输出与原初稿完全相同 schema 的 JSON，不要解释。

初稿:
{draft_json}

审查:
{review_json}
"""



_REPAIR_REQUIREMENTS = (
    "必须是 JSON 对象，且必须包含字段: stance、company_quality_stance、current_odds_stance (三者取值限 bullish/bearish/neutral)、confidence (0到1)、"
    "lynch_category (取值限 fast_grower/stalwart/cyclical/turnaround/"
    "asset_play/slow_grower/unclear)、thesis_summary、core_counter_evidence、"
    "invalidation_condition、risk_notes (后五个均为字符串)"
)

_DIMENSION_LABELS = {
    "management": "管理层",
    "fundamentals": "基本面",
    "rd": "研发能力",
    "chip_flow": "筹码",
    "price_position": "股价位置",
    "cycle_position": "周期",
    "policy_geopolitics": "政策形势",
    "retail_sentiment": "散户情绪",
    "shareholder_returns": "股东回报",
    "growth_elasticity": "成长弹性",
    "a_share_structure": "A股资金结构",
    "risk_quality": "财务质量与尾部风险",
}

_SCORE_PROMPT = """以下是对一只股票十二个维度的分析。请对每个维度打分: -10 表示极度利空, +10 表示极度利多, 0 表示中性。维度名固定为: management, fundamentals, rd, chip_flow, price_position, cycle_position, policy_geopolitics, retail_sentiment, shareholder_returns, growth_elasticity, a_share_structure, risk_quality。

输出 JSON 数组 (12 个元素, 不要任何其他文字):
[{{"dimension": "management", "score": -3, "note": "一句理由 (15字以内)"}}]

维度分析:
{analyses_block}"""

_SCORE_REPAIR = "必须是 JSON 数组且恰有 12 个元素, 每个元素含 dimension (取值限 management/fundamentals/rd/chip_flow/price_position/cycle_position/policy_geopolitics/retail_sentiment/shareholder_returns/growth_elasticity/a_share_structure/risk_quality)、score (-10 到 10 的数字)、note (短字符串)"


