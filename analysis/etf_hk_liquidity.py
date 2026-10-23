# -*- coding: utf-8 -*-
"""HK-listed equity research: mainland earnings and offshore USD/HKD liquidity.

The long-cycle investment principle is a HYPOTHESIS, not an automatic trade
signal.  HKMA and FRED records are public observations. Never disclose private
research-source attribution or raw livestream excerpts in API responses.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from io import StringIO
import math
import re
import time
from zoneinfo import ZoneInfo

import akshare as ak
import httpx
import pandas as pd

from tools.utils import utils

_HKMA_URL = ("https://api.hkma.gov.hk/public/market-data-and-statistics/"
             "daily-monetary-statistics/daily-figures-interbank-liquidity")
_FED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFEDTARU"
_CACHE_TTL = 1800
_CACHE: dict[str, tuple[float, dict]] = {}
_LOCKS: dict[str, asyncio.Lock] = {}
_HK_NAME = re.compile(r"港股|港股通|香港|恒生|H股|中概互联|中概互联网|港交所", re.I)
_US_FOREIGN = re.compile(r"纳斯达克|标普500|美股|日经|欧洲|印度|德国|日本", re.I)

# What the investor has extracted from their long-horizon private research:
# do not expose the source name, nickname, raw transcript, URL or a quote.
_HYPOTHESIS = (
    "多数以内地业务为主的港股公司，盈利预期主要受中国经济与行业周期影响；"
    "港元融资与离岸估值还受美元利率、港元拆息、香港银行体系总结余、"
    "南向与国际资本供求影响。"
    "内地需求疲弱、美国货币紧缩与港元融资收紧叠加时需警惕估值压力；"
    "关注流动性压力是否接近极端并出现边际改善，但无法实时认定绝对低点。"
)


def _n(value):
    try:
        n = float(str(value).replace(",", "").replace("%", ""))
        return n if math.isfinite(n) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _day(value):
    try:
        d = pd.to_datetime(value, errors="coerce")
        return None if pd.isna(d) else d.date()
    except (TypeError, ValueError):
        return None


def is_hk_equity_exposure(name: str, holdings: list[dict] | None = None) -> bool:
    """Require a HK identity or a meaningful actual five-digit HK holdings slice."""
    name = str(name or "")
    if _US_FOREIGN.search(name) and not _HK_NAME.search(name):
        return False
    if _HK_NAME.search(name):
        return True
    weight = sum(
        max(0, _n(h.get("weight_pct")) or 0)
        for h in (holdings or [])
        if re.fullmatch(r"\d{5}", str(h.get("code") or ""))
    )
    return weight >= 15.0


def hkma_liquidity_snapshot(payload: dict, *, as_of: date) -> dict:
    """Filter by observation date, not API order; guard stale and partial data."""
    records = ((payload or {}).get("result") or {}).get("records")
    if not isinstance(records, list):
        return {"status": "unavailable"}
    rows = {}
    for rec in records:
        if not isinstance(rec, dict):
            continue
        d = _day(rec.get("end_of_date"))
        if d and d <= as_of:
            rows[d] = rec
    if not rows:
        return {"status": "unavailable"}
    days = sorted(rows)
    latest = days[-1]
    if (as_of-latest).days > 7:
        return {"status": "stale", "as_of": latest.isoformat()}
    rec = rows[latest]
    hibor = _n(rec.get("hibor_fixing_1m"))
    balance = _n(rec.get("closing_balance"))
    if hibor is None and balance is None:
        return {"status": "unavailable"}
    out = {
        "status": "observed", "as_of": latest.isoformat(),
        "hibor_1m_pct": hibor, "aggregate_balance_hkd_million": balance,
        "overnight_hibor_pct": _n(rec.get("hibor_overnight")),
        "hkma_base_rate_pct": _n(rec.get("disc_win_base_rate")),
        "source": "香港金融管理局每日银行同业市场流动资金数据",
        "source_url": _HKMA_URL,
    }
    def historical(key: str, days_back: int):
        valid = [(d, _n(rows[d].get(key))) for d in days if d < latest
                 and d >= latest-timedelta(days=days_back)]
        valid = [(d, v) for d, v in valid if v is not None]
        return valid[0] if len(valid) >= max(7, days_back//5) else None
    past_h = historical("hibor_fixing_1m", 40)
    past_b = historical("closing_balance", 70)
    if hibor is not None and past_h:
        out["hibor_1m_change_bp"] = round((hibor-past_h[1])*100, 1)
        out["hibor_comparison_as_of"] = past_h[0].isoformat()
    if balance is not None and past_b and past_b[1] > 0:
        out["aggregate_balance_change_pct"] = round(
            (balance/past_b[1]-1)*100, 1
        )
        out["balance_comparison_as_of"] = past_b[0].isoformat()
    return out


def fed_rate_snapshot(frame, *, as_of: date) -> dict:
    if frame is None or getattr(frame, "empty", True):
        return {"status": "unavailable"}
    f = pd.DataFrame(frame).copy()
    key = "DFEDTARU"
    dcol = "observation_date" if "observation_date" in f else "DATE"
    if key not in f or dcol not in f:
        return {"status": "unavailable"}
    f["day"] = pd.to_datetime(f[dcol], errors="coerce")
    f["rate"] = pd.to_numeric(f[key], errors="coerce")
    f = f.dropna(subset=["day", "rate"])
    f = f[(f["day"].dt.date <= as_of) & (f["rate"] >= 0)]
    f = f.sort_values("day").drop_duplicates("day", keep="last")
    if f.empty:
        return {"status": "unavailable"}
    latest = f.iloc[-1]
    when = latest["day"].date()
    if (as_of-when).days > 12:
        return {"status": "stale", "as_of": when.isoformat()}
    out = {"status": "observed", "as_of": when.isoformat(),
           "fed_target_upper_pct": round(float(latest["rate"]), 3),
           "source": "FRED DFEDTARU 美联储目标区间上限"}
    previous = f[f["day"].dt.date <= when-timedelta(days=60)]
    if not previous.empty:
        old = previous.iloc[-1]
        out["fed_target_change_60d_bp"] = round(
            (float(latest["rate"])-float(old["rate"]))*100, 1
        )
        out["fed_comparison_as_of"] = old["day"].date().isoformat()
    return out


def inflation_snapshot(frame, *, as_of: date, indicator: str) -> dict:
    """CPI/PPI YoY as data-release-date observations, not current-day prices."""
    if frame is None or getattr(frame, "empty", True):
        return {"status": "unavailable"}
    if isinstance(frame, pd.Series):
        f = pd.DataFrame({"day": frame.index, "rate": frame.values})
    else:
        f = pd.DataFrame(frame)
        if {"日期", "今值"}.issubset(f.columns):
            f = f.rename(columns={"日期": "day", "今值": "rate"})
        elif {"date", "value"}.issubset(f.columns):
            f = f.rename(columns={"date": "day", "value": "rate"})
        elif {"index", "value"}.issubset(f.columns):
            f = f.rename(columns={"index": "day", "value": "rate"})
        else:
            return {"status": "unavailable"}
    f["day"] = pd.to_datetime(f["day"], errors="coerce")
    f["rate"] = pd.to_numeric(f["rate"], errors="coerce")
    f = f.dropna(subset=["day", "rate"])
    f = f[f["day"].dt.date <= as_of].sort_values("day")
    if f.empty or (as_of-f.iloc[-1]["day"].date()).days > 75:
        return {"status": "unavailable"}
    row = f.iloc[-1]
    return {"status": "observed", "as_of": row["day"].date().isoformat(),
            "yoy_pct": round(float(row["rate"]), 2),
            "source": f"中国{indicator}同比公开经济指标（记录日期）"}


def assess_hk_liquidity(hkma: dict, fed: dict, cpi: dict, ppi: dict) -> dict:
    """No unsupported 'best buy' prediction from rates or one tight observation."""
    hb = _n(hkma.get("hibor_1m_change_bp"))
    bal = _n(hkma.get("aggregate_balance_change_pct"))
    us = _n(fed.get("fed_target_change_60d_bp"))
    cp = _n(cpi.get("yoy_pct")) if cpi.get("status") == "observed" else None
    pp = _n(ppi.get("yoy_pct")) if ppi.get("status") == "observed" else None
    tightening = [
        (hb is not None and hb >= 30),
        (bal is not None and bal <= -12),
        (us is not None and us >= 25),
    ]
    easing = [
        (hb is not None and hb <= -20),
        (bal is not None and bal >= 10),
        (us is not None and us <= -25),
    ]
    tcount, ecount = sum(tightening), sum(easing)
    if tcount >= 2:
        regime = "funding_pressure"
        reading = "美元与港元融资数据共同显示资金压力，港股估值可能承压；还不能确定流动性谷底。"
    elif ecount >= 2:
        regime = "funding_easing"
        reading = "多项港元/美元资金指标边际改善，可观察港股估值修复，但未证明估值足够便宜。"
    elif (sum(x is not None for x in (hb, bal, us)) >= 2):
        regime = "mixed"
        reading = "融资指标没有形成同向信号，不能把单一港元拆息或美联储利率变化称为港股流动性底部。"
    else:
        regime = "insufficient"
        reading = "港元及美元资金指标不足，尚不能确认离岸流动性的方向。"
    if cp is not None and pp is not None and cp < 0 and pp < 0:
        earnings = "CPI和PPI同比均为负，企业定价与盈利需要关注持续通缩压力。"
    elif pp is not None and pp < 0:
        earnings = "PPI同比为负反映工业品价格压力，不等于所有消费价格或企业利润都在下降。"
    elif cp is not None and cp < 0:
        earnings = "CPI同比为负，需求与企业提价能力值得进一步核验。"
    else:
        earnings = "当前可用CPI/PPI未共同确认通缩，盈利仍须按实际企业财报核对。"
    return {
        "regime": regime, "funding_assessment": reading,
        "china_inflation_assessment": earnings,
        "sustained_pressure_confirmed": tcount >= 2,
        "funding_rebound_confirmed": ecount >= 2,
        "liquidity_bottom_confirmed": False,
        "buy_timing_principle": (
            "先观察基本面和估值，再等待港元融资压力极值后的持续改善；"
            "单纯低流动性可能意味着危机深化，不能认定买在最低点。"
        ),
        "framework": _HYPOTHESIS,
    }


async def _ak_data(func: str):
    method = getattr(ak, func, None)
    if not callable(method):
        return None
    try:
        return await asyncio.wait_for(asyncio.to_thread(method), timeout=11)
    except Exception as exc:
        utils.logger.warning("[hk liquidity] %s unavailable: %s", func, exc)
        return None


async def _external_data(url: str, params: dict | None = None):
    try:
        async with httpx.AsyncClient(timeout=9, follow_redirects=True) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
        if url == _FED_URL:
            return pd.read_csv(StringIO(r.text))
        return r.json()
    except Exception as exc:
        utils.logger.warning("[hk liquidity] %s unavailable: %s", url, exc)
        return None


async def get_hk_liquidity_context(*, as_of: date | None = None) -> dict:
    as_of = as_of or datetime.now(ZoneInfo("Asia/Shanghai")).date()
    ck = as_of.isoformat()
    cached = _CACHE.get(ck)
    if cached and time.monotonic()-cached[0] < _CACHE_TTL:
        return dict(cached[1])
    lock = _LOCKS.setdefault(ck, asyncio.Lock())
    async with lock:
        cached = _CACHE.get(ck)
        if cached and time.monotonic()-cached[0] < _CACHE_TTL:
            return dict(cached[1])
        raw = await asyncio.gather(
            _external_data(_HKMA_URL, {"pagesize": 100, "sortby": "end_of_date",
                                       "sortorder": "desc"}),
            _external_data(_FED_URL),
            _ak_data("macro_china_cpi_yearly"),
            _ak_data("macro_china_ppi_yearly"),
        )
        hkma = hkma_liquidity_snapshot(raw[0], as_of=as_of)
        fed = fed_rate_snapshot(raw[1], as_of=as_of)
        cpi = inflation_snapshot(raw[2], as_of=as_of, indicator="CPI")
        ppi = inflation_snapshot(raw[3], as_of=as_of, indicator="PPI")
        context = {
            "exposure": "hk_equities",
            "as_of": as_of.isoformat(),
            "hkma": hkma, "fed": fed, "china_cpi": cpi, "china_ppi": ppi,
            "assessment": assess_hk_liquidity(hkma, fed, cpi, ppi),
        }
        _CACHE[ck] = (time.monotonic(), context)
        return dict(context)
