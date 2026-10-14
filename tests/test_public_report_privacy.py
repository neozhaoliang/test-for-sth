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


def test_legacy_report_without_private_sources_still_removes_known_post_cases():
    # Prior API responses already dropped knowledge_excerpts; a second GET
    # must still be safe without access to its original private source corpus.
    old = {
        "stock_code": "SH600023",
        "stock_name": "浙能电力",
        "summary": {
            "thesis_summary": (
                "该企业分红兑现依赖经营现金流。"
                "投资者观察：军师祭咖啡认为齐鲁银行短期会出现回撤。"
                "腾讯高增长可能跑输农业银行。"
                "需要结合电价和煤价评估股息。"
            ),
            "dimension_analyses": {
                "chip_flow": "据某博主认为，红利股短线波动大。",
                "price_position": "该股PB为0.97倍；与齐鲁银行(SH601665)相比。",
            },
        },
        "new_internal_field": {
            "username": "军师祭咖啡",
            "raw_post": "全部原文、作者与无关案例",
        },
        "valuation_model": {
            "style_context": {
                "kol_style_hypotheses": [{
                    "author": "军师祭咖啡",
                    "source_url": "https://xueqiu.com/4780688814/410221558",
                }],
                "style_regime": "dividend_leading",
                "dated_market_events": [{"headline": "红利基金申购限制"}],
            }
        },
    }
    public = public_stock_report(old)
    value = str(public)
    for term in (
        "军师祭咖啡", "齐鲁银行", "腾讯", "农业银行", "SH601665",
        "xueqiu.com", "new_internal_field", "kol_style_hypotheses",
        "投资者观察", "据某博主",
    ):
        assert term not in value, term
    assert "股息" in public["summary"]["thesis_summary"]
    assert public["valuation_model"]["style_context"]["style_regime"] == "dividend_leading"
    assert public_stock_report(public) == public


def test_new_unknown_poster_name_from_private_metadata_is_not_exposed():
    report = _report()
    report["knowledge_excerpts"].append({
        "author": "示例交易大师917", "title": "新能源讨论",
        "distilled": "讨论资金结构，不能直接认定公司会上涨。",
    })
    report["summary"]["thesis_summary"] = (
        "本股近期有红利风格参与。示例交易大师917认为当前趋势向上。"
        "只按本股实际数据判断。"
    )
    public = public_stock_report(report)
    assert "示例交易大师917" not in str(public)
    assert "只按本股实际数据判断" in public["summary"]["thesis_summary"]


def test_prompt_blocks_only_use_anonymous_investment_methods():
    from types import SimpleNamespace
    from analysis.report_blocks import (
        _build_candidates_block, _build_debate_block,
    )
    from analysis.knowledge_context import format_knowledge_hypotheses

    candidates = [SimpleNamespace(
        user_nickname="军师祭咖啡",
        credibility_note="齐鲁银行上涨后应减仓",
        historical_thesis=["腾讯与农业银行的股价比较"],
        latest_posts=["https://xueqiu.com/4780688814/410221558"],
    )]
    candidates_prompt = _build_candidates_block(candidates)
    debate_prompt = _build_debate_block({
        "bull": {"count": 2, "verified": 1},
        "bear": {"count": 4, "verified": 2},
        "bull_core": "军师祭咖啡看好齐鲁银行(SH601665)",
        "bear_core": "腾讯的股价可能下跌",
        "verdict": "跟随军师祭咖啡",
    })
    hypotheses_prompt = format_knowledge_hypotheses([{
        "author": "军师祭咖啡",
        "title": "齐鲁银行",
        "claim": "腾讯不如农业银行",
        "source_url": "https://xueqiu.com/4780688814/410221558",
        "verification_questions": [
            "公司当前的市净率与分红率是否支持买入？",
        ],
    }])
    combined = candidates_prompt + debate_prompt + hypotheses_prompt
    for term in ("军师祭咖啡", "齐鲁银行", "腾讯", "农业银行",
                 "SH601665", "xueqiu.com"):
        assert term not in combined
    assert "市净率与分红率" in hypotheses_prompt
    assert "多方2条" in debate_prompt


