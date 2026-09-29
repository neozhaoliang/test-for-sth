from datetime import date

from analysis.a_share_structure import (
    _classify_holder,
    _quarter_candidates,
    _summarize_unlocks,
)


class FakeFrame:
    def __init__(self, rows):
        self._rows = rows
        self.empty = not rows
        self.columns = list(rows[0].keys()) if rows else []

    def iterrows(self):
        for i, row in enumerate(self._rows):
            yield i, row


def test_special_holder_classification_is_explicit_not_inferred():
    assert "national_team" in _classify_holder("中央汇金资产管理有限责任公司")
    assert "foreign" in _classify_holder("香港中央结算有限公司")
    assert "social_security" in _classify_holder("全国社保基金一一三组合")
    assert _classify_holder("张三") == []


def test_quarter_candidates_only_include_completed_quarters():
    rows = _quarter_candidates(date(2026, 9, 29), limit=3)
    assert rows[0] == ("20262", "20260630")
    assert all(x[1] <= "20260929" for x in rows)


def test_unlock_summary_separates_recent_and_upcoming_supply():
    df = FakeFrame(
        [
            {
                "解禁时间": date(2026, 10, 15),
                "解禁股东数": 3,
                "解禁数量": 100_000_000,
                "实际解禁数量": None,
                "未解禁数量": 100_000_000,
                "实际解禁数量市值": None,
                "占总市值比例": 5.0,
                "占流通市值比例": 12.5,
                "限售股类型": "定向增发机构配售股份",
            },
            {
                "解禁时间": date(2026, 8, 15),
                "解禁股东数": 1,
                "解禁数量": 20_000_000,
                "实际解禁数量": 20_000_000,
                "未解禁数量": 0,
                "实际解禁数量市值": 500_000_000,
                "占总市值比例": 1.0,
                "占流通市值比例": 2.0,
                "限售股类型": "首发原股东限售股份",
            },
        ]
    )

    out = _summarize_unlocks(df, date(2026, 9, 29))

    assert len(out["upcoming_12m"]) == 1
    assert len(out["recent_6m"]) == 1
    assert out["max_upcoming_float_ratio_pct"] == 12.5
    assert out["upcoming_12m"][0]["date"] == "2026-10-15"
