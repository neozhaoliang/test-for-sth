"""Time-safe capital-flow attribution: who receives cash, who pays, what dilutes.

The official issuer ledger is a compact source-checked supplement, not a claim
that every holder trade has been captured. Generic CNINFO title metadata can
support an existence signal only; NEVER infer an executed deal or amount
from a reduction plan or a proposed corporate repurchase.
"""
from __future__ import annotations

import json
import math
import re
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Optional

_LEDGER_DIR = Path(__file__).resolve().parents[1] / "data" / "issuer_capital_events"
_REDUCTION = re.compile(r"减持|持股比例变动|股份减持", re.I)
_FINANCING = re.compile(r"增发|配股|非公开发行|向特定对象发行|H股|再融资|可转债|募集资金", re.I)
_BUYBACK = re.compile(r"回购", re.I)
_TRADE_LABELS = {
    "insider_sale": "内部人/控股股东实际减持",
    "holder_sale": "其他股东实际减持",
    "equity_issuance": "公司股权融资",
    "corporate_buyback": "公司回购",
}


def _date(v):
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def _number(v):
    try:
        out = float(v)
        return out if math.isfinite(out) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _official_issuer_ledger(stock_code: str, as_of: date) -> list:
    code6 = re.sub(r"\D", "", str(stock_code))[-6:]
    if not re.fullmatch(r"\d{6}", code6):
        return []
    try:
        data = json.loads((_LEDGER_DIR / (code6 + ".json")).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if data.get("stock_code") != code6:
        return []
    allowed = set(_TRADE_LABELS)
    rows = []
    for row in data.get("events") or []:
        if not isinstance(row, dict):
            continue
        day = _date(row.get("disclosed_at"))
        if day is None or day > as_of:
            continue
        if row.get("event_type") not in allowed:
            continue
        if not str(row.get("source_url") or "").startswith("https://"):
            continue
        r = dict(row)
        r["source_tier"] = "official_filing"
        r["execution_vs_plan"] = row.get("status")
        r["market_position_at_trade"] = "unverified"
        rows.append(r)
    return sorted(rows, key=lambda x: x["disclosed_at"], reverse=True)


def _metadata_clues(primary_evidence, cutoff: date, already: list) -> list:
    """Only official announcement title/date/source. Not an executed transaction."""
    official_urls = {x.get("source_url", "").lower() for x in already}
    clues = []
    seen = set()
    for entry in primary_evidence or []:
        if not isinstance(entry, Mapping):
            continue
        title = str(entry.get("title") or "").strip()
        day = _date(entry.get("published_at"))
        url = str(entry.get("url") or "")
        if not title or day is None or day > cutoff:
            continue
        if not url.startswith("https://") or url.lower() in official_urls:
            continue
        kind = (
            "insider_sale" if _REDUCTION.search(title) else
            "equity_issuance" if _FINANCING.search(title) else
            "corporate_buyback" if _BUYBACK.search(title) else None
        )
        if kind is None:
            continue
        key = (day.isoformat(), title)
        if key in seen:
            continue
        seen.add(key)
        clues.append({
            "disclosed_at": day.isoformat(), "event_type": kind,
            "document_title": title[:180], "source_url": url,
            "status": "title_only_unverified", "execution_vs_plan": "unknown",
            "source_tier": "official_announcement_metadata",
            "market_position_at_trade": "unverified",
        })
    return sorted(clues, key=lambda e: e["disclosed_at"], reverse=True)[:12]


def build_issuer_capital_context(stock_code: str, *, as_of=None,
                                 primary_evidence=None,
                                 research_profile=None) -> dict:
    cutoff = _date(as_of) or date.today()
    verified = _official_issuer_ledger(stock_code, cutoff)
    clues = _metadata_clues(primary_evidence, cutoff, verified)
    executed = [r for r in verified
                if r.get("status") == "completed"
                and r.get("event_type") in ("insider_sale", "holder_sale")]
    issuances = [r for r in verified
                 if r.get("event_type") == "equity_issuance"
                 and r.get("status") == "completed"]
    buybacks = [r for r in verified if r.get("event_type") == "corporate_buyback"]
    tech = getattr(research_profile, "archetype", "") == "technology"
    # Strict bookkeeping: a sale by a shareholder does NOT inject cash into
    # the company, and a company share buyback is NOT an insider purchase.
    return {
        "status": "source_verified_events" if verified else
                  "announcement_titles_only" if clues else "data_incomplete",
        "as_of": cutoff.isoformat(), "issuer_code": re.sub(r"\D", "", stock_code)[-6:],
        "source_verified_events": verified,
        "title_only_clues": clues,
        "completed_holder_sales": len(executed),
        "completed_equity_issuances": len(issuances),
        "buyback_plan_records": sum(1 for x in buybacks if x.get("status") == "plan_only"),
        "capital_question": (
            "科技公司向股权市场融资或使用股权激励，会改变股份数量与资本配置。"
            "股东承担稀释、估值下跌及投入回报不足的风险；"
            "融资也可能创造价值，必须用投入产出、资本回报与每股指标验证，不能先验认定每次融资都是利益输送。"
            if tech else
            "股权融资、分红、回购和内部人减持须分账，关注对每股权益和现金回报的影响。"
        ),
        "warnings": [
            "这是已验证的公告样本，不是全历史减持的完整性证明。",
            "减持计划或持股比例变动公告不能直接当成交股份；仅已有实施文件的才能列实际数量。",
            "出售股份所得归出售股东，不归上市公司；增发募集资金才进入发行人账本。",
            "股权激励用途回购使用公司资金，不等同注销式回购或董事个人增持。",
            "事件减持时股价所处估值/复权价格分位尚未核实，不把成交价高简单称为逃顶。",
        ],
    }


def issuer_capital_prompt_block(context: Optional[Mapping[str, Any]]) -> str:
    ctx = context or {}
    output = [
        "## 谁从科技行情中获得现金？现有股东承担什么风险？",
        str(ctx.get("capital_question") or ""),
        "必须在管理层、筹码、股东回报、成长弹性维度独立审视："
        "董事/大股东个人减持、公司融资及投资回报、现金分红、"
        "注销式回购、股权激励式回购、公开发行新股的股份增加。",
        "原始知识库只提供思考框架；不得透露知识库作者/账号/原帖或引用其他公司类比。",
    ]
    events = ctx.get("source_verified_events") or []
    for r in events[:10]:
        segments = [
            f"公告 {r.get('disclosed_at')} / {_TRADE_LABELS.get(r.get('event_type'), '')}",
            f"状态 {r.get('status')}",
            f"主体 {r.get('actor', r.get('actor_type', '公司'))}",
            f"来源 {r.get('source_url')}",
        ]
        for key, label, unit in (
            ("trade_start","交易期起",""),
            ("trade_end","交易期止",""),
            ("shares","股份数量","股"),
            ("percentage_of_total_before","占发行前总股本比例（公告口径）","%"),
            ("price_yuan","减持成交均价","元/股"),
            ("issue_price_hkd","H股发行价","港元/股"),
            ("estimated_net_proceeds_hkd","预计融资净额","港元"),
            ("min_budget_yuan","回购预算下限（非实际执行）","元"),
            ("max_budget_yuan","回购预算上限（非实际执行）","元"),
            ("planned_use","回购用途",""),
        ):
            if r.get(key) is not None:
                segments.append(f"{label}={r[key]}{unit}")
        output.append("；".join(segments))
    for r in (ctx.get("title_only_clues") or [])[:5]:
        output.append(
            f"仅公告标题线索：{r['disclosed_at']} {r['document_title']} "
            f"{r['source_url']}；状态/交易规模尚未核实，不计入实际减持。"
        )
    if not events and not ctx.get("title_only_clues"):
        output.append("本次没有取得可核实的相关减持/再融资公告，不得推断没有发生减持。")
    output.append(
        "上述事件中的个人卖股、上市公司H股发行和员工激励回购不得合并金额；"
        "2026年H股发行与尚未执行完毕的拟回购计划不得互相冲抵。"
        "公司的股权融资可创造价值，但需要独立核实项目实际回报和每股稀释，"
        "不能把募集资金总额直接等同股东受损金额。"
    )
    return "\n".join(output)
