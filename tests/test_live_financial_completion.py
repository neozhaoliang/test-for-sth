"""Live cash-profit coverage should use dated issuer numbers when F10 omits a row."""
from datetime import date

import pytest

from analysis import live_financial_completion as completion


REPORT_PERIOD = "2026-06-30"
OFFICIAL_URL = "https://static.cninfo.com.cn/finalpage/2026-08-25/1225497561.PDF"
CALENDAR = [{
    "period": REPORT_PERIOD,
    "published_at": "2026-08-25",
    "report_type": "semiannual",
    "title": "万华化学2026年半年度报告",
    "url": OFFICIAL_URL,
}]
OFFICIAL_VALUES = {
    "period": REPORT_PERIOD,
    "monetary_unit": "元",
    "net_profit": 10_062_963_544.81,
    "operating_cash_flow": 11_784_141_040.70,
}
AS_OF = date(2026, 10, 14)


@pytest.mark.asyncio
async def test_wanhua_2026_h1_missing_f10_cash_ratio_completed_from_original_filing(monkeypatch):
    """The source values are from Wanhua's published 2026 interim report."""
    calls = []

    async def fake_parse(filing):
        calls.append(filing["url"])
        return dict(OFFICIAL_VALUES)

    monkeypatch.setattr(completion, "_fetch_and_parse", fake_parse)
    original = {
        "facts": {"finance_period": REPORT_PERIOD,
                  "revenue": 119_315_863_794.85,
                  "net_profit": 10_062_963_544.81,
                  "cash_to_profit_ratio": None},
        "sources": ["https://basic.10jqka.com.cn/600309/finance.html"],
        "source_map": {"finance": "https://basic.10jqka.com.cn/600309/finance.html"},
    }
    updated = await completion.complete_live_cash_profit(
        original, CALENDAR, as_of=AS_OF,
    )
    facts = updated["facts"]
    assert calls == [OFFICIAL_URL]
    assert facts["operating_cash_flow"] == pytest.approx(11_784_141_040.70)
    assert facts["net_profit"] == pytest.approx(10_062_963_544.81)
    assert facts["cash_to_profit_ratio"] == pytest.approx(1.171)
    assert facts["net_profit_basis"] == "归属于上市公司股东的净利润"
    assert facts["cash_profit_source"] == "巨潮资讯原始定期报告"
    assert facts["cash_profit_period"] == REPORT_PERIOD
    assert facts["revenue"] == 119_315_863_794.85
    assert updated["source_map"]["cash_profit_official_filing"] == OFFICIAL_URL
    assert OFFICIAL_URL in updated["sources"]
    assert "cash_profit_source" not in original["facts"]
    assert OFFICIAL_URL not in original["sources"]


@pytest.mark.asyncio
async def test_existing_f10_ratio_is_not_refetched_or_overwritten(monkeypatch):
    async def should_not_fetch(*args, **kwargs):
        raise AssertionError("Existing ratio should avoid PDF fetch")

    monkeypatch.setattr(completion, "_fetch_and_parse", should_not_fetch)
    original = {"facts": {"finance_period": REPORT_PERIOD, "cash_to_profit_ratio": 1.21}}
    assert await completion.complete_live_cash_profit(
        original, CALENDAR, as_of=AS_OF,
    ) is original


