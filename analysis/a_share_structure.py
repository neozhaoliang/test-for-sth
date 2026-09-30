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
import logging
import re
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo




logger = logging.getLogger("MediaCrawler")


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
_ETF_RE = re.compile(r"ETF|交易型开放式指数", re.I)


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
    import akshare as ak

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
        logger.warning(
            f"[a_share_structure] institution detail {code6}/{quarter_code} failed: "
            f"{type(e).__name__}: {str(e)[:140]}"
        )
        return None


async def _fetch_unlock_queue(code6: str):
    import akshare as ak

    try:
        return await asyncio.wait_for(
            asyncio.to_thread(
                ak.stock_restricted_release_queue_em,
                symbol=code6,
            ),
            timeout=_FETCH_TIMEOUT_S,
        )
    except Exception as e:
        logger.warning(
            f"[a_share_structure] unlock queue {code6} failed: "
            f"{type(e).__name__}: {str(e)[:140]}"
        )
        return None


async def _fetch_top10(code6: str, report_date: str):
    import akshare as ak

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
        logger.warning(
            f"[a_share_structure] top10 holders {code6}/{report_date} failed: "
            f"{type(e).__name__}: {str(e)[:140]}"
        )
        return None


def _summarize_institutions(df) -> tuple[List[Dict], List[Dict], List[Dict]]:
    if df is None or df.empty:
        return [], [], []
    rows: List[Dict] = []
    fund_details: List[Dict] = []
    etf_details: List[Dict] = []

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

        if inst_type == "基金":
            details: List[Dict] = []
            for _, r in sub.iterrows():
                name = str(
                    r.get("持股机构全称")
                    or r.get("持股机构简称")
                    or ""
                ).strip()
                if not name:
                    continue
                item = {
                    "name": name,
                    "code": str(r.get("持股机构代码") or ""),
                    "latest_shares": _num(r.get("最新持股数")),
                    "latest_float_ratio_pct": _num(r.get("最新占流通股比例")),
                    "float_ratio_change_pct": _num(r.get("占流通股比例增幅")),
                }
                details.append(item)
            details.sort(
                key=lambda x: (
                    x.get("latest_float_ratio_pct") is not None,
                    x.get("latest_float_ratio_pct") or 0,
                ),
                reverse=True,
            )
            # Keep the full disclosed fund list internally so quarter-to-quarter matching
            # is not biased to only the current top 20.  The public report is sliced later.
            fund_details = details
            etf_details = [x for x in details if _ETF_RE.search(x.get("name") or "")]

    return rows, fund_details, etf_details


def _compare_institution_summaries(
    current: List[Dict],
    previous: List[Dict],
) -> List[Dict]:
    """Compare two disclosed quarter snapshots by institution type."""
    if not current or not previous:
        return []
    prev_map = {str(x.get("type") or ""): x for x in previous}
    rows: List[Dict] = []
    for cur in current:
        inst_type = str(cur.get("type") or "")
        prev = prev_map.get(inst_type)
        if not inst_type or not prev:
            continue

        cur_ratio = _num(cur.get("latest_float_ratio_pct"))
        prev_ratio = _num(prev.get("latest_float_ratio_pct"))
        cur_shares = _num(cur.get("latest_shares"))
        prev_shares = _num(prev.get("latest_shares"))
        cur_n = _num(cur.get("institutions"))
        prev_n = _num(prev.get("institutions"))

        row = {"type": inst_type}
        if cur_ratio is not None and prev_ratio is not None:
            row["float_ratio_change_pp"] = round(cur_ratio - prev_ratio, 3)
            row["current_float_ratio_pct"] = round(cur_ratio, 3)
            row["previous_float_ratio_pct"] = round(prev_ratio, 3)
        if cur_shares is not None and prev_shares is not None:
            row["shares_change"] = round(cur_shares - prev_shares, 0)
            row["current_shares"] = round(cur_shares, 0)
            row["previous_shares"] = round(prev_shares, 0)
            row["shares_change_pct"] = (
                round((cur_shares - prev_shares) / prev_shares * 100, 2)
                if prev_shares else None
            )
        if cur_n is not None and prev_n is not None:
            row["institution_count_change"] = int(cur_n - prev_n)
        rows.append(row)
    return rows


def _fund_key(item: Dict) -> str:
    code = str(item.get("code") or "").strip()
    if code and code.lower() != "nan":
        return f"code:{code}"
    return f"name:{str(item.get('name') or '').strip()}"


