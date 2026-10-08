# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/test/test_fundamentals.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#
# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

"""
同花顺 F10 解析器的字段级不变量测试。

重点覆盖"不抛异常、只会静默产出错数字"的两类缺陷：
- 跨报告期串数: 财务页每个报告期各一张表，各表行集合不同，按行名跨表取值会
  把不同期的数字拼进同一组比率 (实测会让 601899 的现金流/净利润算错)；
- 表头/列错位与单位换算。

页面结构用合成 fixture，不依赖网络，可离线运行。
"""

import pytest

from analysis.fundamentals import (
    _amount_yuan,
    _parse_finance_metrics,
    _parse_observe,
    _parse_profile,
    _parse_provider,
    _pct,
)


def test_amount_yuan_units():
    assert _amount_yuan("417.78亿") == pytest.approx(41_778_000_000)
    assert _amount_yuan("3,700.00万") == pytest.approx(37_000_000)
    assert _amount_yuan("512元") == 512
    assert _amount_yuan("1,234.5") == 1234.5
    # 无法解析时必须返回 None，绝不能退化成 0.0 (0.0 会被当成真实数值写进报告)
    assert _amount_yuan("--") is None
    assert _amount_yuan("") is None
    assert _amount_yuan(None) is None


def test_pct_parsing():
    assert _pct("182.49%") == pytest.approx(182.49)
    assert _pct("-44.08%") == pytest.approx(-44.08)
    assert _pct("--") is None


_FINANCE_PAGE = """
<div class="m_tab_content" >
<table><tbody>
<tr><th>变动科目</th><th>本期数值</th><th>上期数值</th><th>变动幅度</th><th>变动原因</th></tr>
<tr><td>营业收入(元)</td><td>417.78亿</td><td>147.89亿</td><td>182.49%</td><td>需求增长</td></tr>
<tr><td>研发投入(元)</td><td>12.34亿</td><td>5.86亿</td><td>110.62%</td><td>加大投入</td></tr>
</tbody></table>
<div class="part_all_show_btn"><a href="javascript:void(0);" data='data_2026-06-30' class="arrow_btn btndown godown" tag="alter"></a></div>
</div>
<div class="m_tab_content" style="display:none;">
<table><tbody>
<tr><th>变动科目</th><th>本期数值</th><th>上期数值</th><th>变动幅度</th><th>变动原因</th></tr>
<tr><td>营业收入(元)</td><td>194.96亿</td><td>75.32亿</td><td>158.83%</td><td>需求增长</td></tr>
<tr><td>研发费用(元)</td><td>6.45亿</td><td>2.90亿</td><td>122.05%</td><td>加大投入</td></tr>
</tbody></table>
<div class="part_all_show_btn"><a href="javascript:void(0);" data='data_2026-03-31' class="arrow_btn btndown godown" tag="alter"></a></div>
</div>
"""


def test_finance_metrics_uses_newest_period_only():
    """回归: 各期表的行集合不同，按行名跨表合并会把两期数字拼到一起。"""
    period, metrics = _parse_finance_metrics(_FINANCE_PAGE)
    assert period == "2026-06-30"
    assert metrics["营业收入(元)"]["current"] == pytest.approx(41_778_000_000)
    # 2026Q1 表的科目不得混入
    assert "研发费用(元)" not in metrics
    assert metrics["研发投入(元)"]["current"] == pytest.approx(1_234_000_000)
    assert metrics["研发投入(元)"]["yoy_pct"] == pytest.approx(110.62)


def test_finance_metrics_unlabeled_period_yields_nothing():
    """取不到期间标记就不给数——猜期间会把不同期的数字混进同一组比率。"""
    unlabeled = _FINANCE_PAGE.replace("data='data_2026-06-30'", "data=''")
    period, metrics = _parse_finance_metrics(unlabeled)
    assert period == "2026-03-31"
    assert metrics["营业收入(元)"]["current"] == pytest.approx(19_496_000_000)

    period, metrics = _parse_finance_metrics(_FINANCE_PAGE.replace("data='data_", "data='x"))
    assert period is None
    assert metrics == {}


