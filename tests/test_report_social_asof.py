import asyncio
from datetime import date
from types import SimpleNamespace

from analysis import report_social
from model.m_analysis import KnowledgeExcerpt


class StockScore:
    def __init__(self):
        self.stock_code = "600000"
        self.wilson_score = 0.42
        self.hit_rate = 0.75
        self.correct = 3
        self.incorrect = 1


class UserScore:
    def __init__(self):
        self.user_id = "u1"
        self.user_nickname = "历史用户"
        self.by_stock = [StockScore()]


def test_historical_thesis_bypasses_current_digest_and_passes_as_of(monkeypatch):
    seen = {}

    def fake_load_records(user_id, *, as_of=None):
        seen["user_id"] = user_id
        seen["as_of"] = as_of
        return [
            {
                "stock_code": "600000",
                "thesis": "当时已经发表的观点",
            }
        ]

    monkeypatch.setattr(report_social, "load_records", fake_load_records)

    out = report_social.historical_thesis(
        "u1",
        "600000",
        as_of=date(2024, 6, 30),
    )

    assert out == ["当时已经发表的观点"]
    assert seen["as_of"] == date(2024, 6, 30)


def test_historical_candidate_context_never_populates_live_social(monkeypatch):
    def fake_historical_thesis(user_id, stock_code, *, as_of=None):
        return ["历史观点"]

    monkeypatch.setattr(
        report_social,
        "historical_thesis",
        fake_historical_thesis,
    )
    inputs = SimpleNamespace(
        stock_code="600000",
        xueqiu_stock={"should": "be cleared"},
        debate={"should": "be cleared"},
        sentiment={"should": "be cleared"},
    )

    rows = report_social.collect_historical_candidate_context(
        inputs,
        [UserScore()],
        as_of=date(2024, 6, 30),
    )

    assert inputs.xueqiu_stock is None
    assert inputs.debate is None
    assert inputs.sentiment is None
    assert len(rows) == 1
    assert rows[0].latest_posts == []
    assert rows[0].historical_thesis == ["历史观点"]


def test_historical_knowledge_filter_is_model_independent():
    rows = [
        KnowledgeExcerpt(
            source="test",
            title="2024-05-01 历史观点",
            distilled="关于估值与资金风格的历史记录",
            source_url="https://example.test/1",
            published_at="2024-05-01 10:00:00",
        )
    ]
    out = asyncio.run(
        report_social.filter_relevant_knowledge(
            rows,
            "600000",
            "测试股份",
            "银行",
            use_llm=False,
        )
    )
    assert out == rows