def test_capital_ledger_is_never_exported_even_with_official_filing():
    report = _report()
    report["issuer_capital_context"] = {
        "source_verified_events": [{
            "source_tier": "official_filing", "event_type": "insider_sale",
            "status": "completed", "actor": "实际披露的股东",
            "actor_type": "holder", "shares": 10000,
            "price_yuan": 8.7, "disclosed_at": "2026-10-01",
            "source_url": "https://www.cninfo.com.cn/example.pdf",
            "internal_source_id": "PRIVATE_LEDGER_ITEM",
            "raw_post": "军师祭咖啡",
        }],
        "raw_ledger": {"source_id": "PRIVATE_SECRET"},
    }
    report["shareholder_capital_summary"] = {
        "completed_holder_sales": [{
            "category": "insider_sale", "shares": 1,
            "internal_source_id": "PRIVATE_LEDGER_ITEM",
        }],
        "private_source_data": "PRIVATE_SECRET",
    }
    public = public_stock_report(report)
    text = str(public)
    for item in ("issuer_capital_context", "source_verified_events",
                 "PRIVATE_SECRET", "PRIVATE_LEDGER_ITEM", "raw_ledger"):
        assert item not in text
    sales = public["shareholder_capital_summary"]["completed_holder_sales"]
    assert sales[0]["shares"] == 10000
    assert sales[0]["source_url"].startswith("https://www.cninfo.com.cn")
    assert public_stock_report(public) == public


def test_privacy_identifier_guard_preserves_ordinary_machine_tokens():
    report = _report()
    report["valuation_model"]["style_context"]["style_regime"] = "dividend_leading"
    report["summary"]["thesis_summary"] = (
        "本公司年度分红分析符合dividend_leading的机器标记。"
        "账号：ABCD123456789的原始昵称不应公开。"
    )
    public = public_stock_report(report)
    assert public["valuation_model"]["style_context"]["style_regime"] == "dividend_leading"
    assert "ABCD123456789" not in public["summary"]["thesis_summary"]
    assert "机器标记" in public["summary"]["thesis_summary"]


def test_public_thesis_drops_cross_dimension_review_appendix_but_keeps_thesis():
    report = _report()
    report["summary"]["thesis_summary"] = (
        "紫金矿业是我愿意长期持有的公司：2026年上半年经营现金流555亿元，"
        "但30.13元不是加仓价，PB 4.00并不便宜。企业质量看多，当前赔率中性。"
        "跨维度重复因子只作为同一组底层事实计一次，不因估值/价格、"
        "盈利/现金流、周期景气、分红/再融资等在多方向出现而重复加权；"
        "筹码/资金面存在口径冲突，不作为方向性证据。"
    )
    public = public_stock_report(report)
    thesis = public["summary"]["thesis_summary"]
    assert thesis.endswith("企业质量看多，当前赔率中性。")
    assert "经营现金流555亿元" in thesis
    assert "30.13元" in thesis
    assert "跨维度" not in thesis
    assert "重复加权" not in thesis
    assert "筹码/资金面存在口径冲突" not in thesis
    assert public_stock_report(public) == public


def test_public_review_guard_preserves_substantive_conflict_and_dimension_facts():
    report = _report()
    concrete = (
        "交易所10月10日披露融资余额12亿元，而10月8日的统计值为11亿元，"
        "时点不同，暂不能据此判断机构持续增仓。"
    )
    report["summary"]["thesis_summary"] = concrete
    report["summary"]["dimension_analyses"]["chip_flow"] = (
        "本股的融资余额仍需核验。"
        "跨维度重复因子不能重复计分；"
        + concrete
    )
    public = public_stock_report(report)
    assert public["summary"]["thesis_summary"] == concrete
    assert "跨维度" not in public["summary"]["dimension_analyses"]["chip_flow"]
    assert concrete in public["summary"]["dimension_analyses"]["chip_flow"]
    assert "本股的融资余额仍需核验。" in public["summary"]["dimension_analyses"]["chip_flow"]
