"""Founder key-person and management succession must be investigated, not invented."""
from datetime import date

from analysis.succession_risk import (
    build_succession_evidence,
    succession_prompt_block,
)
from analysis.management_capital import build_management_capital_record
from analysis.report_blocks import _build_management_capital_block
from analysis import report_contract


def official(title, date_string, *, url=None, tier="S", source="巨潮资讯"):
    return {
        "title": title, "published_at": date_string, "url": url,
        "source_name": source, "source_tier": tier,
    }


def test_related_governance_titles_flagged_but_not_called_confirmed_succession():
    rows = [
        official("关于董事长辞职及聘任新任董事长的公告", "2025-09-18 08:30:00",
                 url="https://example.org/official/1"),
        official("关于董事长辞职及聘任新任董事长的公告", "2025-09-18",
                 url="https://example.org/official/1"),
        official("关于提名总经理候选人的公告", "2025-08-20"),
        official("关于核心产品调整的公告", "2025-08-21"),
        official("关于选举独立董事的公告", "2025-08-19"),
        official("关于总经理辞职的公告", "2027-01-01"),
        official("公司公开讨论：创始人退位", "2025-08-25",
                 source="雪球帖子", tier="C"),
    ]
    result = build_succession_evidence(
        primary_evidence=rows,
        executive_profile={"chairman": "陈某"},
        as_of=date(2025, 10, 1),
    )
    assert result["status"] == "related_disclosures"
    assert result["disclosure_count_in_available_evidence"] == 2
    assert len(result["related_announcements"]) == 2
    assert result["related_announcements"][0]["published_at"] == "2025-09-18"
    assert result["related_announcements"][1]["disclosure_stage"] == "proposal_or_nomination"
    assert result["founder_identity_verified"] is False
    assert result["successor_competence_verified"] is False
    assert result["current_chairman_name"] == "陈某"


def test_out_of_window_and_undated_items_must_not_create_hindsight():
    result = build_succession_evidence(
        primary_evidence=[
            official("关于总经理辞任的公告", "2026-10-20"),
            official("关于董事长调整的公告", ""),
            official("关于总经理任免的公告", "2025-02-01", source="市场媒体", tier="B"),
        ],
        as_of=date(2026, 10, 13),
    )
    assert result["status"] == "no_verified_transition_details"
    assert result["related_announcements"] == []
    assert any("不等于没有交班问题" in x for x in result["limitations"])


def test_management_capital_contains_succession_and_prompt_requires_real_evidence():
    record = build_management_capital_record(
        executive_profile={"chairman": "某董事长", "joined_year": "2005"},
        primary_evidence=[
            official("关于公司总经理辞职及聘任新任总经理的公告", "2026-03-02",
                     url="https://example.org/3"),
        ],
        as_of=date(2026, 10, 13),
    )
    assert record["succession"]["status"] == "related_disclosures"
    prompt = _build_management_capital_block(record)
    assert "创始人依赖与管理层传承" in prompt
    assert "2026-03-02" in prompt
    assert "关于公司总经理辞职" in prompt
    assert "独立经历完整经营周期" in prompt
    assert "不得无来源地给贴现率增加固定百分点" in prompt


def test_unavailable_succession_facts_are_explicit_unknown_not_guessed():
    text = succession_prompt_block(build_succession_evidence(
        primary_evidence=[], as_of=date(2026, 10, 13),
    ))
    assert "未检出高管交接标题" in text
    assert "不得据此判断没有接班风险" in text
    assert "不能声称创始人离任后存在某个已知失败概率" in text
    assert "一手公告" not in text or "有限" in text
    assert "创始人依赖" in _build_management_capital_block(None)


def test_llm_management_tool_and_main_prompt_enforce_succession_without_rigid_penalty():
    assert "succession" in report_contract._PROMPT_VERSION
    description = next(item["description"] for item in report_contract._ANALYSIS_TOOLS
                       if item["name"] == "analyze_management")
    assert "接班传承" in description
    assert "创始人" in description
    assert "接班人身份/安排/能力暂缺" in description
    assert "不得机械给继任者扣分" in description
    prompt = report_contract._PROMPT_TEMPLATE
    assert "接班传承是管理层必查事项" in prompt
    assert "不能将“大概率平庸”当作无证据的统计事实" in prompt
