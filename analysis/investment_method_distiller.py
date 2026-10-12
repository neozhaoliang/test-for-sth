"""Private learning of reusable investment *reasoning*, not a stock scoring table.

The LLM reads pre-distilled private research context once and abstracts
competing methods. Its output is rechecked for source identities, tickers,
original phrases and citations before it can enter the investment synthesis.

The model's subjective judgment belongs to the FINAL research LLM, not here.
This stage does not choose bullish/bearish or give numeric factor weights.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Iterable

_PRIVATE_URL = re.compile(r"https?://\S+|(?:SH|SZ|BJ)\d{6}|(?<!\d)\d{6}(?!\d)", re.I)
_TICKER_COMPANY = re.compile(r"[\u4e00-\u9fff]{2,12}\s*[（(](?:SH|SZ|BJ)?\d{6}[)）]", re.I)
_EXPECTED_KEYS = ("insight", "use_when", "possible_mistake", "company_question")


def _published(value: Any):
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _deidentify_seed(text: str, excerpts: Iterable) -> str:
    """Remove accounts and named issuer examples from private model input."""
    s = _TICKER_COMPANY.sub("某公司", text)
    s = _PRIVATE_URL.sub("（来源信息省略）", s)
    # No legacy off-stock issuer examples are needed to learn the mechanism.
    for name in ("齐鲁银行", "腾讯", "农业银行",
                 "军师祭咖啡", "买股票的老木匠", "双木林叔", "陈chensir"):
        s = s.replace(name, "某公司")
    for entry in excerpts:
        for name in (
            getattr(entry, "author", None), getattr(entry, "source", None)
        ):
            if name and len(str(name).strip()) >= 3:
                s = s.replace(str(name), "（来源已匿名）")
    return s


def validate_method_output(data: Any, selected: list) -> list[dict]:
    """Fail closed: no raw quote, byline, ID, URL or unrelated case is forwarded."""
    if not isinstance(data, list):
        return []
    forbidden = {
        str(getattr(e, "author", "") or "").strip()
        for e in selected
    } | {
        str(getattr(e, "source", "") or "").strip()
        for e in selected
    }
    forbidden = {v for v in forbidden if len(v) >= 3}
    for term in ("军师祭咖啡", "买股票的老木匠", "双木林叔",
                 "陈chensir", "齐鲁银行", "腾讯", "农业银行"):
        forbidden.add(term)
    raw = [
        re.sub(r"\s+", "", str(getattr(e, "distilled", "") or ""))
        for e in selected
    ]
    valid = []
    seen = set()
    for method in data[:7]:
        if not isinstance(method, dict):
            continue
        values = {k: str(method.get(k) or "").strip() for k in _EXPECTED_KEYS}
        if not all(12 <= len(v) <= 330 for v in values.values()):
            continue
        combined = "\n".join(values.values())
        condensed = re.sub(r"\s+", "", combined)
        if (_PRIVATE_URL.search(combined) or _TICKER_COMPANY.search(combined)
                or any(term in combined for term in forbidden)
                or re.search(r"原帖|作者|博主|某位投资者|引用来源|雪球账号", combined)):
            continue
        # Reject pasted excerpts. Paraphrase of the IDEA is permitted.
        if any(
            original[i:i+36] in condensed
            for original in raw if len(original) >= 36
            for i in range(0, len(original)-35, 18)
        ):
            continue
        key = values["insight"]
        if key not in seen:
            valid.append(values)
            seen.add(key)
        if len(valid) >= 4:
            break
    return valid


async def compile_investment_methods(
    excerpts: Iterable, *, as_of: str, archetype: str,
    industry: str = "", stock_name: str = "",
    fallback_principles: Iterable = (),
) -> list[dict]:
    """LLM distills contextual views; rule-based methods are a safe fallback.

    Internal knowledge is private. No raw post becomes a user-facing source.
    """
    cutoff = _published(as_of) or date.today()
    available = [
        e for e in excerpts
        if _published(getattr(e, "published_at", None))
        and _published(e.published_at) <= cutoff
        and bool(str(getattr(e, "distilled", "") or "").strip())
    ]
    # No FIFO-only selection: otherwise evergreen stockholder-cost methods
    # are crowded out by a dozen recent but less useful market commentary posts.
    priority = []
    if archetype == "technology":
        from analysis.market_research_context import (
            select_evergreen_tech_finance_excerpts,
        )
        priority.extend(select_evergreen_tech_finance_excerpts(
            available, as_of=as_of, limit=5,
        ))
    if stock_name:
        priority.extend(
            e for e in available
            if stock_name in (str(getattr(e, "title", "") or "") + " " +
                              str(getattr(e, "distilled", "") or ""))
        )
    priority.extend(available)
    selected = []
    seen = set()
    for e in priority:
        key = (getattr(e, "source", ""), getattr(e, "title", ""),
               getattr(e, "published_at", ""))
        if key not in seen:
            selected.append(e)
            seen.add(key)
        if len(selected) >= 14:
            break
    baseline = [
        {
            "insight": str(p.get("principle") or ""),
            "use_when": "当这一经济机制与公司商业模式、股价及交易阶段有关时",
            "possible_mistake": "这一观点可能过时、判断有偏差，不能取代公司的实际数据",
            "company_question": str(p.get("company_check") or ""),
        }
        for p in fallback_principles
        if isinstance(p, dict) and p.get("principle") and p.get("company_check")
    ][:4]
    if not selected:
        return baseline
    items = [
        _deidentify_seed(str(getattr(e, "distilled", "") or "")[:660], selected)
        for e in selected
    ]
    prompt = (
        "你正在为一个有独立主见的投资经理整理私有投资经验。"
        "以下是经过匿名处理的历史观点材料，可能互相矛盾，也可能是错误认识。"
        "请归纳最有辨识度的2—4种可迁移的投资思考方式，不要为当前股票下结论。"
        "不要复述原帖句子；不要引用任何作者、账号、来源、具体股票案例或链接。"
        "允许保留观点的鲜明倾向和其可能犯错之处；不要把鲜明观点中和成平庸口号。"
        "尤其识别资金从哪里来、股东支付了什么成本、谁获得现金、"
        "市场共识与内在价值为何可能背离、周期底部/顶部的风险不对称。"
        "不要求所有方法都适用，也不要求方法之间一致。"
        "\n\n本次研究类型：" + archetype + "；行业：" + industry +
        "；当前公司：" + stock_name +
        "\n返回JSON数组，每个元素都必须包含字符串字段"
        "insight、use_when、possible_mistake、company_question；"
        "company_question必须指向本公司能检查的事实，不得包含其他股票名称。"
        "\n\n匿名化材料（内部资料，不是事实证据）：\n" +
        "\n".join(f"[{i+1}] {v}" for i, v in enumerate(items))
    )
    try:
        from backtest.llm_client import call_json_ex
        generated, _ = await call_json_ex(
            prompt, max_tokens=2000,
            repair_requirements=(
                "仅输出JSON数组，每项包含insight/use_when/"
                "possible_mistake/company_question四个字符串字段；"
                "不能出现原文、作者、股票代码、账号或链接。"
            ),
        )
        validated = validate_method_output(generated, selected)
        return validated if validated else baseline
    except Exception:
        return baseline


def investment_manager_prompt(methods: Iterable, *, archetype: str) -> str:
    """Subjective choice of central argument, NOT an equal-weight framework."""
    rows = [
        "## 独立投资经理模式：形成主观、可辩论的判断",
        "你不是财务指标评分器，也不是对冲各项利弊的公关稿作者。",
        "把已披露的事实与投资方法结合，找出影响股票未来回报的最关键矛盾。"
        "你可以有强烈的看法，也可以反对常见共识；对未来的解释、概率和"
        "估值假设可主观判断，但必须和已披露事实区别清楚。",
        "以下只是可供选用或质疑的投资方法，不代表必须采纳的规则，"
        "不应按条目数量、行业标签、固定阈值或评分平均数形成最终立场。",
    ]
    for item in list(methods or [])[:4]:
        rows.append(
            "方法候选：" + str(item.get("insight") or "") +
            "；适用情形：" + str(item.get("use_when") or "") +
            "；可能的错误：" + str(item.get("possible_mistake") or "") +
            "；可检验问题：" + str(item.get("company_question") or "")
        )
    rows.extend([
        "你可以判断这些方法中没有一个适用；应说明为什么，并选择更合理的"
        "分析角度。高位重视风险、低位关注利好是一种思维习惯，不是触发买卖"
        "结论的数学公式。行情、分红或财务数据缺失时可进行有条件的判断，"
        "但不能把假设伪装成已披露事实或无依据报出精确目标价。",
        "最终投资结论要清晰：在当前价格下你最担心或最期待什么？"
        "市场大多数人可能忽略了什么？哪一个因素能推翻你的观点？"
        "上述思考融入一段自然的投资评论，不另设学术审查模块。"
        "十二项分析是支持独立判断的素材，绝对不是十二票投票。",
        "正文严禁任何投资者昵称、账号、原帖、链接或无关股票案例。",
    ])
    return "\n".join(rows)
