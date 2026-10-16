"""Operating-fundamentals-only ROE valuation laboratory.

Two *conditional* models, not a market-price fitted fair value:
1. Clean-surplus finite (10-year) residual income with ROE fading to
   required return, and terminal residual income = 0 (terminal PB = 1).
2. Constant-ROE constant-growth dividend perpetuity as an optimistic
   persistence cross-check when 0 <= g < min(ROE, r).

P0 never enters valuation; it may only be used later for comparison.
Annual accounting ROE is not unconditionally a perpetual franchise return.
"""
from __future__ import annotations

from datetime import date
from math import isfinite
from statistics import median
from typing import Any, Mapping

from analysis.valuation_payout import annual_payout_from_eps


def number(x):
    if isinstance(x, bool):
        return None
    try:
        y = float(x)
        return y if isfinite(y) else None
    except (TypeError, ValueError, OverflowError):
        return None


def quantile(values, fraction):
    a = sorted(values)
    if not a:
        return None
    i = (len(a)-1)*fraction
    lo = int(i)
    hi = min(lo+1, len(a)-1)
    return a[lo]+(i-lo)*(a[hi]-a[lo])


def calculate_roe_prices(*, book_per_share: float, roe_pct: float,
                         growth_pct: float, discount_pct: float,
                         horizon: int = 10) -> dict:
    """Two algebraically coherent, explicit conditional valuation models.

    First-year EPS=B0*ROE; target growth in *book value* arises from
    retention b=g/ROE (100% retained-earnings conversion assumption).
    To honor the growth_pct slider, B(t) grows by exactly g each year;
    payout_t=(ROE_t-g)/ROE_t therefore changes as ROE fades. Modeling
    a fixed payout instead would falsely imply that the requested growth
    rate falls over the horizon while still labelling it "long-term".
    Modeled ROE fades linearly to required return at horizon; the
    clean-surplus identity B(t)=B(t-1)+EPS-D holds.
    Beyond horizon ROE = discount rate: future residual income = 0.
    """
    b = number(book_per_share)
    roe = number(roe_pct)
    g = number(growth_pct)
    k = number(discount_pct)
    if (b is None or roe is None or g is None or k is None
            or b <= 0 or not 0 < roe <= 100 or not 0 < k <= 40
            or not isinstance(horizon, int) or not 5 <= horizon <= 30):
        return {"status": "invalid_input", "reason": "需要正的每股净资产、ROE、折现率与5—30年期限"}
    roe, g, k = roe/100, g/100, k/100
    if g < 0 or g >= roe or g >= k-0.005:
        return {"status": "invalid_growth",
                "reason": "稳态增长率须非负、低于ROE，且至少低于贴现率0.5个百分点；否则不适用"}
    payout = 1-g/roe
    book = b
    residual_pv = 0.0
    dividend_pv = 0.0
    for year in range(1, horizon+1):
        # Linear fade: starting ROE in year 1, required return by year N.
        year_roe = roe+(k-roe)*(year-1)/(horizon-1)
        earnings = book*year_roe
        # Sustain the explicit equity-growth assumption at every forecast
        # year. Dividends are residual; this is NOT a distribution forecast.
        dividend = earnings - book*g
        residual_pv += (earnings-k*book)/(1+k)**year
        dividend_pv += dividend/(1+k)**year
        book += earnings-dividend
        if not isfinite(book) or book <= 0:
            return {"status": "invalid_book", "reason": "模型净资产出现非正值"}
    terminal_pv = book/(1+k)**horizon
    finite = b + residual_pv
    # Independent clean-surplus identity must hold (within floating precision).
    assert abs(finite-(dividend_pv+terminal_pv)) < 1e-7*max(1, abs(finite))
    steady = b*(roe-g)/(k-g)
    if not all(isfinite(x) for x in (finite, steady)):
        return {"status": "invalid_result", "reason": "计算结果不是有限数"}
    return {
        "status": "calculated",
        "finite_fade_price": round(finite, 4),
        "steady_growth_price": round(steady, 4),
        "implied_payout_pct": round(payout*100, 3),
        "implied_payout_final_year_pct": round((1-g/k)*100, 3),
        "assumed_book_growth_pct": growth_pct,
        "pv_residual_income_yuan": round(residual_pv, 4),
        "first_year_eps_yuan": round(b*roe, 4),
        "first_year_dividend_yuan": round(b*(roe-g), 4),
        "end_book_per_share": round(book, 4),
        "terminal_book_share_pct": round(100*terminal_pv/finite, 2) if finite>0 else None,
        "horizon_years": horizon,
        "notes": [
            "主模型：有限期剩余收益折现，ROE在%d年内线性回归权益资本成本，期末PB=1（不由股价拟合）" % horizon,
            "对照模型：恒定ROE和增长率的永续股利模型，只是强持续性假设",
            "增长率=g是预测期每年的账面权益增长目标；随ROE下行，模型会逐年调节隐含派息率（首年与末年不同）。派息率是数学反推，非真实现金分红预测",
        ],
    }


