from analysis.point_in_time_shareholder import parse_shareholder_count_text


def test_parse_period_end_common_shareholder_count():
    text = "报告期末普通股股东总数（户） 123,456"
    assert parse_shareholder_count_text(text) == 123456


def test_parse_alternative_shareholder_count_label():
    text = "期末普通股股东总数（户）：98，765"
    assert parse_shareholder_count_text(text) == 98765


def test_unrelated_number_is_not_treated_as_shareholder_count():
    assert parse_shareholder_count_text("营业收入 123456789 元") is None
