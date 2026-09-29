# -*- coding: utf-8 -*-
"""
Point-in-time shareholder-return events from CNINFO announcement metadata.

Historical research must not use today's aggregate dividend/buyback tables because later
implementation progress and final amounts can overwrite what was known at the cutoff date.

This module converts only announcements already present in the as-of-filtered CNINFO
primary-evidence ledger into conservative event records.
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple


_DIVIDEND_PATTERNS = (
    re.compile(r"每\s*10\s*股[^\d]{0,20}派(?:发)?(?:现金红利)?\s*([\d.]+)\s*元"),
    re.compile(r"10\s*派\s*([\d.]+)\s*元?"),
)

_BUYBACK_RE = re.compile(r"回购")


def _date(item: Dict) -> str:
    return str(item.get("published_at") or "")[:10]


def _dividend_amount_per_10(title: str):
    for pattern in _DIVIDEND_PATTERNS:
        m = pattern.search(title or "")
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
    return None


def _dividend_progress(title: str) -> str:
    text = title or ""
    if any(x in text for x in ("实施公告", "实施方案", "派发实施")):
        return "implemented"
    if any(x in text for x in ("预案", "利润分配方案", "分配方案")):
        return "proposal"
    if "权益分派" in text:
        return "announced"
    return "unknown"


def _buyback_progress(title: str) -> str:
    text = title or ""
    if any(x in text for x in ("完成回购", "回购结果", "回购完成")):
        return "completed"
    if any(x in text for x in ("回购进展", "进展公告")):
        return "in_progress"
    if any(x in text for x in ("回购报告书", "回购方案", "回购股份方案")):
        return "proposal"
    return "announced"


def build_point_in_time_capital_returns(
    primary_evidence: List[Dict],
) -> Tuple[List[Dict], List[Dict]]:
    """
    Return (dividend_history, buyback_history) using only already-filtered announcements.

    Empty output means "no captured announcement in this evidence window", not proof that
    the company never paid a dividend or never repurchased shares.
    """
    dividends: List[Dict] = []
    buybacks: List[Dict] = []
    seen_dividend = set()
    seen_buyback = set()

    for item in primary_evidence or []:
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        published = _date(item)
        url = item.get("url")
        category = str(item.get("category") or "")

        if category == "权益分派" or "权益分派" in title or "利润分配" in title:
            key = (published, title)
            if key not in seen_dividend:
                seen_dividend.add(key)
                dividends.append(
                    {
                        "announce_date": published,
                        "available_at": published or None,
                        "dividend_per_10_shares": _dividend_amount_per_10(title),
                        "progress": _dividend_progress(title),
                        "title": title,
                        "url": url,
                        "source_tier": "S",
                        "point_in_time": True,
                    }
                )

        if _BUYBACK_RE.search(title):
            key = (published, title)
            if key not in seen_buyback:
                seen_buyback.add(key)
                buybacks.append(
                    {
                        "announce_date": published,
                        "available_at": published or None,
                        "progress": _buyback_progress(title),
                        "planned_amount_range": [None, None],
                        "actual_amount": None,
                        "title": title,
                        "url": url,
                        "source_tier": "S",
                        "point_in_time": True,
                    }
                )

    dividends.sort(key=lambda x: x.get("announce_date") or "", reverse=True)
    buybacks.sort(key=lambda x: x.get("announce_date") or "", reverse=True)
    return dividends, buybacks
