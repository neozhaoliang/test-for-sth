"""Public 2025 filing checks: published figures, not live valuation forecasts."""
import pytest
from analysis.valuation_payout import annual_payout_from_cash_totals

def test_601717_reported_2025_dividend_rate_uses_profit_not_weighted_eps():
    net_profit = 4_293_392_302.84
    cash = 2_231_749_912.50
    ratio = 100 * cash / net_profit
    assert ratio == pytest.approx(51.9776, abs=0.01)
    # Basic EPS 2.447 is based on weighted shares, not dividend-entitled shares.
    assert abs(100 * 1.25 / 2.447 - ratio) > 0.7

def test_single_year_must_not_be_misrepresented_as_multiyear_policy():
    result=annual_payout_from_cash_totals(
       annual_profits=[{
         "fiscal_year":2025, "attributable_net_profit_yuan":4_293_392_302.84,
         "published_at":"2026-03-31",
         "source_url":"https://money.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?id=12044028&stockid=601717",
       }],
       dividend_totals=[{
         "fiscal_year":2025, "cash_dividend_yuan":2_231_749_912.50,
         "implemented_at":"2026-06-18", "progress":"实施",
         "source_url":"https://money.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?id=12385368&stockid=601717",
       }],
       as_of="2026-10-08")
    assert result["status"]=="insufficient_matched_fiscal_years"
    assert result["observations"][0]["payout_pct"] == pytest.approx(51.98,abs=0.01)

def test_601919_year_end_dividend_is_not_full_year_payout():
    # 2025 year-end cash 0.44/share; 2025 EPS 1.99/share.
    # 0.44/1.99 is neither cash/earnings nor total annual payout
    # because other installments and weighted-average share count matter.
    assert 0.44/1.99 < 0.25
    result=annual_payout_from_cash_totals(
      annual_profits=[{"fiscal_year":2025,
                      "attributable_net_profit_yuan":30_868_147_821.38,
                      "published_at":"2026-03-20",
                      "source_url":"https://money.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?id=12005360&stockid=601919"}],
      dividend_totals=[],
      as_of="2026-10-08")
    assert result["status"]=="insufficient_matched_fiscal_years"
    assert result["observations"]==[]
