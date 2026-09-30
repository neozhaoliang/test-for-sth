import pytest

from analysis.snapshot_compare import compare_replayed_reports


def _report(
    snapshot_id: str = "snap-1",
    *,
    stance="neutral",
    confidence=0.4,
    score=1.0,
):
    return {
        "stock_code": "600036",
        "as_of": "2024-06-30",
        "research_mode": "historical",
        "prompt_version": "prompt-test",
        "generated_at": 1,
        "summary": {
            "stance": stance,
            "company_quality_stance": "bullish",
            "current_odds_stance": stance,
            "confidence": confidence,
            "dimension_scores": [
                {"key": "fundamentals", "score": score},
                {"key": "valuation", "score": -1.0},
            ],
        },
        "validation": {
            "replay": {
                "snapshot_id": snapshot_id,
                "snapshot_as_of": "2024-06-30",
                "snapshot_mode": "historical",
                "source_prompt_version": "",
                "replay_prompt_version": "prompt-test",
                "integrity_verified": True,
            }
        },
    }


def test_compare_replayed_reports_tracks_stance_confidence_and_scores():
    a = _report()
    b = _report(stance="bullish", confidence=0.6, score=2.5)

    result = compare_replayed_reports(a, b)

    assert result["same_frozen_input"] is True
    assert result["snapshot_id"] == "snap-1"
    assert result["stance_changes"]["stance"] == {"a": "neutral", "b": "bullish"}
    assert result["confidence"]["delta_b_minus_a"] == 0.2
    assert result["dimension_scores"]["fundamentals"]["delta_b_minus_a"] == 1.5
    assert result["changed_dimension_count"] == 1
    assert result["max_abs_dimension_delta"] == 1.5


def test_compare_replayed_reports_rejects_different_snapshots():
    with pytest.raises(ValueError, match="different snapshots"):
        compare_replayed_reports(_report("snap-a"), _report("snap-b"))
