"""Market rotation is multi-sector, dated, optional-LLM and fail-closed."""
from datetime import date
from types import SimpleNamespace

import pytest

from analysis.market_rotation import (
    build_sector_rotation, default_rotation_reading,
    validate_rotation_reading, interpret_sector_rotation,
)


def observations():
    vals = {
        "有色金属": 12.3, "小金属": 6.1, "化学制药": 8.9,
        "医疗器械": 5.2, "电力行业": 1.2, "银行": 0.4,
        "半导体": -9.5, "软件开发": -7.4, "通信设备": -6.0,
    }
    return {
        name: {"latest_date":"2026-10-14", "d20_pct":value,
               "d60_pct":value*1.5, "ytd_pct":value*2}
        for name,value in vals.items()
    }


def test_cross_industry_ranking_not_red_growth_pair():
    rotation=build_sector_rotation(observations(),as_of=date(2026,10,15))
    assert rotation["status"]=="observed"
    assert rotation["coverage"]==9
    assert rotation["sectors"][0]["name"]=="有色金属"
    assert rotation["sectors"][-1]["name"]=="半导体"
    assert "化学制药" in rotation["leaders"]
    assert rotation["up_count"]==6 and rotation["down_count"]==3
    assert rotation["median_return_pct"] == 1.2
    fallback=default_rotation_reading(rotation)
    assert "化学制药" in fallback["featured_sectors"]
    assert "半导体" in fallback["featured_sectors"]


def test_stale_future_or_mismatched_board_cannot_be_used():
    rows=observations()
    rows["有色金属"]["latest_date"]="2026-10-16"
    rows["银行"]["latest_date"]="2026-09-20"
    rows["化学制药"]["d20_pct"]=float("nan")
    rows["假行业"]={"latest_date":"2026-10-14","d20_pct":999.0}
    rotation=build_sector_rotation(rows,as_of=date(2026,10,15))
    assert rotation["status"]=="observed"
    assert {r["name"] for r in rotation["sectors"]}.isdisjoint(
        {"有色金属", "银行", "化学制药", "假行业"}
    )


def test_sparse_or_stale_board_data_cannot_claim_sector_rotation():
    data={name: observations()[name] for name in list(observations())[:4]}
    partial=build_sector_rotation(data,as_of=date(2026,10,15))
    assert partial["status"]=="partial"
    assert partial["coverage"]==4 and len(partial["sectors"])==4
    assert partial["rotation_detectable"] is False
    assert "样本覆盖不足" in partial["note"]
    assert build_sector_rotation(observations(),as_of=date(2027,1,1))["status"]=="insufficient_data"


def test_llm_sector_choice_is_bounded_by_observed_names_and_no_faked_numbers():
    rotation=build_sector_rotation(observations(),as_of=date(2026,10,15))
    chosen={
        "featured_sectors":["化学制药","医疗器械","有色金属","半导体","软件开发","不存在的行业"],
        "summary":"最近医药和资源板块明显强于半导体与软件行业，市场的行业差异比单纯讨论红利和科技更有解释力。",
        "hypotheses":["这种差异可能反映对盈利可见度和利率环境的不同预期，还需行业业绩和估值验证。"],
    }
    out=validate_rotation_reading(chosen,rotation)
    assert out["status"]=="llm_selected"
    assert out["featured_sectors"][:3]==["化学制药","医疗器械","有色金属"]
    assert "不存在的行业" not in out["featured_sectors"]
    for bad in (
        {**chosen,"summary":"医药板块涨幅19.00%，确认机构正在全面进场。"},
        {**chosen,"summary":"买股票的老木匠认为医药与有色板块必然上涨。"},
        {**chosen,"featured_sectors":["不存在的行业"]},
    ):
        assert validate_rotation_reading(bad,rotation)["status"]=="numeric_fallback"


def test_bear_market_does_not_mislabel_relative_resilience_as_up():
    data={key:{**row,"d20_pct": -abs(row["d20_pct"])-0.3}
          for key,row in observations().items()}
    rotation=build_sector_rotation(data,as_of=date(2026,10,15))
    assert rotation["up_count"]==0
    sample={
        "featured_sectors":["银行","电力行业","化学制药","有色金属"],
        "summary":"银行和电力上涨，市场正在全面走强并且医药资金大量流入。",
    }
    assert validate_rotation_reading(sample,rotation)["status"]=="numeric_fallback"


