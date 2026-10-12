"""Public-facing projection of private equity research results.

Knowledge-base material is an internal research input, NEVER a public citation
or source list. Allow-list fields rather than relying on fragile UI hiding.
Old cached reports are passed through the same gate on every public response.
"""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Mapping

# Anything newly added to the private AnalysisReport is private by default.
_PUBLIC_FIELDS = frozenset((
    "stock_code", "stock_name", "research_mode", "as_of", "realtime_quote",
    "industry_comparison", "shareholder_trend", "chip_price_context",
    "position_risk", "issuer_capital_context", "dividend_history", "buyback_history", "dividend_chart",
    "commodity_signal", "freight_signal", "margin_signal", "market_context",
    "rmb_signal", "macro_rates", "policy_events", "profitability_trend",
    "fundamentals", "valuation", "valuation_model", "valuation_history",
    "rd_team", "major_events", "refinancing_history", "executive_profile",
    "governance_alerts", "a_share_structure", "management_capital",
    "filing_calendar", "research_profile", "sentiment", "summary",
    "shareholder_capital_summary", "generated_at",
))
_PUBLIC_MODEL_FIELDS = frozenset((
    "framework_version", "status", "route", "valuation", "cross_checks",
    "investor_reference", "style_context", "macro_stress_scenarios",
    "evidence_gate", "macro_context", "assumptions", "interactive_inputs",
    "warnings",
))
_PRIVATE_STYLE_KEYS = frozenset((
    "kol_style_hypotheses", "investment_principles", "knowledge_excerpts",
    "source_posts", "raw_posts", "original_posts", "candidate_views",
))
# Last-resort guards for source identifiers and examples previously leaked
# by legacy reports even after the original corpus was removed.
_KNOWN_SOURCE_ALIASES = (
    "军师祭咖啡", "买股票的老木匠", "双木林叔", "陈chensir", "chensir",
)
_LEGACY_UNRELATED_CASES = ("齐鲁银行", "腾讯", "农业银行")
_STOCK_CODE = re.compile(
    r"(?P<name>[\u4e00-\u9fa5]{2,10})[（(](?:SH|SZ|BJ)?(?P<code>\d{6})[）)]",
    re.I,
)
_EXTERNAL_POST_LINK = re.compile(
    r"https?://(?:www\.)?(?:xueqiu\.com/\d+|t\.cn/|weibo\.com/\d+|"
    r"bilibili\.com/(?:read|video)/)", re.I,
)
_SOURCE_ATRIBUTION = re.compile(
    r"(?:投资者观察|(?:有|某位?)?(?:博主|大V|KOL|投资者|雪球用户)"
    r"(?:指出|认为|表示|提到|说|发帖|的帖子|观点)|据(?:某|有)?(?:博主|投资者|帖子))"
)
_POST_ID = re.compile(r"(?:用户|账号|作者|ID|id)[：:\s]*[0-9A-Za-z_]{8,}")
_SENTENCE_BREAK = re.compile(r"(?<=[。！？；\n])")


def _private_cues(raw: Mapping[str, Any]) -> tuple[set[str], set[str], list[str]]:
    target = re.sub(r"\D", "", str(raw.get("stock_code") or ""))[-6:]
    stock_name = str(raw.get("stock_name") or "")
    names = set(_KNOWN_SOURCE_ALIASES)
    off_topic = {s for s in _LEGACY_UNRELATED_CASES if s != stock_name}
    excerpts: list[str] = []
    for entry in raw.get("knowledge_excerpts") or []:
        if not isinstance(entry, dict):
            continue
        for key in ("author", "nickname", "user_nickname"):
            alias = str(entry.get(key) or "").strip()
            if len(alias) >= 3:
                names.add(alias)
        title = str(entry.get("title") or "")
        body = str(entry.get("distilled") or "")
        for item in _STOCK_CODE.finditer(title + " " + body):
            if item.group("code") != target:
                off_topic.add(item.group("name"))
        if body:
            excerpts.append(re.sub(r"\s+", "", body))
    for user in raw.get("candidates") or []:
        if isinstance(user, dict):
            alias = str(user.get("user_nickname") or "").strip()
            if len(alias) >= 3:
                names.add(alias)
            for key in ("historical_thesis", "latest_posts"):
                for post in user.get(key) or []:
                    if isinstance(post, str):
                        excerpts.append(re.sub(r"\s+", "", post))
    return names, off_topic, excerpts


def _contaminated(part: str, names: set[str], cases: set[str], excerpts: list[str],
                  target_code: str) -> bool:
    if any(name in part for name in names | cases if name):
        return True
    if _EXTERNAL_POST_LINK.search(part) or _POST_ID.search(part) or _SOURCE_ATRIBUTION.search(part):
        return True
    if any(m.group("code") != target_code for m in _STOCK_CODE.finditer(part)):
        return True
    tight = re.sub(r"\s+", "", part)
    if len(tight) >= 20:
        for excerpt in excerpts:
            if len(excerpt) >= 20 and excerpt[:min(35, len(excerpt))] in tight:
                return True
            if len(excerpt) >= 40 and any(
                excerpt[i:i + 35] in tight
                for i in range(0, len(excerpt) - 34, 20)
            ):
                return True
    return False


