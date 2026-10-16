"""Date-limited, independently checked macro fallback observations.

These values were checked against the linked official issuer pages on 2026-10-16.
They are NOT live API responses and must NEVER silently be labelled realtime.
A fallback expires BEFORE the next scheduled policy release/meeting. Historical
research must not use this file as a reconstructed point-in-time database.
"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo
from typing import Optional


# (publication/observation day, first invalid date, source). Dates checked
# against original issuer pages, not inferred from model knowledge.
FED_DATE = date(2026, 9, 16)
FED_EXPIRY = date(2026, 10, 27)  # FOMC Oct 27–28; stop before meeting.
FED_URL = "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm"
UST_DATE = date(2026, 10, 14)
UST_EXPIRY = date(2026, 10, 19)  # daily series; only a short outage bridge.
UST_URL = "https://fred.stlouisfed.org/series/DGS10"
CHINA_DATE = date(2026, 9, 20)
CHINA_EXPIRY = date(2026, 10, 20)  # next expected monthly LPR release.
CHINA_URL = "https://www.bankofchina.com/fimarkets/lilv/fd32/201310/t20131031_2591219.html"
JAPAN_DATE = date(2026, 9, 18)
JAPAN_EXPIRY = date(2026, 10, 29)  # next BOJ decision Oct 29–30.
JAPAN_URL = "https://www.boj.or.jp/en/"
EURO_DATE = date(2026, 9, 10)
EURO_EXPIRY = date(2026, 10, 27)  # deliberately conservative; refresh before Oct.
EURO_URL = "https://www.ecb.europa.eu/press/pr/date/2026/html/ecb.mp260910~314e508016.en.html"

NOTE = ("带日期的官方原始来源后备快照（2026-10-16人工核对）；"
        "并非本次实时抓取，逾期自动停用，必须检查下一次政策决定。")


def _today() -> date:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def _eligible(observed: date, expiry: date, day: date) -> bool:
    return observed <= day < expiry


def us_reference(*, today: Optional[date] = None) -> dict:
    day = today or _today()
    out = {"source_name": "官方公告与美联储H.15核验后备",
           "source_tier": "A", "source_mode": "dated_verified_fallback",
           "fallback_note": NOTE, "source_urls": {}}
    if _eligible(FED_DATE, FED_EXPIRY, day):
        out.update({
            "fed_target_lower_pct": 3.75,
            "fed_target_upper_pct": 4.0,
            "fed_target_lower_as_of": FED_DATE.isoformat(),
            "fed_target_upper_as_of": FED_DATE.isoformat(),
            "fed_target_last_decision_change_pp": .25,
            "fed_target_freshness": {"fresh": True,
                "age_days": (day-FED_DATE).days},
        })
        out["source_urls"].update({"target_lower": FED_URL, "target_upper": FED_URL})
    if _eligible(UST_DATE, UST_EXPIRY, day):
        out.update({
            "us10y_yield_pct": 5.19,
            "us10y_as_of": UST_DATE.isoformat(),
            "us10y_freshness": {"fresh": True,
                "age_days": (day-UST_DATE).days},
        })
        out["source_urls"]["us10y"] = UST_URL
    return out if out["source_urls"] else {}


def china_reference(*, today: Optional[date] = None) -> dict:
    day = today or _today()
    if not _eligible(CHINA_DATE, CHINA_EXPIRY, day):
        return {}
    return {
        "source_name": "中国银行公布的全国银行间同业拆借中心LPR（后备）",
        "source_tier": "A", "source_mode": "dated_verified_fallback",
        "fallback_note": NOTE, "source_url": CHINA_URL,
        "as_of": CHINA_DATE.isoformat(),
        "lpr_1y_pct": 3.0, "lpr_5y_pct": 3.5,
        "lpr_1y_change_6obs_pp": 0.0, "lpr_5y_change_6obs_pp": 0.0,
        "freshness": {"fresh": True, "age_days": (day-CHINA_DATE).days},
        "note": "LPR为贷款市场报价利率；不是中国人民银行政策利率。",
    }


def international_reference(region: str, *, today: Optional[date] = None) -> dict:
    day = today or _today()
    conf = {
        "japan": (JAPAN_DATE, JAPAN_EXPIRY, JAPAN_URL, "日本央行", 1.25, .25,
                  "无担保隔夜拆借利率目标"),
        "euro": (EURO_DATE, EURO_EXPIRY, EURO_URL, "欧洲央行", 2.50, .25,
                 "存款便利利率（非主要再融资利率）"),
    }.get(region)
    if conf is None:
        return {}
    observed, expiry, source, label, rate, delta, definition = conf
    if not _eligible(observed, expiry, day):
        return {}
    return {
        "bank": label, "decision_as_of": observed.isoformat(),
        "policy_rate_pct": rate, "last_decision_change_pp": delta,
        "change_180d_pp": None,
        "freshness": {"fresh": True, "age_days": (day-observed).days},
        "source_name": "央行官方决议（有日期后备快照）",
        "source_mode": "dated_verified_fallback",
        "fallback_note": NOTE, "source_tier": "A", "source_url": source,
        "rate_definition_note": definition + "，不得当作实际利率。",
    }
