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
    verbose = len(raw) > 850
    tone_score = _scripted_score(raw)
    return {
        "needs_edit": overloaded or verbose or tone_score >= 2,
        "numeric_overload": overloaded,
        "too_long": verbose,
        "scripted_tone": tone_score >= 2,
        "scripted_score": tone_score,
        "numeric_mentions": len(nums),
        "dense_paragraphs": long_dense,
        "paragraph_count": len(paras),
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
    if len(revised) < 110 or len(revised) > 850:
        return False
    if _PRIVATE.search(revised):
        return False
    if not _preserves_material_themes(original, revised):
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
    if (original_signals["numeric_overload"] and len(old_nums) >= 11
            and len(new_nums) > min(8, int(len(old_nums) * .5))):
        return False
    if edited_signals["needs_edit"]:
        return False
    if (original_signals["scripted_tone"] and
            edited_signals["scripted_score"] >= original_signals["scripted_score"]):
        return False
    if original and len(revised) < min(130, len(original) * .20):
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
    if not (stats["needs_edit"] or _lead_price_conflicts(original, inputs)):
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
            "这是一份投资研究初稿，不是可以直接给人看的结论。"
            "请重新组织成正常人愿意读的投资评论，不要按原文逐句换同义词。\n"
            "先想明白：在这个价格下，这只股票最重要的一两件事是什么？"
            "围绕这些事写清'发生了什么—为什么—影响买入决定'。"
            "完整财务数据、宏观背景、筹码和接班等资料已经在详细章节，"
            "这里不必逐个汇报，也不必将所有研究问题塞进结论。\n"
            "通常写200—500字，内容复杂可以略长，但必须明显短于原稿；"
            "不规定段落数或顺序，不要列清单。最多选约三到六个真正关键的数字，"
            "数字后面要解释意思。删掉非关键项目名称、重复百分比和研究术语。"
            "千万别写'正常化盈利收益率''分母造出来的便宜''能力连续性风险'"
            "'原始未复权同步性观察''资料缺口尚待验证'这类生硬说法。"
            "对没有核实的风险，可以一句话提醒，别铺开整个核验过程。\n"
            "语气像认真讨论股票，不演投资大师，不用'我最担心的是'、"
            "'这不是而是'之类作秀句，也不用'公司表现亮眼但风险仍存'、"
            "'整体来看'、'后续需要关注'等空话。"
            "可以直说这只股票值不值得现在买及原因；不强行说正反两面。\n"
            "原稿的总体看多/看空/中性立场不可变。重要的现金回款、客户、"
            "基金抱团、地缘政策等关键问题如在原稿里明确成立，不能被删没。"
            "允许删掉非决定性的事实和全部非关键数字；不允许新增任何未经确认的"
            "人名、百分比、日期、事件或资金流动。不能照搬知识库作者的说法。\n"
            + quote_context + "\n"
            "不可修改的立场字段：" + str(stances) +
            "\n原始研究记录：\n" + original +
            "\n只输出包含thesis_summary字符串的JSON对象。"
        )
        result, _ = await call_json_ex(
            prompt, max_tokens=2200,
            repair_requirements=(
                "只能输出包含thesis_summary的JSON对象，字数宜精简。"
                "不得编造数字或未核实事实，可删无关细节并使用提供的已核实行情基准价。"
                "不能更改总体投资立场，不得套话、模仿知识库作者。"
            ),
        )
        new_text = result.get("thesis_summary") if isinstance(result, Mapping) else None
        if not accept_readable_edit(original, new_text, inputs):
            logger.warning(
                "[analysis.thesis_readability] prose edit rejected; "
                "original numbers=%d", stats["numeric_mentions"]
            )
            return parsed
        logger.info(
            "[analysis.thesis_readability] simplified thesis numerics %d -> %d",
            len(_numbers(original)), len(_numbers(new_text))
        )
        return {**parsed, "thesis_summary": new_text.strip()}
    except Exception as exc:
        logger.warning(
            "[analysis.thesis_readability] optional edit unavailable: %s",
            type(exc).__name__,
        )
        return parsed
