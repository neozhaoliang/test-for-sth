# -*- coding: utf-8 -*-
"""
Point-in-time financial facts from exact CNINFO periodic-report PDFs.

Historical mode must not use today's reconstructed F10 history.  This adapter reads the
latest filing version that was actually published by as_of and extracts a deliberately
small set of standardized metrics from the original PDF text.

Scope is intentionally conservative:
- revenue
- attributable net profit
- operating cash flow
- weighted-average ROE
- total assets
- total liabilities when extractable

Missing fields stay None.  The parser never substitutes values from a later filing.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date
from typing import Dict, List, Optional

from analysis.filing_archive import _get_pdf_pages_text
from analysis.filing_calendar import (
    get_financial_filing_calendar,
    latest_available_filing_by_period,
)


logger = logging.getLogger("MediaCrawler")

_PARSE_TIMEOUT_S = 60
_MAX_FILINGS = 8
_MAX_LATEST_PERIOD_AGE_DAYS = 400


def _clean(text: str) -> str:
    text = text.replace("\u3000", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return text


def _num(text: str) -> Optional[float]:
    raw = str(text or "").replace(",", "").replace("，", "").strip()
    if raw in {"", "-", "--", "—", "不适用"}:
        return None
    # Parentheses in financial statements mean negative.
    neg = raw.startswith("(") and raw.endswith(")")
    raw = raw.strip("()")
    try:
        value = float(raw)
    except ValueError:
        return None
    return -value if neg else value


def _detect_monetary_unit(text: str) -> tuple[float, str]:
    """
    Detect the primary monetary unit used by the main financial-indicator table.

    Values returned by this module are normalized to yuan.  We inspect a local window around
    the main-accounting-data heading / revenue row so later per-share "(人民币元)" labels do
    not override a table-level "人民币百万元" declaration.
    """
    anchors = [
        i for i in (
            text.find("主要会计数据"),
            text.find("主要财务数据"),
            text.find("营业收入"),
        )
        if i >= 0
    ]
    anchor = min(anchors) if anchors else 0
    window = text[max(0, anchor - 500): anchor + 1800]
    compact = re.sub(r"\s+", "", window)

    patterns = (
        (r"人民币百万元|单位[:：]?百万元", 1_000_000.0, "元(原表:百万元)"),
        (r"人民币万元|单位[:：]?万元", 10_000.0, "元(原表:万元)"),
        (r"人民币千元|单位[:：]?千元", 1_000.0, "元(原表:千元)"),
        (r"单位[:：]?元", 1.0, "元"),
    )
    for pattern, multiplier, label in patterns:
        if re.search(pattern, compact):
            return multiplier, label
    return 1.0, "元(单位未明确，按原值)"


def _scale_money(value: Optional[float], multiplier: float) -> Optional[float]:
    return value * multiplier if value is not None else None


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _find_primary_metric_block(pages: List[str]) -> str:
    for i, page in enumerate(pages):
        compact = _compact(page)
        if "主要会计数据和财务指标" in compact or "主要财务数据" in compact:
            parts = [page]
            if i + 1 < len(pages):
                parts.append(pages[i + 1])
            return _clean("\n".join(parts))
    return ""


def _find_balance_sheet_block(pages: List[str]) -> str:
    for i, page in enumerate(pages):
        if "合并资产负债表" in _compact(page):
            parts = [page]
            if i + 1 < len(pages):
                parts.append(pages[i + 1])
            if i + 2 < len(pages):
                parts.append(pages[i + 2])
            return _clean("\n".join(parts))
    return ""


def _label_regex(label: str) -> re.Pattern:
    # PDF text extraction often inserts line breaks/spaces inside Chinese labels.
    # Match the label while tolerating whitespace BETWEEN characters, but never remove
    # whitespace from the surrounding numeric cells.
    parts = [re.escape(ch) for ch in label if not ch.isspace()]
    return re.compile(r"\s*".join(parts))


def _segment_after_label(
    text: str,
    label: str,
    max_chars: int = 320,
    *,
    stop_labels: Optional[List[str]] = None,
) -> str:
    source = text or ""
    match = _label_regex(label).search(source)
    if not match:
        return ""
    segment = source[match.start() : match.start() + max_chars]
    start = match.end() - match.start()
    cut = len(segment)
    for stop in stop_labels or []:
        stop_match = _label_regex(stop).search(segment, pos=start)
        if stop_match:
            cut = min(cut, stop_match.start())
    return segment[:cut]


def _numbers_in_segment(segment: str) -> List[float]:
    out: List[float] = []
    for raw in re.findall(r"[()\-—]?\d[\d,]*(?:\.\d+)?\)?", segment):
        value = _num(raw)
        if value is not None:
            out.append(value)
    return out


def _money_metric_from_row(
    text: str,
    labels: List[str],
    *,
    prefer_ytd: bool,
    stop_labels: Optional[List[str]] = None,
) -> Optional[float]:
    for label in labels:
        segment = _segment_after_label(
            text,
            label,
            stop_labels=stop_labels,
        )
        if not segment:
            continue
        values = _numbers_in_segment(segment)
        if not values:
            continue

        # Filter out percentage-like cells. Listed-company revenue/profit/OCF values in a
        # report's native monetary unit are normally orders of magnitude larger than the
        # adjacent YoY percentage columns. Keep a fallback for very small values.
        money_like = [v for v in values if abs(v) >= 1000]
        candidates = money_like or values
        if not candidates:
            continue
        return candidates[-1] if prefer_ytd and len(candidates) >= 2 else candidates[0]
    return None


def _roe_from_primary_block(text: str, *, prefer_ytd: bool) -> Optional[float]:
    for label in (
        "年化后归属于本行普通股股东的加权平均净资产收益率",
        "归属于本行普通股股东的加权平均净资产收益率",
        "加权平均净资产收益率",
        "净资产收益率",
    ):
        segment = _segment_after_label(
            text,
            label,
            stop_labels=[
                "年化后扣除非经常性损益",
                "经营活动产生的现金流量净额",
                "本报告期末",
            ],
        )
        if not segment:
            continue

        # Report tables commonly append a footnote marker immediately after the metric
        # unit, e.g. "加权平均净资产收益率(%)(1) 16.08 ...".  That "(1)" is not a
        # negative value. Strip only unit-adjacent footnotes; later "(1,208)" data cells
        # in other metrics remain valid negative numbers.
        segment = re.sub(
            r"((?:\(%\)|（%）|%))\s*(?:\(\d{1,2}\)|（\d{1,2}）)",
            r"\1",
            segment,
            count=1,
        )
        values = [v for v in _numbers_in_segment(segment) if -100 <= v <= 100]
        if not values:
            continue
        if prefer_ytd and len(values) >= 3:
            # Q3 tables are typically:
            # current-quarter ROE, YoY change(pp), YTD ROE, YTD YoY change(pp)
            return values[2]
        return values[0]
    return None


def _bank_percent_metric_from_pages(
    pages: List[str],
    labels: List[str],
    *,
    reject_prefix: Optional[str] = None,
) -> Optional[float]:
    """Extract the current-period value of a bank-specific percentage metric."""
    for page in pages:
        source = _clean(page)
        for label in labels:
            pattern = _label_regex(label)
            for match in pattern.finditer(source):
                if reject_prefix:
                    prefix = _compact(source[max(0, match.start() - 12):match.start()])
                    if prefix.endswith(reject_prefix):
                        continue
                # Keep the original whitespace between table cells.  Compacting here would
                # turn values such as "2.02 2.29" into "2.022.29" and corrupt parsing.
                segment = source[match.end(): match.end() + 180]
                segment = re.sub(
                    r"^\s*(?:\(%\)|（%）|%)?\s*(?:\(\d{1,2}\)|（\d{1,2}）)?\s*[:：]?",
                    "",
                    segment,
                )
                values = [
                    v for v in _numbers_in_segment(segment)
                    if -1000 <= v <= 1000
                ]
                if values:
                    return values[0]
    return None

def _bank_capital_metrics_from_pages(pages: List[str]) -> Dict[str, Optional[float] | str]:
    """Prefer the bank's actual consolidated capital ratios over regulatory minima.

    Bank reports often state regulatory floors immediately before the actual capital table.
    A first-label-wins parser can therefore mistake e.g. "应不低于11.25%" for the bank's
    own capital adequacy ratio.  Prefer explicit "本集团...核心一级...一级...资本充足率"
    sentences, then fall back to the consolidated section of the capital table.
    """
    basis_patterns = (
        ("本集团高级法", r"本集团高级法下"),
        ("本集团权重法", r"本集团权重法下"),
        ("本集团标准法", r"本集团标准法下"),
    )
    for page in pages:
        compact = _compact(page)
        for basis, prefix in basis_patterns:
            m = re.search(
                prefix
                + r"核心一级资本充足率[:：]?([\d.]+)%"
                + r".{0,80}?一级资本充足率[:：]?([\d.]+)%"
                + r".{0,80}?资本充足率[:：]?([\d.]+)%",
                compact,
            )
            if m:
                return {
                    "core_tier1_capital_adequacy_pct": float(m.group(1)),
                    "tier1_capital_adequacy_pct": float(m.group(2)),
                    "capital_adequacy_pct": float(m.group(3)),
                    "capital_adequacy_basis": basis,
                }

    # Fallback for reports that expose only a table. Restrict parsing to the consolidated
    # "本集团" section and stop before "本公司", so company-only ratios cannot leak in.
    for page in pages:
        compact = _compact(page)
        if "资本充足率" not in compact or "本集团" not in compact:
            continue
        source = _clean(page)
        start_match = _label_regex("本集团").search(source)
        if not start_match:
            continue
        end_match = _label_regex("本公司").search(source, pos=start_match.end())
        segment = source[start_match.start(): end_match.start() if end_match else len(source)]
        core = _bank_percent_metric_from_pages([segment], ["核心一级资本充足率"])
        tier1 = _bank_percent_metric_from_pages(
            [segment], ["一级资本充足率"], reject_prefix="核心"
        )
        total = _bank_percent_metric_from_pages(
            [segment], ["资本充足率"], reject_prefix="一级"
        )
        if any(v is not None for v in (core, tier1, total)):
            return {
                "core_tier1_capital_adequacy_pct": core,
                "tier1_capital_adequacy_pct": tier1,
                "capital_adequacy_pct": total,
                "capital_adequacy_basis": "本集团披露口径",
            }
    return {}


def _bank_metrics_from_pages(pages: List[str]) -> Dict[str, Optional[float] | str]:
    compact_all = "".join(_compact(x) for x in pages if x)
    is_bank = any(
        marker in compact_all
        for marker in (
            "归属于本行股东",
            "不良贷款率",
            "拨备覆盖率",
            "核心一级资本充足率",
            "净利息收益率",
        )
    )
    if not is_bank:
        return {}

    capital = _bank_capital_metrics_from_pages(pages)
    return {
        "financial_subtype": "bank",
        "industry_hint": "银行",
        "net_interest_margin_pct": _bank_percent_metric_from_pages(
            pages, ["净利息收益率", "净息差"]
        ),
        "npl_ratio_pct": _bank_percent_metric_from_pages(
            pages, ["不良贷款率"]
        ),
        "provision_coverage_pct": _bank_percent_metric_from_pages(
            pages, ["拨备覆盖率"]
        ),
        "loan_provision_ratio_pct": _bank_percent_metric_from_pages(
            pages, ["贷款拨备率"]
        ),
        **capital,
    }


def parse_financial_report_pages(pages: List[str]) -> Dict:
    primary = _find_primary_metric_block(pages)
    if not primary:
        # Fallback preserves support for older/odd filings.
        primary = _extract_relevant_text(pages)

    compact_primary = _compact(primary)
    prefer_ytd = (
        "年初至报告期末" in compact_primary
        and "本报告期" in compact_primary
    )
    monetary_multiplier, monetary_unit = _detect_monetary_unit(primary)

    revenue = _money_metric_from_row(
        primary,
        ["营业收入"],
        prefer_ytd=prefer_ytd,
        stop_labels=[
            "归属于上市公司股东的净利润",
            "归属于母公司所有者的净利润",
            "归属于本行股东的净利润",
        ],
    )
    net_profit = _money_metric_from_row(
        primary,
        [
            "归属于上市公司股东的净利润",
            "归属于母公司所有者的净利润",
            "归属于本行股东的净利润",
        ],
        prefer_ytd=prefer_ytd,
        stop_labels=[
            "归属于上市公司股东的扣除非经常性损益的净利润",
            "扣除非经常性损益后归属于本行股东的净利润",
            "基本每股收益",
            "归属于本行普通股股东的基本每股收益",
        ],
    )
    ocf = _money_metric_from_row(
        primary,
        ["经营活动产生的现金流量净额"],
        prefer_ytd=prefer_ytd,
        stop_labels=[
            "基本每股收益",
            "归属于本行普通股股东的基本每股收益",
            "加权平均净资产收益率",
        ],
    )
    roe = _roe_from_primary_block(primary, prefer_ytd=prefer_ytd)

    balance = _find_balance_sheet_block(pages)
    total_assets = _first_number_after(balance, ["资产总计", "总资产"]) if balance else None
    total_liabilities = _first_number_after(balance, ["负债合计", "总负债"]) if balance else None

    revenue = _scale_money(revenue, monetary_multiplier)
    net_profit = _scale_money(net_profit, monetary_multiplier)
    ocf = _scale_money(ocf, monetary_multiplier)

    if balance:
        balance_multiplier, _ = _detect_monetary_unit(balance)
        total_assets = _scale_money(total_assets, balance_multiplier)
        total_liabilities = _scale_money(total_liabilities, balance_multiplier)

    net_margin = (
        round(net_profit / revenue * 100, 3)
        if revenue not in (None, 0) and net_profit is not None
        else None
    )
    debt_ratio = (
        round(total_liabilities / total_assets * 100, 3)
        if total_assets not in (None, 0) and total_liabilities is not None
        else None
    )
    cash_to_profit = (
        round(ocf / net_profit, 3)
        if net_profit not in (None, 0) and ocf is not None
        else None
    )
    bank_metrics = _bank_metrics_from_pages(pages)

    return {
        "revenue": revenue,
        "net_profit": net_profit,
        "operating_cash_flow": ocf,
        "roe_pct": roe,
        "net_margin_pct": net_margin,
        "total_assets": total_assets,
        "total_liabilities": total_liabilities,
        "debt_ratio_pct": debt_ratio,
        "cash_to_profit_ratio": cash_to_profit,
        "monetary_unit": monetary_unit,
        "monetary_multiplier": monetary_multiplier,
        "basis": "ytd" if prefer_ytd else "period",
        **bank_metrics,
    }


def _first_number_after(text: str, labels: List[str]) -> Optional[float]:
    for label in labels:
        # Keep the match local to one logical line / short PDF-text run so we do not jump
        # to an unrelated later table value.
        pattern = (
            re.escape(label)
            + r"[^\d()\-]{0,45}"
            + r"([()\-—]?[\d,]+(?:\.\d+)?\)?)"
        )
        m = re.search(pattern, text)
        if m:
            value = _num(m.group(1))
            if value is not None:
                return value
    return None


def _first_percent_after(text: str, labels: List[str]) -> Optional[float]:
    for label in labels:
        # Common prose/table layout: "净资产收益率 12.50%"
        m = re.search(
            re.escape(label)
            + r"[^\d()\-]{0,60}"
            + r"([()\-—]?[\d,]+(?:\.\d+)?\)?)\s*%",
            text,
        )
        if m:
            return _num(m.group(1))

        # Some bank reports put the unit in the column heading:
        # "加权平均净资产收益率(%) 16.08".
        m = re.search(
            re.escape(label)
            + r"[^\d\-]{0,40}(?:\(%\)|（%）|%)"
            + r"[^\d()\-]{0,20}"
            + r"([()\-—]?[\d,]+(?:\.\d+)?\)?)",
            text,
        )
        if m:
            return _num(m.group(1))
    return None


def _extract_relevant_text(pages: List[str]) -> str:
    selected: List[str] = []
    for text in pages:
        if not text:
            continue
        compact = re.sub(r"\s+", "", text)
        if any(
            key in compact
            for key in (
                "主要会计数据和财务指标",
                "营业收入",
                "归属于上市公司股东的净利润",
                "经营活动产生的现金流量净额",
                "合并资产负债表",
                "资产总计",
                "负债合计",
            )
        ):
            selected.append(text)
    return _clean("\n".join(selected))


def _latest_period_is_fresh_enough(period: str, as_of: date) -> bool:
    """Reject obviously stale filing calendars rather than treating old filings as current."""
    try:
        period_date = date.fromisoformat(str(period)[:10])
    except ValueError:
        return False
    age_days = (as_of - period_date).days
    return 0 <= age_days <= _MAX_LATEST_PERIOD_AGE_DAYS


def parse_financial_report_text(text: str) -> Dict:
    text = _clean(text)
    monetary_multiplier, monetary_unit = _detect_monetary_unit(text)
    revenue = _first_number_after(text, ["营业收入"])
    net_profit = _first_number_after(
        text,
        [
            "归属于上市公司股东的净利润",
            "归属于母公司所有者的净利润",
            "归属于本行股东的净利润",
        ],
    )
    ocf = _first_number_after(text, ["经营活动产生的现金流量净额"])
    roe = _first_percent_after(
        text,
        [
            "年化后归属于本行普通股股东的加权平均净资产收益率",
            "归属于本行普通股股东的加权平均净资产收益率",
            "加权平均净资产收益率",
            "净资产收益率",
        ],
    )
    total_assets = _first_number_after(
        text,
        ["资产总计", "总资产"],
    )
    total_liabilities = _first_number_after(
        text,
        ["负债合计", "总负债"],
    )

    revenue = _scale_money(revenue, monetary_multiplier)
    net_profit = _scale_money(net_profit, monetary_multiplier)
    ocf = _scale_money(ocf, monetary_multiplier)
    total_assets = _scale_money(total_assets, monetary_multiplier)
    total_liabilities = _scale_money(total_liabilities, monetary_multiplier)

    net_margin = (
        round(net_profit / revenue * 100, 3)
        if revenue not in (None, 0) and net_profit is not None
        else None
    )
    debt_ratio = (
        round(total_liabilities / total_assets * 100, 3)
        if total_assets not in (None, 0) and total_liabilities is not None
        else None
    )
    cash_to_profit = (
        round(ocf / net_profit, 3)
        if net_profit not in (None, 0) and ocf is not None
        else None
    )

    return {
        "revenue": revenue,
        "net_profit": net_profit,
        "operating_cash_flow": ocf,
        "roe_pct": roe,
        "net_margin_pct": net_margin,
        "total_assets": total_assets,
        "total_liabilities": total_liabilities,
        "debt_ratio_pct": debt_ratio,
        "cash_to_profit_ratio": cash_to_profit,
        "monetary_unit": monetary_unit,
        "monetary_multiplier": monetary_multiplier,
    }


async def _fetch_and_parse(item: Dict) -> Optional[Dict]:
    url = str(item.get("url") or "")
    if not url:
        return None
    pages = await _get_pdf_pages_text(url)
    if not pages:
        return None
    try:
        metrics = await asyncio.wait_for(
            asyncio.to_thread(parse_financial_report_pages, pages),
            timeout=_PARSE_TIMEOUT_S,
        )
    except Exception:
        return None
    if not any(v is not None for v in metrics.values()):
        return None
    return {
        "period": str(item.get("period") or ""),
        "published_at": str(item.get("published_at") or ""),
        "report_type": str(item.get("report_type") or ""),
        "title": str(item.get("title") or ""),
        "url": url,
        "announcement_id": str(item.get("announcement_id") or ""),
        **metrics,
    }


async def get_point_in_time_financials(
    stock_code: str,
    as_of: date,
    *,
    lookback_years: int = 6,
) -> Optional[Dict]:
    calendar = await get_financial_filing_calendar(
        stock_code,
        as_of=as_of,
        lookback_years=lookback_years,
    )
    selected = latest_available_filing_by_period(calendar, as_of)
    rows = sorted(
        selected.values(),
        key=lambda x: (str(x.get("period") or ""), str(x.get("published_at") or "")),
        reverse=True,
    )[:_MAX_FILINGS]

    parsed: List[Dict] = []
    # Serial on purpose: PDFs are large and CNINFO should not be hammered.
    for item in rows:
        result = await _fetch_and_parse(item)
        if result:
            parsed.append(result)

    if not parsed:
        return None

    parsed.sort(key=lambda x: x["period"])
    latest = parsed[-1]
    if not _latest_period_is_fresh_enough(str(latest.get("period") or ""), as_of):
        logger.warning(
            "[point_in_time_financials] %s latest parsed filing period %s is too stale for as_of=%s; "
            "treat historical financials as unavailable",
            stock_code,
            latest.get("period"),
            as_of.isoformat(),
        )
        return None

    periods = [
        {
            "period": x["period"],
            "published_at": x["published_at"],
            "net_margin_pct": x.get("net_margin_pct"),
            "roe_pct": x.get("roe_pct"),
            "debt_ratio_pct": x.get("debt_ratio_pct"),
            "cash_to_profit_ratio": x.get("cash_to_profit_ratio"),
            "revenue": x.get("revenue"),
            "net_profit": x.get("net_profit"),
            "operating_cash_flow": x.get("operating_cash_flow"),
            "net_interest_margin_pct": x.get("net_interest_margin_pct"),
            "npl_ratio_pct": x.get("npl_ratio_pct"),
            "provision_coverage_pct": x.get("provision_coverage_pct"),
            "loan_provision_ratio_pct": x.get("loan_provision_ratio_pct"),
            "core_tier1_capital_adequacy_pct": x.get("core_tier1_capital_adequacy_pct"),
            "tier1_capital_adequacy_pct": x.get("tier1_capital_adequacy_pct"),
            "capital_adequacy_pct": x.get("capital_adequacy_pct"),
            "capital_adequacy_basis": x.get("capital_adequacy_basis"),
        }
        for x in parsed
    ]

    return {
        "as_of": as_of.isoformat(),
        "latest_period": latest["period"],
        "latest_published_at": latest["published_at"],
        "latest": latest,
        "periods": periods,
        "source_tier": "S",
        "source": "CNINFO original periodic-report PDFs",
    }



def to_historical_fundamentals(data: Optional[Dict]) -> Optional[Dict]:
    """Map point-in-time filing facts into the report's fundamentals contract."""
    if not data:
        return None
    latest = data.get("latest") or {}
    facts = {
        "finance_period": latest.get("period"),
        "revenue": latest.get("revenue"),
        "net_profit": latest.get("net_profit"),
        "operating_cash_flow": latest.get("operating_cash_flow"),
        "cash_to_profit_ratio": latest.get("cash_to_profit_ratio"),
        "roe_pct": latest.get("roe_pct"),
        # Historical PDF parser intentionally does not pretend to know fields it did not
        # extract from the exact filing version.
        "rd_investment_yuan": None,
        "rd_intensity_pct": None,
        "accounts_receivable_yuan": None,
        "inventory_yuan": None,
        "sw_industry": None,
        "financial_subtype": latest.get("financial_subtype"),
        "industry_hint": latest.get("industry_hint"),
        "net_interest_margin_pct": latest.get("net_interest_margin_pct"),
        "npl_ratio_pct": latest.get("npl_ratio_pct"),
        "provision_coverage_pct": latest.get("provision_coverage_pct"),
        "loan_provision_ratio_pct": latest.get("loan_provision_ratio_pct"),
        "core_tier1_capital_adequacy_pct": latest.get("core_tier1_capital_adequacy_pct"),
        "tier1_capital_adequacy_pct": latest.get("tier1_capital_adequacy_pct"),
        "capital_adequacy_pct": latest.get("capital_adequacy_pct"),
        "capital_adequacy_basis": latest.get("capital_adequacy_basis"),
    }
    missing = []
    for field, label in (
        ("revenue", "营业收入"),
        ("net_profit", "归母净利润"),
        ("operating_cash_flow", "经营现金流"),
    ):
        if facts.get(field) is None:
            missing.append(label)
    if facts.get("financial_subtype") == "bank":
        bank_required = (
            ("net_interest_margin_pct", "净息差"),
            ("npl_ratio_pct", "不良贷款率"),
            ("provision_coverage_pct", "拨备覆盖率"),
            ("core_tier1_capital_adequacy_pct", "核心一级资本充足率"),
            ("capital_adequacy_pct", "资本充足率"),
        )
        missing.extend(
            f"{label}(历史原始财报未解析到)"
            for field, label in bank_required
            if facts.get(field) is None
        )
        missing.append("申万行业(历史模式以银行财报特征替代行业归档)")
    else:
        missing.extend(
            [
                "客户/供应商集中度(历史原始财报解析暂未覆盖)",
                "专利/研发结构(历史原始财报解析暂未覆盖)",
                "申万行业(历史原始财报解析暂未覆盖)",
            ]
        )
    url = latest.get("url")
    return {
        "facts": facts,
        "valuation": None,
        "management_narrative": "",
        "self_disclosed_risks": "",
        "top_customers": [],
        "top_suppliers": [],
        "major_events": [],
        "refinancing_history": [],
        "executive_profile": None,
        "governance_alerts": [],
        "missing": missing,
        "sources": [url] if url else [],
        "source_map": {"finance": url} if url else {},
        "point_in_time": True,
        "available_at": latest.get("published_at"),
    }


