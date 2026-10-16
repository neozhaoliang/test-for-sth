# -*- coding: utf-8 -*-
"""
Current interest-rate context for equity research.

US data comes from Federal Reserve series distributed through FRED, using the no-key CSV
download endpoint.  China LPR uses AkShare/Eastmoney.  Every series carries an as-of date
and freshness flag; stale data is never presented as "current".
"""

from __future__ import annotations

import asyncio
import logging
import csv
import io
from datetime import date, datetime
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo
from analysis.macro_verified_fallback import us_reference, china_reference, international_reference




logger = logging.getLogger("MediaCrawler")


_FRED_TIMEOUT_S = 20
_LPR_TIMEOUT_S = 25
_FRED_BASE = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
_FRESH_DAYS = {
    "fed_target": 10,
    "us10y": 10,
    "china_lpr": 45,
}


def _today() -> date:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def _parse_date(value) -> Optional[date]:
    if value is None:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _num(value) -> Optional[float]:
    if value in (None, "", "."):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _freshness(
    observation_date: Optional[date],
    max_days: int,
    *,
    reference_date: Optional[date] = None,
) -> Dict:
    if observation_date is None:
        return {"fresh": False, "age_days": None}
    reference_date = reference_date or _today()
    age = (reference_date - observation_date).days
    return {"fresh": 0 <= age <= max_days, "age_days": age}


