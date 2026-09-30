# -*- coding: utf-8 -*-
"""
Deterministic management / capital-allocation record.

The purpose is to turn scattered governance, dividend, buyback and refinancing facts into
one auditable record.  It does not assign a moral label to management and it does not
forecast stock returns.  The same underlying event is grouped once so the synthesis layer
does not double-count it across "management" and "shareholder return" dimensions.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Dict, Iterable, List, Optional


_NEGATIVE_GOV_RE = re.compile(r"立案|处罚|警示|违规|调查|问询|谴责|处分|诉讼|仲裁")
_INSIDER_REDUCTION_RE = re.compile(r"减持|减持计划|减持股份")
_REFINANCING_RE = re.compile(r"定增|增发|配股|可转债|发行股份|发行股票|再融资|募集资金")
_DIVIDEND_RE = re.compile(r"分红|利润分配|权益分派|现金红利")
_BUYBACK_RE = re.compile(r"回购")


def _date_year(value) -> Optional[int]:
    if value is None:
        return None
    s = str(value)
    m = re.search(r"(20\d{2}|19\d{2})", s)
    return int(m.group(1)) if m else None


def _num(value) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        if value != value:
            return None
    except Exception:
        pass
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _window_years(window: int, as_of_year: int) -> set[int]:
    return set(range(as_of_year - window + 1, as_of_year + 1))


def _primary_matches(primary: Iterable[Dict], pattern: re.Pattern) -> List[Dict]:
    rows: List[Dict] = []
    seen = set()
    for item in primary or []:
        text = f"{item.get('category','')} {item.get('title','')}"
        if not pattern.search(text):
            continue
        key = item.get("url") or f"{item.get('published_at','')}|{item.get('title','')}"
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "published_at": item.get("published_at"),
                "category": item.get("category"),
                "title": item.get("title"),
                "url": item.get("url"),
                "source_name": item.get("source_name") or "巨潮资讯",
            }
        )
    rows.sort(key=lambda x: str(x.get("published_at") or ""), reverse=True)
    return rows


def _is_realized_dividend(row: Dict) -> bool:
    """
    Decide whether a dividend record is evidence of an actually implemented distribution.

    Point-in-time CNINFO records carry an explicit progress field. A proposal with a stated
    amount is still only a proposal and must not be counted as paid. Legacy/live aggregate
    rows may lack progress; for those, a positive per-10-share cash amount is treated as a
    realized historical record.
    """
    progress = str(row.get("progress") or "").strip().lower()
    if progress:
        return progress in {"implemented", "completed", "paid"}
    return (_num(row.get("dividend_per_10_shares")) or 0) > 0


def _summarize_window(
    *,
    window: int,
    as_of_year: int,
    dividend_history: List[Dict],
    buyback_history: List[Dict],
    refinancing_history: List[Dict],
    primary_evidence: List[Dict],
) -> Dict:
    years = _window_years(window, as_of_year)

    div_rows = [
        d for d in dividend_history
        if _date_year(d.get("announce_date")) in years
        and _is_realized_dividend(d)
    ]
    dividend_years = sorted(
        {
            _date_year(d.get("announce_date"))
            for d in div_rows
            if _date_year(d.get("announce_date"))
        },
        reverse=True,
    )
    known_amount_rows = [
        d for d in div_rows
        if (_num(d.get("dividend_per_10_shares")) or 0) > 0
    ]
    # None means "realized dividends exist but the captured metadata did not disclose the
    # per-10-share amount". Returning 0 here would incorrectly imply no cash distribution.
    div_total_per10 = (
        round(
            sum(
                _num(d.get("dividend_per_10_shares")) or 0.0
                for d in known_amount_rows
            ),
            4,
        )
        if known_amount_rows
        else (0.0 if not div_rows else None)
    )

    buy_rows = [
        b for b in buyback_history
        if _date_year(b.get("announce_date")) in years
    ]
    buyback_actual = sum(_num(b.get("actual_amount")) or 0.0 for b in buy_rows)

    refi_rows = [
        r for r in refinancing_history
        if _date_year(r.get("announce_date")) in years
    ]

    primary_in_window = [
        p for p in primary_evidence
        if _date_year(p.get("published_at")) in years
    ]
    reductions = _primary_matches(primary_in_window, _INSIDER_REDUCTION_RE)
    governance_negatives = _primary_matches(primary_in_window, _NEGATIVE_GOV_RE)
    primary_refi = _primary_matches(primary_in_window, _REFINANCING_RE)
    primary_dividends = _primary_matches(primary_in_window, _DIVIDEND_RE)
    primary_buybacks = _primary_matches(primary_in_window, _BUYBACK_RE)

    return {
        "window_years": window,
        "calendar_years": sorted(years),
        "dividend_years_count": len(dividend_years),
        "dividend_years": dividend_years,
        "cash_dividend_per_10_total": div_total_per10,
        "dividend_records": len(div_rows),
        "cash_dividend_amount_records": len(known_amount_rows),
        "cash_dividend_amount_complete": len(known_amount_rows) == len(div_rows),
        "buyback_records": len(buy_rows),
        "buyback_actual_amount_yuan": round(buyback_actual, 2),
        "refinancing_records": len(refi_rows),
        "insider_reduction_announcements": len(reductions),
        "governance_negative_announcements": len(governance_negatives),
        "primary_refinancing_announcements": len(primary_refi),
        "primary_dividend_announcements": len(primary_dividends),
        "primary_buyback_announcements": len(primary_buybacks),
    }


def _execution_record(profitability_trend: Optional[Dict]) -> Dict:
    periods = (profitability_trend or {}).get("periods") or []
    if not periods:
        return {}

    valid_roe = [
        _num(p.get("roe_pct")) for p in periods
        if _num(p.get("roe_pct")) is not None
    ]
    valid_growth = [
        _num(p.get("net_profit_growth_pct")) for p in periods
        if _num(p.get("net_profit_growth_pct")) is not None
    ]
    valid_net_margin = [
        _num(p.get("net_margin_pct")) for p in periods
        if _num(p.get("net_margin_pct")) is not None
    ]

    out: Dict = {
        "period_start": periods[0].get("period"),
        "period_end": periods[-1].get("period"),
        "period_count": len(periods),
    }
    if valid_roe:
        out["roe_start_pct"] = round(valid_roe[0], 2)
        out["roe_latest_pct"] = round(valid_roe[-1], 2)
        out["roe_change_pp"] = round(valid_roe[-1] - valid_roe[0], 2)
        out["roe_min_pct"] = round(min(valid_roe), 2)
        out["roe_max_pct"] = round(max(valid_roe), 2)
    if valid_growth:
        out["net_profit_growth_positive_periods"] = sum(1 for x in valid_growth if x > 0)
        out["net_profit_growth_observations"] = len(valid_growth)
        out["net_profit_growth_latest_pct"] = round(valid_growth[-1], 2)
    if valid_net_margin:
        out["net_margin_latest_pct"] = round(valid_net_margin[-1], 2)
        out["net_margin_change_pp"] = round(valid_net_margin[-1] - valid_net_margin[0], 2)
    return out


def build_management_capital_record(
    *,
    dividend_history: Optional[List[Dict]] = None,
    buyback_history: Optional[List[Dict]] = None,
    refinancing_history: Optional[List[Dict]] = None,
    primary_evidence: Optional[List[Dict]] = None,
    executive_profile: Optional[Dict] = None,
    profitability_trend: Optional[Dict] = None,
    as_of: Optional[date] = None,
) -> Dict:
    """
    Build one evidence record used by both management-quality and shareholder-return analysis.

    No scalar "management score" is produced on purpose.  A high dividend record can coexist
    with weak execution or governance issues, so downstream synthesis should cite the facts.
    """
    dividend_history = dividend_history or []
    buyback_history = buyback_history or []
    refinancing_history = refinancing_history or []
    primary_evidence = primary_evidence or []
    executive_profile = executive_profile or {}
    as_of = as_of or datetime.now().date()
    as_of_year = as_of.year

    joined_year = executive_profile.get("joined_year")
    tenure_years = None
    try:
        if joined_year:
            joined_year = int(joined_year)
            if 1950 <= joined_year <= as_of_year:
                tenure_years = as_of_year - joined_year
    except (TypeError, ValueError):
        tenure_years = None

    primary_reductions = _primary_matches(primary_evidence, _INSIDER_REDUCTION_RE)
    primary_governance_negatives = _primary_matches(primary_evidence, _NEGATIVE_GOV_RE)
    primary_refinancing = _primary_matches(primary_evidence, _REFINANCING_RE)

    alignment = {
        "chairman": executive_profile.get("chairman"),
        "joined_year": joined_year,
        "tenure_years": tenure_years,
        "chairman_salary_wan": _num(executive_profile.get("chairman_salary_wan")),
        "chairman_shares": executive_profile.get("chairman_shares"),
        "chairman_education": executive_profile.get("chairman_education"),
    }

    notes: List[str] = []
    if not executive_profile:
        notes.append("高管任期/薪酬/持股资料暂缺，不能据此评价利益绑定。")
    if not primary_evidence:
        notes.append("本次未取得巨潮治理/资本运作公告，治理记录完整性受限。")

    return {
        "as_of": as_of.isoformat(),
        "alignment": alignment,
        "execution": _execution_record(profitability_trend),
        "five_year": _summarize_window(
            window=5,
            as_of_year=as_of_year,
            dividend_history=dividend_history,
            buyback_history=buyback_history,
            refinancing_history=refinancing_history,
            primary_evidence=primary_evidence,
        ),
        "ten_year": _summarize_window(
            window=10,
            as_of_year=as_of_year,
            dividend_history=dividend_history,
            buyback_history=buyback_history,
            refinancing_history=refinancing_history,
            primary_evidence=primary_evidence,
        ),
        "recent_insider_reduction_events": primary_reductions[:8],
        "recent_governance_negative_events": primary_governance_negatives[:8],
        "recent_primary_refinancing_events": primary_refinancing[:8],
        "notes": notes,
        "source_tier": "A" if primary_evidence else "B",
        "method": (
            "巨潮一手公告优先；分红/回购历史与F10再融资用于长期资本分配统计。"
            "同一事件在管理层与股东回报分析中共享，不作为两份独立证据重复计分。"
        ),
    }
