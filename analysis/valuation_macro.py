"""Point-in-time China/A-share and external macro risk transmission.

Observations are *not* forecast sensitivities. E.g. export share does not
identify net FX exposure (foreign costs, USD liabilities, hedges).
No free 'style premium' is added to a target unless calibrated/supplied.
"""
from __future__ import annotations

from datetime import date
from math import isfinite
from typing import Any, Dict, Mapping, Optional

from analysis.valuation_models import calculate_industry_valuation


def _date(value):
    try:
        return date.fromisoformat(str(value or "")[:10])
    except (ValueError, TypeError):
        return None


def _valid(x):
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (ValueError, TypeError, OverflowError):
        return None
    return v if isfinite(v) else None


def build_macro_valuation_context(
    *, research_profile=None, macro_rates=None, rmb_signal=None,
    market_context=None, fundamentals=None, as_of=None, valuation_history=None,
    policy_events=None, commodity_signal=None, freight_signal=None,
) -> Dict[str, Any]:
    """Returns observed context + transmission hypotheses, NEVER target repricing."""
    data = (fundamentals or {}).get("facts") or {}
    m = macro_rates or {}
    us, cn = m.get("us") or {}, m.get("china") or {}
    stock = (market_context or {}).get("stock") or {}
    archetype = getattr(research_profile, "archetype", None) or "general"
    industry = getattr(research_profile, "industry", None) or ""
    request_day = _date(as_of) or _date(m.get("as_of"))
    external_share = _valid(data.get("overseas_revenue_pct"))
    if external_share is not None and not (0 <= external_share <= 100):
        external_share = None

    def observation(label, value, when, source, fresh=None):
        day = _date(when)
        safe = value is not None and day is not None and (
            request_day is None or day <= request_day)
        if label in ("stock_ytd_return_pct",
                     "usdcny_midpoint_cny_per_usd", "usdcny_change_30obs_pct"):
            safe = safe and (request_day is None or (
                day is not None and 0 <= (request_day - day).days <= 7))
        if fresh is not None:
            safe = safe and bool(fresh)
        return {"metric": label, "value": value if safe else None,
                "as_of": when, "source": source, "usable": bool(safe)}

    indices = (market_context or {}).get("indices") or []
    idx = {str(row.get("name") or ""): row for row in indices}
    red = idx.get("上证红利")
    growth = [idx.get("科创50"), idx.get("创业板指")]
    growth = [row for row in growth if row]
    style_context = {
        "status": "insufficient_data",
        "regime": "unknown",
        "dividend_minus_growth_ytd_pp": None,
        "policy_threshold_pp": 8.0,
        "note": "短期风格相对收益不是企业盈利预测，不能自动加减合理价值",
    }
    if red and growth:
        returns = [_valid(red.get("ytd_pct"))]+[_valid(row.get("ytd_pct")) for row in growth]
        dates = [_date(red.get("latest_date"))]+[_date(row.get("latest_date")) for row in growth]
        if all(v is not None for v in returns) and all(d is not None for d in dates):
            if (max(dates)-min(dates)).days <= 7 and (
                request_day is None or 0 <= (request_day-max(dates)).days <= 7
            ):
                spread = returns[0] - sum(returns[1:]) / len(growth)
                regime = "dividend_leading" if spread >= 8 else (
                    "growth_leading" if spread <= -8 else "mixed"
                )
                style_context = {
                    "status": "observed",
                    "regime": regime,
                    "dividend_minus_growth_ytd_pp": round(spread, 2),
                    "as_of": max(dates).isoformat(),
                    "policy_threshold_pp": 8,
                    "note": "使用上证红利相对科创50/创业板指年内收益；8pp为情景标签阈值，非经验定价beta",
                }

    observations = [
        observation("usdcny_midpoint_cny_per_usd",
                    _valid((rmb_signal or {}).get("usdcny_midpoint_cny_per_usd")),
                    (rmb_signal or {}).get("as_of"),
                    (rmb_signal or {}).get("source_name")),
        observation("usdcny_change_30obs_pct",
                    _valid((rmb_signal or {}).get("usdcny_change_30obs_pct")),
                    (rmb_signal or {}).get("as_of"),
                    (rmb_signal or {}).get("source_name")),
        observation("us_10y_yield_pct", _valid(us.get("us10y_yield_pct")),
                    us.get("us10y_as_of"),
                    (us.get("source_urls") or {}).get("us10y"),
                    (us.get("us10y_freshness") or {}).get("fresh", False)),
        observation("us_10y_change_90d_pp", _valid(us.get("us10y_change_90d_pp")),
                    us.get("us10y_as_of"),
                    (us.get("source_urls") or {}).get("us10y"),
                    (us.get("us10y_freshness") or {}).get("fresh", False)),
        observation("china_lpr_5y_pct", _valid(cn.get("lpr_5y_pct")),
                    cn.get("as_of"), cn.get("source_url"),
                    (cn.get("freshness") or {}).get("fresh", False)),
        observation("stock_ytd_return_pct", _valid(stock.get("ytd_pct")),
                    stock.get("latest_date"), "A-share stock price data"),
    ]
    rmb = rmb_signal or {}
    # RMB collector only returns direction, no dated USD/CNY level. As-of
    # live collector with no observation timestamp is NOT point-in-time auditable.
    fx_direction = rmb.get("rmb_trend") if rmb.get("rmb_trend") in (
        "appreciating", "depreciating", "stable") else None
    if rmb.get("as_of") is None or (
        request_day and (not _date(rmb.get("as_of")) or
        not 0 <= (request_day - _date(rmb.get("as_of"))).days <= 7)
    ):
        # Direction is qualitative live context, not historical model input.
        fx_usable = False
    else:
        fx_usable = True

    warnings = []
    pathways = [
        {"factor": "USD/CNY",
         "route": "外销收入结算币种 → 折算收入 / 进口成本 / 美元债务 / 套保 → 净FCFE",
         "evidence": f"海外收入占比{external_share}%" if external_share is not None else "海外收入占比缺失",
         "quantifiable": False},
        {"factor": "US_10Y",
         "route": "美债收益率 → 全球风险偏好/美元融资成本/行业估值；需美元债权重和历史敏感度",
         "evidence": "新鲜美债10年期数据" if any(x["metric"] == "us_10y_yield_pct" and x["usable"] for x in observations) else "美债数据不可用/过期",
         "quantifiable": False},
        {"factor": "China_rates",
         "route": "LPR/政策和国债利率 → 信贷成本、融资约束与人民币股权折现率",
         "evidence": "LPR观察值，非中国无风险收益率或央行政策利率",
         "quantifiable": False},
        {"factor": "A_share_style",
         "route": "红利/科技风格迁移、ETF及公募持仓拥挤 → 估值折价/风险溢价情景",
         "evidence": "必须先校验风格指数与估值分位，不允许凭叙事加减目标价",
         "quantifiable": False},
        {"factor": "China_policy_governance",
         "route": "国企资本开支/分红约束、价格管制、能源安全/房地产政策、限售解禁",
         "evidence": "需核验国资属性、牌照监管及一手公告，不能推断为国企",
         "quantifiable": False},
    ]
    if archetype == "cyclical":
        pathways.append({"factor": "external_commodity_cycle",
             "route": "国际商品/航运供需 → 人民币成交价 → 中周期利润/FCFE与库存",
             "evidence": "需产品匹配的商品或运价锚；不可用铜价替代所有资源股",
             "quantifiable": False})
    if archetype == "stable_yield":
        pathways.append({"factor": "regulated_tariffs",
             "route": "中国电价/辅助服务/煤价/核电发电利用小时 → 可分配现金",
             "evidence": "需要核对监管文件和固定资产投资、项目负债",
             "quantifiable": False})
    if external_share is None:
        warnings.append("海外收入比例缺失，不知道外部冲击暴露程度")
    elif external_share > 0:
        warnings.append("海外收入占比并非美元净敞口，不能直接推导汇兑损益或税后利润")
    if not fx_usable:
        warnings.append("美元兑人民币仅有定性趋势或缺少完整观测日期，不进入数值估值")
    if not any(x["metric"] == "us_10y_yield_pct" and x["usable"] for x in observations):
        warnings.append("美债收益率缺少新鲜数据，数值敏感性保持缺失")
    return {
        "status": "context_only", "industry_archetype": archetype,
        "industry": industry, "as_of": str(request_day or ""),
        "observations": observations,
        "a_share_style": style_context,
        "rmb_direction": fx_direction,
        "rmb_is_dated": fx_usable,
        "overseas_revenue_pct": external_share,
        "transmission_paths": pathways,
        "warnings": warnings,
        "quantified_equity_impact": None,
        "macro_shocks_applied": False,
        "notes": [
            "A股风险溢价与风格偏好并不等同企业内在现金流；没有已验证的beta不自动调整价值",
            "汇率影响方向取决于净外币现金流/负债/套保和公司定价能力，不由海外营收占比单独决定",
            "估值必须基于人民币投资者的币种一致的现金流与要求收益率，避免美债利率双重计入",
        ],
    }


