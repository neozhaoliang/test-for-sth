"""Dated first-party governance fact cards, not analyst opinions.

Small audited fallback registry for MATERIAL, ALREADY CONFIRMED events whose
CNINFO announcement titles or short result windows can miss the formal outcome.
New entries MUST supply public primary-source URLs, announcement date, effective
date, role and scope. This is not a live-current-officer database: facts say
who was elected as of the specified board meeting, not who holds the role
forever. Historical reports must never look past their cutoff.
"""
from __future__ import annotations

from datetime import date
from typing import Optional


# The Dec 31 HKEX filing confirms all three people and is available that day.
# Company's Jan 1 Chinese announcement is a corroborating, later source.
# Proposed retirement on Nov 28 is NOT equivalent to completed turnover.
_VERIFIED_TRANSITIONS = {
    "601899": {
        "proposal": {
            "published_at": "2025-11-28",
            "status": "proposed",
            "company": "紫金矿业",
            "founder": "陈景河",
            "description": (
                "陈景河因年龄和家庭原因不再接受第九届董事会董事候选人提名；"
                "拟聘为终身荣誉董事长、高级顾问。提案时尚未完成换届。"
            ),
            "source_name": "香港交易所上市公司原始公告",
            "source_url": "https://www1.hkexnews.hk/listedco/listconews/sehk/2025/1128/2025112802986.pdf",
        },
        "confirmed": {
            "published_at": "2025-12-31",
            "effective_at": "2025-12-31",
            "status": "completed",
            "company": "紫金矿业",
            "founder": "陈景河",
            "founder_departed_roles": ["董事长", "执行董事"],
            "founder_new_roles": ["终身荣誉董事长", "高级顾问"],
            "chairman_appointed": "邹来昌",
            "president_appointed": "林泓富",
            "continued_advisory_role": True,
            "description": (
                "2025年12月31日完成第九届董事会换届。创始人陈景河不再担任执行董事、董事长，"
                "经股东会同意聘为终身荣誉董事长、高级顾问，继续在重大战略与资源协调方面提供支持；"
                "邹来昌当选董事长，林泓富获聘总裁。"
            ),
            "source_name": "香港交易所上市公司董事会换届公告",
            "source_url": "https://www1.hkexnews.hk/listedco/listconews/sehk/2025/1231/2025123102223.pdf",
            "corroborating_source_url": "https://static.cninfo.com.cn/finalpage/2026-01-01/1224914995.PDF",
            "corroborating_published_at": "2026-01-01",
            "interpretation_limit": (
                "换届结果是已确认事实，继任团队能否独立延续完整金铜周期的投资回报不是已证事实。"
                "荣誉董事长/高级顾问不是法定董事长或执行董事，亦不能据此断言创始人已彻底退出战略影响。"
            ),
        },
    },
}


def verified_transition(
    stock_code: str, *, as_of: Optional[date] = None,
) -> Optional[dict]:
    """Select the most recent available verified transition stage, cutoff safe.

    A proposal is explicitly identified as pending until a dated completion
    record is published. Only domestic six-digit SH601899 is registered here.
    """
    raw = str(stock_code or "").upper().strip()
    if not (raw in ("SH601899", "601899")):
        return None
    record = _VERIFIED_TRANSITIONS.get("601899")
    if not record:
        return None
    day = as_of or date.today()
    if day >= date.fromisoformat(record["confirmed"]["published_at"]):
        item = dict(record["confirmed"])
        # The supplementary CNINFO document was released Jan 1, not Dec 31.
        if day < date.fromisoformat(item["corroborating_published_at"]):
            item.pop("corroborating_source_url", None)
            item.pop("corroborating_published_at", None)
        return item
    if day >= date.fromisoformat(record["proposal"]["published_at"]):
        return dict(record["proposal"])
    return None