def _compare_fund_details(
    current: List[Dict],
    previous: List[Dict],
    *,
    limit: int = 15,
) -> Dict[str, List[Dict]]:
    """Find the largest disclosed fund increases/decreases between two quarters."""
    if not current or not previous:
        return {"increased": [], "decreased": [], "newly_seen": [], "exited_top_list": []}

    cur_map = {_fund_key(x): x for x in current if str(x.get("name") or "").strip()}
    prev_map = {_fund_key(x): x for x in previous if str(x.get("name") or "").strip()}

    changes: List[Dict] = []
    newly_seen: List[Dict] = []
    exited: List[Dict] = []

    for key, cur in cur_map.items():
        prev = prev_map.get(key)
        if not prev:
            newly_seen.append(cur)
            continue
        cur_ratio = _num(cur.get("latest_float_ratio_pct"))
        prev_ratio = _num(prev.get("latest_float_ratio_pct"))
        cur_shares = _num(cur.get("latest_shares"))
        prev_shares = _num(prev.get("latest_shares"))
        item = {
            "name": cur.get("name"),
            "code": cur.get("code"),
            "current_float_ratio_pct": cur_ratio,
            "previous_float_ratio_pct": prev_ratio,
            "current_shares": cur_shares,
            "previous_shares": prev_shares,
        }
        if cur_ratio is not None and prev_ratio is not None:
            item["float_ratio_change_pp"] = round(cur_ratio - prev_ratio, 4)
        if cur_shares is not None and prev_shares is not None:
            item["shares_change"] = round(cur_shares - prev_shares, 0)
            item["shares_change_pct"] = (
                round((cur_shares - prev_shares) / prev_shares * 100, 2)
                if prev_shares else None
            )
        if (
            item.get("float_ratio_change_pp") not in (None, 0)
            or item.get("shares_change") not in (None, 0)
        ):
            changes.append(item)

    for key, prev in prev_map.items():
        if key not in cur_map:
            exited.append(prev)

    def magnitude(x: Dict) -> float:
        ratio = abs(_num(x.get("float_ratio_change_pp")) or 0.0)
        shares = abs(_num(x.get("shares_change_pct")) or 0.0) / 100.0
        return ratio + shares

    increased = [
        x for x in changes
        if (_num(x.get("float_ratio_change_pp")) or 0) > 0
        or (
            x.get("float_ratio_change_pp") is None
            and (_num(x.get("shares_change")) or 0) > 0
        )
    ]
    decreased = [
        x for x in changes
        if (_num(x.get("float_ratio_change_pp")) or 0) < 0
        or (
            x.get("float_ratio_change_pp") is None
            and (_num(x.get("shares_change")) or 0) < 0
        )
    ]
    increased.sort(key=magnitude, reverse=True)
    decreased.sort(key=magnitude, reverse=True)
    newly_seen.sort(
        key=lambda x: _num(x.get("latest_float_ratio_pct")) or 0.0,
        reverse=True,
    )
    exited.sort(
        key=lambda x: _num(x.get("latest_float_ratio_pct")) or 0.0,
        reverse=True,
    )
    return {
        "increased": increased[:limit],
        "decreased": decreased[:limit],
        "newly_seen": newly_seen[:limit],
        "exited_top_list": exited[:limit],
    }


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


def _summarize_unlocks(df, today: date) -> Dict:
    if df is None or df.empty or "解禁时间" not in df.columns:
        return {"upcoming_12m": [], "recent_6m": [], "max_upcoming_float_ratio_pct": None}

    upcoming: List[Dict] = []
    recent: List[Dict] = []
    end = today + timedelta(days=365)
    recent_start = today - timedelta(days=183)

    for _, row in df.iterrows():
        raw_date = row.get("解禁时间")
        if raw_date is None:
            continue
        if isinstance(raw_date, datetime):
            dt = raw_date.date()
        elif isinstance(raw_date, date):
            dt = raw_date
        else:
            try:
                dt = datetime.strptime(str(raw_date)[:10], "%Y-%m-%d").date()
            except ValueError:
                continue
        item = {
            "date": dt.isoformat(),
            "shareholders": int(_num(row.get("解禁股东数")) or 0) or None,
            "unlock_shares": _num(row.get("解禁数量")),
            "actual_unlock_shares": _num(row.get("实际解禁数量")),
            "remaining_locked_shares": _num(row.get("未解禁数量")),
            "actual_market_value_yuan": _num(row.get("实际解禁数量市值")),
            "total_market_ratio_pct": _num(row.get("占总市值比例")),
            "float_market_ratio_pct": _num(row.get("占流通市值比例")),
            "type": str(row.get("限售股类型") or ""),
        }
        if today <= dt <= end:
            upcoming.append(item)
        elif recent_start <= dt < today:
            recent.append(item)

    upcoming.sort(key=lambda x: x["date"])
    recent.sort(key=lambda x: x["date"], reverse=True)
    ratios = [
        x["float_market_ratio_pct"]
        for x in upcoming
        if x.get("float_market_ratio_pct") is not None
    ]
    return {
        "upcoming_12m": upcoming[:10],
        "recent_6m": recent[:10],
        "max_upcoming_float_ratio_pct": round(max(ratios), 3) if ratios else None,
    }


