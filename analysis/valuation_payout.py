"""Build comparable-year payout evidence; announcement year is NOT fiscal year."""
from __future__ import annotations
from datetime import date
from math import isfinite
from statistics import median


def number(value):
    try:
        x=float(value)
        return x if isfinite(x) else None
    except (TypeError,ValueError):
        return None


def annual_payout_from_eps(*, annual_eps, dividends, as_of):
    """Only explicit earnings fiscal years and explicit dividend fiscal years match.

    annual_eps: [{fiscal_year, eps_yuan, published_at, source_url}]
    dividends: [{fiscal_year, dividend_per_10_shares, progress,
                 announce_date, source_url}]
    Returns no estimate if coverage is insufficient or statuses are uncertain.
    """
    cutoff=date.fromisoformat(str(as_of)[:10])
    earnings={}
    for e in annual_eps or []:
        yr=e.get("fiscal_year")
        eps=number(e.get("eps_yuan"))
        published=str(e.get("published_at") or "")[:10]
        if not isinstance(yr,int) or eps is None or eps<=0 or not published or published>cutoff.isoformat() or not e.get("source_url"):
            continue
        if yr in earnings and earnings[yr]["eps"]!=eps:
            return {"status":"conflicting_eps","payout_pct":None,"years":[]}
        earnings[yr]={"eps":eps,"source":e["source_url"]}
    by_year={}
    for row in dividends or []:
        yr=row.get("fiscal_year")
        paid=number(row.get("dividend_per_10_shares"))
        published=str(row.get("announce_date") or "")[:10]
        status=str(row.get("progress") or "").strip().lower()
        if not isinstance(yr,int) or not published or published>cutoff.isoformat():
            continue
        if any(t in status for t in ("预案","待实施","未实施","不分配","取消","proposal","pending","unpaid")):
            continue
        if not any(t in status for t in ("实施","completed","implemented","paid","派发","除权除息")):
            continue
        if paid is None or paid<0 or not row.get("source_url"):
            continue
        by_year.setdefault(yr,[]).append({"cash_per_share":paid/10,"source":row["source_url"]})
    obs=[]
    for yr, e in earnings.items():
        if yr not in by_year:
            continue
        total=sum(x["cash_per_share"] for x in by_year[yr])
        ratio=100*total/e["eps"]
        if 0<ratio<=100:
            obs.append({"fiscal_year":yr,"payout_pct":round(ratio,4),
                        "eps_source":e["source"],
                        "dividend_sources":[x["source"] for x in by_year[yr]]})
    obs.sort(key=lambda x:x["fiscal_year"])
    if len(obs)<3:
        return {"status":"insufficient_matched_fiscal_years","payout_pct":None,
                "years":[x["fiscal_year"] for x in obs],"observations":obs}
    recent=obs[-5:]
    return {"status":"verified","payout_pct":round(median(x["payout_pct"] for x in recent),3),
            "years":[x["fiscal_year"] for x in recent],"observations":recent}
