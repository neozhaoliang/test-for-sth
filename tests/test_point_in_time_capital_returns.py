from analysis.point_in_time_capital_returns import build_point_in_time_capital_returns


def test_build_historical_dividend_and_buyback_events_without_future_backfill():
    primary = [
        {
            "category": "权益分派",
            "title": "2025年年度权益分派实施公告 每10股派5.00元",
            "published_at": "2026-03-20 08:00:00",
            "url": "https://example.com/div.pdf",
        },
        {
            "category": "股权变动",
            "title": "关于股份回购进展的公告",
            "published_at": "2026-03-25 08:00:00",
            "url": "https://example.com/buyback.pdf",
        },
    ]

    dividends, buybacks = build_point_in_time_capital_returns(primary)

    assert dividends[0]["dividend_per_10_shares"] == 5.0
    assert dividends[0]["progress"] == "implemented"
    assert dividends[0]["available_at"] == "2026-03-20"

    assert buybacks[0]["progress"] == "in_progress"
    assert buybacks[0]["actual_amount"] is None
    assert buybacks[0]["planned_amount_range"] == [None, None]
