"""Select and execute industry valuation with a strict source manifest.

This module keeps screening ratios/estimated long-run ROE separate from
investable valuations. A user/issuer supplied explicit projection is required.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, Mapping, Optional

from analysis.valuation_macro import (build_macro_valuation_context,
                                      stress_valuation_with_verified_exposures)
from analysis.valuation_models import (calculate_industry_valuation, select_models,
                                       dividend_discount, adjusted_nav)
from analysis.investor_valuation import investor_reference_valuation
from analysis.market_research_context import (
    build_style_investment_context, style_investment_prompt_block,
)


_EVIDENCE_RULES = {
    "dividend_discount": {
        "annual_dividends_per_share": "forecast",
        "terminal_dividend_per_share": "forecast",
        "required_return_pct": "policy",
        "terminal_growth_pct": "policy",
    },
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
    return {"status": "documented_input_manifest" if not (missing or invalid) else "incomplete",
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
    reference = investor_reference_valuation(
        archetype=archetype,
        dividend_history=getattr(inputs, "dividend_history", None),
        valuation=getattr(inputs, "valuation", None),
        profitability_trend=getattr(inputs, "profitability_trend", None),
        valuation_history=getattr(inputs, "valuation_history", None),
        quote=getattr(inputs, "quote", None),
        as_of=getattr(inputs, "as_of", None),
    )
    style_context = build_style_investment_context(
        as_of=getattr(inputs, "as_of", None),
        archetype=archetype, industry=industry,
        macro_context=macro,
        knowledge_excerpts=getattr(inputs, "knowledge_excerpts", None),
        market_context=getattr(inputs, "market_context", None),
    )
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
    if checked["status"] != "documented_input_manifest":
        reasons.append("缺少标记来源及假设依据的行业模型输入，不采用任意ROE/payout默认值生成目标价")
    policy = _extra_guards(route["primary"], payload)
    if policy:
        reasons.append(policy)
    val = {
        "status": "insufficient_evidence", "route": route,
        "required": list(required), "missing": checked["missing"],
        "reason": "；".join(reasons) if reasons else "尚未满足模型适用性",
    }
    if checked["status"] == "documented_input_manifest" and not reasons:
        val = calculate_industry_valuation(
            archetype=archetype, industry=industry, inputs=payload, market_price=price)
    cross_checks = []
    independent_inputs = context.get("cross_check_inputs") or {}
    independent_sources = context.get("cross_check_provenance") or {}
    if not isinstance(independent_inputs, dict):
        independent_inputs = {}
    if not isinstance(independent_sources, dict):
        independent_sources = {}
    for secondary in route["cross_checks"]:
        item = independent_inputs.get(secondary) or {}
        prov = independent_sources.get(secondary) or {}
        check = _evidence_status(item, prov, _EVIDENCE_RULES[secondary], as_of)
        if check["status"] != "documented_input_manifest":
            cross_checks.append({
                "model": secondary, "status": "not_run",
                "reason": "缺少独立输入来源，不能伪造第二种模型的验证结果",
                "missing": check["missing"], "invalid": check["invalid"],
            })
            continue
        try:
            if secondary == "dividend_discount":
                other = dividend_discount(
                    annual_dividends_per_share=item["annual_dividends_per_share"],
                    terminal_dividend_per_share=item["terminal_dividend_per_share"],
                    required_return_pct=item["required_return_pct"],
                    terminal_growth_pct=item["terminal_growth_pct"],
                    market_price=price,
                )
            elif secondary == "adjusted_nav":
                other = adjusted_nav(
                    fair_assets_per_share=item["fair_assets_per_share"],
                    total_obligations_per_share=item["total_obligations_per_share"],
                    realization_tax_and_cost_per_share=item["realization_tax_and_cost_per_share"],
                    market_price=price,
                )
            else:
                other = calculate_industry_valuation(
                    archetype="general", inputs=item, market_price=price,
                )
        except (TypeError, ValueError, OverflowError) as exc:
            cross_checks.append({"model": secondary, "status": "invalid_inputs",
                                 "reason": str(exc)})
            continue
        entry = {"model": secondary, "status": other.get("status"),
                 "valuation": other}
        if val.get("status") == "calculated" and other.get("status") == "calculated":
            main_val = val["intrinsic_per_share"]
            second_val = other["intrinsic_per_share"]
            if main_val > 0:
                entry["disagreement_pct_of_primary"] = round(
                    100 * (second_val - main_val) / main_val, 2
                )
                if abs(entry["disagreement_pct_of_primary"]) >= 30:
                    entry["warning"] = "模型估值相差30%以上：不能取平均，须核对资本开支、终值及资产负债口径"
        cross_checks.append(entry)
    # Shock coefficients are company-specific calibrated observations, not
    # market-wide constants. No calibrated exposure => no numerical stress.
    scenario_context = context.get("scenario_context") or {}
    if not isinstance(scenario_context, dict):
        scenario_context = {}
    calibrated = scenario_context.get("macro_sensitivities") or {}
    user_shocks = scenario_context.get("macro_shocks") or []
    if not isinstance(calibrated, dict):
        calibrated = {}
    if not isinstance(user_shocks, list):
        user_shocks = []
    macro_stress = []
    if val.get("status") == "calculated":
        for scenario_shock in user_shocks[:8]:
            if not isinstance(scenario_shock, dict):
                continue
            macro_stress.append(stress_valuation_with_verified_exposures(
                archetype=archetype, industry=industry,
                model_inputs=payload, shock=scenario_shock, exposures=calibrated,
                market_price=price, as_of=as_of,
            ))
    if macro_stress:
        macro["macro_shocks_applied"] = any(x.get("status") == "calculated" for x in macro_stress)
    return {
        "framework_version": "industry-multimodel-2026-10-09-v1",
        "assumption_status": "user_documented_not_independently_audited",
        "status": val["status"], "route": route,
        "valuation": val, "cross_checks": cross_checks,
        "investor_reference": reference, "style_context": style_context,
        "evidence_gate": checked, "macro_context": macro,
        "assumptions": {
            "required_return_pct": payload.get("required_return_pct"),
            "terminal_growth_pct": payload.get("terminal_growth_pct"),
        },
        "interactive_inputs": payload if val.get("status") == "calculated" else None,
        "provenance": source_manifest if val.get("status") == "calculated" else None,
        "warnings": [
            "输入来源清单通过格式校验不代表已核验原始年报、现金流预测可靠性或财务调整正确性",
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
            f"完整现金流/行业模型缺少预测数据；缺口={calculation.get('missing')}。"
            "这不代表估值完全缺失，须继续使用下列投资者参考估值框架。"
        )
    reference = model.get("investor_reference") or {}
    lines.append(
        "投资者参考估值（非内在价值，不是保证买点）："
        f"状态={reference.get('status')}；"
        f"历史已实施分红锚={reference.get('dividend_basis')}；"
        f"现金股息收益率参考情景={reference.get('dividend_scenarios')}；"
        f"历史PB中枢={reference.get('historical_pb_anchor')}；"
        f"跨周期ROE/盈利收益率={reference.get('normalized_earnings')}；"
        f"明确的假设={reference.get('key_assumptions')}。"
    )
    lines.append(style_investment_prompt_block(model.get("style_context")))
    lines.extend([
        f"宏观环境观测={macro.get('observations')}",
        f"美元兑人民币定性趋势={macro.get('rmb_direction')}，数值可用={macro.get('rmb_is_dated')}",
        f"外围收入占比={macro.get('overseas_revenue_pct')}%；"
        f"行业/市场传导路径={macro.get('transmission_paths')}",
        f"宏观证据缺口={macro.get('warnings')}",
        f"经校准的宏观压力情景={model.get('macro_stress_scenarios')}",
        f"独立第二种模型交叉检验={model.get('cross_checks')}",
        "严禁用海外收入占比直接推美元净敞口，不得将美债利率或A股红利风格自动加成固定PE。",
        "若完整模型状态不为calculated，严禁把参考收益率价格说成内在价值或精准目标价；"
        "但必须说明可用的股息率参考价、历史PB位置及当前隐含收益率，"
        "不能仅以缺少完整FCFE为由放弃估值。",
    ])
    return "\n".join(lines)
