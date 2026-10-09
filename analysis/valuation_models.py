"""Industry-specific, evidence-gated equity valuation models.

No model is selected by the current stock price or investor sentiment.
All monetary model inputs are explicit PER SHARE amounts in one currency.
Source/observation metadata belongs to supplied assumptions and is audited
by the orchestration layer. This module deliberately performs no downloads.

Valuation is conditional, not a certification of a price target.
"""
from __future__ import annotations

from math import isfinite
from typing import Any, Dict, List, Mapping, Optional, Sequence

MIN_TERMINAL_SPREAD = 0.04
MAX_HORIZON = 15


def _f(x: Any, *, allow_negative: bool = False) -> float:
    if isinstance(x, bool):
        raise ValueError("boolean is not a numeric value")
    try:
        v = float(x)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("missing or non-numeric financial value") from None
    if not isfinite(v) or (not allow_negative and v < 0):
        raise ValueError("non-finite or invalid negative financial value")
    return v


def _rate(v: Any, *, allow_negative: bool = False) -> float:
    x = _f(v, allow_negative=allow_negative)
    if x >= 100 or (allow_negative and x < -30):
        raise ValueError("rate is outside plausible validation bounds")
    return x / 100


def _discount_k(v: Any) -> float:
    k = _rate(v)
    if not (0.04 <= k <= 0.35):
        raise ValueError("shareholder required return must be between 4% and 35%")
    return k


def _terminal_g(v: Any, k: float) -> float:
    g = _rate(v, allow_negative=True)
    if g > 0.04:
        raise ValueError("terminal growth above 4% requires manual review")
    if k - g < MIN_TERMINAL_SPREAD - 1e-12:
        raise ValueError("terminal growth is too close to required return")
    return g


def select_models(archetype: str, industry: str = "") -> dict:
    """Route by operating economics, not by a generic historical P/E."""
    labels = {
        "bank": ("bank_residual_income", ["dividend_discount"], "资本约束/不良率/拨备/净息差必审"),
        "financial": ("financial_residual_income", [], "保险/券商会计与资本口径差异大，需专属调整"),
        "cyclical": ("midcycle_fcfe", ["adjusted_nav"], "全周期销量、商品价、成本与维持性资本开支"),
        "stable_yield": ("regulated_dividend_discount", ["owner_fcfe"], "电价/燃料/资本开支/债务/核电退役义务"),
        "consumer_brand": ("owner_fcfe", ["dividend_discount"], "品牌、渠道、经营性现金转换与定价权"),
        "technology": ("owner_fcfe", [], "研发资本化、客户集中、补贴、股权激励和稀释"),
        "general": ("owner_fcfe", ["adjusted_nav"], "自由现金流、维持性CAPEX与资本配置"),
    }
    primary, secondary, checkpoints = labels.get(archetype, labels["general"])
    text = str(industry or "")
    if any(x in text for x in ("房地产", "开发经营", "不动产")):
        primary, secondary, checkpoints = "adjusted_nav", ["owner_fcfe"], "项目折价、债务、预售义务和税费"
    elif any(x in text for x in ("保险", "寿险")):
        primary, secondary, checkpoints = "insurance_embedded_value", [], "内含价值假设、准备金与偿付能力核验"
    elif any(x in text for x in ("核电", "核能")) and archetype == "stable_yield":
        checkpoints += "；退役/乏燃料负债、投运进度与项目融资"
    return {"primary": primary, "cross_checks": secondary,
            "required_checks": checkpoints, "archetype": archetype, "industry": text}


def _result(model: str, intrinsic: float, components: Dict[str, Any], market_price: Optional[float]) -> dict:
    if not isfinite(intrinsic) or intrinsic < 0:
        raise ValueError("invalid intrinsic equity value")
    result = {"status": "calculated", "model": model,
              "intrinsic_per_share": round(intrinsic, 4),
              "components": components,
              "observed_market_price": None,
              "margin_of_safety_pct": None}
    if market_price is not None:
        price = _f(market_price)
        if price <= 0:
            raise ValueError("market_price must be positive")
        result["observed_market_price"] = price
        if intrinsic > 0:
            result["margin_of_safety_pct"] = round((intrinsic - price) / intrinsic * 100, 2)
    return result