_PROVIDER_SECTION = """
<div class="m_box" id="provider">
<ul><li class="cur"><a href="javascript:void(0);" class="operateTab" tag="1">2025-12-31</a></li>
<li><a class="operateTab" tag="2">2024-12-31</a></li></ul>
<p>前5大客户：共销售了 317.38亿 元,占营业收入的 75.98%</p>
<p>前5大供应商：共采购了 145.20亿 元,占总采购额的 51.50%</p>
<table><tbody>
<tr><th>客户名称</th><th>销售额（元）</th><th>占比</th></tr>
<tr><td>客户A</td><td>100.50亿</td><td>24.06%</td></tr>
<tr><td>客户B</td><td>85.20亿</td><td>20.40%</td></tr>
</tbody></table>
<table><tbody>
<tr><th>供应商名称</th><th>采购额（元）</th><th>占比</th></tr>
<tr><td>供应商A</td><td>149.36亿</td><td>35.76%</td></tr>
</tbody></table>
</div>
<div class="m_box" id="next"></div>
"""


def test_provider_concentration_columns_align():
    """占比必须落在 [0,100]，且明细表的"占比"列不能取成"金额"列。"""
    out = _parse_provider(_PROVIDER_SECTION)
    assert out["period"] == "2025-12-31"
    assert out["top5_customer_pct"] == pytest.approx(75.98)
    assert out["top5_supplier_pct"] == pytest.approx(51.50)
    assert out["top5_customer_amount_yuan"] == pytest.approx(31_738_000_000)

    for key in ("top5_customer_pct", "top5_supplier_pct"):
        assert 0 < out[key] <= 100

    cust = out["customers"]
    assert [c["name"] for c in cust] == ["客户A", "客户B"]
    assert cust[0]["pct"] == pytest.approx(24.06)
    assert cust[0]["amount_yuan"] == pytest.approx(10_050_000_000)
    # 明细占比之和不得超过合计占比 (列错位会立刻违反这条)
    assert sum(c["pct"] for c in cust) <= out["top5_customer_pct"] + 0.01


_OBSERVE_SECTION = """
<div class="m_box" id="observe">
<div class="m_tab_content m_tab_content2">
一、报告期内公司从事的主要业务 公司为全球领先的光模块供应商。 查看全部▼
一、报告期内公司从事的主要业务 公司为全球领先的光模块供应商，产品覆盖400G/800G/1.6T全系列。
二、核心竞争力分析 公司具备大规模交付能力与快速迭代能力，深度绑定头部云厂商客户群体。
三、公司面临的风险和应对措施 1、地缘政治风险 主要出口市场为北美等国家或地区。
四、主营业务分析 报告期内，公司实现营业收入417.78亿元，同比增加182.49%。
</div>
</div>
<div class="m_box" id="next"></div>
"""


def test_observe_yoy_keeps_direction_sign():
    """回归: 公告原文写"同比减少44.08%"，丢掉方向词会让现金流下滑显示成增长。"""
    html = """
    <div class="m_box" id="observe">
    <div class="m_tab_content m_tab_content2">
    一、主要业务 公司从事光模块业务。 查看全部▼
    一、主要业务 公司从事光模块业务，产品覆盖高速光模块全系列，客户遍布全球各地。
    二、经营情况讨论 报告期内，实现营业收入417.78亿元，同比增加182.49%；
    归属于上市公司股东的净利润136.51亿元，同比增加241.70%；
    经营活动产生的现金流量净额18.00亿元，同比减少44.08%。
    </div>
    </div>
    """
    facts = _parse_observe(html)["_facts"]
    assert facts["revenue_yoy_pct"] == pytest.approx(182.49)
    assert facts["net_profit_yoy_pct"] == pytest.approx(241.70)
    assert facts["operating_cash_flow_yoy_pct"] == pytest.approx(-44.08)


