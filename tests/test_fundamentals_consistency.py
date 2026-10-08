from analysis.fundamentals import _coherent_yoy_pct, _parse_finance_metrics


def test_coherent_yoy_repairs_lost_negative_sign():
    current = 23_330_000_000.0
    previous = 25_777_000_000.0

    assert _coherent_yoy_pct(current, previous, 9.49) == -9.49


def test_coherent_yoy_keeps_consistent_positive_sign():
    current = 28_224_000_000.0
    previous = 25_777_000_000.0

    assert _coherent_yoy_pct(current, previous, 9.49) == 9.49


def test_parse_finance_metrics_repairs_cashflow_yoy_direction_from_values():
    html = """
    <div class="m_tab_content" data='data_2026-06-30'>
      <table>
        <tr>
          <th>变动科目</th><th>本期数值</th><th>上期数值</th><th>变动幅度</th><th>变动原因</th>
        </tr>
        <tr>
          <td>经营活动产生的现金流量净额(元)</td>
          <td>233.30亿</td>
          <td>257.77亿</td>
          <td>9.49%</td>
          <td>结算节奏变化</td>
        </tr>
      </table>
    </div>
    """

    period, metrics = _parse_finance_metrics(html)
    row = metrics["经营活动产生的现金流量净额(元)"]

    assert period == "2026-06-30"
    assert row["current"] == 23_330_000_000.0
    assert row["previous"] == 25_777_000_000.0
    assert row["yoy_pct"] == -9.49
