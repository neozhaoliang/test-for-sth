"""Improve the reader-facing investment verdict without inventing a new verdict.

The preceding agent performs 12 detailed analyses. Those numeric audits belong
in dimension text and data tables. A final thesis should communicate a few
decisive facts and their causal implications in natural Chinese conversation.

A bounded optional LLM copy edit runs when a submitted thesis is overloaded
with figures OR reads like an obviously prewritten answer template. Candidate
edits are screened for loss of the core decision, numeric invention, private
source leaks, and failure to improve the triggering defect.
The original verdict is retained whenever the LLM is unavailable or unsafe.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import logging
import re
from typing import Any, Mapping

logger = logging.getLogger(__name__)

_NUM = re.compile(r"(?<![A-Za-z0-9])\d+(?:,\d{3})*(?:\.\d+)?")
_PRIVATE = re.compile(
    r"买股票的老木匠|军师祭咖啡|陈chensir|双木林叔|private_source_id|"
    r"https?://|\b(?:SH|SZ|BJ)\d{6}\b", re.I
)
_DENSE_INDICATORS = re.compile(
    r"(?:PE|PB|ROE|EPS|TTM|LPR|同比|环比|分位|股东户数|"
    r"融资余额|资产负债率|增长率|收益率|派息率)", re.I
)

# Signals, not a blacklist of ordinary vocabulary. These catch repeated
# self-conscious rhetorical scaffolds found in actual generated verdicts.
# A single common conjunction like "但是" never triggers a rewrite.
_SCRIPTED_PHRASES = (
    (re.compile(r"我不怀疑[^。！？\n]{0,110}我怀疑的是"), 2),
    (re.compile(r"(?:问题|关键|重点)不在于[^。！？\n]{0,90}而在于"), 2),
    (re.compile(r"这不是[^。！？\n]{0,100}而是"), 1),
    (re.compile(r"单看[^。！？\n]{0,100}合在一起"), 1),
    (re.compile(r"(?:什么会让我改变看法|我最不满意的是|钱怎么流也让我警惕)"), 1),
    (re.compile(r"(?:公司质量我认|我给(?:中性|看多|看空)|容错空间太薄)"), 1),
    (re.compile(r"(?:先说公司质量|经营上确实有硬东西|最后再说买点)"), 1),
    (re.compile(r"(?:综上所述|综合来看|总体来看|整体而言|值得注意的是|需要强调的是)"), 1),
    (re.compile(r"(?:风险与(?:机遇|机会)并存|经营韧性|核心矛盾|结构性脆弱|赔率空间|边际催化)"), 1),
    (re.compile(r"(?:仍需(?:持续)?关注|后续(?:需要|仍需)(?:持续)?(?:关注|跟踪)|需要重点跟踪)"), 1),
    (re.compile(r"(?:从(?:基本面|估值|风险|资金面|筹码)角度来看)"), 1),
    (re.compile(r"(?:首先[^。！？]{0,160}其次|一方面[^。！？]{0,160}另一方面)"), 1),
    (re.compile(r"(?:本质是在买|这一侧我认可|立场给(?:中性|看多|看空)|几个方向同时逆风)"), 2),
)


def _scripted_score(text: str) -> int:
    return sum(weight * len(pattern.findall(text or ""))
               for pattern, weight in _SCRIPTED_PHRASES)



def _numbers(text: str) -> list[Decimal]:
    numbers = []
    for token in _NUM.findall(text or ""):
        try:
            numbers.append(Decimal(token.replace(",", "")))
        except InvalidOperation:
            continue
    return numbers


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n+", text or "") if p.strip()]


def _prose_layout(text: str) -> dict[str, int]:
    """Detect unreadable delivery, not an arbitrary overall word count.

    Large evidence inventories crammed into one breath are much harder to read
    than long explanations with ordinary sentences and separate paragraphs.
    A normal long-form report should not trigger just for having many words.
    """
    paras = _paragraphs(text)
    sentences = [
        item.strip()
        for para in paras
        for item in re.split(r"[。！？\n]+", para)
        if item.strip()
    ]
    dense = sum(
        (len(_numbers(item)) >= 5 and len(item) >= 65)
        or (len(_numbers(item)) >= 3 and len(item) >= 120)
        or len(item) >= 190
        for item in sentences
    )
    packed_paras = sum(
        len(para) >= 230 and (
            len(_numbers(para)) >= 8 or
            para.count("；") + para.count(";") >= 3
        )
        for para in paras
    )
    return {
        "dense_sentences": dense,
        "packed_paragraphs": packed_paras,
        "longest_sentence_chars": max((len(v) for v in sentences), default=0),
    }


# An edit may remove less decisive figures, but must not silently erase the
# actual reason for the stance. These are factual themes, NOT stock verdicts.
_MATERIAL_THEMES = {
    "cash_collection": (
        re.compile(r"经营(?:活动)?现金流|现金流[/／]净利润|现金回款|回款不足"),
        re.compile(r"现金流|回款|现金收"),
    ),
    "foreign_policy": (
        re.compile(r"1260H|BIS|实体清单|制裁|出口管制|美国国防部", re.I),
        re.compile(r"1260H|BIS|清单|制裁|出口管制|美国国防部", re.I),
    ),
    "overseas_revenue": (
        re.compile(r"海外(?:营收|收入)|境外(?:营收|收入)"),
        re.compile(r"海外|境外"),
    ),
    "north_american_customers": (
        re.compile(r"北美(?:营收|客户|市场)|美国(?:客户|采购|市场)"),
        re.compile(r"北美|美国"),
    ),
    "fund_crowding": (
        re.compile(r"机构抱团|机构集中|公募.{0,10}(?:基金|重仓|持股)|基金.{0,12}(?:持股|重仓|抱团)|资金抱团"),
        re.compile(
            r"机构.{0,14}(?:抱团|集中|重仓|持仓|拥挤|买盘|退出)|"
            r"公募.{0,16}(?:基金|持仓|重仓|买盘)|"
            r"基金.{0,14}(?:持仓|抱团|重仓|集中|买盘)|"
            r"资金.{0,8}抱团"
        ),
    ),
    "insider_sale": (
        re.compile(r"(?:实控人|大股东|高管|董事长).{0,8}减持"),
        re.compile(r"减持|卖出"),
    ),
}


def _preserves_material_themes(original: str, revised: str) -> bool:
    return all(
        not source.search(original or "") or bool(kept.search(revised or ""))
        for source, kept in _MATERIAL_THEMES.values()
    )



def _quote_price(inputs: Any) -> Decimal | None:
    """The actual as-of quote, not a historical holder observation."""
    try:
        raw = (getattr(inputs, "quote", None) or {}).get("latest_price")
        price = Decimal(str(raw))
        if price.is_finite() and price > 0:
            return price
    except (ValueError, TypeError, InvalidOperation):
        pass
    return None


_LEAD_PRICE = re.compile(
    r"^\s*(?:现价|按|以|现在|当前)?\s*"
    r"(?P<price>\d+(?:\.\d+)?)\s*元"
    r"(?=买|对应|的股票|这个价格|的市盈率|的市净率|，)"
)


def _lead_price_conflicts(text: str, inputs: Any) -> bool:
    reference = _quote_price(inputs)
    if reference is None:
        return False
    match = _LEAD_PRICE.search(str(text or ""))
    if not match:
        return False
    stated = Decimal(match.group("price"))
    return abs(stated - reference) > max(Decimal("0.15"), reference * Decimal("0.02"))


def align_verified_lead_price(parsed: dict, inputs: Any = None) -> dict:
    """Replace only a provably wrong *opening current quote* with the input quote.

    Do not alter historical prices in the shareholder series or silently
    recalculate PE/PB: valuation ratios remain governed by their own as-of
    source. This prevents an obviously invalid '9.85元买' from reaching readers
    when the as-of market quote says 29.xx.
    """
    original = str(parsed.get("thesis_summary") or "")
    if not _lead_price_conflicts(original, inputs):
        return parsed
    match = _LEAD_PRICE.search(original)
    reference = _quote_price(inputs)
    if not match or reference is None:
        return parsed
    reference_text = format(reference.normalize(), "f")
    corrected = (
        original[:match.start("price")] + reference_text +
        original[match.end("price"):]
    )
    logger.warning(
        "[analysis.thesis_readability] corrected inconsistent opening quote: %s -> %s",
        match.group("price"), reference_text,
    )
    return {**parsed, "thesis_summary": corrected}


_SHARE_SALE_TOTAL = re.compile(
    r"(?P<count>两|二|三|四|五|六)笔[^。！？；\n]{0,18}?合计"
    r"\s*(?P<total>\d+(?:\.\d+)?)\s*万股"
)
_SHARES_BEFORE_TOTAL = re.compile(
    r"(?<![\d.])(?P<amount>\d+(?:\.\d+)?)\s*万股"
)
_CHINESE_COUNT = {"两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6}


def inconsistent_share_sale_totals(text: str) -> list[dict]:
    """Check only explicit N-transaction 万股 totals with N previous quantities.

    This is deliberately conservative: it does not infer ownership of separate
    trades, currency proceeds, unrelated counts or issuer financing.
    """
    result = []
    raw = str(text or "")
    for match in _SHARE_SALE_TOTAL.finditer(raw):
        n = _CHINESE_COUNT[match.group("count")]
        preceding = raw[max(0, match.start() - 480): match.start()]
        quantities = [
            Decimal(v.group("amount"))
            for v in _SHARES_BEFORE_TOTAL.finditer(preceding)
        ][-n:]
        if len(quantities) != n:
            continue
        expected = sum(quantities, Decimal("0"))
        stated = Decimal(match.group("total"))
        if abs(expected - stated) > Decimal("0.02"):
            result.append({
                "start": match.start(), "end": match.end(),
                "stated": stated, "expected": expected,
            })
    return result


def suppress_invalid_share_sale_totals(parsed: dict) -> dict:
    """Remove a demonstrably wrong total instead of publishing it.

    This doesn't silently 'correct' the sum: multiple listed trades may overlap
    in a way the input data has not verified. It also avoids carrying forward
    a claimed aggregate cash proceeds from that same unsupported total.
    """
    raw = str(parsed.get("thesis_summary") or "")
    errors = inconsistent_share_sale_totals(raw)
    if not errors:
        return parsed
    for issue in reversed(errors):
        start, end = issue["start"], issue["end"]
        tail = raw[end:]
        proceeds = re.match(
            r"[、，]?卖方拿到约\d+(?:\.\d+)?亿元(?:，进的是个人账户)?",
            tail,
        )
        if proceeds:
            end += proceeds.end()
        raw = (
            raw[:start] +
            "相关减持的合计数量及金额需要核对，不能直接采用" +
            raw[end:]
        )
        logger.warning(
            "[analysis.thesis_readability] inconsistent share sale total: %s vs %s",
            issue["stated"], issue["expected"],
        )
    return {**parsed, "thesis_summary": raw}


def readability_signals(text: str) -> dict:
    """A trigger, not a language-quality score or automated investment stance."""
    raw = str(text or "")
    nums = _numbers(raw)
    paras = _paragraphs(raw)
    long_dense = sum(
        len(_numbers(p)) >= 7 or
        (len(p) >= 270 and len(_DENSE_INDICATORS.findall(p)) >= 5)
        for p in paras
    )
    # Neither length nor paragraph count is a measure of human speech.
    overloaded = (
        len(nums) >= 16
        or (len(nums) >= 11 and long_dense >= 2)
        or (len(raw) >= 760 and long_dense >= 2)
    )
    tone_score = _scripted_score(raw)
    layout = _prose_layout(raw)
    # Length itself never forces a rewrite; packing and canned phrasing do.
    verbose = len(raw) > 850 and (
        layout["dense_sentences"] > 0 or layout["packed_paragraphs"] > 0
        or tone_score >= 2
    )
    return {
        "needs_edit": (
            overloaded or verbose or tone_score >= 2
            or layout["dense_sentences"] >= 2
            or layout["packed_paragraphs"] >= 1
        ),
        "numeric_overload": overloaded,
        "too_long": verbose,
        "scripted_tone": tone_score >= 2,
        "scripted_score": tone_score,
        "numeric_mentions": len(nums),
        "dense_paragraphs": long_dense,
        "paragraph_count": len(paras),
        **layout,
    }


def _verified_names(inputs: Any) -> tuple[str, ...]:
    record = (((getattr(inputs, "management_capital", None) or {})
               .get("succession") or {}).get("verified_transition") or {})
    if record.get("status") != "completed":
        return ()
    return tuple(str(record.get(k) or "") for k in
                 ("founder", "chairman_appointed") if record.get(k))


def accept_readable_edit(original: str, edited: str, inputs: Any = None) -> bool:
    """Require an actual improvement and reject unsupported concrete numbers.

    Keep all field-level investment stances outside this edit unchanged.
    Numeric mentions are checked with Decimals, so 392 and 392.00 match.
    """
    if not isinstance(edited, str):
        return False
    revised = edited.strip()
    # Reject empty/incomplete rewrites, not longer explanations. Previously an
    # 850-character ceiling silently discarded valid, readable paragraphs.
    if len(revised) < 100:
        return False
    if _PRIVATE.search(revised):
        return False
    if not _preserves_material_themes(original, revised):
        return False
    if inconsistent_share_sale_totals(revised):
        return False
    if _lead_price_conflicts(revised, inputs):
        return False
    old_nums, new_nums = _numbers(original), _numbers(revised)
    allowed_nums = set(old_nums)
    quote_price = _quote_price(inputs)
    if quote_price is not None:
        allowed_nums.add(quote_price)
    if not set(new_nums).issubset(allowed_nums):
        return False
    original_signals = readability_signals(original)
    edited_signals = readability_signals(revised)
    # Improve the *way* the numbers are explained. Do not enforce an arbitrary
    # eight-number budget or confuse >850 characters with unreadable writing.
    if (original_signals["numeric_overload"] and len(old_nums) >= 16
            and len(new_nums) >= len(old_nums)
            and len(revised) <= len(original)):
        return False
    if (original_signals["scripted_tone"] and
            edited_signals["scripted_score"] >= original_signals["scripted_score"]):
        return False
    # A rewrite that still contains a conspicuous rhetorical script is no
    # improvement, even if it is shorter and uses fewer figures.
    if edited_signals["scripted_score"] >= 2:
        return False
    # A rewrite that preserves every single figure in equally dense sentences
    # is still the same evidence inventory in different words.
    if original_signals["dense_sentences"] >= 2:
        if edited_signals["dense_sentences"] >= 2:
            return False
    if original_signals["packed_paragraphs"] >= 1:
        if edited_signals["packed_paragraphs"] >= 1:
            return False
    # Prevent erasing the investment argument; long, substantive explanations
    # are welcome as long as they remain grounded in the source figures.
    if original and len(revised) < min(100, len(original) * .16):
        return False
    # Verified succession details remain in management analysis. A brief
    # reader thesis may omit them when a different issue drives the price.
    # An otherwise well-written edit with no recognisable price or company
    # quality distinction is not a complete investment judgment.
    if not re.search(r"价格|价位|现价|买入|估值|赔率", revised):
        return False
    if not re.search(r"公司|企业|生意|经营|盈利|利润|现金流|订单|矿山|客户|金铜|分红", revised):
        return False
    return True


async def polish_thesis_prose(parsed: dict, inputs: Any = None) -> dict:
    """Optional post-review, post-miner stage. Changes ONLY thesis_summary."""
    original = str(parsed.get("thesis_summary") or "")
    stats = readability_signals(original)
    if not (stats["needs_edit"] or _lead_price_conflicts(original, inputs)
            or inconsistent_share_sale_totals(original)):
        return parsed
    stances = {key:parsed.get(key) for key in (
        "stance", "company_quality_stance", "current_odds_stance"
    )}
    try:
        from backtest.llm_client import call_json_ex

        quote_price = _quote_price(inputs)
        quote_context = (
            "本次已核实的行情基准价：" + str(quote_price) + "元。"
            "原稿开头的价格如果与此冲突，必须舍弃原稿错价，"
            "可使用此基准价，也可以不在正文报买入价格。"
            if quote_price is not None else
            "本次缺少可靠行情基准价，不要复述没有证实的具体当前买入价。"
        )
        prompt = (
            "你现在是稿件编辑，不是研究员。原文的研究工作已经完成。"
            "请把它改成投资者读得懂、愿意从头看到尾的中文。"
            "改写句子和叙述顺序，不是删除几个形容词或逐句换同义词。\n"
            "先看清楚公司真正赚钱的方式、这笔利润有多少转成现金、"
            "目前股价对未来有多高的要求，然后选这只股票独有的主要风险。"
            "与这个判断无关的技术细节、历史高低点清单、员工持股和融资账户"
            "的逐笔数字放在后面的专题里，不要都搬到一篇评论。\n"
            "先想明白：在这个价格下，这只股票最重要的一两件事是什么？"
            "围绕这些事写清'发生了什么—为什么—影响买入决定'。"
            "完整财务数据、宏观背景、筹码和接班等资料已经在详细章节，"
            "这里不必逐个汇报，也不必将所有研究问题塞进结论。\n"
            "没有固定字数、段落数或数字个数的要求，写多长由内容决定。"
            "写长可以，但每段都要真的解释问题，不能堆数字或反复说同一句话。"
            "写短可以，但别为了省字数把关键原因、条件与后果都省略掉。"
            "必要的数字保留并讲清含义，非关键数字和研究记录放在专项章节。"
            "一个自然段讲一件事。句子要有停顿：数字讲完就解释数字，"
            "不许连续通过分号和顿号把三个以上独立的数据表拼在一起。"
            "一段里如果出现七八个数字，通常就应该拆开、删掉或挪到详表。"
            "段落不要求一样长，不必每个段落都给一份利好利空清单。"
            "读者已经知道PE、PB这些基本指标，不用展示术语储备。"
            "句子不要塞进太多互不相干的百分比、估值口径和时点。"
            "要解释正常年份能赚多少，就直接谈正常年份可能赚多少钱；"
            "要解释当前估值，就说这个价格隐含了什么盈利预期。"
            "不要写'正常化盈利收益率''分母造出来的便宜''能力连续性风险'"
            "'原始未复权同步性观察''资料缺口尚待验证'这类生硬说法。"
            "对没有核实的风险，可以一句话提醒，别铺开整个核验过程。\n"
            "语气就像有人认真讨论股票，不演投资大师，不用'我最担心的是'、"
            "'这不是而是'、'本质是在买'、'这一侧我认可'、'立场给中性'"
            "之类作秀句，也不用'公司表现亮眼但风险仍存'、'整体来看'、"
            "'几个方向同时逆风'、'后续需要关注'等套话。"
            "可以直说这只股票值不值得现在买及原因；不强行说正反两面。"
            "不要用'最该回答的不是X而是Y'作开头，不要宣布'生意有硬处'、"
            "'现金这一关更刺眼'、'赔率不对称'来代替解释。"
            "说盈利回落时可能亏多少，必须有相应盈利和估值测算支撑，"
            "否则说清可能发生的方向，不要报拍脑袋的腰斩或三成涨幅。\n"
            "写法参考（仅看表达方式，下面不是任何公司的事实）："
            "'今年利润确实高了不少，但公司今年卖得更多，还是产品涨价了？"
            "要是主要靠涨价，明年价格回落时还能赚多少？"
            "先算明白这个，再看现在的股价便不便宜。'"
            "这段只是普通人解释问题的方式，不是统一模板，不要照搬。\n"
            "原稿的总体看多/看空/中性立场不可变。重要的现金回款、客户、"
            "基金抱团、地缘政策等关键问题如在原稿里明确成立，不能被删没。"
            "允许删掉非决定性的事实和全部非关键数字；不允许新增任何未经确认的"
            "人名、百分比、日期、事件或资金流动。不能照搬知识库作者的说法。\n"
            + quote_context + "\n"
            + (
                "原稿中有减持数量合计不等于逐笔数量之和的错误。"
                "请不要使用有争议的合计股数、套现金额或基于它们作判断；"
                "逐笔数据若对立场重要可以保留，但必须分开叙述。\n"
                if inconsistent_share_sale_totals(original) else ""
            )
            + "不可修改的立场字段：" + str(stances) +
            "\n原始研究记录：\n" + original +
            "\n只输出包含thesis_summary字符串的JSON对象。"
        )
        # A rejected edit previously returned the original wall of text.
        # Give the editor one focused second chance. Never accept invented
        # numbers or a rewrite that erases decisive investment risks.
        for attempt in range(2):
            work_prompt = prompt
            if attempt:
                work_prompt += (
                    "\n\n刚才的改写未通过事实或可读性检查。"
                    "请重新写完整的thesis_summary。"
                    "不要一段堆十几个数字，不要长句连长句；"
                    "每个段落只讲一件事，主要观点和重要风险要保留。"
                    "请特别注意单位、股价、日期及原文的因果限定，"
                    "不要加入新数字，也不要为了短而删掉关键结论。"
                )
            result, _ = await call_json_ex(
                work_prompt, max_tokens=4096,
                repair_requirements=(
                    "只输出包含thesis_summary的JSON对象；篇幅不限。"
                    "自然叙述，不要堆数或套模板。"
                    "不可编造或修改数字，允许删去无关事实。"
                    "保留主要投资判断，不得照搬私有知识库作者。"
                ),
            )
            new_text = result.get("thesis_summary") if isinstance(result, Mapping) else None
            if accept_readable_edit(original, new_text, inputs):
                logger.info(
                    "[analysis.thesis_readability] reader edit accepted "
                    "(attempt=%d, numerics %d -> %d, packed paragraphs %d -> %d)",
                    attempt + 1,
                    len(_numbers(original)), len(_numbers(new_text)),
                    stats["packed_paragraphs"],
                    readability_signals(new_text)["packed_paragraphs"],
                )
                return {**parsed, "thesis_summary": new_text.strip()}
            logger.warning(
                "[analysis.thesis_readability] reader edit rejected "
                "(attempt=%d, original numeric=%d, packed=%d)",
                attempt + 1, stats["numeric_mentions"], stats["packed_paragraphs"],
            )
        return parsed
    except Exception as exc:
        logger.warning(
            "[analysis.thesis_readability] optional edit unavailable: %s",
            type(exc).__name__,
        )
        return parsed