def _safe_prose(value: str, names: set[str], cases: set[str],
                excerpts: list[str], target_code: str) -> str:
    if not value:
        return value
    pieces = _SENTENCE_BREAK.split(value)
    clean = [
        part for part in pieces
        if part and not _contaminated(part, names, cases, excerpts, target_code)
    ]
    result = "".join(clean).strip()
    return result or "本项需要依据本公司数据重新分析。"


def _sanitize_narrative(payload: Any, names: set[str], cases: set[str],
                        excerpts: list[str], target_code: str) -> Any:
    """Protect all narrative strings, including new fields in legacy reports."""
    if isinstance(payload, str):
        return _safe_prose(payload, names, cases, excerpts, target_code)
    if isinstance(payload, list):
        return [
            _sanitize_narrative(x, names, cases, excerpts, target_code)
            for x in payload
        ]
    if isinstance(payload, dict):
        return {
            k: _sanitize_narrative(v, names, cases, excerpts, target_code)
            for k, v in payload.items()
        }
    return payload


def _capital_alignment_summary(private: Any) -> dict | None:
    """Return independent, typed issuer capital facts, not research-source posts."""
    if not isinstance(private, dict):
        return None
    events = private.get("source_verified_events") or []
    actual = []
    issued = []
    plans = []
    for r in events:
        if not isinstance(r, dict) or r.get("source_tier") != "official_filing":
            continue
        base = {
            "disclosed_at": r.get("disclosed_at"),
            "source_url": r.get("source_url"),
            "actor": r.get("actor") or ("上市公司" if r.get("actor_type") == "issuer" else ""),
            "shares": r.get("shares"),
        }
        if r.get("event_type") in ("insider_sale", "holder_sale") and r.get("status") == "completed":
            actual.append({**base, "average_sale_price_yuan": r.get("price_yuan"),
                           "category": r.get("event_type"), "trade_start": r.get("trade_start"),
                           "trade_end": r.get("trade_end")})
        elif r.get("event_type") == "equity_issuance" and r.get("status") == "completed":
            issued.append({**base, "category": "equity_issuance",
                           "issue_price_hkd": r.get("issue_price_hkd")})
        elif r.get("event_type") == "corporate_buyback":
            plans.append({**base, "category": "corporate_buyback",
                          "status": r.get("status"), "planned_use": r.get("planned_use")})
    if not actual and not issued and not plans:
        return None
    return {
        "completed_holder_sales": actual[:10],
        "equity_issuances": issued[:5],
        "corporate_buybacks": plans[:5],
        "reconciled_ownership_not_additional_sales": [
            {"disclosed_at": r.get("disclosed_at"), "reported_group_sales_shares":
             r.get("reported_group_sales_shares"), "fully_reconciled":
             r.get("fully_reconciled")}
            for r in (private.get("reconciled_ownership_not_additional_sales") or [])[:5]
        ],
        "basis": "仅根据正式实施公告。减持所得属于卖方，股权融资进入公司，回购方案不代表已执行。",
    }


def public_stock_report(raw: Mapping[str, Any]) -> dict:
    """Safe copy for REST/UI; private source data is never serialized."""
    names, cases, excerpts = _private_cues(raw)
    target = re.sub(r"\D", "", str(raw.get("stock_code") or ""))[-6:]
    out = {
        key: deepcopy(value)
        for key, value in raw.items()
        if key in _PUBLIC_FIELDS
    }
    derived_capital = _capital_alignment_summary(raw.get("issuer_capital_context"))
    if derived_capital:
        out["shareholder_capital_summary"] = derived_capital
    model = out.get("valuation_model")
    if isinstance(model, dict):
        out["valuation_model"] = {
            key: value for key, value in model.items()
            if key in _PUBLIC_MODEL_FIELDS
        }
        style = out["valuation_model"].get("style_context")
        if isinstance(style, dict):
            for field in _PRIVATE_STYLE_KEYS:
                style.pop(field, None)
    summary = out.get("summary")
    if isinstance(summary, dict):
        for key in ("core_counter_evidence", "invalidation_condition", "risk_notes"):
            summary.pop(key, None)
        if isinstance(summary.get("thesis_summary"), str):
            summary["thesis_summary"] = _safe_prose(
                summary["thesis_summary"], names, cases, excerpts, target
            )
        analyses = summary.get("dimension_analyses")
        if isinstance(analyses, dict):
            for key, item in analyses.items():
                if isinstance(item, str):
                    analyses[key] = _safe_prose(item, names, cases, excerpts, target)
    # A provider may add a free-text field anywhere in a future result. Scan
    # the entire allow-listed response, not only today's visible UI sections.
    # The target issuer's own name/code are exempt from unrelated-case checks.
    protected_identity = {
        key: out.pop(key) for key in ("stock_code", "stock_name") if key in out
    }
    out = _sanitize_narrative(out, names, cases, excerpts, target)
    out.update(protected_identity)
    return out