def discounted_equity_cash_flow(
    *, annual_cash_flow_per_share: Sequence[float], terminal_cash_flow_per_share: float,
    required_return_pct: float, terminal_growth_pct: float,
    market_price: Optional[float] = None, model: str = "owner_fcfe",
) -> dict:
    """Explicit equity cashflows after maintenance/growth capex, WC, net borrowing.

    Do not substitute operating cash flow or accounting earnings for FCFE.
    Forecast per-share values must already incorporate expected share dilution.
    Terminal input = next-year sustainable FCFE, independently estimated.
    """
    k = _discount_k(required_return_pct)
    g = _terminal_g(terminal_growth_pct, k)
    if not (3 <= len(annual_cash_flow_per_share) <= MAX_HORIZON):
        raise ValueError("explicit FCFE forecast must contain 3-15 years")
    flows = [_f(v, allow_negative=True) for v in annual_cash_flow_per_share]
    tail = _f(terminal_cash_flow_per_share)
    if tail <= 0:
        raise ValueError("normalized next-year FCFE must be positive")
    pv_flows = sum(v / (1 + k) ** (i + 1) for i, v in enumerate(flows))
    terminal_pv = (tail / (k - g)) / (1 + k) ** len(flows)
    total = pv_flows + terminal_pv
    result = _result(model, total, {
        "pv_explicit_cash_flows": round(pv_flows, 4),
        "pv_terminal": round(terminal_pv, 4),
        "terminal_value_share_pct": round(100 * terminal_pv / total, 2) if total > 0 else None,
        "forecast_years": len(flows),
        "required_return_pct": required_return_pct,
        "terminal_growth_pct": terminal_growth_pct,
        "units": "currency/share",
    }, market_price)
    warnings = []
    if total <= 0:
        warnings.append("预测现金流现值不足以支持正向股权价值")
    if total > 0 and terminal_pv / total >= 0.70:
        warnings.append("终值占比≥70%，价值主要依赖未验证的远期现金流")
    if any(v < 0 for v in flows):
        warnings.append("预测期含负股权自由现金流，须核验再融资与稀释")
    result["warnings"] = warnings
    return result


def dividend_discount(
    *, annual_dividends_per_share: Sequence[float], terminal_dividend_per_share: float,
    required_return_pct: float, terminal_growth_pct: float,
    market_price: Optional[float] = None, regulated: bool = False,
) -> dict:
    """Explicit paid/feasible DPS; never reinterpret buybacks as dividends."""
    return discounted_equity_cash_flow(
        annual_cash_flow_per_share=annual_dividends_per_share,
        terminal_cash_flow_per_share=terminal_dividend_per_share,
        required_return_pct=required_return_pct,
        terminal_growth_pct=terminal_growth_pct,
        market_price=market_price,
        model="regulated_dividend_discount" if regulated else "dividend_discount",
    )


def residual_income(
    *, opening_book_per_share: float, annual_roe_pct: Sequence[float],
    annual_payout_pct: Sequence[float], required_return_pct: float,
    market_price: Optional[float] = None, financial: bool = False,
) -> dict:
    """Clean-surplus residual income, terminal residual income conservatively ZERO.

    V0=B0+sum[(ROE_t-k)*B_(t-1)/(1+k)^t].
    Terminal franchise value beyond explicit projection is not fabricated.
    Requires clean-surplus book, verified distributable profits and capital ratios.
    """
    book = _f(opening_book_per_share)
    if book <= 0:
        raise ValueError("positive adjusted common book is required")
    k = _discount_k(required_return_pct)
    if len(annual_roe_pct) != len(annual_payout_pct) or not (3 <= len(annual_roe_pct) <= MAX_HORIZON):
        raise ValueError("ROE and payout paths must both cover 3-15 years")
    residual_pv = 0.0
    rows: List[Dict] = []
    for i, (raw_r, raw_p) in enumerate(zip(annual_roe_pct, annual_payout_pct), start=1):
        roe, payout = _rate(raw_r, allow_negative=True), _rate(raw_p)
        if payout > 1:
            raise ValueError("cash payout cannot exceed 100% in this model")
        earnings = book * roe
        residual = (roe - k) * book
        residual_pv += residual / (1 + k) ** i
        book += earnings * (1 - payout)
        if book <= 0:
            raise ValueError("insolvent projected equity; capital infusion must be modeled separately")
        rows.append({"year": i, "roe_pct": raw_r, "payout_pct": raw_p,
                     "closing_book": round(book, 4)})
    value = _result("financial_residual_income" if financial else "bank_residual_income",
                    _f(opening_book_per_share) + residual_pv,
                    {"pv_residual_income": round(residual_pv, 4),
                     "opening_adjusted_book_per_share": opening_book_per_share,
                     "ending_book_per_share": round(book, 4),
                     "terminal_residual_income": 0,
                     "forecast_years": len(rows), "book_path": rows,
                     "required_return_pct": required_return_pct,
                     "clean_surplus_assumed": True}, market_price)
    value["warnings"] = [
        "终值残余收益设为0是保守情景假设；需核验监管资本与不良/准备金",
        "未建模资本补充、分拆、OCI、会计重估、优先股和摊薄"
    ]
    return value


