# -*- coding: utf-8 -*-
"""
Current interest-rate context for equity research.

US data comes from Federal Reserve series distributed through FRED, using the no-key CSV
download endpoint.  China LPR uses AkShare/Eastmoney.  Every series carries an as-of date
and freshness flag; stale data is never presented as "current".
"""

from __future__ import annotations

import asyncio
import csv
import io
from datetime import date, datetime
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import akshare as ak
import httpx

from tools.utils import utils


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


def _freshness(as_of: Optional[date], max_days: int) -> Dict:
    if as_of is None:
        return {"fresh": False, "age_days": None}
    age = (_today() - as_of).days
    return {"fresh": 0 <= age <= max_days, "age_days": age}


async def _fetch_fred_series(series_id: str) -> List[Dict]:
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
        utils.logger.warning(
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
        utils.logger.warning(f"[macro_rates] FRED {series_id} parse failed: {e}")
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


async def _fetch_us_rates() -> Optional[Dict]:
    lower_rows, upper_rows, ten_rows = await asyncio.gather(
        _fetch_fred_series("DFEDTARL"),
        _fetch_fred_series("DFEDTARU"),
        _fetch_fred_series("DGS10"),
    )
    if not (lower_rows or upper_rows or ten_rows):
        return None

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
        out["fed_target_freshness"] = _freshness(row["date"], _FRESH_DAYS["fed_target"])
    if ten_rows:
        row = ten_rows[-1]
        out["us10y_yield_pct"] = row["value"]
        out["us10y_as_of"] = row["date"].isoformat()
        out["us10y_change_30d_pp"] = _change_from_days(ten_rows, 30)
        out["us10y_change_90d_pp"] = _change_from_days(ten_rows, 90)
        out["us10y_freshness"] = _freshness(row["date"], _FRESH_DAYS["us10y"])
    return out


async def _fetch_china_lpr() -> Optional[Dict]:
    try:
        df = await asyncio.wait_for(
            asyncio.to_thread(ak.macro_china_lpr),
            timeout=_LPR_TIMEOUT_S,
        )
    except Exception as e:
        utils.logger.warning(
            f"[macro_rates] China LPR failed: {type(e).__name__}: {str(e)[:140]}"
        )
        return None

    if df is None or df.empty or "TRADE_DATE" not in df.columns:
        return None
    df = df.dropna(subset=["TRADE_DATE"]).sort_values("TRADE_DATE")
    if df.empty:
        return None

    row = df.iloc[-1]
    as_of = _parse_date(row.get("TRADE_DATE"))
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
        "as_of": as_of.isoformat() if as_of else "",
        "lpr_1y_pct": one_y,
        "lpr_5y_pct": five_y,
        "lpr_1y_change_6obs_pp": previous_value("LPR1Y"),
        "lpr_5y_change_6obs_pp": previous_value("LPR5Y"),
        "freshness": _freshness(as_of, _FRESH_DAYS["china_lpr"]),
        "note": "LPR是贷款市场报价利率，不等同于央行政策利率。",
    }


async def get_macro_rate_context() -> Optional[Dict]:
    us, china = await asyncio.gather(_fetch_us_rates(), _fetch_china_lpr())
    if not us and not china:
        return None

    warnings: List[str] = []
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
        "as_of": _today().isoformat(),
        "us": us,
        "china": china,
        "warnings": warnings,
        "source_tier": "A" if us else "B",
    }
