"""Evidence-limited management succession / founder dependency assessment.

Only first-party *dated* governance announcement metadata can identify that a
succession-related disclosure exists; a title is not proof of actual succession,
executive competence, founder identity, or a measurable probability of failure.

This is a research-input flag, not an investment rating or mechanical discount.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Iterable, Mapping, Optional
from analysis.verified_governance_events import verified_transition

# Require both an office and an action to reduce unrelated AGM/board notices.
_OFFICE = re.compile(r"董事长|总经理|总裁|首席执行官|CEO|实际控制人|核心管理人员|高管")
_ACTION = re.compile(
    r"辞任|辞职|离任|卸任|退休|换届|改选|选举|聘任|任命|继任|接任|"
    r"交接|变更|更换|调整|去世|逝世|不再担任|解聘|提名"
)
_PLAN = re.compile(r"拟|候选人|提名|草案|议案|计划|预案")
_ANNOUNCEMENT_SOURCE = {"巨潮资讯", "上海证券交易所", "深圳证券交易所", "北京证券交易所"}


def _day(value: Any) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def build_succession_evidence(
    *,
    primary_evidence: Optional[Iterable[Mapping[str, Any]]] = None,
    executive_profile: Optional[Mapping[str, Any]] = None,
    as_of: Optional[date] = None,
    stock_code: str = "",
) -> dict:
    """Surface independently verifiable succession signals, not assumed defects."""
    cutoff = as_of or date.today()
    relevant = []
    seen = set()
    for item in primary_evidence or []:
        if not isinstance(item, Mapping):
            continue
        title = str(item.get("title") or "").strip()
        published = _day(item.get("published_at"))
        if not title or not published or published > cutoff:
            continue
        # Do not treat media / discussion or unsourced metadata as official.
        source = str(item.get("source_name") or "").strip()
        tier = str(item.get("source_tier") or "").upper()
        if source not in _ANNOUNCEMENT_SOURCE and tier != "S":
            continue
        if not (_OFFICE.search(title) and _ACTION.search(title)):
            continue
        unique = item.get("url") or (published.isoformat(), title)
        if unique in seen:
            continue
        seen.add(unique)
        relevant.append({
            "published_at": published.isoformat(),
            "title": title[:220],
            "url": str(item.get("url") or "")[:700] or None,
            "disclosure_stage": "proposal_or_nomination" if _PLAN.search(title)
                                else "announcement_requires_document_check",
            "source_name": source or "一手公告",
        })
    relevant.sort(key=lambda x: x["published_at"], reverse=True)

    transition = verified_transition(stock_code, as_of=cutoff)
    person = (executive_profile or {}).get("chairman")
    verified_chair = (transition or {}).get("chairman_appointed")
    from_profile = str(person).strip()[:70] if person else None
    result_status = ("confirmed_transition" if (transition or {}).get("status") == "completed"
                     else "proposed_transition" if transition else
                     "related_disclosures" if relevant else "no_verified_transition_details")
    return {
        "as_of": cutoff.isoformat(),
        "status": result_status,
        "verified_transition": transition,
        "current_chairman_name": verified_chair or from_profile,
        "f10_chairman_name": from_profile,
        "chairman_source_conflict": bool(verified_chair and from_profile and verified_chair != from_profile),
        "founder_identity_verified": bool(transition),
        "successor_competence_verified": False,
        "related_announcements": relevant[:8],
        "disclosure_count_in_available_evidence": len(relevant),
        "limitations": [
            "现有公告只是有限时间窗和类别下的元数据，未找到相关标题不等于没有交班问题。",
            "标题只能证明存在相关公告；是否真正交接、谁是创始人、继任者业绩必须核对公告正文或年报。",
            "非创始人接任不等于能力下降；没有个体经营业绩证据时不能推断必然平庸或捏造失败概率。",
        ],
        "research_questions": [
            "创始人是否仍控制战略方向、核心客户、研发与资本配置？有无关键人依赖？",
            "董事长与总经理的实际权责如何交接？是否披露继任安排、候选人履历及授权制度？",
            "现任或拟任接班人是否独立经历完整经营周期，是否留下可核验的ROE、现金流和投入资本回报记录？",
            "重大决策是否依赖一人拍板，还是由可持续的治理、激励与人才梯队支撑？",
            "若控制权与战略能力随创始人离任而变化，未来超额ROE持续期和估值安全边际如何调整？",
        ],
    }


def succession_prompt_block(data: Optional[Mapping[str, Any]]) -> str:
    """Mandatory but bounded evidence digest for the management dimension."""
    if not data:
        return "创始人依赖与管理层传承：本次未取得可核验的接班证据，暂不能判断。"
    transition = data.get("verified_transition") or {}
    if transition.get("status") == "completed":
        rows = [
            "创始人依赖与管理层传承：正式换届已完成，不属于资料缺口。",
            f"{transition.get('effective_at')}：创始人{transition.get('founder')}卸任董事长、执行董事；"
            f"{transition.get('chairman_appointed')}当选董事长，{transition.get('president_appointed')}获聘总裁。",
            "卸任创始人继续担任终身荣誉董事长、高级顾问，保留一定战略指导作用，"
            "但不再担任公司法定董事长。",
            f"原始公告日期={transition.get('published_at')}；官方文件={transition.get('source_url')}",
            "换届事实已经证实。新团队能否独立延续完整金铜周期的资本配置、矿山建设、"
            "矿权获取能力，仍需未来经营记录检验，不能预设成功或失败。",
        ]
        if transition.get("corroborating_source_url"):
            rows.append(
                f"复核公告={transition.get('corroborating_source_url')} "
                f"（发布{transition.get('corroborating_published_at')}）"
            )
        if data.get("chairman_source_conflict"):
            rows.append(
                f"旧F10董事长画像为{data.get('f10_chairman_name')}，"
                f"与换届时官方选举结果不一致；不得沿用过期画像。"
            )
        rows.append(
            "论述时必须区分已确认的人事交接和未来独立经营能力的不确定性，"
            "禁止写接班安排或继任者身份资料暂缺。"
        )
        return "\n".join(rows)
    if transition.get("status") == "proposed":
        return (
            "创始人依赖与管理层传承：截至该研究日仅有换届提议，不能当作已经生效。\n"
            f"{transition.get('description')}\n"
            f"官方文件={transition.get('source_url')}。不得预知后来的人事结果。"
        )
    rows = [
        "创始人依赖与管理层传承（管理层评价必须讨论，不可因没有公告而宣称无风险）：",
        f"分析基准日：{data.get('as_of') or '未注明'}；"
        f"现有董事长画像：{data.get('current_chairman_name') or '暂缺'}；"
        "创始人身份/接班人独立业绩：均未由本模块核实。",
    ]
    events = data.get("related_announcements") or []
    if events:
        rows.append("需进一步核验正文的接班相关公告（只代表标题线索）：")
        for item in events[:5]:
            rows.append(
                f"  {item.get('published_at')}: {item.get('title')}"
                f"；状态={item.get('disclosure_stage')}"
                + (f"；链接={item.get('url')}" if item.get("url") else "")
            )
    else:
        rows.append("现有有限公告元数据未检出高管交接标题；不得据此判断没有接班风险。")
    rows.extend(
        "核查方向：" + q for q in (data.get("research_questions") or [])
    )
    rows.append(
        "判断纪律：创始人可能拥有不可复制的战略、技术和文化能力，这是需要核查的关键人风险，"
        "但不能声称创始人离任后存在某个已知失败概率或自动判继任者平庸。"
        "只有实际交接、独立经营业绩和制度化证据充足时才能作有方向的结论；"
        "若证明关键人风险明显，需反映在企业长期质量、超额ROE持续性或情景安全边际的主观判断中，"
        "不得无来源地给贴现率增加固定百分点。"
    )
    return "\n".join(rows)