def adjusted_nav(
    *, fair_assets_per_share: float, total_obligations_per_share: float,
    realization_tax_and_cost_per_share: float, market_price: Optional[float] = None,
) -> dict:
    assets = _f(fair_assets_per_share)
    obligations = _f(total_obligations_per_share)
    realization = _f(realization_tax_and_cost_per_share)
    val = assets - obligations - realization
    if val < 0:
        raise ValueError("adjusted NAV negative; equity impairment/restructuring scenario required")
    result = _result("adjusted_nav", val, {
        "fair_assets_per_share": assets,
        "all_obligations_per_share": obligations,
        "liquidation_taxes_cost_per_share": realization,
        "currency": "user-supplied-consistent-currency",
    }, market_price)
    result["warnings"] = ["资产公允价值、表外担保、债务优先顺位及处置税费必须逐项核实"]
    return result


def calculate_industry_valuation(
    *, archetype: str, industry: str = "", inputs: Optional[Mapping[str, Any]] = None,
    market_price: Optional[float] = None,
) -> dict:
    """No fabricated EPS, terminal cash flow, peer P/E, WACC or discount premium."""
    route = select_models(archetype, industry)
    model = route["primary"]
    required = {
        "owner_fcfe": ("annual_cash_flow_per_share", "terminal_cash_flow_per_share",
                       "required_return_pct", "terminal_growth_pct"),
        "midcycle_fcfe": ("annual_cash_flow_per_share", "terminal_cash_flow_per_share",
                         "required_return_pct", "terminal_growth_pct"),
        "regulated_dividend_discount": ("annual_dividends_per_share",
                                        "terminal_dividend_per_share",
                                        "required_return_pct", "terminal_growth_pct"),
        "bank_residual_income": ("opening_book_per_share", "annual_roe_pct",
                                 "annual_payout_pct", "required_return_pct"),
        "financial_residual_income": ("opening_book_per_share", "annual_roe_pct",
                                      "annual_payout_pct", "required_return_pct"),
        "adjusted_nav": ("fair_assets_per_share", "total_obligations_per_share",
                         "realization_tax_and_cost_per_share"),
        "insurance_embedded_value": ("audited_embedded_value_per_share", "adjustment_per_share"),
    }
    data = dict(inputs or {})
    missing = [key for key in required[model] if data.get(key) is None]
    if model == "insurance_embedded_value":
        # Insurance EV assumptions and risk adjustments require insurer-specific
        # actuarial audits; simplistic EV multiples are not an implementation.
        return {"status": "unsupported", "route": route, "missing": missing,
                "reason": "保险内含价值必须核对精算、利率与偿付能力假设，当前不自动定价"}
    if missing:
        return {"status": "insufficient_evidence", "route": route,
                "required": list(required[model]), "missing": missing,
                "reason": "缺少模型必要的公司现金流/资本/负债与情景预测，禁止生成目标价"}
    try:
        if model in ("owner_fcfe", "midcycle_fcfe"):
            val = discounted_equity_cash_flow(
                **{k: data[k] for k in required[model]},
                market_price=market_price, model=model)
        elif model == "regulated_dividend_discount":
            val = dividend_discount(
                **{k: data[k] for k in required[model]},
                regulated=True, market_price=market_price)
        elif model in ("bank_residual_income", "financial_residual_income"):
            val = residual_income(
                **{k: data[k] for k in required[model]},
                financial=model.startswith("financial"), market_price=market_price)
        else:
            val = adjusted_nav(
                **{k: data[k] for k in required[model]},
                market_price=market_price)
    except (TypeError, ValueError, OverflowError) as exc:
        return {"status": "invalid_inputs", "route": route,
                "reason": str(exc), "missing": []}
    val["route"] = route
    return val