def test_observe_dedupes_preview_and_splits_sections():
    out = _parse_observe(_OBSERVE_SECTION)
    # 页面同时渲染截断预览与全文，只保留全文，否则 prompt 预算翻倍
    assert out["management_narrative"].startswith("四、主营业务分析")
    assert out["management_narrative"].count("公司为全球领先的光模块供应商") == 0
    assert out["self_disclosed_risks"].startswith("三、公司面临的风险和应对措施")
    assert "地缘政治风险" in out["self_disclosed_risks"]
    assert out["_facts"]["revenue"] == pytest.approx(41_778_000_000)
    assert out["_facts"]["revenue_yoy_pct"] == pytest.approx(182.49)


_PROFILE_PAGE = """
<div class="m_box">
<span class="hltip f12" title="市盈率(动态)">市盈率(动态)：</span><span class="tip f12">39.969</span>
<span class="hltip f12" title="市净率">市净率：</span><span class="tip f12">11.80</span>
<span class="hltip f12" title="每股净资产">每股净资产：</span><span class="tip f12">78.53元</span>
<span class="hltip f12" title="流通A股">流通A股：</span><span class="tip f12">11.10亿股</span>
<span class="hltip f12" title="更新日期">更新日期：</span><span class="tip f12">2026-09-18</span>
<span class="hltip f12" title="每股资本公积金">每股资本公积金：</span><span class="tip f12">5.09元</span>
</div>
"""


def test_profile_label_mapping():
    out = _parse_profile(_PROFILE_PAGE)
    assert out["pe_dynamic"] == pytest.approx(39.969)
    assert out["pb"] == pytest.approx(11.80)
    assert out["nav_per_share"] == pytest.approx(78.53)
    assert out["float_shares"] == pytest.approx(1_110_000_000)
    assert out["valuation_as_of"] == "2026-09-18"
    # 未登记的标签不得凭空造字段
    assert "每股资本公积金" not in out


def test_profile_absent_labels_never_invent_values():
    """页面改版时宁可整块缺失，也不能让 PE/PB 变成 0.0 被当成真实估值。"""
    assert _parse_profile("<div class='m_box'>没有任何标签</div>") == {}


def test_assemble_reconciles_observe_yoy_with_finance_absolute_values():
    observe_html = """
    <div class="m_box" id="observe">
      <div class="m_tab_content m_tab_content2">
      一、主要业务 航运业务。 查看全部▼
      一、主要业务 航运业务。
      二、经营情况讨论 报告期内，经营活动产生的现金流量净额233.30亿元，同比增加9.49%。
      </div>
    </div>
    """
    finance_html = """
    <div class="m_tab_content">
      <table><tbody>
      <tr><th>变动科目</th><th>本期数值</th><th>上期数值</th><th>变动幅度</th><th>原因</th></tr>
      <tr><td>经营活动产生的现金流量净额(元)</td><td>233.30亿</td><td>257.77亿</td><td>9.49%</td><td>变化</td></tr>
      </tbody></table>
      <div class="part_all_show_btn"><a data='data_2026-06-30'></a></div>
    </div>
    """
    from analysis.fundamentals import _assemble

    pages = {
        "operate": None,
        "holder": None,
        "finance": finance_html,
        "profile": None,
        "event": None,
        "capital": None,
        "company": None,
    }
    # _assemble reads observe from operate.html, so inject the observe fragment there.
    pages["operate"] = observe_html
    out = _assemble("601919", pages)
    facts = out["facts"]
    assert facts["operating_cash_flow"] == pytest.approx(23_330_000_000)
    assert facts["operating_cash_flow_previous"] == pytest.approx(25_777_000_000)
    assert facts["operating_cash_flow_yoy_pct"] == pytest.approx(-9.49)