def build_roe_discount_lab(*, valuation=None, profitability_trend=None,
                           valuation_assumption_context=None, as_of=None,
                           archetype="general") -> dict:
    """Derive only historically evidenced anchors; expose gaps to user sliders.

    Missing cost of equity is always user-set: it cannot be estimated from
    historical P/E, P/B, price regression, or stock return without noise.
    """
    try:
        cutoff = date.fromisoformat(str(as_of)[:10])
    except (ValueError, TypeError):
        cutoff = date.today()
    book = number((valuation or {}).get("nav_per_share"))
    result = {
        "status": "missing_book" if not book or book <= 0 else "interactive_assumptions",
        "method": "clean_surplus_roe_fade_10y_plus_gordon_v1",
        "book_per_share": round(book, 4) if book and book>0 else None,
        "industry_archetype": archetype,
        "book_basis": "财务数据中的每股净资产；如股本、少数股东权益或资产减值发生重大变化须重新核验",
        "roe_observations": [], "roe_anchor": None,
        "eps_growth_evidence": None, "payout_evidence": None,
        "defaults": {"roe_pct": None, "growth_pct": None, "discount_pct": None},
        "parameter_basis": {
            "roe": "最近最多5个完整会计年度的ROE分位与中位数；非季度ROE年化，不用股价回归",
            "growth": "历史EPS复合增长与留存收益约束交叉检查；周期股不由历史EPS外推长期增长",
            "discount": "权益资本成本需独立于市场股价：可参考同期中国长期国债收益率+审慎权益风险溢价+具体经营风险，证据不足由用户设定",
        },
        "warnings": [
            "历史ROE包含一次性损益、资产减值或杠杆放大时，应手动修正正常化ROE",
            "权益资本成本是投资者要求回报率，不是历史股价拟合出的无噪声常数",
            "模型没有审计FCFE、维持性资本开支、发债、增发稀释和非经常性利润；输出仅为条件估值",
            "十年ROE回归资本成本是压力情景，不是已核实的未来盈利预测；权益增长率和隐含派息不代表矿山真实现金流",
        ],
    }
    if result["status"] == "missing_book":
        result["warnings"].append("缺少正的每股净资产；不能凭现价代填")
        return result

    annual = {}
    eps = {}
    for row in (profitability_trend or {}).get("periods") or []:
        try:
            d = date.fromisoformat(str(row.get("period") or "")[:10])
        except ValueError:
            continue
        if d > cutoff or d.month != 12 or d.day != 31:
            continue
        # Period end alone does not prove publication: last full calendar year
        # may be not yet published at the requested as-of date.
        if d.year == cutoff.year or (d.year == cutoff.year-1 and cutoff.month < 5):
            continue
        r = number(row.get("roe_pct"))
        e = number(row.get("eps_yuan"))
        if r is not None and -100 < r < 100:
            annual[d.year] = r
        if e is not None:
            eps[d.year] = e
    yrs = sorted(annual)[-5:]
    vals = [annual[y] for y in yrs]
    result["roe_observations"] = [{"year": y, "roe_pct": round(annual[y], 3)} for y in yrs]
    if len(vals) >= 3:
        q25, q50, q75 = [quantile(vals, q) for q in (.25, .5, .75)]
        result["roe_anchor"] = {
            "status": "historical_observed",
            "years": yrs, "low_pct": round(q25, 3),
            "median_pct": round(q50, 3), "high_pct": round(q75, 3),
            "note": "历史样本分位数，作为压力情景区间而非ROE预测置信区间",
        }
        if q50 > 0:
            result["defaults"]["roe_pct"] = round(min(60, q50), 2)
    else:
        result["warnings"].append("完整年度ROE少于3期，ROE只能由用户手动设置")

    eps_years = sorted(eps)[-5:]
    if (len(eps_years) >= 4 and all(eps[y]>0 for y in eps_years)
            and eps_years == list(range(eps_years[0], eps_years[-1]+1))):
        observed_g = 100*((eps[eps_years[-1]]/eps[eps_years[0]])**(1/(len(eps_years)-1))-1)
        result["eps_growth_evidence"] = {
            "years": eps_years, "historical_eps_cagr_pct": round(observed_g, 3),
            "note": "连续完整年度EPS CAGR，不是未来增长预测；含周期、会计与股份变化影响",
        }

    ctx = valuation_assumption_context or {}
    paid = annual_payout_from_eps(
        annual_eps=ctx.get("annual_eps"),
        dividends=ctx.get("fiscal_dividends"),
        as_of=cutoff.isoformat(),
    )
    result["payout_evidence"] = {
        "status": paid["status"],
        "payout_pct": paid.get("payout_pct"),
        "fiscal_years": paid.get("years") or [],
        "note": "同一财年EPS及已实施现金派息比率，仅作为隐含分红率交叉检验",
    }
    # Do not make an unsupported default perpetual EPS growth assumption.
    # Cyclicals always require human judgement on normalized profit/cycle.
    roe_anchor = result["defaults"]["roe_pct"]
    observed = result["eps_growth_evidence"]
    payout = paid.get("payout_pct") if paid["status"] == "verified" else None
    if (archetype not in ("cyclical",) and roe_anchor and observed and payout is not None):
        sustainable_ceiling = roe_anchor*(1-payout/100)
        observed_cagr = observed["historical_eps_cagr_pct"]
        if observed_cagr >= 0:
            # cap below ROE and leave 2pp headroom for discount >= 4%.
            result["defaults"]["growth_pct"] = round(max(0, min(observed_cagr, sustainable_ceiling, 3.0)), 2)
            result["parameter_basis"]["growth"] = (
                "历史EPS正增长CAGR与ROE×已实施派息留存率取低，并将默认稳态增长限制在3%；"
                "这仅是可修改的模型锚而非未来长期事实"
            )
    else:
        result["warnings"].append("增长率没有同时满足EPS历史、真实派息、行业适用性条件；请手动设定（可选零增长压力情景）")

    # An explicit hurdle is NOT inferred from quote, PE, PB or stock returns.
    result["parameter_basis"]["discount"] += "。暂未接入可核验的同期中国国债期限结构及公司个别风险溢价，因此默认留空。"
    return result
