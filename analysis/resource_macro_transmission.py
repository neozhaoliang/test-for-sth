"""Evidence-bounded monetary and currency channels for resource miners.

A USD commodity-price shock and a USD/CNY shock are NOT interchangeable.
Price translation is only a unit-price scenario, never a company earnings
sensitivity, and financing depends on currency, float/fixed share and hedges.
"""
from __future__ import annotations

from math import isfinite
from typing import Mapping

from analysis.resource_mining_thesis import is_resource_miner


def _number(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        n = float(v)
        return round(n, 4) if isfinite(n) else None
    except (TypeError, ValueError, OverflowError):
        return None


def build_resource_macro_transmission(*, stock_name="", archetype="",
                                      industry="", macro_context=None,
                                      macro_rates=None, rmb_signal=None,
                                      commodity_signal=None):
    if not is_resource_miner(archetype=archetype, industry=industry,
                            stock_name=stock_name):
        return None
    m = macro_context or {}
    observations = {
        item["metric"]: item for item in (m.get("observations") or [])
        if isinstance(item, Mapping) and item.get("usable")
        and item.get("value") is not None and item.get("as_of")
    }
    def observation(name):
        item=observations.get(name) or {}
        return _number(item.get("value"))
    usd_cny = observation("usdcny_midpoint_cny_per_usd")
    fx_change = observation("usdcny_change_30obs_pct")
    fx_date = (observations.get("usdcny_change_30obs_pct") or {}).get("as_of")
    fed_lower = observation("fed_target_lower_pct")
    fed_upper = observation("fed_target_upper_pct")
    fed_change = observation("fed_target_change_180d_pp")
    us10y = observation("us_10y_yield_pct")
    us10y_change = observation("us10y_change_30d_pp")
    fed_date = (observations.get("fed_target_upper_pct") or {}).get("as_of")
    interest = (macro_rates or {}).get("us") or {}
    # A missing policy series is not proof of easing or tightening.
    policy = (
        "近180天目标利率上限上调" if fed_change is not None and fed_change > .005
        else "近180天目标利率上限下调" if fed_change is not None and fed_change < -.005
        else "近180天目标利率上限未变" if fed_change is not None else "政策利率方向未核实"
    )
    # Classify *products*, not just an index. Zijin has both gold and copper.
    signature = str(stock_name or "") + " " + str(industry or "")
    gold = any(x in signature for x in ("紫金矿业", "黄金", "金矿", "贵金属"))
    copper = any(x in signature for x in ("紫金矿业", "铜矿", "铜业", "铜金属"))
    route = str((commodity_signal or {}).get("route") or "")
    gold = gold or route == "gold_precious"
    copper = copper or route == "copper"
    products = [p for p, enabled in (("黄金", gold), ("铜", copper)) if enabled]
    if not products:
        products = ["公司主要资源产品（待核对矿种及结算货币）"]
    # If the US-dollar price of a commodity is unchanged, a CNY-per-USD
    # decline changes its theoretical CNY unit price by the same percentage.
    # This is NOT a revenue/margin/valuation sensitivity estimate.
    unhedged_unit_price_effect_pct = (
        fx_change if fx_change is not None and (gold or copper) else None
    )
    if fx_change is not None:
        if fx_change < -.05:
            currency_reading = (
                "人民币相对美元升值。在金/铜美元标价不变的对照情景下，"
                "相同商品的人民币标价下行；若美元金铜价格涨幅大于汇率降幅，"
                "人民币商品价格仍可上涨。"
            )
        elif fx_change > .05:
            currency_reading = (
                "人民币相对美元贬值。在美元金/铜标价不变的对照情景下，"
                "人民币标价有上行支撑，但不能等同于净利润增加。"
            )
        else:
            currency_reading = (
                "最近30个报价日美元兑人民币变化较小，"
                "金铜美元价格和矿山经营成本可能更重要。"
            )
    else:
        currency_reading = (
            "本次缺少有日期的美元兑人民币变化数据，"
            "不能给当期外汇方向定性；仍需核查结算币种和境内外成本。"
        )
    rate_reading = (
        "美联储目标区间" + (f"{fed_lower:g}%–{fed_upper:g}%" if
        fed_lower is not None and fed_upper is not None else "本次未核实")
        + "，" + policy + "。美国利率和实际收益率变动可能影响黄金持有机会成本、"
        "美元融资及金铜风险溢价；铜还受工业需求、供给约束和能源投资影响。"
        "政策利率并不等同实际利率，不能机械预言金价走势。"
    )
    financing = (
        "人民币升值可能减轻以美元计价的净负债折算压力，"
        "但也可能减少美元结算收入的人民币价值。"
        "实际净效果取决于境内外矿山成本、美元债务及现金、固定/浮动利率和远期套保；"
        "海外收入占比不能当作净美元敞口。"
    )
    result = {
        "status": "evidence_bounded",
        "products": products,
        "fx": {"usd_cny": usd_cny, "change_30obs_pct": fx_change,
               "as_of": fx_date, "unit_price_scenario_pct": unhedged_unit_price_effect_pct},
        "fed": {"lower_pct":fed_lower, "upper_pct":fed_upper,
                "change_180d_pp":fed_change, "as_of":fed_date,
                "direction":policy, "us10y_pct":us10y, "us10y_change_30d_pp":us10y_change},
        "currency_reading": currency_reading,
        "rate_reading": rate_reading,
        "financing_reading": financing,
        "evidence_limit": (
            "商品人民币标价的情景计算不是净利润或股价预测；"
            "未独立核实金铜实际结算价、净外币头寸、套保、浮动利率债务及资本开支前，"
            "不提供利率/汇率每变动1%的公司利润弹性。"
        ),
    }
    return result


def resource_macro_prompt_block(reading) -> str:
    if not reading:
        return ""
    return (
        "## 本矿业公司货币与产业周期的关键因果传导（不是模型给定的买卖结论）\n"
        "产品：" + "、".join(reading["products"]) + "\n"
        "汇率观察（美元兑人民币，人民币/美元）：" + str(reading["fx"]) + "\n"
        "美联储及国债利率观察：" + str(reading["fed"]) + "\n"
        "汇率：" + reading["currency_reading"] + "\n"
        "利率：" + reading["rate_reading"] + "\n"
        "融资与敞口：" + reading["financing_reading"] + "\n"
        "不确定性：" + reading["evidence_limit"] + "\n"
        "请结合公司实际金铜价格、矿山成本和公司官方披露说明当期哪个因素更重要。"
        "不能把'海外收入占比缺失'当作不能讨论黄金/铜的美元定价和人民币折算的理由。"
        "必须区分美元金铜报价上涨、人民币升值、美国加息及真实利率四件事；"
        "本金币净敞口未知时可提出条件判断，但严禁编造利润敏感性。"
    )
