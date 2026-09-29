# -*- coding: utf-8 -*-
"""
A-share capital structure / institutional ownership signals.

Only public, reproducible data is used.  This module does not infer unnamed "main force"
activity.  It summarizes disclosed institutional holdings and notable top-10 free-float
shareholders, including public funds, social-security funds, insurance, QFII/foreign
holders, and publicly disclosed state-capital holders such as Central Huijin / CSF /
China Reform / Chengtong when they actually appear in the shareholder list.
"""

from __future__ import annotations

import asyncio
import re
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import akshare as ak

from tools.utils import utils


_FETCH_TIMEOUT_S = 15

_SPECIAL_HOLDER_PATTERNS = {
    "national_team": re.compile(
        r"中央汇金|中国证券金融|证金|国新投资|国新央企|中国国新|诚通金控|中国诚通"
    ),
    "social_security": re.compile(r"全国社保|社保基金"),
    "insurance": re.compile(r"保险|人寿|太平洋人寿|新华人寿|泰康|太保寿险|平安人寿"),
    "foreign": re.compile(
        r"香港中央结算|QFII|挪威中央银行|阿布达比|新加坡政府|摩根|高盛|瑞银|花旗|美林"
    ),
    "public_fund": re.compile(r"证券投资基金|基金管理|基金－|基金-"),
}

_INSTITUTION_TYPES = ("基金", "全国社保", "QFII", "保险")


def _bare_code(stock_code: str) -> str:
    return re.sub(r"\D", "", stock_code)[-6:]


def _em_symbol(code6: str) -> str:
    if code6.startswith(("6", "9")):
        return f"sh{code6}"
    if code6.startswith(("0", "3")):
        return f"sz{code6}"
    if code6.startswith(("4", "8")):
        return f"bj{code6}"
    return code6


def _quarter_candidates(today: date, limit: int = 6) -> List[Tuple[str, str]]:
    """Return newest completed quarter-end candidates as (quarter_code, yyyymmdd)."""
    rows: List[Tuple[date, str, str]] = []
    for year in range(today.year - 2, today.year + 1):
        for q, month, day in ((1, 3, 31), (2, 6, 30), (3, 9, 30), (4, 12, 31)):
            d = date(year, month, day)
            if d <= today:
                rows.append((d, f"{year}{q}", d.strftime("%Y%m%d")))
    rows.sort(reverse=True)
    return [(qcode, ds) for _, qcode, ds in rows[:limit]]


def _num(value) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        if value != value:  # NaN
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


async def _fetch_institute_detail(code6: str, quarter_code: str):
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(
                ak.stock_institute_hold_detail,
                stock=code6,
                quarter=quarter_code,
            ),
            timeout=_FETCH_TIMEOUT_S,
        )
    except Exception as e:
        utils.logger.warning(
            f"[a_share_structure] institution detail {code6}/{quarter_code} failed: "
            f"{type(e).__name__}: {str(e)[:140]}"
        )
        return None


async def _fetch_top10(code6: str, report_date: str):
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(
                ak.stock_gdfx_free_top_10_em,
                symbol=_em_symbol(code6),
                date=report_date,
            ),
            timeout=_FETCH_TIMEOUT_S,
        )
    except Exception as e:
        utils.logger.warning(
            f"[a_share_structure] top10 holders {code6}/{report_date} failed: "
            f"{type(e).__name__}: {str(e)[:140]}"
        )
        return None


def _summarize_institutions(df) -> List[Dict]:
    if df is None or df.empty:
        return []
    rows: List[Dict] = []
    for inst_type in _INSTITUTION_TYPES:
        sub = df[df["持股机构类型"] == inst_type] if "持股机构类型" in df.columns else None
        if sub is None or sub.empty:
            continue

        latest_ratios = [
            x for x in (_num(v) for v in sub.get("最新占流通股比例", [])) if x is not None
        ]
        ratio_changes = [
            x for x in (_num(v) for v in sub.get("占流通股比例增幅", [])) if x is not None
        ]
        latest_shares = [
            x for x in (_num(v) for v in sub.get("最新持股数", [])) if x is not None
        ]
        rows.append(
            {
                "type": inst_type,
                "institutions": int(len(sub)),
                "latest_float_ratio_pct": round(sum(latest_ratios), 3) if latest_ratios else None,
                "float_ratio_change_pct": round(sum(ratio_changes), 3) if ratio_changes else None,
                "latest_shares": round(sum(latest_shares), 0) if latest_shares else None,
            }
        )
    return rows


def _classify_holder(name: str) -> List[str]:
    return [
        category
        for category, pattern in _SPECIAL_HOLDER_PATTERNS.items()
        if pattern.search(name or "")
    ]


def _summarize_top10(df) -> tuple[List[Dict], Dict[str, List[Dict]]]:
    if df is None or df.empty:
        return [], {key: [] for key in _SPECIAL_HOLDER_PATTERNS}

    rows: List[Dict] = []
    special: Dict[str, List[Dict]] = {key: [] for key in _SPECIAL_HOLDER_PATTERNS}
    for _, row in df.iterrows():
        name = str(row.get("股东名称") or "")
        item = {
            "rank": int(_num(row.get("名次")) or 0) or None,
            "name": name,
            "holder_type": str(row.get("股东性质") or ""),
            "shares": _num(row.get("持股数")),
            "float_ratio_pct": _num(row.get("占总流通股本持股比例")),
            "change": str(row.get("增减") or ""),
            "change_ratio_pct": _num(row.get("变动比率")),
        }
        item["categories"] = _classify_holder(name)
        rows.append(item)
        for category in item["categories"]:
            special[category].append(item)
    return rows, special


async def get_a_share_structure(stock_code: str) -> Optional[Dict]:
    code6 = _bare_code(stock_code)
    if len(code6) != 6:
        return None

    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    selected: Optional[Tuple[str, str]] = None
    institute_df = None

    # Direct institution-detail endpoint is stock-specific; try newest completed periods
    # until one has disclosed data.
    for quarter_code, report_date in _quarter_candidates(today, limit=3):
        df = await _fetch_institute_detail(code6, quarter_code)
        if df is not None and not df.empty:
            selected = (quarter_code, report_date)
            institute_df = df
            break

    if selected is None:
        # We can still try top-10 holders for the latest completed quarter.
        candidates = _quarter_candidates(today, limit=1)
        if not candidates:
            return None
        selected = candidates[0]

    quarter_code, report_date = selected
    top10_df = await _fetch_top10(code6, report_date)

    institution_summary = _summarize_institutions(institute_df)
    top10, special = _summarize_top10(top10_df)

    if not institution_summary and not top10:
        return None

    notes: List[str] = []
    if special.get("national_team"):
        notes.append("前十大流通股东中存在公开可识别的国家资本/国家队持仓。")
    else:
        notes.append("前十大流通股东中未识别到汇金/证金/国新/诚通；这不等于其一定未持有，只表示本期前十大未见。")
    if special.get("foreign"):
        notes.append("前十大流通股东中存在香港中央结算/QFII等可识别境外资金。")

    return {
        "report_period": report_date,
        "quarter_code": quarter_code,
        "institution_summary": institution_summary,
        "top10_free_holders": top10,
        "special_holders": special,
        "notes": notes,
        "source_tier": "B",
        "sources": [
            "AkShare stock_institute_hold_detail (Sina institutional holdings)",
            "AkShare stock_gdfx_free_top_10_em (Eastmoney top-10 free-float holders)",
        ],
    }