@pytest.mark.asyncio
async def test_byd_h1_cash_conversion_recovers_when_f10_still_shows_q1(monkeypatch):
    """A stale F10 report period must not conceal released H1 figures."""
    calls = []
    async def fake_parse(filing):
        calls.append(filing)
        return {
            "period": "2026-06-30",
            "monetary_unit": "元(原表:归母净利及经营现金流逐行标注)",
            "net_profit": 12_325_408_000.0,
            "operating_cash_flow": 37_334_744_000.0,
        }
    monkeypatch.setattr(completion, "_fetch_and_parse", fake_parse)
    h1 = {
        "period": "2026-06-30",
        "published_at": "2026-08-29",
        "report_type": "semiannual",
        "url": "https://static.cninfo.com.cn/finalpage/2026-08-29/1225531835.PDF",
    }
    f10 = {
        "facts": {
            "finance_period": "2026-03-31",
            "net_profit": 2_000_000_000,
            "operating_cash_flow": None,
            "cash_to_profit_ratio": None,
            "roe_pct": 4.86,
        },
        "sources": [],
    }
    out = await completion.complete_live_cash_profit(
        f10, [h1], as_of=date(2026, 10, 20),
    )
    facts = out["facts"]
    assert len(calls) == 1
    assert facts["finance_period"] == "2026-03-31", "F10 fields keep their actual date"
    assert facts["roe_pct"] == 4.86
    assert facts["cash_profit_period"] == "2026-06-30"
    assert facts["cash_to_profit_ratio"] == pytest.approx(3.029)
    assert facts["operating_cash_flow"] == 37_334_744_000.0
    assert facts["net_profit"] == 12_325_408_000.0
    assert f10["facts"]["net_profit"] == 2_000_000_000
    assert out["source_map"]["cash_profit_official_filing"] == h1["url"]


@pytest.mark.asyncio
async def test_newer_official_filing_replaces_stale_f10_ratio(monkeypatch):
    """An older valid ratio is not the latest available reporting period."""
    async def fake_parse(filing):
        return {
            "period": "2026-06-30", "monetary_unit": "元",
            "net_profit": 100, "operating_cash_flow": 303,
        }
    monkeypatch.setattr(completion, "_fetch_and_parse", fake_parse)
    old_f10 = {
        "facts": {"finance_period": "2026-03-31", "cash_to_profit_ratio": 1.2}
    }
    updated = await completion.complete_live_cash_profit(
        old_f10,
        [{"period": "2026-06-30", "published_at": "2026-08-29",
          "url": "https://static.cninfo.com.cn/example.pdf"}],
        as_of=date(2026, 10, 20),
    )
    assert updated["facts"]["cash_to_profit_ratio"] == pytest.approx(3.03)
    assert updated["facts"]["cash_profit_period"] == "2026-06-30"


@pytest.mark.asyncio
async def test_financial_quarters_cannot_be_combined(monkeypatch):
    async def should_not_fetch(*args, **kwargs):
        raise AssertionError("No cross-period mixing permitted")

    monkeypatch.setattr(completion, "_fetch_and_parse", should_not_fetch)
    f10 = {"facts": {"finance_period": "2026-09-30", "net_profit": 14e9}}
    assert await completion.complete_live_cash_profit(f10, CALENDAR, as_of=AS_OF) is f10


@pytest.mark.asyncio
async def test_filing_must_have_been_published_by_analysis_date(monkeypatch):
    async def should_not_fetch(*args, **kwargs):
        raise AssertionError("No lookahead permitted")

    monkeypatch.setattr(completion, "_fetch_and_parse", should_not_fetch)
    original = {"facts": {"finance_period": REPORT_PERIOD}}
    assert await completion.complete_live_cash_profit(
        original, CALENDAR, as_of=date(2026, 8, 24),
    ) is original


@pytest.mark.asyncio
@pytest.mark.parametrize("parsed", [
    {"period": REPORT_PERIOD, "monetary_unit": "元",
     "net_profit": 100, "operating_cash_flow": None},
    {"period": "2026-03-31", "monetary_unit": "元",
     "net_profit": 100, "operating_cash_flow": 200},
    {"period": REPORT_PERIOD, "monetary_unit": "元(单位未明确，按原值)",
     "net_profit": 100, "operating_cash_flow": 200},
    {"period": REPORT_PERIOD, "monetary_unit": "元",
     "net_profit": 0, "operating_cash_flow": 200},
])
async def test_incomplete_or_unverified_filing_does_not_invent_cash_ratio(monkeypatch, parsed):
    async def fake_parse(filing):
        return parsed

    monkeypatch.setattr(completion, "_fetch_and_parse", fake_parse)
    original = {"facts": {"finance_period": REPORT_PERIOD, "cash_to_profit_ratio": None}}
    assert await completion.complete_live_cash_profit(
        original, CALENDAR, as_of=AS_OF,
    ) is original
