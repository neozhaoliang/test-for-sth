# -*- coding: utf-8 -*-
"""ETF theme/index discovery and transparent scale-based ordering.

A matching fund must be an exchange-traded ETF, not an ETF feeder, LOF,
index mutual fund or a similarly named strategy. Unknown fund assets are
never replaced by turnover, outstanding shares or an invented zero.
"""
from __future__ import annotations

from datetime import date, datetime
import math
import re
import unicodedata
from typing import Any


def key(value: Any) -> str:
    raw = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[\\s·_()（）/\u3000-]+", "", raw)


_GOLD_STOCK = re.compile(r"黄金股|黄金产业|黄金矿业|金矿股|黄金股票")
_CSI300_EXTRAS = re.compile(
    r"增强|价值|成长|红利|低波|低估|质量|等权|策略|因子|esg|smart|"
    r"精选|动量|消费|金融|医药|高股息|现金流|基本面", re.I
)


def theme(query: str) -> str | None:
    q = key(query)
    if ("黄金股" in q or "黄金产业" in q or "黄金矿业" in q or
            "金矿股" in q):
        return "黄金产业股票"
    if ("沪深300" in q or "csi300" in q) and not _CSI300_EXTRAS.search(q):
        return "沪深300"
    return None


def matches(query: str, fund: dict) -> bool:
    q = key(query)
    name = key(fund.get("name"))
    code = str(fund.get("code") or "")
    if not q:
        return False
    # Search by exact/prefix code does not expand to the whole index family.
    if q.isdigit() or re.fullmatch(r"(?:sh|sz)\\d{1,6}", q):
        return code.startswith(q[-6:] if q.startswith(("sh", "sz")) else q)
    group = theme(query)
    if group == "黄金产业股票":
        return bool(_GOLD_STOCK.search(name))
    if group == "沪深300":
        return "沪深300" in name and not _CSI300_EXTRAS.search(name)
    # ETF appears anywhere in a long product name and may precede the manager.
    return q in name or q.replace("etf", "") in name.replace("etf", "")


def assets_yi(shares: Any, unit_nav: Any) -> float | None:
    """NAV(元/份) × outstanding shares(份), expressed in 亿元."""
    try:
        shares, unit_nav = float(shares), float(unit_nav)
        if not all(map(math.isfinite, (shares, unit_nav))) or shares <= 0 or unit_nav <= 0:
            return None
        return round(shares * unit_nav / 1e8, 4)
    except (TypeError, ValueError, OverflowError):
        return None


def recent_day(value: Any, max_age_days: int = 30, today: date | None = None) -> str | None:
    text = str(value or "").strip()[:10].replace("/", "-")
    try:
        day = date.fromisoformat(text)
    except ValueError:
        return None
    now = today or datetime.now().date()
    if 0 <= (now - day).days <= max_age_days:
        return day.isoformat()
    return None


def ranked_matches(query: str, rows: list[dict], limit: int = 40) -> list[dict]:
    selected = [r for r in rows if matches(query, r)]
    # Explicit code is resolved exactly/prefix-first, regardless of scale.
    code_query = bool(re.fullmatch(r"(?:sh|sz)?\\d{1,6}", key(query)))
    if code_query:
        selected.sort(key=lambda r: (r["code"] != key(query)[-6:],
                                     -float(r.get("scale_yi") or 0), r["code"]))
    else:
        selected.sort(key=lambda r: (r.get("scale_yi") is None,
                                     -float(r.get("scale_yi") or 0), r["code"]))
    return selected[:max(1, min(int(limit), 60))]
