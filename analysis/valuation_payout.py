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

def collect_declared_fiscal_payout_inputs(*, profitability_trend, dividend_history,
                                          primary_evidence, stock_code, as_of, filing_calendar=None):
    """Join dividends ONLY to same-day issuer implementation notices with explicit fiscal years.

    Ambiguous notices and multi-match dates are dropped, not allocated to a guessed year.
    """
    import re
    cutoff=date.fromisoformat(str(as_of)[:10])
    annual_eps=[]
    annual_filings = {}
    for filing in filing_calendar or []:
        if filing.get("report_type") != "annual":
            continue
        period = str(filing.get("period") or "")[:10]
        published = str(filing.get("published_at") or "")[:10]
        if not period.endswith("-12-31") or not published or published > cutoff.isoformat() or not filing.get("url"):
            continue
        old=annual_filings.get(period)
        if old is None or old["published_at"] < published:
            annual_filings[period] = {"published_at": published, "url": filing["url"]}
    code="".join(ch for ch in str(stock_code) if ch.isdigit())[-6:]
    for row in (profitability_trend or {}).get("periods") or []:
        period=str(row.get("period") or "")[:10]
        if not period.endswith("-12-31") or period>cutoff.isoformat():
            continue
        eps=number(row.get("eps_yuan"))
        if eps is None or eps<=0:
            continue
        # The filing calendar timestamps market availability; it does not certify
        # the third-party EPS against the filed PDF (including later restatements).
        filing=annual_filings.get(period)
        if not filing:
            continue
        annual_eps.append({
            "fiscal_year":int(period[:4]),"eps_yuan":eps,
            "published_at":filing["published_at"],"source_url":filing["url"],
        })
    declarations={}
    for evidence in primary_evidence or []:
        title=str(evidence.get("title") or "")
        if not re.search(r"权益分派实施|分红派息实施|利润分配实施",title):
            continue
        years=set(int(y) for y in re.findall(r"(20\d{2})年(?:年度|度|中期|末期)",title))
        day=str(evidence.get("published_at") or "")[:10]
        if len(years)!=1 or not day or day>cutoff.isoformat() or not evidence.get("url"):
            continue
        declarations.setdefault(day,[]).append((years.pop(),evidence["url"]))
    matched=[]
    for dividend in dividend_history or []:
        day=str(dividend.get("announce_date") or "")[:10]
        notices=declarations.get(day,[])
        if len(notices)!=1:
            continue
        yr,url=notices[0]
        matched.append({**dividend,"fiscal_year":yr,"source_url":url})
    return {"annual_eps":annual_eps,"fiscal_dividends":matched,
            "note":"EPS数值来自第三方历史指标，仅用年报披露日标记可用时间；尚未逐项与原始PDF核对，不应用于严格历史回测"}
