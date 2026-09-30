import asyncio
from datetime import date

import pytest

from analysis import filing_archive


@pytest.mark.asyncio
async def test_archive_uses_latest_filing_version_available_by_as_of(monkeypatch, tmp_path):
    calendar = [
        {
            "report_type": "annual",
            "period": "2024-12-31",
            "published_at": "2025-03-20",
            "title": "2024年年度报告",
            "url": "https://example.com/original.pdf",
            "announcement_id": "orig",
        },
        {
            "report_type": "annual",
            "period": "2024-12-31",
            "published_at": "2025-04-15",
            "title": "2024年年度报告（修订版）",
            "url": "https://example.com/revised.pdf",
            "announcement_id": "rev",
        },
        {
            "report_type": "q1",
            "period": "2025-03-31",
            "published_at": "2025-04-25",
            "title": "2025年第一季度报告",
            "url": "https://example.com/q1.pdf",
            "announcement_id": "q1",
        },
    ]

    async def fake_calendar(stock_code, as_of=None, lookback_years=6):
        return calendar

    async def fake_download(url):
        return b"%PDF-1.4\n" + url.encode("utf-8")

    monkeypatch.setattr(
        filing_archive,
        "get_financial_filing_calendar",
        fake_calendar,
    )
    monkeypatch.setattr(filing_archive, "_download_pdf", fake_download)

    path = await filing_archive.archive_financial_filings(
        "600000",
        date(2025, 4, 1),
        root=tmp_path,
    )
    manifest = filing_archive.load_filing_archive_manifest(path)

    assert len(manifest.entries) == 1
    assert manifest.entries[0].announcement_id == "orig"
    assert manifest.entries[0].published_at == "2025-03-20"
    assert filing_archive.verify_filing_archive(path)["ok"] is True


@pytest.mark.asyncio
async def test_filing_archive_preserves_changed_bytes_instead_of_overwriting(monkeypatch, tmp_path):
    calendar = [
        {
            "report_type": "annual",
            "period": "2024-12-31",
            "published_at": "2025-03-20",
            "title": "2024年年度报告",
            "url": "https://example.com/original.pdf",
            "announcement_id": "same-id",
        }
    ]

    async def fake_calendar(stock_code, as_of=None, lookback_years=6):
        return calendar

    payloads = [b"%PDF-1.4\nfirst", b"%PDF-1.4\nsecond"]

    async def fake_download(url):
        return payloads.pop(0)

    monkeypatch.setattr(
        filing_archive,
        "get_financial_filing_calendar",
        fake_calendar,
    )
    monkeypatch.setattr(filing_archive, "_download_pdf", fake_download)

    first = await filing_archive.archive_financial_filings(
        "600000",
        date(2025, 4, 1),
        root=tmp_path,
    )
    second = await filing_archive.archive_financial_filings(
        "600000",
        date(2025, 4, 1),
        root=tmp_path,
    )

    assert first == second
    pdfs = sorted(first.glob("*.pdf"))
    assert len(pdfs) == 2



@pytest.mark.asyncio
async def test_pdf_download_concurrent_requests_share_one_inflight_fetch(monkeypatch):
    filing_archive._pdf_cache.clear()
    filing_archive._pdf_inflight.clear()
    calls = 0

    async def fake_uncached(url):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return b"%PDF-1.4\nshared"

    monkeypatch.setattr(
        filing_archive,
        "_download_pdf_uncached",
        fake_uncached,
    )
    a, b, c = await asyncio.gather(
        filing_archive._download_pdf("https://example.com/a.pdf"),
        filing_archive._download_pdf("https://example.com/a.pdf"),
        filing_archive._download_pdf("https://example.com/a.pdf"),
    )

    assert calls == 1
    assert a == b == c == b"%PDF-1.4\nshared"



@pytest.mark.asyncio
async def test_pdf_page_text_concurrent_requests_share_one_parse(monkeypatch):
    filing_archive._pdf_pages_cache.clear()
    filing_archive._pdf_pages_inflight.clear()
    calls = 0

    async def fake_download(url):
        return b"%PDF-1.4\nplaceholder"

    def fake_extract(body):
        nonlocal calls
        calls += 1
        return ["page one", "page two"]

    monkeypatch.setattr(filing_archive, "_download_pdf", fake_download)
    monkeypatch.setattr(filing_archive, "_extract_pdf_pages", fake_extract)

    a, b, c = await asyncio.gather(
        filing_archive._get_pdf_pages_text("https://example.com/report.pdf"),
        filing_archive._get_pdf_pages_text("https://example.com/report.pdf"),
        filing_archive._get_pdf_pages_text("https://example.com/report.pdf"),
    )

    assert calls == 1
    assert a == b == c == ["page one", "page two"]
    assert a is not b