def _trend_note(periods: List[Dict], key: str, label: str) -> str:
    usable = [x for x in periods if x.get(key) is not None]
    if len(usable) < 2:
        return f"{label}数据不足"
    first, last = usable[0], usable[-1]
    a, b = float(first[key]), float(last[key])
    diff = b - a
    direction = "上升" if diff > 0.5 else ("下降" if diff < -0.5 else "基本持平")
    return (
        f"{label}从 {a:.2f}% {direction}至 {b:.2f}% "
        f"(区间 {first['period']}~{last['period']})"
    )


def to_historical_profitability(data: Optional[Dict]) -> Optional[Dict]:
    """Map exact filing metrics into the existing profitability-trend contract."""
    if not data:
        return None
    periods = data.get("periods") or []
    if not periods:
        return None
    return {
        "periods": periods,
        "gross_margin_trend_note": "毛利率历史原始财报解析暂未覆盖",
        "net_margin_trend_note": _trend_note(periods, "net_margin_pct", "净利率"),
        "roe_trend_note": _trend_note(periods, "roe_pct", "ROE"),
        "debt_ratio_trend_note": _trend_note(periods, "debt_ratio_pct", "资产负债率"),
        "point_in_time": True,
        "as_of": data.get("as_of"),
        "source_tier": "S",
        "available_at": data.get("latest_published_at"),
    }
