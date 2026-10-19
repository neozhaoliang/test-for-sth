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
        len(nums) >= 22
        or (len(nums) >= 13 and long_dense >= 2)
        or (len(raw) >= 800 and long_dense >= 3)
    )
    tone_score = _scripted_score(raw)
    return {
        "needs_edit": overloaded or tone_score >= 2,
        "numeric_overload": overloaded,
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
    if len(revised) < 110 or len(revised) > 2400:
        return False
    if _PRIVATE.search(revised):
        return False
    if not _preserves_material_themes(original, revised):
        return False
    old_nums, new_nums = _numbers(original), _numbers(revised)
    if old_nums and not set(new_nums).issubset(set(old_nums)):
        return False
    if not old_nums and new_nums:
        return False
    original_signals = readability_signals(original)
    edited_signals = readability_signals(revised)
    if (original_signals["numeric_overload"] and len(old_nums) >= 13
            and len(new_nums) > min(9, int(len(old_nums) * .5))):
        return False
    if edited_signals["needs_edit"]:
        return False
    if (original_signals["scripted_tone"] and
            edited_signals["scripted_score"] >= original_signals["scripted_score"]):
        return False
    if original and len(revised) < min(130, len(original) * .20):
        return False
    for name in _verified_names(inputs):
        if name not in revised:
            return False
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
    if not stats["needs_edit"]:
        return parsed
    stances = {key:parsed.get(key) for key in (
        "stance", "company_quality_stance", "current_odds_stance"
    )}
    try:
        from backtest.llm_client import call_json_ex
        prompt = (
            "把下面的股票判断改成正常投资讨论里的中文，保留原有投资判断。"
            "不需要扮演老师、老朋友、股评人，不要追求‘像真人说话’。"
            "这项工作是删掉不必要的表演和结构，不是换一种新的口语模板。\n"
            "优先处理：\n"
            "- 不必段段先抛判断，再给理由，再收一个漂亮的结尾。"
            "不要每只股票都套上公司质量、现金流、股东、技术、筹码、条件反转的统一顺序。\n"
            "- 不刻意制造‘我不怀疑……我怀疑的是……’‘这不是……而是……’"
            "‘单看……合在一起……’这种对称句式；不用设问转场"
            "‘什么会让我改变看法’，不靠‘我最不满意的是’之类句子表演个人态度。\n"
            "- 哪里真正有问题，就把事实、具体动作和后果放在那里说。"
            "一句话说得清就一句话，必要时讲几段；段落长短可以不齐。"
            "不要用'公司基本面坚实但风险仍需关注''风险与机遇并存'的公关腔，"
            "不要把'总负债增长'写成'资本结构阶段性承压'。"
            "不要写固定数量的理由，不必强迫结尾交代未来条件。\n"
            "- 以下仅是写法对照，不是本公司数据，不得照搬："
            "生硬：'经营表现亮眼，但现金转换仍待改善，未来需关注回款质量。'"
            "自然：'利润涨得快，钱却没收回来多少。先看应收和存货，才能知道增长占用了多少现金。'"
            "不要把自然理解成每段都写一遍这个句式。\n"
            "- 原文中的关键财务和公司事实不能变，特别不能删掉决定性的"
            "客户、海外政策、机构集中、现金回款、内部人交易等主题。"
            "保留左右结论的关键依据以及审慎限定；数字太密时删掉非决定性数字，"
            "细节已经在专项章节。不要新增数字、金额、比例、日期、名称、职务、"
            "事件、价格目标或假设。\n"
            "- 保留股票当前可买与否的原立场，"
            "不要替原文添加第一人称立场、感叹号、俏皮话、道德训诫、万能免责声明。"
            "只许删除和改写冗余措辞，不得动结构化字段。\n"
            "- 不要引用、模仿或泄露知识库中任何特定作者的原话、账号或视频。\n"
            "只返回JSON对象，字段为thesis_summary。\n\n"
            "不可修改的立场字段：" + str(stances) +
            "\n待修改的原文：\n" + original
        )
        result, _ = await call_json_ex(
            prompt, max_tokens=2400,
            repair_requirements=(
                "只能输出包含thesis_summary的JSON对象；原立场、数字、人物、日期与事实不可更改。"
                "不得使用刻意的对仗、反问、扮演式第一人称和同质化段落模板，也不可引用知识库作者。"
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
