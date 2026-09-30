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



def test_parse_holder_names_survives_pdf_whitespace_splitting():
    text = """
    前 10 名 无 限 售 条 件 股 东 持 股 情 况
    中 央 汇 金 资 产 管 理 有 限 责 任 公 司
    香 港 中 央 结 算 有 限 公 司
    全 国 社 保 基 金 一 一 三 组 合
    """
    rows = parse_top_holder_names(text)
    names = {x["name"] for x in rows}

    assert any("中央汇金资产管理有限责任公司" in x for x in names)
    assert any("香港中央结算有限公司" in x for x in names)
    assert any("全国社保基金一一三组合" in x for x in names)



def test_parse_bank_style_a_share_holder_heading():
    text = """
    前十名普通股股东
    香港中央结算有限公司
    中国远洋运输有限公司
    全国社保基金一一三组合
    """
    rows = parse_top_holder_names(text)
    names = {x["name"] for x in rows}

    assert any("香港中央结算有限公司" in x for x in names)
    assert any("中国远洋运输有限公司" in x for x in names)
    assert any("全国社保基金一一三组合" in x for x in names)



def test_holder_parser_stops_before_related_party_notes():
    text = """
    前10名普通股股东持股情况
    招商局轮船有限公司
    中国远洋运输有限公司
    香港中央结算有限公司
    注：
    招商局轮船有限公司为招商局集团有限公司的子公司；
    中国远洋运输有限公司为中国远洋海运集团有限公司的子公司。
    """
    rows = parse_top_holder_names(text)
    names = {x["name"] for x in rows}

    assert "招商局轮船有限公司" in names
    assert "中国远洋运输有限公司" in names
    assert "香港中央结算有限公司" in names
    assert not any("招商局集团有限公司" == x for x in names)
    assert not any("中国远洋海运集团有限公司" == x for x in names)
