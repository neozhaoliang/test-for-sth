"""Joint stock-price-position and retail-holder dispersion context.

Price location is descriptive (52-week closing range), NOT intrinsic valuation.
Holder count alone does not identify investor identity or institutional distribution.
Do not produce a directional signal if observation dates or underlying fields are stale.
"""
from __future__ import annotations

from datetime import date
from math import isfinite
from typing import Dict, Optional


def _finite(value) -> Optional[float]:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if isfinite(x) else None


def _date(value) -> Optional[date]:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def assess_chip_price_context(
    market_context: Optional[Dict],
    shareholder_trend: Optional[Dict],
    *,
    as_of: Optional[str] = None,
    max_holder_lag_days: int = 180,
) -> Dict:
    """Conservative, reproducible price-context overlay on holder-count movements."""
    stock = (market_context or {}).get("stock") or {}
    low, high, current = (
        _finite(stock.get("w52_low")),
        _finite(stock.get("w52_high")),
        _finite(stock.get("latest")),
    )
    latest = _date(stock.get("latest_date"))
    cutoff = _date(as_of)
    result: Dict = {
        "status": "insufficient_data",
        "position_52w_pct": None,
        "position_band": "unknown",
        "holder_change_pct": None,
        "holder_context": "unknown",
        "price_date": stock.get("latest_date"),
        "holder_period": (shareholder_trend or {}).get("as_of"),
        "assessment": "缺少可比较的股价或股东户数数据，不能把散户人数解释为利空。",
        "notes": [
            "股价位置指52周收盘区间，不等于估值高低或企业内在价值。",
            "股东户数变化只反映持有人账户数变化，不证明机构派发或主力吸筹。",
        ],
    }
    if (low is None or high is None or current is None or not (0 < low < high)
            or latest is None or not low <= current <= high
            or (cutoff is not None and latest > cutoff)):
        return result
    position = (current - low) / (high - low)
    band = "low" if position <= .25 else "high" if position >= .75 else "middle"
    result.update(
        position_52w_pct=round(100 * position, 2),
        position_band=band,
        status="price_only",
        assessment="52周价格位置已确定，但股东户数缺失或不具备时间可比性；不能给筹码方向性结论。",
    )
    holder = shareholder_trend or {}
    change = _finite(holder.get("change_pct"))
    holder_day = _date(holder.get("as_of"))
    if change is None or holder_day is None or holder_day > latest:
        return result
    lag = (latest - holder_day).days
    if lag > max_holder_lag_days or (cutoff is not None and holder_day > cutoff):
        result["notes"].append(f"股东户数报告期距离股价时点 {lag} 天，超过 {max_holder_lag_days} 天阈值；不联动解读。")
        return result
    result["holder_change_pct"] = round(change, 2)
    result["holder_lag_days"] = lag
    result["status"] = "contextualized"
    if change > 0:
        if band == "low":
            kind = "low_price_more_holders_neutral"
            note = "52周相对低位且股东户数增加，不应因散户多或增加直接扣分；可能是下跌承接或被动分散，仍需现金流、估值与后续趋势验证。"
        elif band == "high":
            kind = "high_price_more_holders_watch"
            note = "52周相对高位且股东户数增加，提示高位筹码分散风险；只有成交量、机构持股、融资盘与基本面共同验证时，才能进一步推断派发。"
        else:
            kind = "middle_price_more_holders_ambiguous"
            note = "52周中间位置且股东户数增加，单独不构成利空；等待价量、估值及机构变化交叉核验。"
    elif change < 0:
        kind = "holders_decreasing_neutral"
        note = "股东户数减少意味着账户集中度可能提高，但并不证明机构吸筹；需观察机构持仓和股价趋势。"
    else:
        kind = "holders_stable_neutral"
        note = "股东户数基本未变，不能仅凭散户人数高低判断筹码风险。"
    result["holder_context"] = kind
    result["assessment"] = note
    return result


def render_chip_price_context(result: Optional[Dict]) -> str:
    if not result:
        return "股价位置与股东户数联合解读: 暂缺。散户多本身不是利空。"
    p = result.get("position_52w_pct")
    ch = result.get("holder_change_pct")
    return (
        f"股价位置与股东户数联合解读：状态={result.get('status')}，"
        f"52周区间位置={str(p) + '%' if p is not None else '暂缺'}，"
        f"股东户数变化={str(ch) + '%' if ch is not None else '暂缺'}，"
        f"价位={result.get('position_band')}，判断={result.get('assessment')}\n"
        "注意：低位散户人数多不单独扣分；高位户数增加才触发谨慎核验，"
        "也不能直接断言机构派发；价位不等于估值。"
    )
