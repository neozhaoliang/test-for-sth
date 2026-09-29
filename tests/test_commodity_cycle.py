from analysis.commodity import _select_cycle_route, _series_stats


def test_coal_never_routes_to_copper():
    route = _select_cycle_route(
        "煤炭开采",
        {"management_narrative": "公司主要从事动力煤开采和销售"},
    )
    assert route is not None
    assert route["name"] == "coal"
    assert all(symbol != "CU0" for _, symbol, _ in route["anchors"])


def test_gold_lithium_and_oil_have_product_specific_routes():
    assert _select_cycle_route("贵金属", {"management_narrative": "黄金矿山"})["name"] == "gold_precious"
    assert _select_cycle_route("能源金属", {"management_narrative": "碳酸锂生产"})["name"] == "lithium"
    assert _select_cycle_route("油气开采", None)["name"] == "crude_oil"


def test_generic_resource_industry_without_product_keyword_returns_none():
    assert _select_cycle_route("有色金属", {"management_narrative": "多元资源业务"}) is None


def test_copper_requires_copper_product_context_not_generic_mining():
    assert _select_cycle_route("工业金属", {"management_narrative": "主营铜矿及铜冶炼"})["name"] == "copper"
    assert _select_cycle_route("采掘", {"management_narrative": "综合矿业"}) is None


def test_series_stats_calculates_direction_and_one_year_position():
    closes = [100.0 + i for i in range(100)]
    dates = [f"2026-01-{(i % 28) + 1:02d}" for i in range(100)]
    stats = _series_stats(dates, closes)

    assert stats["latest"] == 199.0
    assert stats["change_20d_pct"] == round((199.0 - 178.0) / 178.0 * 100, 2)
    assert stats["change_60d_pct"] == round((199.0 - 138.0) / 138.0 * 100, 2)
    assert stats["position_1y_pct"] == 100.0
