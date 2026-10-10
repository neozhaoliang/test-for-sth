"""Public stock analysis never exports raw knowledge posts or author identities."""
from copy import deepcopy

from analysis.public_report import public_stock_report


def _report():
    return {
        "stock_code": "SH600023", "stock_name": "浙能电力",
        "knowledge_excerpts": [
            {
                "author": "军师祭咖啡", "source": "xueqiu_4780688814",
                "title": "齐鲁银行(SH601665)阶段交易",
                "source_url": "https://xueqiu.com/4780688814/410221558",
                "distilled": "关于红利股，阶段涨幅过快时要重新测算收益率，"
                             "高位拥挤会放大回撤；齐鲁银行与本次标的业务不同。"
            },
            {
                "author": "买股票的老木匠", "source": "bili_laomujiang",
                "title": "估值观察", "source_url": "https://example.com/post2",
                "distilled": "短期抱团资金与企业盈利改善是两种不同的上涨驱动力。"
            }
        ],
        "candidates": [{"user_nickname": "军师祭咖啡", "latest_posts": [
            "涉及齐鲁银行的看法"
        ]}],
        "evidence": [{"snippet": "军师祭咖啡说"}],
        "review": {"raw_kol_excerpt": "军师祭咖啡"},
        "summary": {
            "thesis_summary": "股息率下滑时重新核查现金流。"
                             "军师祭咖啡认为齐鲁银行可能有波段回撤。"
                             "本公司煤价和电价的变动仍值得观察。",
            "dimension_analyses": {
                "fundamentals": "本公司现金流仍需复核。",
                "chip_flow": "买股票的老木匠说：短期抱团资金与企业盈利改善是两种不同的上涨驱动力。",
                "price_position": "该股在低位时，股东户数增加不宜机械解读为利空。",
            },
            "core_counter_evidence": "军师祭咖啡原文"
        },
        "valuation_model": {
            "style_context": {
                "style_regime": "dividend_leading",
                "investment_principles": [{
                    "id": "crowding", "principle": "先检查位置和短期涨幅"
                }],
                "kol_style_hypotheses": [{
                    "author": "军师祭咖啡", "claim": "齐鲁银行",
                    "source_url": "https://xueqiu.com/4780688814/410221558",
                }],
                "dated_market_events": [{
                    "headline": "红利基金大额申购限制",
                    "source_url": "https://example.com/public-news",
                }]
            }
        },
    }


def test_public_projection_suppresses_posters_other_stocks_and_original_sources():
    original = _report()
    original_copy = deepcopy(original)
    public = public_stock_report(original)
    assert original == original_copy  # do not mutate private research/snapshots
    for secret in (
        "军师祭咖啡", "买股票的老木匠", "齐鲁银行", "601665",
        "https://xueqiu.com", "kol_style_hypotheses",
        "knowledge_excerpts", "candidates", "evidence", "review",
        "investment_principles",
    ):
        assert secret not in str(public), secret
    assert public["valuation_model"]["style_context"]["style_regime"] == "dividend_leading"
    assert public["valuation_model"]["style_context"]["dated_market_events"]
    assert "本公司煤价和电价" in public["summary"]["thesis_summary"]
    assert "本公司现金流" in public["summary"]["dimension_analyses"]["fundamentals"]
    assert "本公司数据" in public["summary"]["dimension_analyses"]["chip_flow"]


def test_public_projection_is_idempotent_and_does_not_drop_market_facts():
    first = public_stock_report(_report())
    assert public_stock_report(first) == first
    assert first["valuation_model"]["style_context"]["dated_market_events"][0][
        "headline"
    ] == "红利基金大额申购限制"
