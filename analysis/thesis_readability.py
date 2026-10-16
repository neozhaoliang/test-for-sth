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

_NUM = re.compile(r"(?<![A-Za-z0-9])\d+(?:,\d{3})*(?:\.\d+)?")
_PRIVATE = re.compile(
    r"买股票的老木匠|军师祭咖啡|陈chensir|双木林叔|private_source_id|"
    r"https?://|\b(?:SH|SZ|BJ)\d{6}\b", re.I
)
_DENSE_INDICATORS = re.compile(
    r"(?:PE|PB|ROE|EPS|TTM|LPR|同比|环比|分位|股东户数|"
    r"融资余额|资产负债率|增长率|收益率|派息率)", re.I
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
    return {
        "needs_edit": overloaded,
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
    if len(_paragraphs(revised)) < 3:
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
            "你是投资讨论区里讲道理、说人话的老朋友，也是一位善于解释问题的老师。"
            "请把下面的投资结论改写成一段真正有人愿意看完的中文回答，而不是审计报告。"
            "你的任务是删减和组织信息，不是补做股票研究。\n"
            "写作要求：\n"
            "1. 第一段直接、自然地回答这个价格值不值得买；坚持原立场，"
            "公司质量与买入赔率可以不同。不要用“我给中性”“这不是……而是……”等套话。\n"
            "2. 从长串事实中只选择对结论最关键的3—5个理由，"
            "每个理由用日常语言说清楚为什么重要。用3—6个自然段，不要小标题、编号或条目。\n"
            "3. 全文一般保留3—7处真正不可缺少的数字（如现价、利润、估值或持有人变化），"
            "日期非必要不用写到日；剩余财报细节在报告其他章节，不必重新逐条抄写。"
            "不要接连罗列营业收入、增长率、利润、现金流、负债率、分红、回购、产量。\n"
            "4. 多用完整中文句子，少用PE/PB/ROE/EPS/TTM等缩写、括号、斜杠、百分比串，"
            "若提指标只解释它在投资决策中的含义。让复杂的货币周期、矿业资本开支"
            "或筹码变化读起来像人与人谈话，而不是机械核查清单。\n"
            "5. 不要写“本次资料里没有价格锚”“本次检索”“没有证据不能”"
            "等机械免责声明；真正影响结论的未知风险，用一句正常话说清楚即可。\n"
            "6. 不得改变判断方向，不得新增或修改任何数字、人名、日期、"
            "公司事项、管理层职务或假定价格；允许删掉不重要数字，"
            "但保留原稿最关键的已证实事实、最强反方理由及真正能改变判断的条件。"
            "绝对不能凭空提出新的估值情景。\n"
            "7. 不要泄露或提及私人知识库作者、帖子、工具或内部审查流程。\n"
            "返回JSON：{\"thesis_summary\":\"自然、分段的回答\"}。\n\n"
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
