from analysis.rd_team import parse_rd_team_text


def test_parse_rd_team_standard_table_and_ignore_companywide_education():
    text = """
员工教育程度类别
博士 500
硕士 1500
本科 3000

研发人员情况
研发人员数量（人） 1,000
研发人员数量占公司总人数的比例（%） 20.0
研发人员学历结构
博士 50
硕士 300
本科 550
专科 80
高中及以下 20
研发人员年龄结构
30 岁以下 350
30-40 岁 400
40-50 岁 180
50-60 岁 60
60 岁及以上 10
"""
    result = parse_rd_team_text(text)

    assert result is not None
    assert result["rd_headcount"] == 1000
    assert result["rd_staff_ratio_pct"] == 20.0
    assert result["education"]["doctor"] == 50
    assert result["education"]["master"] == 300
    assert result["education"]["bachelor"] == 550
    assert sum(result["education"].values()) == 1000
    assert result["age"]["under_30"] == 350
    assert sum(result["age"].values()) == 1000


def test_parse_rd_team_drops_misaligned_structure_table():
    text = """
研发人员情况
研发人员数量（人） 100
研发人员数量占公司总人数的比例（%） 5.0
学历结构
博士 900
硕士 800
本科 700
"""
    result = parse_rd_team_text(text)

    assert result is not None
    assert result["rd_headcount"] == 100
    assert result["education"] == {}


def test_parse_rd_team_requires_rd_context():
    assert parse_rd_team_text("员工人数 1000，硕士 300，本科 500") is None
