"""Investor knowledge is learned as optional, fallible methods, never formulas.

Model input may use private excerpts internally; output to stock-report LLM
must be paraphrased, de-attributed, without copied ticker/quote/source data.
"""
from types import SimpleNamespace
import pytest

from analysis.investment_method_distiller import (
    compile_investment_methods, investment_manager_prompt,
    validate_method_output, _deidentify_seed,
)


def kb(body, *, date="2026-09-22"):
    return SimpleNamespace(
        title="齐鲁银行(SH601665)和腾讯投资观察",
        distilled=body,
        author="军师祭咖啡",
        source="private_source_4780688814",
        published_at=date,
        source_url="https://xueqiu.com/4780688814/410221558",
    )


def methods():
    return [
        {
            "insight": "成长股昂贵时，利润增速很高也可能由估值压缩造成负回报。",
            "use_when": "当价格隐含连续高增长而行业资本开支可能降速的时候。",
            "possible_mistake": "如果现金利润和客户需求连续改善，阶段性高价仍可能合理。",
            "company_question": "确认本公司实际订单、经营现金流和高估值的预期是否匹配。",
        },
        {
            "insight": "融资成本不仅是财务利息，新股稀释也会把压力转移给原持有人。",
            "use_when": "公司在高估值阶段发行新股且同时存在内部人减持时。",
            "possible_mistake": "资金如果创造了高于资本成本的回报，增发仍可增加每股价值。",
            "company_question": "核对当前公司融资净额、发行股数和项目投入后的现金回报。",
        },
    ]


def test_method_sanitizer_rejects_raw_kol_names_urls_tickers_and_quotes():
    record=kb("红利股高涨时，齐鲁银行(SH601665)在市场拥挤中也会面临价格回撤。")
    examples=methods()
    contaminated=[
        {**examples[0], "insight": "军师祭咖啡认为腾讯比某公司更值得买。"},
        {**examples[1], "company_question": "参考齐鲁银行(SH601665)的减持经历。"},
        {**examples[0], "use_when": "原帖在https://xueqiu.com/4780688814/410221558"},
    ]
    safe=validate_method_output(contaminated+examples,[record])
    assert safe==examples
    assert "齐鲁银行" not in str(safe)
    seed=_deidentify_seed(record.distilled,[record])
    assert "SH601665" not in seed
    assert "齐鲁银行" not in seed


@pytest.mark.asyncio
async def test_llm_distills_competing_methods_not_user_ids(monkeypatch):
    captured=[]
    async def fake_call(prompt, **kwargs):
        captured.append(prompt)
        return methods(), "end_turn"
    monkeypatch.setattr("backtest.llm_client.call_json_ex",fake_call)
    record=kb("军师祭咖啡提醒，齐鲁银行(SH601665)阶段上涨伴随融资盘风险。")
    result=await compile_investment_methods(
        [record],as_of="2026-10-12",archetype="technology",
        stock_name="中际旭创", industry="光模块",
    )
    assert result==methods()
    assert len(captured)==1
    for forbidden in ("军师祭咖啡", "SH601665", "齐鲁银行",
                      "xueqiu.com/4780688814"):
        assert forbidden not in captured[0]
        assert forbidden not in str(result)
    prompt=investment_manager_prompt(result,archetype="technology")
    assert "可质疑" in prompt or "质疑" in prompt
    assert "不是打分" in prompt or "不是财务指标" in prompt or "不是十二项" in prompt
    assert "融资" in prompt and "可能的错误" in prompt
    assert "看多、看空" not in prompt or "不是" in prompt


@pytest.mark.asyncio
async def test_rejected_llm_methods_fall_back_to_curated_principles(monkeypatch):
    async def bad_call(prompt, **kwargs):
        return [{"insight":"军师祭咖啡原帖","use_when":"齐鲁银行股价如何"}], "end_turn"
    monkeypatch.setattr("backtest.llm_client.call_json_ex",bad_call)
    baseline=[{
        "principle":"高位股权融资并不是股东的无成本收入，需要检验新增股本和持有人利益。",
        "company_check":"核对新发行股份、发行价及资本投入所创造的每股回报。",
    }]
    result=await compile_investment_methods(
        [kb("科技公司发行新股可能导致稀释和市场风格拥挤。")],
        as_of="2026-10-12",archetype="technology",
        fallback_principles=baseline,
    )
    assert len(result)==1
    assert "高位股权融资" in result[0]["insight"]
    assert "军师祭咖啡" not in str(result)


@pytest.mark.asyncio
async def test_historical_cutoff_and_no_source_no_fake_methods(monkeypatch):
    async def must_not_run(*args, **kwargs):
        raise AssertionError("Future private source should be excluded before LLM")
    monkeypatch.setattr("backtest.llm_client.call_json_ex",must_not_run)
    result=await compile_investment_methods(
        [kb("未来发布的融资观点",date="2026-10-13")],
        as_of="2026-10-12",archetype="technology",
    )
    assert result==[]
    assert "必须看空" not in investment_manager_prompt([],archetype="technology")


def test_subjective_view_is_not_an_average_of_twelve_scores():
    prompt=investment_manager_prompt(methods(),archetype="technology")
    assert "不是十二票投票" in prompt
    assert "市场大多数人可能忽略了什么" in prompt
    assert "你可以判断这些方法中没有一个适用" in prompt


@pytest.mark.asyncio
async def test_older_tech_dilution_method_is_not_crowded_out_by_newer_posts(monkeypatch):
    captured=[]
    async def fake_call(prompt, **kwargs):
        captured.append(prompt)
        return methods(), "end_turn"
    monkeypatch.setattr("backtest.llm_client.call_json_ex",fake_call)
    unrelated=[
        SimpleNamespace(
            source=f"short_news_{i}", title="市场新闻", author="匿名来源",
            distilled=f"行业板块的短期风格交易观察序号{i}，"
                      "某些板块走势更多是资金风险偏好变化而不是公司利润。",
            published_at="2026-10-09",
        )
        for i in range(30)
    ]
    veteran=SimpleNamespace(
        source="historical_financing", title="科技股股东稀释的资本成本",
        author="买股票的老木匠", published_at="2024-05-09",
        distilled="科技股高位增发募集资金，高管同期减持，普通股东承受稀释，"
                  "需要考察资本开支能否提高每股价值。",
    )
    out=await compile_investment_methods(
        unrelated+[veteran],as_of="2026-10-12",archetype="technology",
        stock_name="中际旭创",industry="光通信"
    )
    assert out==methods()
    assert len(captured)==1
    assert "高位增发募集资金" in captured[0]
    assert "买股票的老木匠" not in captured[0]


def test_report_synthesis_never_replaces_stance_by_a_risk_rule():
    import inspect
    from analysis.report_synthesis import _generate_summary
    source=inspect.getsource(_generate_summary)
    assert "enforce_asymmetric_odds(" not in source
    assert "compile_investment_methods(" in source
    assert "investment_manager_prompt(" in source
