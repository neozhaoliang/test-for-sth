"""Resource-miner thesis context: dated KOL mechanisms, holder history and capital-cycle risks.

Never turn an investor's theory into a market fact, and never expose private
investor identities/posts in the final public-facing research narrative.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Iterable, Mapping

_MINING = re.compile(r"矿业|采矿|矿山|矿产|有色|贵金属|黄金|铜矿|金矿|锂矿|钴矿|镍矿|铅锌|稀土|冶金|矿物", re.I)
_NON_MINING = re.compile(r"黄金珠宝|珠宝首饰|珠宝零售|首饰制造")
_RESOURCE = re.compile(r"大宗商品|有色|金属|贵金属|金价|黄金|铜价|铜矿|矿山|矿业|资源股|原油|锂价|商品价格", re.I)
_MONEY = re.compile(r"货币现象|货币属性|流动性|货币供应|货币宽松|货币紧缩|央行|加息|降息|实际利率|美元指数|美元走强|缩表|信用扩张|信用收缩|货币政策", re.I)
_SUMMARY_FIELDS = {
    "monetary": re.compile(r"货币|流动性|利率|美元|金融条件"),
    "holders": re.compile(r"股东户数|持股账户|股东人数|筹码|持有人分散"),
    "capex": re.compile(r"扩张|扩产|资本开支|资本配置|矿山投资|并购|项目投入"),
    "succession": re.compile(r"接班|继任|接任|卸任|换届|传承|关键人|创始人|管理层"),
}
_REDACT = re.compile(r"买股票的老木匠|军师祭咖啡|陈chensir|双木林叔|https?://|\b(?:SH|SZ|BJ)\d{6}\b", re.I)


def is_resource_miner(*, archetype: str = "", industry: str = "", stock_name: str = "") -> bool:
    label = " ".join((str(industry or ""), str(stock_name or "")))
    return (
        str(archetype or "") == "cyclical"
        and bool(_MINING.search(label))
        and not bool(_NON_MINING.search(label))
    )


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except (ValueError, TypeError):
        return None


def select_resource_monetary_excerpts(
    excerpts: Iterable, *, as_of: str = "", limit: int = 8,
) -> list:
    """Protect older resource-money-cycle reasoning from a relevance-model miss.

    Only original, dated and point-in-time-valid excerpts enter the *private*
    research context. No author name, URL, original quote or account is output.
    """
    cutoff = _day(as_of) or date.today()
    items = []
    for e in excerpts or []:
        when = _day(getattr(e, "published_at", ""))
        if when is None or when > cutoff:
            continue
        body = (str(getattr(e, "title", "") or "") + " " +
                str(getattr(e, "distilled", "") or ""))
        if not (_RESOURCE.search(body) and _MONEY.search(body)):
            continue
        score = sum(bool(re.search(word, body, re.I)) for word in (
            r"货币现象|货币属性|货币供应",
            r"流动性|利率|加息|降息|缩表",
            r"供需|成本|利润|周期|价格|高位",
        ))
        items.append((score, when.toordinal(), e))
    items.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [e for _, _, e in items[:max(0, limit)]]


def select_resource_succession_excerpts(
    excerpts: Iterable, *, as_of: str = "", limit: int = 5,
) -> list:
    """Recall dated private research on a named miner's founder/succession.

    These are hypothesis prompts, NOT proof that a formal resignation occurred.
    The actual officer facts always come from first-party filings.
    """
    cutoff = _day(as_of) or date.today()
    relevant = []
    for item in excerpts or []:
        published = _day(getattr(item, "published_at", ""))
        if published is None or published > cutoff:
            continue
        body = str(getattr(item, "title", "") or "") + " " + str(
            getattr(item, "distilled", "") or ""
        )
        if not re.search(r"紫金矿业|陈景河|邹来昌", body):
            continue
        if not re.search(r"创始人|接班|继任|退休|卸任|换届|管理层|董事长|经营传承", body):
            continue
        relevant.append((published, item))
    relevant.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in relevant[:max(0, limit)]]


def _holder_trajectory(inputs: Any) -> dict:
    """Pick a historical low-to-later-high in dated holder observations.

    The current 52-week price position is *not* used to erase an earlier
    run-up-and-dispersion episode. Holder counts are accounts, not persons.
    """
    chip = getattr(inputs, "chip_price_context", None) or {}
    observations = chip.get("price_holder_series") or (
        (getattr(inputs, "shareholder_trend", None) or {}).get("series") or []
    )
    cutoff = _day(getattr(inputs, "as_of", None))
    rows = {}
    for o in observations:
        if not isinstance(o, Mapping):
            continue
        period = _day(o.get("period"))
        available = _day(o.get("holder_published_at") or o.get("available_at") or o.get("published_at"))
        if not period or (cutoff and period > cutoff):
            continue
        if str(getattr(inputs, "research_mode", "")) == "historical" and (
            not available or available > cutoff
        ):
            continue
        try:
            count = int(o.get("holders"))
        except (ValueError, TypeError, OverflowError):
            continue
        if count <= 0:
            continue
        rows[period] = {"period": period, "holders": count, "price": o.get("price")}
    ordered = [rows[k] for k in sorted(rows)]
    if len(ordered) < 2:
        return {}
    best = None
    for later_idx in range(1, len(ordered)):
        end = ordered[later_idx]
        prior = min(ordered[:later_idx], key=lambda x: x["holders"])
        rise_pct = (end["holders"] / prior["holders"] - 1) * 100
        if best is None or rise_pct > best["holder_rise_pct"]:
            best = {
                "start": prior["period"].isoformat(), "end": end["period"].isoformat(),
                "start_holders": prior["holders"], "end_holders": end["holders"],
                "holder_rise_pct": round(rise_pct, 2),
                "start_price": prior["price"], "end_price": end["price"],
            }
    return best or {}


def resource_miner_prompt_block(inputs: Any) -> str:
    """Investor reasoning reminder tied to actual report inputs, never a sell rule."""
    profile = getattr(inputs, "research_profile", None)
    if not is_resource_miner(
        archetype=getattr(profile, "archetype", ""),
        industry=getattr(profile, "industry", ""),
        stock_name=getattr(inputs, "stock_name", ""),
    ):
        return ""
    excerpts = select_resource_monetary_excerpts(
        getattr(inputs, "knowledge_excerpts", ()) or (),
        as_of=str(getattr(inputs, "as_of", "") or ""),
    )
    holder = _holder_trajectory(inputs)
    succession_private = select_resource_succession_excerpts(
        getattr(inputs, "knowledge_excerpts", ()) or (),
        as_of=str(getattr(inputs, "as_of", "") or ""),
    )
    succession_record = (
        (getattr(inputs, "management_capital", None) or {}).get("succession") or {}
    )
    confirmed = succession_record.get("verified_transition") or {}
    lines = [
        "## 矿业/有色：影响买入赔率的行业特殊矛盾（仅是研究问题，不自动看空）",
        "货币与商品：有色及黄金有金融/货币属性，不能只按产量、矿石品位和终端供需推断价格。"
        "从美元流动性、名义/实际利率、美元汇率、通胀预期与商品定价的传导分析，"
        "同时考虑铜/锂的工业供需和黄金避险/储备需求。'商品只是货币现象、与供需无关'不是确定事实。",
        (
            "本地知识库有截至研究日可用的资源/货币观点；请将其作为可质疑的"
            "经济机制，并使用本次有日期的宏观及商品数据检验。"
            if excerpts else
            "本次筛选未保留可用的资源/货币知识库条目；仅作行业机制检验，"
            "不得伪称已读到特定投资者原话。"
        ),
        "宏观时点：本次可参考美国目标利率、美国国债收益率、中国LPR，以及另行获取的"
        "日本/欧洲央行有日期的决议记录；后两者是二级数据，必须留意时效与公告口径。"
        "没有有效政策利率变动值就不得断言某央行正在加息或全球已进入统一紧缩周期。"
        "不能把高利率水平当成刚加息，也不能把'预期'说成已经发生。",
        "利润穿透：区分产量增长、资源品售价上涨、汇率和单位成本变化导致的利润。"
        "商品价格回落时应检查归母利润、毛利率、经营现金流和净负债的敏感度，"
        "不能因为上半年财报漂亮就默认为可持续盈利。",
        "扩张周期：矿业景气高位新建矿山/并购可能抬高矿权和建造成本，"
        "建设期现金流出却先于投产现金流入；必须核查有日期的实际资本开支、"
        "在建项目、购矿对价、维持性与扩张性支出、资金来源、投产时间和中周期ROIC。"
        "资料缺失时只说明暴露于高位扩张的风险假设，不得把尚未核实的支出称为事实。",
        "接班与关键人：对长期依赖核心管理者进行矿权收购、海外协调、"
        "成本控制和资本配置的公司，要检验继任团队是否经历过完整金属周期。"
        "现任管理层任职和派息优秀不能证明交班后业绩同样优秀；缺少交接公告与继任者"
        "独立经营记录时，明确这是未解决的能力连续性风险，不可编造交接已经完成。",
    ]
    if succession_private:
        lines.append(
            "本地知识库确有该矿业公司创始人/换届/接班相关历史研究；"
            "其机制与投资提醒应参与核验，但不得替代正式公告，"
            "不得对外引用原帖、账号和来源。"
        )
    if confirmed.get("status") == "completed":
        lines.append(
            f"已经证实的人事交接：{confirmed.get('effective_at')}，"
            f"陈景河卸任董事长及执行董事；邹来昌当选董事长、林泓富获聘总裁；"
            "陈景河继续任终身荣誉董事长、高级顾问。"
            f"第一手证据：{confirmed.get('source_url')}。"
            "交接事实不是缺口；尚待验证的是继任团队在完整金铜周期中的独立资本配置与管理能力。"
        )
    if holder and holder["holder_rise_pct"] >= 40:
        lines.append(
            f"股东户数历史阶段：{holder['start']}的{holder['start_holders']:,}户"
            f"→{holder['end']}的{holder['end_holders']:,}户"
            f"（+{holder['holder_rise_pct']:.2f}%）；"
            "需要判断此阶段是否曾出现股价上涨伴随增户、随后价格回撤但户数继续上升。"
            "注意账户数不等于散户人数，也不能据此断言机构派发。"
        )
        if holder["start_price"] is not None and holder["end_price"] is not None:
            lines.append(
                f"上述期末配对交易日价格：{holder['start_price']}元→{holder['end_price']}元；"
                "原始未复权价仅用于历史同步性观察，不能替代严格复权收益率。"
            )
    else:
        lines.append("股东户数大幅分散的可比历史序列不足；不编造数值或仅凭绝对户数看空。")
    lines.append(
        "最终结论需要主次分明地解释货币/实际利率传导、历史筹码结构、"
        "周期高位扩张风险和管理层关键人风险的相对重要性；"
        "有可靠数据要引用，无数据要承认缺口。不因四个问题的数量机械否定企业质量。"
        "必须区别'这家公司是好企业'与'这个价格买入的期望回报'。"
    )
    return "\n".join(lines)


def resource_summary_omissions(text: str, inputs: Any) -> list[str]:
    """Diagnose only; never change the verdict or insert unproved hard facts."""
    if not resource_miner_prompt_block(inputs):
        return []
    omissions = []
    has_macro_knowledge = bool(select_resource_monetary_excerpts(
        getattr(inputs, "knowledge_excerpts", ()) or (),
        as_of=str(getattr(inputs, "as_of", "") or ""),
    ))
    if has_macro_knowledge and not _SUMMARY_FIELDS["monetary"].search(text or ""):
        omissions.append("monetary")
    if _holder_trajectory(inputs).get("holder_rise_pct", 0) >= 100 and not _SUMMARY_FIELDS["holders"].search(text or ""):
        omissions.append("holders")
    for key in ("capex", "succession"):
        if not _SUMMARY_FIELDS[key].search(text or ""):
            omissions.append(key)
    confirmed = ((getattr(inputs, "management_capital", None) or {})
                 .get("succession") or {}).get("verified_transition") or {}
    if confirmed.get("status") == "completed":
        stated = str(text or "")
        names = (str(confirmed.get("founder") or ""),
                 str(confirmed.get("chairman_appointed") or ""))
        if not all(name and name in stated for name in names):
            if "succession" not in omissions:
                omissions.append("succession")
    return omissions


async def repair_resource_summary(parsed: dict, inputs: Any) -> dict:
    """One optional revision of prose only; preserve model stance and other fields."""
    missing = resource_summary_omissions(str(parsed.get("thesis_summary") or ""), inputs)
    if not missing:
        return parsed
    try:
        from backtest.llm_client import call_json_ex
        prompt = (
            "请仅重写投资结论的自然语言解释，不更改原有的综合倾向、公司质量、"
            "赔率观点、任何价格或数字。以下有数据依据的矿业行业研究问题在原结论中缺失："
            + ", ".join(missing) + "。"
            "请用通常4—6个自然段、约450—900字把投资判断讲清楚，必要时可以更长。"
            "首段明确给出公司质量、现价赔率与立场，其后用公司实际数据解释为什么；"
            "避免数字、缩写、多个风险名词挤成一段，不能只把风险清单串成一句话。"
            "重点是回答和解释的语气，而不是追求最短摘要；"
            "覆盖货币和商品价格的条件关系、股东户数历史路径、"
            "高位扩张资本成本、接班能力延续风险四方面。"
            "若具体事实缺失，明确标为风险假设或资料缺口，不可编造央行政策、矿山项目、人物或公告；"
            "既不机械看空也不改变原始倾向；不提作者、原帖、内部工具和其他公司。"
            "\n\n本次研究依据：\n" + resource_miner_prompt_block(inputs) +
            "\n\n原投资结论：\n" + str(parsed.get("thesis_summary") or "") +
            "\n\n只返回JSON对象：{\"thesis_summary\":\"新结论\"}。"
        )
        result, _ = await call_json_ex(
            prompt, max_tokens=3000,
            repair_requirements="必须只返回包含thesis_summary字符串的JSON对象，不得修改立场或引用私有账号。",
        )
        revised = result.get("thesis_summary") if isinstance(result, dict) else None
        if not isinstance(revised, str) or not 100 <= len(revised) <= 3000:
            return parsed
        if _REDACT.search(revised):
            return parsed
        # Refuse a rewrite that drops instead of covers the salient missing risks.
        if any(not _SUMMARY_FIELDS[k].search(revised) for k in missing):
            return parsed
        verified = (((getattr(inputs, "management_capital", None) or {})
                    .get("succession") or {}).get("verified_transition") or {})
        if verified.get("status") == "completed":
            # A rewrite that omits or contradicts the named, completed
            # transition must not replace a more accurate draft.
            required = (
                str(verified.get("founder") or ""),
                str(verified.get("chairman_appointed") or ""),
            )
            if not all(name and name in revised for name in required):
                return parsed
        return {**parsed, "thesis_summary": revised}
    except Exception:
        return parsed
