"""Select and execute industry valuation with a strict source manifest.

This module keeps screening ratios/estimated long-run ROE separate from
investable valuations. A user/issuer supplied explicit projection is required.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, Mapping, Optional

from analysis.valuation_macro import build_macro_valuation_context
from analysis.valuation_models import calculate_industry_valuation, select_models


_EVIDENCE_RULES = {
    "owner_fcfe": {
        "annual_cash_flow_per_share": "forecast",
        "terminal_cash_flow_per_share": "forecast",
        "required_return_pct": "policy",
        "terminal_growth_pct": "policy",
    },
    "midcycle_fcfe": {
        "annual_cash_flow_per_share": "forecast",
        "terminal_cash_flow_per_share": "forecast",
        "required_return_pct": "policy",
        "terminal_growth_pct": "policy",
        "cycle_span_years": "historical",
        "maintenance_capex_basis": "historical",
    },
    "regulated_dividend_discount": {
        "annual_dividends_per_share": "forecast",
        "terminal_dividend_per_share": "forecast",
        "required_return_pct": "policy",
        "terminal_growth_pct": "policy",
        "dividend_coverage_by_fcfe": "forecast",
    },
    "bank_residual_income": {
        "opening_book_per_share": "historical",
        "annual_roe_pct": "forecast",
        "annual_payout_pct": "forecast",
        "required_return_pct": "policy",
        "capital_adequacy_verified": "historical",
    },
    "financial_residual_income": {
        "opening_book_per_share": "historical",
        "annual_roe_pct": "forecast",
        "annual_payout_pct": "forecast",
        "required_return_pct": "policy",
        "capital_adequacy_verified": "historical",
    },
    "adjusted_nav": {
        "fair_assets_per_share": "historical",
        "total_obligations_per_share": "historical",
        "realization_tax_and_cost_per_share": "forecast",
    },
}


def _date(x):
    try:
        return date.fromisoformat(str(x or "")[:10])
    except (TypeError, ValueError):
        return None


def _evidence_status(inputs: Mapping[str, Any], provenance: Mapping[str, Any],
                     requirements: Dict[str, str], as_of: str) -> dict:
    cutoff = _date(as_of)
    missing = []
    invalid = []
    for key, kind in requirements.items():
        if inputs.get(key) is None:
            missing.append(key)
            continue
        meta = provenance.get(key)
        if not isinstance(meta, dict):
            invalid.append(f"{key}: missing provenance")
            continue
        # Forecast and discount policy are scenario assumptions, not historical
        # observed facts. Require explicit documented modelling basis.
        if kind in ("forecast", "policy"):
            if not str(meta.get("assumption_basis") or "").strip():
                invalid.append(f"{key}: missing assumption basis")
        else:
            if not str(meta.get("source_url") or "").startswith(("https://", "http://")):
                invalid.append(f"{key}: missing source URL")
        observed = _date(meta.get("available_at"))
        if observed is None or (cutoff and observed > cutoff):
            invalid.append(f"{key}: missing or future evidence availability date")
        if key.endswith("_per_share") or key == "opening_book_per_share":
            if meta.get("unit") != "CNY/share":
                invalid.append(f"{key}: required CNY/share unit (convert first)")
    return {"status": "verified_input_manifest" if not (missing or invalid) else "incomplete",
            "missing": missing, "invalid": invalid}


def _extra_guards(primary, data):
    """Prevent symbolically calculating without sector-specific economics."""
    if primary == "midcycle_fcfe":
        cycle = data.get("cycle_span_years")
        try:
            years = int(cycle)
        except (TypeError, ValueError):
            years = 0
        if years < 5:
            return "周期股需要至少五年覆盖周期的盈利/现金流证据；不可用景气顶部单年推终值"
        if data.get("maintenance_capex_basis") is None:
            return "周期股缺少维持性资本开支证据"
    if primary == "regulated_dividend_discount":
        try:
            coverage = float(data["dividend_coverage_by_fcfe"])
        except (KeyError, TypeError, ValueError):
            return "公用事业缺少现金股息覆盖率预测"
        if coverage < 1:
            return "预计可分配FCFE不能覆盖预测现金股息，需重做股息预测"
    if primary in ("bank_residual_income", "financial_residual_income"):
        if data.get("capital_adequacy_verified") is not True:
            return "金融股必须核对资本充足或偿付能力/监管资本约束"
    return None


def build_industry_valuation_report(inputs: Any) -> dict:
    """Aggregate macro context and run only source-matched industry models."""
    profile = getattr(inputs, "research_profile", None)
    archetype = getattr(profile, "archetype", None) or "general"
    industry = getattr(profile, "industry", None) or ""
    macro = build_macro_valuation_context(
        research_profile=profile, macro_rates=getattr(inputs, "macro_rates", None),
        rmb_signal=getattr(inputs, "rmb_signal", None),
        market_context=getattr(inputs, "market_context", None),
        fundamentals=getattr(inputs, "fundamentals", None),
        as_of=getattr(inputs, "as_of", None),
        valuation_history=getattr(inputs, "valuation_history", None),
        policy_events=getattr(inputs, "policy_events", None),
        commodity_signal=getattr(inputs, "commodity_signal", None),
        freight_signal=getattr(inputs, "freight_signal", None),
    )
    route = select_models(archetype, industry)
    context = getattr(inputs, "valuation_assumption_context", None) or {}
    payload = context.get("industry_valuation_inputs") or {}
    source_manifest = context.get("industry_valuation_provenance") or {}
    as_of = str(getattr(inputs, "as_of", None) or date.today().isoformat())[:10]
    required = _EVIDENCE_RULES.get(route["primary"], {})
    checked = _evidence_status(payload, source_manifest, required, as_of)
    price = (getattr(inputs, "quote", None) or {}).get("latest_price")
    reasons = []
    if route["primary"] == "insurance_embedded_value":
        reasons.append("保险内含价值估值暂不自动执行：需要精算与偿付能力审计")
    if checked["status"] != "verified_input_manifest":
        reasons.append("缺少已核验的行业模型输入与来源，不采用任意ROE/payout默认值生成目标价")
    policy = _extra_guards(route["primary"], payload)
    if policy:
        reasons.append(policy)
    val = {
        "status": "insufficient_evidence", "route": route,
        "required": list(required), "missing": checked["missing"],
        "reason": "；".join(reasons) if reasons else "尚未满足模型适用性",
    }
    if checked["status"] == "verified_input_manifest" and not reasons:
        val = calculate_industry_valuation(
            archetype=archetype, industry=industry, inputs=payload, market_price=price)
    cross_checks = []
    # A cross-check is genuinely independent only with its own input+provenance,
    # not simply by changing the discount rate in the same model.
    for secondary in route["cross_checks"]:
        cross_checks.append({"model": secondary, "status": "not_run",
                             "reason": "未提供独立的模型现金流/资产质量数据，不伪造交叉验证"})
    return {
        "framework_version": "industry-multimodel-2026-10-09-v1",
        "status": val["status"], "route": route,
        "valuation": val, "cross_checks": cross_checks,
        "evidence_gate": checked, "macro_context": macro,
        "assumptions": {
            "required_return_pct": payload.get("required_return_pct"),
            "terminal_growth_pct": payload.get("terminal_growth_pct"),
        },
        "interactive_inputs": payload if val.get("status") == "calculated" else None,
        "provenance": source_manifest if val.get("status") == "calculated" else None,
        "warnings": [
            "市值风格变化属于市场定价条件，不得替代企业现金流、利润质量和资本结构",
            "宏观系数不经公司层面的实证校验，不直接加减估值价格",
            "不同币种、不同日期的资产和现金流不得直接拼接",
        ],
    }


def industry_model_prompt_block(model: dict) -> str:
    route = model.get("route") or {}
    calculation = model.get("valuation") or {}
    macro = model.get("macro_context") or {}
    lines = [
        "行业估值引擎（Python计算，不得虚构价格）:",
        f"企业类型={route.get('archetype')}；行业={route.get('industry')}",
        f"主模型={route.get('primary')}；独立交叉模型={route.get('cross_checks')}",
        f"必要检验={route.get('required_checks')}",
        f"主模型运行状态={calculation.get('status')}",
    ]
    if calculation.get("status") == "calculated":
        lines.append(
            f"情景股权价值={calculation.get('intrinsic_per_share')}元/股；"
            f"组成项={calculation.get('components')}；模型警示={calculation.get('warnings')}"
        )
    else:
        lines.append(
            f"不能计算合理价格。缺口={calculation.get('missing')}；"
            f"原因={calculation.get('reason')}"
        )
    lines.extend([
        f"宏观环境观测={macro.get('observations')}",
        f"美元兑人民币定性趋势={macro.get('rmb_direction')}，数值可用={macro.get('rmb_is_dated')}",
        f"外围收入占比={macro.get('overseas_revenue_pct')}%；"
        f"行业/市场传导路径={macro.get('transmission_paths')}",
        f"宏观证据缺口={macro.get('warnings')}",
        "严禁用海外收入占比直接推美元净敞口，不得将美债利率或A股红利风格自动加成固定PE。",
        "若模型状态不为calculated，不得给出Python核验过的合理目标价；"
        "可讨论价格、历史估值分位和风险情景，但必须区分事实、假设及未知。",
    ])
    return "\n".join(lines)
