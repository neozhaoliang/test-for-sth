"""Optional, conservative language edit of reader-facing dimension analyses.

The underlying analysis and numerical score are NOT changed. Copy edits happen
only for sections with repeated scripted rhetoric, and are rejected if they drop
dates, numeric/unit facts, decisive themes, or add unsupported claims.
"""
from __future__ import annotations

import logging
import re
from decimal import Decimal
from typing import Any, Mapping

from analysis.thesis_readability import (
    _numbers, _preserves_material_themes, _PRIVATE, _scripted_score,
)

logger = logging.getLogger(__name__)

_NUM_UNIT = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(\d+(?:,\d{3})*(?:\.\d+)?)\s*"
    r"(亿元|万元|亿股|万股|元/股|港元|港币|美元|元|%|倍|"
    r"个交易日|年|月|日|户|件|万吨|吨)"
)


def _numeric_units(text: str) -> set[tuple[Decimal, str]]:
    return {
        (Decimal(number.replace(",", "")), unit)
        for number, unit in _NUM_UNIT.findall(text or "")
    }


def accept_dimension_edit(original: str, edited: Any) -> bool:
    """All numeric claims and essential themes survive; tone must improve."""
    if not isinstance(original, str) or not isinstance(edited, str):
        return False
    new = edited.strip()
    if not new or _PRIVATE.search(new):
        return False
    if len(new) < max(35, int(len(original) * .68)):
        return False
    if len(new) > int(len(original) * 1.55) + 10:
        return False
    if set(_numbers(original)) != set(_numbers(new)):
        return False
    if _numeric_units(original) != _numeric_units(new):
        return False
    if not _preserves_material_themes(original, new):
        return False
    if "暂缺" in original and "暂缺" not in new:
        return False
    old_score = _scripted_score(original)
    new_score = _scripted_score(new)
    if old_score < 2 or new_score >= old_score or new_score >= 2:
        return False
    return new != original.strip()


async def polish_dimension_prose(
    analyses: Mapping[str, str] | None, inputs: Any = None
) -> dict[str, str]:
    """One bounded language-only request for the most scripted sections.

    Return a new public-facing mapping. Original analyses remain untouched,
    including the model's scoring / research-review source.
    """
    result = dict(analyses or {})
    flagged = sorted(
        (
            (key, text)
            for key, text in result.items()
            if isinstance(text, str)
            and len(text.strip()) >= 60
            and _scripted_score(text) >= 2
        ),
        key=lambda kv: _scripted_score(kv[1]),
        reverse=True,
    )[:8]
    if not flagged:
        return result

    try:
        from backtest.llm_client import call_json_ex
        import json

        requested = dict(flagged)
        prompt = (
            "下面是同一份股票分析报告中几个过于模板化的段落。"
            "把它们改成正常投资者讨论公司的中文，只修改措辞和句子衔接，"
            "不增加、不减少事实和数字，不增删日期、金额、百分比、估值倍数、"
            "公司名称、名单事件，也不能把风险假设写成已发生的事实。\n"
            "原稿里所有数字及其单位必须保留，不能新增任何数字。"
            "原因和后果要具体说清，不用'经营韧性'、'风险与机遇并存'、"
            "'整体而言'等套话，不能段段套'先表态、再列数字、最后说仍需关注'。"
            "不要故意扮演股民、模仿任何知识库作者，"
            "不要加第一人称感叹、押韵和对仗。"
            "只给这些原有键返回JSON字符串映射，勿改键名，勿给额外解释。\n"
            "待修改内容：\n"
            + json.dumps(requested, ensure_ascii=False)
        )
        edits, _ = await call_json_ex(
            prompt, max_tokens=5200,
            repair_requirements=(
                "只输出与输入相同键的JSON对象，值为完整中文段落。"
                "不能修改或遗漏任一数字及单位，不能编造事实和来源。"
            ),
        )
        if not isinstance(edits, Mapping):
            return result
        for key, old in flagged:
            proposed = edits.get(key)
            if accept_dimension_edit(old, proposed):
                result[key] = proposed.strip()
            else:
                logger.debug(
                    "[analysis.dimension_readability] edit rejected for %s", key
                )
    except Exception as exc:
        logger.warning(
            "[analysis.dimension_readability] optional edit unavailable: %s",
            type(exc).__name__,
        )
    return result
