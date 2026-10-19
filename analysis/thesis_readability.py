"""Improve the reader-facing investment verdict without inventing a new verdict.

The preceding agent performs 12 detailed analyses. Those numeric audits belong
in dimension text and data tables. A final thesis should communicate a few
decisive facts and their causal implications in natural Chinese conversation.

A bounded optional LLM copy edit runs ONLY if the submitted thesis visibly
resembles a data inventory. Candidate edits are screened for loss of the core
decision, numeric invention, private source leaks and failure to reduce clutter.
The original verdict is retained whenever the LLM is unavailable or unsafe.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import logging
import re
from typing import Any, Mapping

logger = logging.getLogger(__name__)

from analysis.thesis_voice import HUMAN_SPEECH_GUIDANCE

_NUM = re.compile(r"(?<![A-Za-z0-9])\d+(?:,\d{3})*(?:\.\d+)?")
_PRIVATE = re.compile(
    r"买股票的老木匠|军师祭咖啡|陈chensir|双木林叔|private_source_id|"
    r"https?://|\b(?:SH|SZ|BJ)\d{6}\b", re.I
)
_DENSE_INDICATORS = re.compile(
    r"(?:PE|PB|ROE|EPS|TTM|LPR|同比|环比|分位|股东户数|"
    r"融资余额|资产负债率|增长率|收益率|派息率)", re.I
)

# These are not forbidden Chinese constructions in themselves. They are trigger
# signals when they appear in a verdict, where LLMs tend to stack them into a
# polished, symmetrical "AI answer" instead of explaining the actual problem.
_FORMULAIC_PATTERNS = (
    re.compile(r"我不(?:怀疑|否认|否定).{0,55}(?:我怀疑|我只是|而是)"),
    re.compile(r"这不是.{0,35}而是"),
    re.compile(r"我并不(?:否定|否认).{0,55}我只是"),
    re.compile(r"什么(?:会|能)让我改变看法"),
    re.compile(r"如果.{0,55}反过来"),
    re.compile(r"真正的问题(?:是|在于)"),
    re.compile(r"所以我(?:并不|不是|只是).{0,45}(?:在那之前|我只是|而是)"),
    re.compile(r"归根结底|综上所述|说到底"),
)


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
    # 4-6 natural paragraphs may be long without being cluttered; numbers and
    # jargon are the issue, not length by itself.
    overloaded = (
        len(nums) >= 22
        or (len(nums) >= 13 and long_dense >= 2)
        or (len(raw) >= 800 and long_dense >= 3)
    )
    formulaic_count = sum(len(pattern.findall(raw)) for pattern in _FORMULAIC_PATTERNS)
    return {
        "needs_edit": overloaded or formulaic_count > 0,
        "numeric_mentions": len(nums),
        "dense_paragraphs": long_dense,
        "paragraph_count": len(paras),
        "formulaic_patterns": formulaic_count,
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
    if len(revised) < 180 or len(revised) > 2400:
        return False
    if _PRIVATE.search(revised):
        return False
    old_nums, new_nums = _numbers(original), _numbers(revised)
    if old_nums and not set(new_nums).issubset(set(old_nums)):
        return False
    if not old_nums and new_nums:
        return False
    if len(old_nums) >= 13 and len(new_nums) > min(9, int(len(old_nums) * .5)):
        return False
    if len(_paragraphs(revised)) < 2 and len(revised) > 360:
        return False
    if readability_signals(revised)["needs_edit"]:
        return False
    if original and len(revised) < min(220, len(original) * .30):
        return False
    for name in _verified_names(inputs):
        if name not in revised:
            return False
    # An otherwise well-written edit with no recognisable price or company
    # quality distinction is not a complete investment judgment.
    if not re.search(r"价格|价位|现价|买入|估值|赔率", revised):
        return False
    if not re.search(r"公司|企业|生意|经营|盈利", revised):
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
            "你在帮一位投资者把已经完成的判断整理成能直接向朋友解释的中文。"
            "不是重新研究，也不是把原稿润色成更漂亮、更完整的标准研报；要改的是机器式的思路和句型。"
            "请先看下面的表达校准，再处理原结论。\n\n"
            + HUMAN_SPEECH_GUIDANCE
            + "\n\n改写要求：\n"
            "1. 保留原来的投资立场、企业质量判断、当前价格判断和关键事实；你只负责表达，不补做研究。"
            "不要把每一段都写成一条结论加三条理由，也不要为了显得完整而把所有风险逐项列完。\n"
            "2. 读者需要听懂的是：你为什么这么看、哪件事最影响判断、这件事会怎样影响生意或股东的钱。"
            "让解释顺着这个问题自然展开，段落数和长短随内容变化，不设固定数量。\n"
            "3. 留下有用的数字，删掉重复数字和无关日期。数字只能删减，不能新增、改写或拼出新估值情景。"
            "不必凑某个数字数量，也不必把详细章节已经展示的资料再抄一遍。\n"
            "4. 不要用‘我不怀疑……我怀疑的是……’、‘这不是……而是……’、"
            "‘我并不否定……我只是……’或‘什么会让我改变看法’之类句型。"
            "不要为了显得口语化而加口头禅、反问、对仗或故意不完整的句子。\n"
            "5. 不要反复写‘本次资料里没有’、‘不能据此判断’等流程式免责声明。"
            "确实影响买卖的未知因素，放在它影响判断的地方用普通话讲清楚。\n"
            "6. 只能使用原稿已有的事实和数字，不得改变事实、管理层姓名、职务或已经核实的交接情况；"
            "不得提及私人知识库作者、帖子、工具或内部审查。\n"
            "返回JSON：{\\"thesis_summary\\":\\"改写后的自然中文结论\\"}。\n\n"
            "不能更改的结构化判断：" + str(stances) +
            "\n原结论：\n" + original
        )
        result, _ = await call_json_ex(
            prompt, max_tokens=2400,
            repair_requirements=(
                "只能输出包含thesis_summary的JSON对象；保持原判断，不可添加新数字、人物、"
                "事实或私有作者，不得变成数据流水账。"
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
