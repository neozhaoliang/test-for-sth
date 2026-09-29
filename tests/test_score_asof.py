from datetime import date, datetime
from zoneinfo import ZoneInfo

from backtest.score import filter_records_as_of, score_user


def _ts(y, m, d, *, millis=False):
    value = int(
        datetime(y, m, d, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
    )
    return value * 1000 if millis else value


def _record(
    status_id,
    predicted_at,
    verified_at,
    verdict,
    stock_code="SH600000",
):
    return {
        "user_id": "u1",
        "user_nickname": "测试用户",
        "status_id": status_id,
        "predicted_at": predicted_at,
        "verified_at": verified_at,
        "verdict": verdict,
        "stock_code": stock_code,
        "stock_name": "测试股份",
    }


def test_historical_credibility_excludes_outcomes_verified_after_as_of():
    records = [
        _record(
            "known",
            _ts(2024, 1, 1, millis=True),
            _ts(2024, 5, 1),
            "correct",
        ),
        _record(
            "future-result",
            _ts(2024, 2, 1, millis=True),
            _ts(2024, 8, 1),
            "incorrect",
        ),
        _record(
            "future-post",
            _ts(2024, 7, 1, millis=True),
            _ts(2024, 7, 20),
            "correct",
        ),
    ]

    kept = filter_records_as_of(records, date(2024, 6, 30))

    assert [x["status_id"] for x in kept] == ["known"]
    score = score_user(kept)
    assert score is not None
    assert score.correct == 1
    assert score.incorrect == 0
    assert score.hit_rate == 1.0


def test_non_scored_view_can_exist_if_published_before_as_of():
    records = [
        _record(
            "view",
            _ts(2024, 3, 1, millis=True),
            0,
            "view",
        )
    ]

    kept = filter_records_as_of(records, date(2024, 6, 30))

    assert len(kept) == 1
    score = score_user(kept)
    assert score is not None
    assert score.total_predictions == 0
    assert score.views == 1
