from analysis.point_in_time_ownership import (
    classify_holder_name,
    parse_top_holder_names,
)


def test_classify_special_historical_holders():
    assert "national_team" in classify_holder_name("中央汇金资产管理有限责任公司")
    assert "foreign" in classify_holder_name("香港中央结算有限公司")
    assert "social_security" in classify_holder_name("全国社保基金一一三组合")
    assert "public_fund" in classify_holder_name("易方达沪深300交易型开放式指数证券投资基金")


def test_parse_holder_names_from_periodic_report_text():
    text = """
    前10名无限售条件股东持股情况
    中央汇金资产管理有限责任公司
    香港中央结算有限公司
    全国社保基金一一三组合
    易方达沪深300交易型开放式指数证券投资基金
    """
    rows = parse_top_holder_names(text)
    names = {x["name"] for x in rows}

    assert any("中央汇金" in x for x in names)
    assert any("香港中央结算" in x for x in names)
    assert any("全国社保基金" in x for x in names)
    assert any("易方达" in x for x in names)
