"""Local scenario configuration is explicit and point-in-time safe."""
import json
from datetime import date

from analysis import valuation_config


def write_config(tmp_path, case):
    p=tmp_path/"valuation_cases.local.json"
    p.write_text(json.dumps({"stocks":{"601717":case}},ensure_ascii=False),encoding="utf8")
    return p


def test_no_case_stays_missing(monkeypatch,tmp_path):
    monkeypatch.setattr(valuation_config,"CONFIG_FILE",tmp_path/"absent.json")
    assert valuation_config.load_local_valuation_case("601717")["status"]=="not_configured"


def test_unapproved_and_future_case_not_loaded(monkeypatch,tmp_path):
    case={"explicit_user_approval":False,"as_of":"2026-10-09",
          "inputs":{},"provenance":{}}
    monkeypatch.setattr(valuation_config,"CONFIG_FILE",write_config(tmp_path,case))
    assert valuation_config.load_local_valuation_case("601717")["status"]=="not_approved"
    case["explicit_user_approval"]=True
    monkeypatch.setattr(valuation_config,"CONFIG_FILE",write_config(tmp_path,case))
    assert valuation_config.load_local_valuation_case(
        "601717",as_of=date(2026,10,8))["status"]=="future_config"


def test_historical_asof_must_match_filed_forecast(monkeypatch,tmp_path):
    case={"explicit_user_approval":True,"as_of":"2026-10-09",
          "inputs":{"required_return_pct":10},
          "provenance":{"required_return_pct":{"available_at":"2026-10-09",
                                               "assumption_basis":"scenario"}}}
    monkeypatch.setattr(valuation_config,"CONFIG_FILE",write_config(tmp_path,case))
    assert valuation_config.load_local_valuation_case(
        "601717",as_of=date(2026,10,10))["status"]=="asof_mismatch"
    good=valuation_config.load_local_valuation_case("601717",as_of=date(2026,10,9))
    assert good["status"]=="configured"
    assert good["context"]["industry_valuation_inputs"]["required_return_pct"]==10
