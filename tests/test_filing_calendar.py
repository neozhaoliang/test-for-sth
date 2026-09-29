from datetime import date

from analysis.filing_calendar import (
    _is_full_report,
    _period_from_title,
    latest_available_filing_by_period,
)


def test_period_from_cninfo_report_titles():
    assert _period_from_title("2024年年度报告", "annual") == "2024-12-31"
    assert _period_from_title("2024年半年度报告", "semiannual") == "2024-06-30"
    assert _period_from_title("2024年第一季度报告", "q1") == "2024-03-31"
    assert _period_from_title("2024年第三季度报告", "q3") == "2024-09-30"


def test_full_report_filter_rejects_summary():
    assert _is_full_report("2024年年度报告", "annual")
    assert not _is_full_report("2024年年度报告摘要", "annual")


def test_latest_available_filing_uses_latest_version_known_by_as_of():
    calendar = [
        {
            "period": "2024-12-31",
            "published_at": "2025-03-20",
            "title": "2024年年度报告",
            "url": "original",
        },
        {
            "period": "2024-12-31",
            "published_at": "2025-04-15",
            "title": "2024年年度报告（修订版）",
            "url": "revised",
        },
        {
            "period": "2025-03-31",
            "published_at": "2025-04-25",
            "title": "2025年第一季度报告",
            "url": "q1",
        },
    ]

    before_revision = latest_available_filing_by_period(
        calendar, date(2025, 4, 1)
    )
    assert before_revision["2024-12-31"]["url"] == "original"
    assert "2025-03-31" not in before_revision

    after_revision = latest_available_filing_by_period(
        calendar, date(2025, 4, 30)
    )
    assert after_revision["2024-12-31"]["url"] == "revised"
    assert after_revision["2025-03-31"]["url"] == "q1"
