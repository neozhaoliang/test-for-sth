from analysis.valuation_payout import annual_payout_from_eps


def fixture():
    eps=[{"fiscal_year":year,"eps_yuan":2,"published_at":f"{year+1}-03-31","source_url":f"https://filing/{year}"} for year in (2022,2023,2024)]
    div=[{"fiscal_year":year,"dividend_per_10_shares":10,"progress":"实施","announce_date":f"{year+1}-06-15","source_url":f"https://dividend/{year}"} for year in (2022,2023,2024)]
    return eps,div


def test_payout_matches_profit_year_not_announcement_year():
    eps, div=fixture()
    r=annual_payout_from_eps(annual_eps=eps,dividends=div,as_of="2026-10-08")
    assert r["status"]=="verified"
    assert r["payout_pct"]==50
    assert r["years"]==[2022,2023,2024]


def test_future_announcements_do_not_leak():
    eps,div=fixture()
    r=annual_payout_from_eps(annual_eps=eps,dividends=div,as_of="2024-12-31")
    assert r["status"]=="insufficient_matched_fiscal_years"


def test_proposals_do_not_count_and_unknown_year_not_guessed():
    eps,div=fixture()
    div[2]["progress"]="预案"
    assert annual_payout_from_eps(annual_eps=eps,dividends=div,as_of="2026-10-08")["status"]=="insufficient_matched_fiscal_years"
    div[2]["progress"]="实施"
    del div[2]["fiscal_year"]
    assert annual_payout_from_eps(annual_eps=eps,dividends=div,as_of="2026-10-08")["status"]=="insufficient_matched_fiscal_years"


def test_missing_source_disqualifies_and_conflicting_eps_rejected():
    eps,div=fixture()
    del div[0]["source_url"]
    assert annual_payout_from_eps(annual_eps=eps,dividends=div,as_of="2026-10-08")["status"]=="insufficient_matched_fiscal_years"
    eps,div=fixture()
    eps.append({**eps[0],"eps_yuan":3})
    assert annual_payout_from_eps(annual_eps=eps,dividends=div,as_of="2026-10-08")["status"]=="conflicting_eps"