async def _fetch_fred_series(series_id: str) -> List[Dict]:
    import httpx

    url = _FRED_BASE.format(series_id=series_id)
    try:
        async with httpx.AsyncClient(
            timeout=_FRED_TIMEOUT_S,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0"},
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            text = resp.text
    except Exception as e:
        logger.warning(
            f"[macro_rates] FRED {series_id} failed: {type(e).__name__}: {str(e)[:140]}"
        )
        return []

    rows: List[Dict] = []
    try:
        for row in csv.DictReader(io.StringIO(text)):
            dt = _parse_date(row.get("DATE") or row.get("observation_date"))
            value = _num(row.get(series_id))
            if dt is not None and value is not None:
                rows.append({"date": dt, "value": value})
    except Exception as e:
        logger.warning(f"[macro_rates] FRED {series_id} parse failed: {e}")
        return []
    rows.sort(key=lambda x: x["date"])
    return rows


def _change_from_days(rows: List[Dict], days: int) -> Optional[float]:
    if not rows:
        return None
    latest = rows[-1]
    cutoff = latest["date"].toordinal() - days
    earlier = None
    for row in reversed(rows[:-1]):
        if row["date"].toordinal() <= cutoff:
            earlier = row
            break
    if earlier is None:
        return None
    return round(latest["value"] - earlier["value"], 3)


async def _fetch_us_rates(as_of: Optional[date] = None) -> Optional[Dict]:
    lower_rows, upper_rows, ten_rows = await asyncio.gather(
        _fetch_fred_series("DFEDTARL"),
        _fetch_fred_series("DFEDTARU"),
        _fetch_fred_series("DGS10"),
    )
    if as_of is not None:
        lower_rows = [x for x in lower_rows if x["date"] <= as_of]
        upper_rows = [x for x in upper_rows if x["date"] <= as_of]
        ten_rows = [x for x in ten_rows if x["date"] <= as_of]
    if not (lower_rows or upper_rows or ten_rows):
        return None

    reference_date = as_of or _today()
    out: Dict = {
        "source_name": "Federal Reserve via FRED",
        "source_tier": "S",
        "source_urls": {
            "target_lower": "https://fred.stlouisfed.org/series/DFEDTARL",
            "target_upper": "https://fred.stlouisfed.org/series/DFEDTARU",
            "us10y": "https://fred.stlouisfed.org/series/DGS10",
        },
    }
    if lower_rows:
        row = lower_rows[-1]
        out["fed_target_lower_pct"] = row["value"]
        out["fed_target_lower_as_of"] = row["date"].isoformat()
        out["fed_target_lower_change_180d_pp"] = _change_from_days(lower_rows, 180)
    if upper_rows:
        row = upper_rows[-1]
        out["fed_target_upper_pct"] = row["value"]
        out["fed_target_upper_as_of"] = row["date"].isoformat()
        out["fed_target_upper_change_180d_pp"] = _change_from_days(upper_rows, 180)
        out["fed_target_freshness"] = _freshness(
            row["date"], _FRESH_DAYS["fed_target"], reference_date=reference_date
        )
    if ten_rows:
        row = ten_rows[-1]
        out["us10y_yield_pct"] = row["value"]
        out["us10y_as_of"] = row["date"].isoformat()
        out["us10y_change_30d_pp"] = _change_from_days(ten_rows, 30)
        out["us10y_change_90d_pp"] = _change_from_days(ten_rows, 90)
        out["us10y_freshness"] = _freshness(
            row["date"], _FRESH_DAYS["us10y"], reference_date=reference_date
        )
    return out


async def _fetch_china_lpr(as_of: Optional[date] = None) -> Optional[Dict]:
    import akshare as ak

    try:
        df = await asyncio.wait_for(
            asyncio.to_thread(ak.macro_china_lpr),
            timeout=_LPR_TIMEOUT_S,
        )
    except Exception as e:
        logger.warning(
            f"[macro_rates] China LPR failed: {type(e).__name__}: {str(e)[:140]}"
        )
        return None

    if df is None or df.empty or "TRADE_DATE" not in df.columns:
        return None
    df = df.dropna(subset=["TRADE_DATE"]).copy()
    df["_date"] = df["TRADE_DATE"].map(_parse_date)
    df = df.dropna(subset=["_date"]).sort_values("_date")
    if as_of is not None:
        df = df[df["_date"] <= as_of]
    if df.empty:
        return None

    row = df.iloc[-1]
    observation_date = row.get("_date") or _parse_date(row.get("TRADE_DATE"))
    reference_date = as_of or _today()
    one_y = _num(row.get("LPR1Y"))
    five_y = _num(row.get("LPR5Y"))

    def previous_value(col: str, months_rows: int = 6) -> Optional[float]:
        values = [
            _num(v) for v in df[col].tail(months_rows + 1).tolist()
            if _num(v) is not None
        ]
        if len(values) < 2:
            return None
        return round(values[-1] - values[0], 3)

    return {
        "source_name": "AkShare / Eastmoney LPR",
        "source_tier": "B",
        "source_url": "https://data.eastmoney.com/cjsj/globalRateLPR.html",
        "as_of": observation_date.isoformat() if observation_date else "",
        "lpr_1y_pct": one_y,
        "lpr_5y_pct": five_y,
        "lpr_1y_change_6obs_pp": previous_value("LPR1Y"),
        "lpr_5y_change_6obs_pp": previous_value("LPR5Y"),
        "freshness": _freshness(
            observation_date,
            _FRESH_DAYS["china_lpr"],
            reference_date=reference_date,
        ),
        "note": "LPR是贷款市场报价利率，不等同于央行政策利率。",
    }


async def get_macro_rate_context(
    *,
    as_of: Optional[date] = None,
    allow_verified_live_fallback: bool = False,
) -> Optional[Dict]:
    us, china = await asyncio.gather(
        _fetch_us_rates(as_of=as_of),
        _fetch_china_lpr(as_of=as_of),
    )
    # An upstream FRED / AkShare failure must not erase already independently
    # verified PUBLIC interest-rate data from the same research period. This
    # limited bridge is intentionally live-only; archived historical research
    # cannot use a 2026 reference as if it had been published in the past.
    fallback_used = []
    if as_of is None or allow_verified_live_fallback:
        reference_us = us_reference(today=as_of or _today())
        if reference_us:
            current = dict(us or {})
            urls = dict(current.get("source_urls") or {})
            supplemented = []
            if (not (current.get("fed_target_freshness") or {}).get("fresh")
                    or current.get("fed_target_lower_pct") is None
                    or current.get("fed_target_upper_pct") is None):
                if reference_us.get("fed_target_upper_pct") is not None:
                    for field in ("fed_target_lower_pct", "fed_target_upper_pct",
                                  "fed_target_lower_as_of", "fed_target_upper_as_of",
                                  "fed_target_freshness",
                                  "fed_target_last_decision_change_pp"):
                        current[field] = reference_us[field]
                    for k in ("target_lower", "target_upper"):
                        urls[k] = reference_us["source_urls"][k]
                    # A 180-day comparison is NOT established by the last hike.
                    current.pop("fed_target_upper_change_180d_pp", None)
                    current.pop("fed_target_lower_change_180d_pp", None)
                    supplemented.append("Fed")
            if (not (current.get("us10y_freshness") or {}).get("fresh")
                    or current.get("us10y_yield_pct") is None):
                if reference_us.get("us10y_yield_pct") is not None:
                    for field in ("us10y_yield_pct", "us10y_as_of",
                                  "us10y_freshness"):
                        current[field] = reference_us[field]
                    for field in ("us10y_change_30d_pp", "us10y_change_90d_pp"):
                        current.pop(field, None)
                    urls["us10y"] = reference_us["source_urls"]["us10y"]
                    supplemented.append("US10Y")
            if supplemented:
                current["source_urls"] = urls
                current["source_mode"] = ("dated_verified_fallback" if us is None
                    else "live_and_dated_verified_fallback")
                current["fallback_note"] = reference_us["fallback_note"]
                fallback_used.extend(supplemented)
                us = current
        if not china or not (china.get("freshness") or {}).get("fresh"):
            reference_china = china_reference(today=as_of or _today())
            if reference_china:
                china = reference_china
                fallback_used.append("China LPR")

    if not us and not china:
        return None

    warnings: List[str] = []
    if fallback_used:
        warnings.append(
            "宏观接口本次缺失/过期；以下 " + "、".join(fallback_used) +
            " 使用已核验且尚未过期的官方日期快照，并非实时重新抓取。"
        )
    if us:
        ff = us.get("fed_target_freshness") or {}
        u10 = us.get("us10y_freshness") or {}
        if not ff.get("fresh", False):
            warnings.append("联邦基金目标区间数据已过新鲜度阈值，不能当作当前利率使用。")
        if not u10.get("fresh", False):
            warnings.append("美国10年期国债收益率数据已过新鲜度阈值，不能当作当前市场利率使用。")
    if china and not (china.get("freshness") or {}).get("fresh", False):
        warnings.append("LPR数据已过新鲜度阈值，不能当作当前贷款报价利率使用。")

    return {
        "as_of": (as_of or _today()).isoformat(),
        "us": us,
        "china": china,
        "warnings": warnings,
        "source_tier": "A" if us else "B",
        "fallback_used": fallback_used,
    }


# These decision-history series are supplemental industry-cycle context, not an
# estimate of monetary liquidity and not a proxy for the company's revenue.
# The Jin10-backed AkShare tables are NOT treated as first-party policy sources.
_INTERNATIONAL_DECISIONS = {
    "japan": ("macro_bank_japan_interest_rate", "日本央行"),
    "euro": ("macro_bank_euro_interest_rate", "欧洲央行"),
}
_DECISION_MAX_STALENESS_DAYS = 120


def _policy_decision_table(df, *, as_of: Optional[date], bank: str) -> Optional[Dict]:
    """Parse dated decisions without assuming that a rate level means a hike.

    Unavailable or stale observations remain explicitly marked and cannot
    establish the latest monetary-policy direction.
    """
    if df is None or getattr(df, "empty", True):
        return None
    if not {"日期", "今值"}.issubset(df.columns):
        return None
    rows = []
    for _, item in df.iterrows():
        dt = _parse_date(item.get("日期"))
        rate = _num(item.get("今值"))
        if dt is None or rate is None or (as_of and dt > as_of):
            continue
        rows.append({"date": dt, "value": rate})
    if not rows:
        return None
    rows.sort(key=lambda x: x["date"])
    # Deduplicate reporting-date collisions; use the most recently encountered value.
    unique = {row["date"]: row for row in rows}
    rows = [unique[d] for d in sorted(unique)]
    latest = rows[-1]
    reference_date = as_of or _today()
    fresh = _freshness(
        latest["date"], _DECISION_MAX_STALENESS_DAYS,
        reference_date=reference_date,
    )
    immediate_delta = (
        round(latest["value"] - rows[-2]["value"], 3)
        if len(rows) > 1 else None
    )
    return {
        "bank": bank,
        "decision_as_of": latest["date"].isoformat(),
        "policy_rate_pct": latest["value"],
        "last_decision_change_pp": immediate_delta,
        "change_180d_pp": _change_from_days(rows, 180),
        "freshness": fresh,
        "source_name": "AkShare / Jin10 central bank decision calendar",
        "source_url": "https://datacenter.jin10.com/reportType/dc_interest_rate_decision",
        "source_tier": "B",
        "rate_definition_note": "不同央行决议口径并不完全相同，不可直接比较数值或当作实际利率。",
    }


async def get_international_policy_rates(
    *, as_of: Optional[date] = None,
) -> Dict:
    """Fetch BOJ/ECB dated decisions, relevant only when the industry needs them.

    Any upstream error is a missing source, never evidence of easing/tightening.
    A historical caller must supply as_of to exclude later decisions.
    """
    import akshare as ak

    async def one(region: str, api_name: str, label: str):
        api = getattr(ak, api_name, None)
        if not callable(api):
            return region, None
        try:
            df = await asyncio.wait_for(
                asyncio.to_thread(api),
                timeout=_LPR_TIMEOUT_S,
            )
            return region, _policy_decision_table(
                df, as_of=as_of, bank=label,
            )
        except Exception as exc:
            logger.warning(
                "[macro_rates] %s policy series unavailable: %s",
                label, type(exc).__name__,
            )
            return region, None

    found = await asyncio.gather(*(
        one(region, api_name, label)
        for region, (api_name, label) in _INTERNATIONAL_DECISIONS.items()
    ))
    result = dict(found)
    if as_of is None:
        for region in _INTERNATIONAL_DECISIONS:
            official = international_reference(region)
            current = result.get(region) or {}
            # Prefer fresh newer provider observations. Never overwrite a
            # newer decision with an older official reference snapshot.
            current_date = _parse_date(current.get("decision_as_of"))
            verified_date = _parse_date(official.get("decision_as_of"))
            if (official and (
                not (current.get("freshness") or {}).get("fresh")
                or current_date is None
                or (verified_date and current_date < verified_date)
            )):
                result[region] = official
                logger.info("[macro_rates] %s: official dated fallback %s",
                            region, official["decision_as_of"])
    return result
