from datetime import date, datetime
from zoneinfo import ZoneInfo

from analysis.primary_sources import (
    _CNINFO_CATEGORY_IDS,
    _announcement_url,
    _format_cninfo_time,
    _query_window,
)


def test_cninfo_categories_cover_core_capital_and_tail_risks():
    for key in ("权益分派", "增发", "配股", "可转债", "解禁", "风险提示", "特别处理和退市"):
        assert key in _CNINFO_CATEGORY_IDS


def test_cninfo_prefers_static_pdf_url_when_adjunct_exists():
    item = {
        "adjunctUrl": "finalpage/2026-09-01/1234567890.PDF",
        "secCode": "600000",
        "announcementId": "abc",
        "orgId": "gssh0600000",
    }
    assert _announcement_url(item) == "https://static.cninfo.com.cn/finalpage/2026-09-01/1234567890.PDF"


def test_cninfo_timestamp_is_converted_to_shanghai_time():
    dt = datetime(2026, 9, 29, 10, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    millis = int(dt.timestamp() * 1000)
    assert _format_cninfo_time(millis).startswith("2026-09-29 10:30")


def test_cninfo_query_window_ends_exactly_at_as_of():
    start, end = _query_window(date(2024, 6, 30), 5)
    assert start == "20190630"
    assert end == "20240630"
