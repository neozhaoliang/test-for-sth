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


def align_holder_price_observations(
    shareholder_trend: Optional[Dict], price_df,
    *, as_of: Optional[str] = None, max_price_gap_days: int = 10,
    max_points: int = 12,
) -> list[Dict]:
    """Join *disclosed* holder-period counts to actual dated stock closes.

    Only nearest *previous* trading-day raw closes are accepted; weekends
    and market holidays are okay within max_price_gap_days. No interpolation,
    forward-looking data, or assumptions that all quarterly dates are known.
    """
    records = (shareholder_trend or {}).get("series") or []
    if not records or price_df is None or getattr(price_df, "empty", True):
        return []
    try:
        if not {"date", "close"}.issubset(price_df.columns):
            return []
        prices = {}
        for point in price_df[["date", "close"]].itertuples(index=False, name=None):
            dt = _date(point[0])
            close = _finite(point[1])
            if dt is not None and close is not None and close > 0:
                prices[dt] = close
    except (AttributeError, TypeError, ValueError):
        return []
    days = sorted(prices)
    cutoff = _date(as_of)
    if cutoff is not None:
        days = [d for d in days if d <= cutoff]
    if not days:
        return []

    from bisect import bisect_right
    observations = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        period = _date(record.get("period"))
        disclosed = _date(record.get("available_at") or record.get("published_at"))
        count = _finite(record.get("holders"))
        if (period is None or count is None or count <= 0
                or count != int(count)):
            continue
        # A real historical view must use information public *at the time*.
        # An F10-only count with unknown disclosure date is strictly live-only.
        if cutoff is not None and (disclosed is None or disclosed > cutoff):
            continue
        pos = bisect_right(days, period) - 1
        if pos < 0:
            continue
        matched_day = days[pos]
        if (period - matched_day).days > max_price_gap_days:
            continue
        observations[period] = {
            "period": period.isoformat(),
            "holders": int(count),
            "price": round(prices[matched_day], 4),
            "price_date": matched_day.isoformat(),
            "holder_published_at": disclosed.isoformat() if disclosed else None,
        }
    # Ascending time order: changes are measured from the earliest real point.
    return [observations[d] for d in sorted(observations)[-max_points:]]


def assess_chip_price_context(
    market_context: Optional[Dict],
    shareholder_trend: Optional[Dict],
    *,
    as_of: Optional[str] = None,
    max_holder_lag_days: int = 180,
    price_holder_series: Optional[list[Dict]] = None,
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
        "price_holder_series": list(price_holder_series or []),
        "comparison_price_change_pct": None,
        "comparison_holder_change_pct": None,
        "comparison_start": None,
        "comparison_end": None,
        "co_movement": None,
        "comparison_note": "仅使用已披露的股东户数以及对应报告期之前最近交易日的实际收盘价，不插值、不预测。",
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
    pairs = result["price_holder_series"]
    if len(pairs) >= 2:
        first, last = pairs[0], pairs[-1]
        first_price, last_price = _finite(first.get("price")), _finite(last.get("price"))
        first_count, last_count = _finite(first.get("holders")), _finite(last.get("holders"))
        if first_price and last_price and first_count and last_count:
            result.update(
                comparison_start=first.get("period"),
                comparison_end=last.get("period"),
                comparison_price_change_pct=round((last_price / first_price - 1) * 100, 2),
                comparison_holder_change_pct=round((last_count / first_count - 1) * 100, 2),
            )
            # Describes co-movement, not identity of trading parties or cause.
            price_move = last_price / first_price - 1
            holders_move = last_count / first_count - 1
            if price_move > .005 and holders_move > .005:
                result["co_movement"] = "价格与股东户数同时增加；高位时需结合换手及机构持仓核验是否存在筹码分散。"
            elif price_move > .005 and holders_move < -.005:
                result["co_movement"] = "价格上涨而股东户数减少；账户集中度提高，但不能据此断定机构吸筹。"
            elif price_move < -.005 and holders_move > .005:
                result["co_movement"] = "价格回撤而股东户数增加；可能存在账户分散，但不能确认谁在接盘。"
            elif price_move < -.005 and holders_move < -.005:
                result["co_movement"] = "价格与股东户数同时减少；不能据此判断风险已经出清。"
            else:
                result["co_movement"] = "价格或股东户数在比较区间内变化较小，趋势不明显。"
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
            note = "52周相对高位且股东户数增加，提示高位筹码分散风险；仍需成交量、机构持股、融资盘与基本面共同验证，才能进一步评估是否存在机构派发，不能据此直接认定。"
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
        f"可比较的已披露股东户数与收盘价配对={len(result.get('price_holder_series') or [])}期；"
        f"配对区间={result.get('comparison_start')}至{result.get('comparison_end')}；"
        f"区间价格变化={result.get('comparison_price_change_pct')}%；"
        f"区间户数变化={result.get('comparison_holder_change_pct')}%。\n"
        f"价量与户数同步性解释={result.get('co_movement') or '暂无足够配对数据'}。\n"
        + "\n".join(
            f"  {p.get('period')}: 户数={p.get('holders')}，"
            f"收盘价={p.get('price')}元(交易日{p.get('price_date')})"
            for p in (result.get('price_holder_series') or [])
        ) + "\n"
        "配对价格是股东统计期末之前最近交易日收盘价（与真实股东披露时点不同），"
        "只陈述历史同步性，不推断机构买卖。\n"
        "注意：低位散户人数多不单独扣分；高位户数增加才触发谨慎核验，"
        "也不能直接断言机构派发；价位不等于估值。"
    )