async def get_a_share_structure(stock_code: str) -> Optional[Dict]:
    code6 = _bare_code(stock_code)
    if len(code6) != 6:
        return None

    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    candidates = _quarter_candidates(today, limit=4)
    if not candidates:
        return None

    # Fetch a few completed quarters concurrently.  We keep the newest disclosed snapshot
    # and the next disclosed snapshot for an explicit quarter-over-quarter comparison.
    institution_frames = await asyncio.gather(
        *(_fetch_institute_detail(code6, qcode) for qcode, _ in candidates)
    )
    disclosed: List[Tuple[str, str, object]] = []
    for (qcode, report_date), df in zip(candidates, institution_frames):
        if df is not None and not df.empty:
            disclosed.append((qcode, report_date, df))

    if disclosed:
        quarter_code, report_date, institute_df = disclosed[0]
        previous_quarter_code = disclosed[1][0] if len(disclosed) > 1 else None
        previous_report_date = disclosed[1][1] if len(disclosed) > 1 else None
        previous_institute_df = disclosed[1][2] if len(disclosed) > 1 else None
    else:
        # We can still use top-10 holders / unlock supply even when the institution-detail
        # endpoint has no disclosed rows.
        quarter_code, report_date = candidates[0]
        institute_df = None
        previous_quarter_code = None
        previous_report_date = None
        previous_institute_df = None
    top10_df, unlock_df = await asyncio.gather(
        _fetch_top10(code6, report_date),
        _fetch_unlock_queue(code6),
    )

    institution_summary, fund_details, etf_details = _summarize_institutions(institute_df)
    previous_summary, previous_fund_details, previous_etf_details = _summarize_institutions(
        previous_institute_df
    )
    institution_qoq = _compare_institution_summaries(
        institution_summary, previous_summary
    )
    fund_qoq = _compare_fund_details(fund_details, previous_fund_details)
    etf_qoq = _compare_fund_details(etf_details, previous_etf_details)
    top10, special = _summarize_top10(top10_df)
    unlocks = _summarize_unlocks(unlock_df, today)

    if not institution_summary and not top10 and not unlocks.get("upcoming_12m") and not unlocks.get("recent_6m"):
        return None

    notes: List[str] = []
    if special.get("national_team"):
        notes.append("前十大流通股东中存在公开可识别的国家资本/国家队持仓。")
    else:
        notes.append("前十大流通股东中未识别到汇金/证金/国新/诚通；这不等于其一定未持有，只表示本期前十大未见。")
    if special.get("foreign"):
        notes.append("前十大流通股东中存在香港中央结算/QFII等可识别境外资金。")
    if previous_report_date and institution_qoq:
        notes.append(
            f"机构季度变化按两个已披露快照 {previous_report_date} → {report_date} 直接做差，"
            "不是根据股价或成交量反推资金行为。"
        )
    if unlocks.get("max_upcoming_float_ratio_pct") is not None:
        notes.append(
            f"未来12个月单批最大解禁约占解禁前流通市值 "
            f"{unlocks['max_upcoming_float_ratio_pct']}%；解禁是潜在供给，不等于股东一定卖出。"
        )

    return {
        "report_period": report_date,
        "quarter_code": quarter_code,
        "previous_quarter_code": previous_quarter_code,
        "previous_report_period": previous_report_date,
        "institution_summary": institution_summary,
        "previous_institution_summary": previous_summary,
        "institution_qoq": institution_qoq,
        "fund_details": fund_details[:20],
        "etf_details": etf_details[:20],
        "fund_qoq": fund_qoq,
        "etf_qoq": etf_qoq,
        "top10_free_holders": top10,
        "special_holders": special,
        "unlock_supply": unlocks,
        "notes": notes,
        "source_tier": "B",
        "sources": [
            "AkShare stock_institute_hold_detail (Sina institutional holdings)",
            "AkShare stock_gdfx_free_top_10_em (Eastmoney top-10 free-float holders)",
            "AkShare stock_restricted_release_queue_em (Eastmoney restricted-share unlocks)",
        ],
    }