@pytest.mark.asyncio
async def test_llm_selects_sectors_with_date_and_real_returns(monkeypatch):
    captured=[]
    async def fake_call(prompt,**kwargs):
        captured.append(prompt)
        return {
            "featured_sectors":["化学制药","医疗器械","有色金属","半导体","软件开发"],
            "summary":"医疗与资源相关行业表现相对靠前，半导体和软件明显靠后，行业轮动的表现不是红利与成长两项可以概括的。",
            "hypotheses":[],
        },"end_turn"
    monkeypatch.setattr("backtest.llm_client.call_json_ex",fake_call)
    rot=build_sector_rotation(observations(),as_of=date(2026,10,15))
    out=await interpret_sector_rotation(rot,stock_industry="贵金属",as_of="2026-10-15")
    assert out["status"]=="llm_selected"
    assert "化学制药" in captured[0]
    assert "有色金属" in captured[0]
    assert "2026-10-14" in captured[0]
    assert "贵金属" in captured[0]


@pytest.mark.asyncio
async def test_llm_failure_preserves_numeric_sector_reading(monkeypatch):
    async def broken(*args,**kwargs):
        raise TimeoutError("LLM unavailable")
    monkeypatch.setattr("backtest.llm_client.call_json_ex",broken)
    out=await interpret_sector_rotation(
        build_sector_rotation(observations(),as_of=date(2026,10,15))
    )
    assert out["status"]=="numeric_fallback"
    assert len(out["featured_sectors"])==6


def test_rotation_checks_new_leaders_against_distinct_previous_window():
    rows=observations()
    older={"半导体":15.0,"软件开发":12.0,"通信设备":9.0,
           "电力行业":7.0,"银行":6.0,"有色金属":-2.0,
           "化学制药":-1.0,"医疗器械":-1.0,"小金属":-3.0}
    for name,record in rows.items():
        record["previous_d20_pct"]=older[name]
    rotation=build_sector_rotation(rows,as_of=date(2026,10,15))
    assert rotation["rotation_detectable"] is True
    assert "半导体" in rotation["previous_leaders"]
    assert "有色金属" in rotation["new_leaders"]
    assert "化学制药" in rotation["new_leaders"]


def test_llm_may_select_dividend_index_when_relevant_but_not_forced():
    rotation=build_sector_rotation(observations(),as_of=date(2026,10,15))
    rotation["benchmarks"]=[{"name":"上证红利","d20_pct":3.0,
                            "latest_date":"2026-10-14","category":"style_index"}]
    candidate={
        "featured_sectors":["有色金属","化学制药","上证红利","半导体","软件开发"],
        "summary":"近期资源和医疗相关行业相对领先，成长板块回撤较大，红利指数表现介于强弱行业之间。",
        "hypotheses":[],
    }
    result=validate_rotation_reading(candidate,rotation)
    assert result["status"]=="llm_selected"
    assert "上证红利" in result["featured_sectors"]
    candidate["featured_sectors"]=[x for x in candidate["featured_sectors"] if x!="上证红利"]
    assert validate_rotation_reading(candidate,rotation)["status"]=="llm_selected"


def test_no_industries_but_valid_indices_produce_limited_fallback():
    from analysis.market_rotation import default_rotation_reading
    rotation=build_sector_rotation({},as_of=date(2026,10,15))
    rotation["benchmarks"]=[
        {"name":"上证指数","d20_pct":-1.2,"latest_date":"2026-10-14","category":"style_index"},
        {"name":"沪深300","d20_pct":-.5,"latest_date":"2026-10-14","category":"style_index"},
        {"name":"上证红利","d20_pct":2.3,"latest_date":"2026-10-14","category":"style_index"},
        {"name":"科创50","d20_pct":-10.3,"latest_date":"2026-10-14","category":"style_index"},
        {"name":"创业板指","d20_pct":-7.1,"latest_date":"2026-10-14","category":"style_index"},
    ]
    reading=default_rotation_reading(rotation)
    assert reading["status"]=="limited_fallback"
    assert "上证红利" in reading["summary"]
    assert "科创50" in reading["summary"]
    assert "全市场行业轮动" in reading["summary"]


@pytest.mark.asyncio
async def test_partial_index_only_llm_can_select_indices_without_fabricating(monkeypatch):
    import backtest.llm_client
    seen=[]
    async def model(prompt,**kwargs):
        seen.append(prompt)
        return {
            "featured_sectors":["上证红利","沪深300","科创50"],
            "summary":"红利指数相对更强，科创相关指数明显回撤，当前只能确认已核验指数之间的强弱对照。",
            "hypotheses":[]
        },"end_turn"
    monkeypatch.setattr(backtest.llm_client,"call_json_ex",model)
    rotation=build_sector_rotation({},as_of=date(2026,10,15))
    rotation["benchmarks"]=[
        {"name":"上证红利","d20_pct":2.3,"latest_date":"2026-10-14"},
        {"name":"沪深300","d20_pct":-.5,"latest_date":"2026-10-14"},
        {"name":"科创50","d20_pct":-10.3,"latest_date":"2026-10-14"},
    ]
    result=await interpret_sector_rotation(rotation,as_of="2026-10-15")
    assert result["status"]=="llm_selected"
    assert result["featured_sectors"]==["上证红利","沪深300","科创50"]
    assert "insufficient_data" in seen[0]
