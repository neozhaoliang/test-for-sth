# -*- coding: utf-8 -*-
"""Dated macro/commodity evidence and industry-specific cycle context for ETFs.

All market observations are sourced at request time, not from the model's
memory. An unavailable feed stays unavailable; nominal bond yields are never
labelled as inflation-adjusted real yields.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from io import StringIO
import math
import time
from zoneinfo import ZoneInfo

import akshare as ak
import httpx
import pandas as pd

from tools.utils import utils

_TTL = 900
_CACHE: dict[str, tuple[float, dict]] = {}
_LOCKS: dict[str, asyncio.Lock] = {}

# Prices are industry-specific, rather than the ETF share price masquerading
# as a commodity cycle. Variables are *watch items* unless sourced.
_SECTORS = (
    ("黄金产业股票", ("黄金股", "黄金产业", "金矿股", "黄金矿业"),
     None, ("金价（美元与人民币）", "美国实际利率/美元", "央行购金及投资需求",
            "金矿品位、产量、单位现金成本、资本开支")),
    ("煤炭", ("煤炭", "煤电"), "ZC0",
     ("动力煤现货价格与长协价", "港口库存", "电厂日耗及发电需求", "产能与进口")),
    ("原油石化", ("石油", "油气", "原油", "石化"), "SC0",
     ("原油价格", "OPEC供应、库存", "炼化价差", "油田投资及产量")),
    ("有色铜铝", ("有色", "铜", "矿业", "金属"), "CU0",
     ("铜/铝现货价格与库存", "矿山供给扰动", "冶炼加工费", "下游制造需求")),
    ("钢铁", ("钢铁", "钢材"), "RB0",
     ("螺纹钢价差", "铁矿石/焦煤成本", "钢材库存", "地产和基建需求")),
    ("基础化工", ("化工", "化学", "制冷剂", "氟化工"), "MA0",
     ("主要产品价格和价差", "行业产能利用率与库存", "扩产速度", "下游需求")),
    ("光伏", ("光伏", "太阳能"), None,
     ("硅料/组件价格", "产能利用率与出清", "海外贸易壁垒", "订单与现金流")),
    ("新能源", ("新能源", "电池", "锂电"), None,
     ("锂盐/电池材料价格", "产能利用率", "车企议价", "资本开支")),
    ("半导体", ("半导体", "芯片", "集成电路"), None,
     ("去库存周期与订单", "晶圆厂利用率", "存储器价格", "资本开支与技术限制")),
    ("医药", ("医药", "医疗", "创新药", "生物科技"), None,
     ("集采价格", "医保支付", "药品放量与研发里程碑", "销售现金流")),
    ("银行", ("银行", "金融"), None,
     ("净息差与存贷款利差", "信用成本与不良生成", "社融信用扩张", "资本充足率")),
    ("地产", ("地产", "房地产"), None,
     ("新房销售面积", "库存去化", "居民信贷", "融资与现金回款")),
    ("消费", ("消费", "白酒", "食品饮料", "家电"), None,
     ("终端销量与价格", "渠道库存", "居民收入预期", "消费信心")),
    ("宽基市场", ("沪深300", "中证500", "中证1000", "上证50", "创业板"), None,
     ("企业盈利广度", "利率与风险溢价", "市场流动性", "成分行业结构")),
)
_GENERIC = ("企业利润趋势与盈利广度", "供需/库存和产能", "产品价格与利润率", "资本开支与政策")


def sector_for_etf(name: str) -> dict:
    title = str(name or "")
    for sector, keys, commodity, drivers in _SECTORS:
        if any(k in title for k in keys):
            return {"sector": sector, "drivers": list(drivers),
                    "commodity_contract": commodity}
    return {"sector": "其他行业或混合主题", "drivers": list(_GENERIC),
            "commodity_contract": None}


def _n(v):
    try:
        n = float(str(v).replace(",", "").replace("%", ""))
        return round(n, 6) if math.isfinite(n) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _rows(df, date_col, value_col, *, today: date, max_age: int,
          lookback_days: int = 460):
    """Reject future/stale points and sort dates; never treat row order as time."""
    if df is None or getattr(df, "empty", True):
        return []
    try:
        if value_col is None and isinstance(df, pd.Series):
            frame = pd.DataFrame({"date": df.index, "value": df.values})
            date_col, value_col = "date", "value"
        else:
            frame = pd.DataFrame(df)
        if date_col not in frame or value_col not in frame:
            return []
        frame = frame[[date_col, value_col]].copy()
        frame.columns = ["date", "value"]
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame["value"] = pd.to_numeric(
            frame["value"].astype(str).str.replace(",", "", regex=False)
                          .str.replace("%", "", regex=False), errors="coerce"
        )
        frame = frame.dropna().sort_values("date")
        frame = frame[(frame["date"].dt.date <= today) &
                      (frame["date"].dt.date >= today - timedelta(days=lookback_days))]
        frame = frame.drop_duplicates(subset=["date"], keep="last")
        points = [{"date": str(row.date.date()), "value": float(row.value)}
                  for row in frame.itertuples() if math.isfinite(float(row.value))]
        if not points or (today - date.fromisoformat(points[-1]["date"])).days > max_age:
            return []
        return points
    except (TypeError, ValueError, AttributeError, KeyError):
        return []


def _observation(points, *, title, unit, source, market=False):
    if not points:
        return None
    last = points[-1]
    out = {"name": title, "value": round(last["value"], 3), "unit": unit,
           "as_of": last["date"], "source": source}
    if market:
        # Previous 21/63/252 available sessions for comparable windows; never
        # claim a 1y return on a newly listed market with only a few sessions.
        for sessions, name in ((21, "return_1m_pct"),
                               (63, "return_3m_pct"), (252, "return_1y_pct")):
            if len(points) >= sessions + 1 and points[-sessions-1]["value"] > 0:
                out[name] = round((last["value"]/points[-sessions-1]["value"]-1)*100, 2)
        if len(points) >= 200:
            window = [x["value"] for x in points[-252:]]
            low, high = min(window), max(window)
            out["position_52w_pct"] = (round((last["value"]-low)/(high-low)*100,1)
                                        if high > low else None)
    else:
        # Compare like-for-like time observations, not price returns.
        if len(points) > 1:
            out["previous_value"] = round(points[-2]["value"], 3)
            out["change_pp_or_units"] = round(last["value"]-points[-2]["value"], 3)
            out["previous_as_of"] = points[-2]["date"]
    return out


async def _ak(name, *, params=None, timeout=11):
    fn = getattr(ak, name, None)
    if fn is None:
        return None
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(fn, **(params or {})), timeout=timeout)
    except Exception as exc:
        utils.logger.warning("[etf macro] %s unavailable: %s", name, exc)
        return None


async def _fred_real_yield(today):
    """FRED DFII10 = market 10-year TIPS real yield (%), not nominal-CPI."""
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFII10"
    try:
        async with httpx.AsyncClient(timeout=9, follow_redirects=True) as client:
            res = await client.get(url)
            res.raise_for_status()
        df = pd.read_csv(StringIO(res.text))
        dcol = "observation_date" if "observation_date" in df else "DATE"
        points = _rows(df, dcol, "DFII10", today=today,
                       max_age=15, lookback_days=460)
        return _observation(points, title="美国10年TIPS实际收益率",
                            unit="%", source="FRED DFII10（10年期通胀保值国债）")
    except Exception as exc:
        utils.logger.warning("[etf macro] FRED DFII10 unavailable: %s", exc)
        return None


async def _collect_uncached(sector: dict, today: date) -> dict:
    gold = sector["sector"] == "黄金产业股票"
    contract = sector["commodity_contract"]
    tasks = {
        "lpr": _ak("macro_china_lpr"),
        "money": _ak("macro_china_money_supply"),
        "pmi": _ak("macro_china_pmi_yearly"),
        "bonds": _ak("bond_zh_us_rate",
                     params={"start_date": (today-timedelta(days=180)).strftime("%Y%m%d")}),
        "real": _fred_real_yield(today),
    }
    if gold:
        tasks.update({
            "au": _ak("spot_hist_sge", params={"symbol": "Au99.99"}, timeout=14),
            "comex": _ak("futures_foreign_hist", params={"symbol": "GC"}, timeout=14),
            "reserves": _ak("macro_china_fx_gold"),
        })
    elif contract:
        tasks["commodity"] = _ak("futures_main_sina", params={
            "symbol": contract,
            "start_date": (today-timedelta(days=500)).strftime("%Y%m%d"),
            "end_date": today.strftime("%Y%m%d"),
        }, timeout=14)
    fetched = await asyncio.gather(*tasks.values(), return_exceptions=True)
    raw = dict(zip(tasks, (None if isinstance(v, BaseException) else v
                           for v in fetched)))
    observations = {}
    def add(key, points, title, unit, source, market=False):
        obj = _observation(points, title=title, unit=unit,
                           source=source, market=market)
        if obj:
            observations[key] = obj

    def p(name, dt, value, age=75, lookback=460):
        return _rows(raw.get(name), dt, value, today=today,
                     max_age=age, lookback_days=lookback)
    add("china_lpr1y", p("lpr", "TRADE_DATE", "LPR1Y", 75),
        "中国1年LPR", "%", "东方财富 LPR（贷款定价参考，不是银行间流动性）")
    add("china_m2_yoy", p("money", "月份", "货币和准货币(M2)同比增长", 75),
        "中国M2同比", "%", "东方财富/中国货币供应量")
    add("china_pmi", p("pmi", "index", "value", 75)
        if isinstance(raw.get("pmi"), pd.DataFrame) else
        _rows(raw.get("pmi"), None, None, today=today, max_age=75),
        "中国官方制造业PMI", "指数点", "金十/国家统计局 PMI")
    for key, col, label in (
        ("us_treasury_10y", "美国国债收益率10年", "美国10年名义国债收益率"),
        ("china_treasury_10y", "中国国债收益率10年", "中国10年国债收益率"),
    ):
        add(key, p("bonds", "日期", col, 15), label, "%",
            "东方财富中美国债收益率")
    if raw.get("real"):
        observations["us_real_yield_10y"] = raw["real"]

    if gold:
        add("gold_cny", p("au", "date", "close", 12),
            "上海金 Au99.99", "元/克", "上海黄金交易所历史日行情（SGE）", True)
        add("gold_usd", p("comex", "date", "close", 12),
            "COMEX GC 黄金连续", "美元/盎司", "新浪外盘黄金期货连续", True)
        # Official central-bank holdings are monthly, not daily transactions.
        add("china_official_gold_reserves",
            p("reserves", "月份", "黄金储备-数值", 80),
            "中国官方黄金储备", "万盎司", "东方财富/外汇与黄金储备")
    elif contract:
        add("sector_commodity", p("commodity", "日期", "收盘价", 12),
            contract + " 主力连续", "期货报价（合约单位）",
            "新浪国内期货主力连续；换月可能产生非现货价差", True)

    return {
        "sector": sector["sector"], "key_drivers": sector["drivers"],
        "observations": observations,
        "as_of": today.isoformat(),
        "missing": [label for label, key in (
            ("国内利率LPR", "china_lpr1y"), ("M2货币同比", "china_m2_yoy"),
            ("制造业PMI", "china_pmi"), ("美国10年实际利率", "us_real_yield_10y"),
            ("上海黄金价", "gold_cny"), ("COMEX黄金价", "gold_usd"),
        ) if key not in observations and (
            key not in ("gold_cny", "gold_usd") or gold)],
        "scope": "现价、利率、M2与PMI各有数据时点；不以ETF价格替代大宗商品价格。"
                 "实际利率缺失时，名义收益率不能充当实际利率。"
                 "行业库存、产量、成本与价差尚未连接的项目仅列为核实清单。",
    }


async def get_etf_market_context(name: str, today: date | None = None) -> dict:
    today = today or datetime.now(ZoneInfo("Asia/Shanghai")).date()
    sector = sector_for_etf(name)
    ck = sector["sector"]
    hit = _CACHE.get(ck)
    if hit and time.monotonic()-hit[0] < _TTL:
        return hit[1]
    lock = _LOCKS.setdefault(ck, asyncio.Lock())
    async with lock:
        hit = _CACHE.get(ck)
        if hit and time.monotonic()-hit[0] < _TTL:
            return hit[1]
        obj = await _collect_uncached(sector, today)
        _CACHE[ck] = (time.monotonic(), obj)
        return obj