def stress_valuation_with_verified_exposures(
    *, archetype: str, industry: str, model_inputs: Mapping[str, Any],
    shock: Mapping[str, float], exposures: Mapping[str, Any],
    market_price: Optional[float] = None,
    as_of: Optional[str] = None,
) -> dict:
    """Apply a macro shock ONLY with independently documented linear sensitivities.

    Shock units:
      usdcny_change_pct / us10y_change_pp / china_rate_change_pp / style_premium_change_pp
    Exposure units:
      fcfe_pct_per_1pct_usdcny / discount_pp_per_1pp_us10y /
      discount_pp_per_1pp_china_rate / discount_pp_per_1pp_style
    Exposures must come from calibrated issuer evidence; no defaults.
    """
    keys = {
        "usdcny_change_pct": "fcfe_pct_per_1pct_usdcny",
        "us10y_change_pp": "discount_pp_per_1pp_us10y",
        "china_rate_change_pp": "discount_pp_per_1pp_china_rate",
        "style_premium_change_pp": "discount_pp_per_1pp_style",
    }
    if not shock or any(name not in keys for name in shock):
        return {"status": "invalid_shock", "reason": "unsupported or missing scenario shock"}
    if any(not _valid(shock[key]) and shock[key] != 0 for key in shock):
        return {"status": "invalid_shock", "reason": "non-finite shock"}
    if not exposures.get("source_url") or not _date(exposures.get("as_of")):
        return {"status": "unquantifiable", "reason": "公司敞口系数缺少来源和日期"}
    cutoff = _date(as_of)
    if cutoff and _date(exposures.get("as_of")) > cutoff:
        return {"status": "unquantifiable", "reason": "公司敏感度校准日期晚于估值基准日"}
    if any(_valid(exposures.get(keys[k])) is None for k in shock):
        return {"status": "unquantifiable", "reason": "缺少冲击渠道的经过验证的敏感度系数"}
    if "usdcny_change_pct" in shock and not (
        "annual_cash_flow_per_share" in model_inputs or
        "annual_dividends_per_share" in model_inputs
    ):
        return {"status": "unsupported", "reason": "非FCFE/股息模型的汇率影响应通过盈利和资本重新预测，而非乘现金流系数"}
    inp = dict(model_inputs)
    impact_factor = 1.0
    new_k = _valid(inp.get("required_return_pct"))
    if new_k is None:
        return {"status": "unquantifiable", "reason": "缺少原始折现率"}
    effects = {}
    for k, shock_val in shock.items():
        movement = float(shock_val)
        sensitivity = float(exposures[keys[k]])
        if k == "usdcny_change_pct":
            impact_factor += movement * sensitivity / 100
            effects["fx_fcfe_impact_pct"] = movement * sensitivity
        else:
            change = movement * sensitivity
            new_k += change
            effects[k + "_required_return_change_pp"] = change
    if impact_factor <= 0:
        return {"status": "invalid_shock", "reason": "FX perturbation made cash flow factor non-positive"}
    inp["required_return_pct"] = new_k
    for path in ("annual_cash_flow_per_share", "annual_dividends_per_share"):
        if path in inp:
            inp[path] = [float(x) * impact_factor for x in inp[path]]
    for path in ("terminal_cash_flow_per_share", "terminal_dividend_per_share"):
        if path in inp:
            inp[path] = float(inp[path]) * impact_factor
    val = calculate_industry_valuation(
        archetype=archetype, industry=industry, inputs=inp, market_price=market_price
    )
    return {"status": val["status"], "effects": effects,
            "result": val, "source_url": exposures["source_url"],
            "source_as_of": exposures["as_of"]}
