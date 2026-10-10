"""Restrict public stock reports to original company research, not KOL source corpora.

Users receive judgments derived from methods, never internal knowledge entries,
external poster identities or raw excerpts (including cached/legacy reports).
"""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Mapping

_PRIVATE_KEYS = (
    "knowledge_excerpts", "candidates", "evidence", "review", "validation",
    "research_quality", "debate", "xueqiu_stock",
)
_PRIVATE_STYLE_KEYS = ("kol_style_hypotheses", "investment_principles")
_KNOWN_KOL_NAMES = (
    "买股票的老木匠", "军师祭咖啡", "双木林叔", "陈chensir", "chensir",
)
_SH_CODE = re.compile(r"(?P<name>[\u4e00-\u9fa5]{2,8})[（(](?:SH|SZ|BJ)?(?P<code>\d{6})[）)]", re.I)


def _sensitive_spans(report: Mapping[str, Any]):
    """Detect identifiable source names and issuer examples from KB metadata."""
    target = re.sub(r"\D", "", str(report.get("stock_code") or ""))[-6:]
    names = set(_KNOWN_KOL_NAMES)
    case_names = set()
    quotes = []
    for entry in report.get("knowledge_excerpts") or []:
        if not isinstance(entry, dict):
            continue
        person = str(entry.get("author") or "").strip()
        if len(person) >= 4:
            names.add(person)
        sample = str(entry.get("distilled") or "")
        for mat in _SH_CODE.finditer(
            str(entry.get("title") or "") + " " + sample
        ):
            if mat.group("code") != target:
                case_names.add(mat.group("name"))
        if sample:
            quotes.append(re.sub(r"\s+", "", sample))
    for person in report.get("candidates") or []:
        if not isinstance(person, dict):
            continue
        nick = str(person.get("user_nickname") or "").strip()
        if len(nick) >= 4:
            names.add(nick)
    return names, case_names, quotes


def _clean_visible_text(text: str, names: set[str], case_names: set[str],
                        quotes: list[str]) -> str:
    if not text:
        return text
    # A long verbatim match is evidence of a copied excerpt, not independent
    # company reasoning: suppress the entire affected field rather than
    # appearing to present it as the assistant's own analysis.
    condensed = re.sub(r"\s+", "", text)
    for quote in quotes:
        if len(quote) < 34:
            if len(quote) >= 20 and quote in condensed:
                return "该项内容需要根据本公司数据重新分析。"
            continue
        for i in range(0, len(quote) - 33, 20):
            if quote[i:i+34] in condensed:
                return "该项内容需要根据本公司数据重新分析。"
    # Drop sentences containing source identity or an unrelated cited
    # company case rather than leaving the quote attached to an alias.
    blocked = {word for word in names | case_names if word and word in text}
    if blocked:
        parts = re.split(r"(?<=[。！？；\n])", text)
        text = "".join(part for part in parts
                       if not any(name in part for name in blocked)).strip()
        if not text:
            return "本项应只使用目标公司的数据和行业规律作出判断。"
    return text


def public_stock_report(raw: Mapping[str, Any]) -> dict:
    """Return a safe *copy* of the public report, without altering snapshots."""
    out = deepcopy(dict(raw))
    names, cases, quotes = _sensitive_spans(out)
    for field in _PRIVATE_KEYS:
        out.pop(field, None)
    model = out.get("valuation_model")
    if isinstance(model, dict):
        style = model.get("style_context")
        if isinstance(style, dict):
            for field in _PRIVATE_STYLE_KEYS:
                style.pop(field, None)
    summary = out.get("summary")
    if isinstance(summary, dict):
        for key in (
            "core_counter_evidence", "invalidation_condition", "risk_notes",
        ):
            summary.pop(key, None)
        for key in ("thesis_summary",):
            if isinstance(summary.get(key), str):
                summary[key] = _clean_visible_text(
                    summary[key], names, cases, quotes
                )
        analyses = summary.get("dimension_analyses")
        if isinstance(analyses, dict):
            for key, value in analyses.items():
                if isinstance(value, str):
                    analyses[key] = _clean_visible_text(
                        value, names, cases, quotes
                    )
    return out
