from datetime import date
import json

from analysis.historical_corpus import (
    future_source_dates,
    save_historical_source_bundle,
    source_vintages,
    verify_historical_source_bundle,
)


def _payloads():
    return {
        "financials": {
            "latest_period": "2024-03-31",
            "latest_published_at": "2024-04-30",
            "latest": {
                "revenue": 86_417_000_000,
                "available_at": "2024-04-30",
            },
        },
        "shareholder_count": {
            "latest_count": 568_738,
            "published_at": "2024-04-30",
        },
        "optional_empty": None,
    }


def test_source_vintages_and_future_detection():
    payloads = _payloads()
    assert source_vintages(payloads) == {
        "financials": "2024-04-30",
        "shareholder_count": "2024-04-30",
    }
    assert future_source_dates(payloads, date(2024, 6, 30)) == []

    payloads["financials"]["future"] = {"available_at": "2024-07-01"}
    assert future_source_dates(payloads, date(2024, 6, 30)) == [
        "financials:2024-07-01"
    ]


def test_save_and_verify_bundle(tmp_path):
    path = save_historical_source_bundle(
        stock_code="600036",
        as_of=date(2024, 6, 30),
        payloads=_payloads(),
        root=tmp_path,
        label="招商银行",
        diagnostics={"quote": {"elapsed_s": 1.23, "error": None}},
    )
    result = verify_historical_source_bundle(path)
    assert result["ok"]
    assert result["checks"]["sources_file"]
    assert result["checks"]["diagnostics_file"]
    assert result["checks"]["sources_semantic"]
    assert result["checks"]["no_future_source_dates"]

    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["bundle_type"] == "historical_public_sources"
    assert manifest["stock_code"] == "600036"
    assert manifest["as_of"] == "2024-06-30"
    assert manifest["future_source_dates"] == []
    assert manifest["source_vintages"]["financials"] == "2024-04-30"

    latest = json.loads(
        (tmp_path / "600036" / "2024-06-30" / "latest.json").read_text(
            encoding="utf-8"
        )
    )
    assert latest["bundle_id"] == manifest["bundle_id"]

    first_diagnostics = (path / "diagnostics.json").read_text(encoding="utf-8")
    second_path = save_historical_source_bundle(
        stock_code="600036",
        as_of=date(2024, 6, 30),
        payloads=_payloads(),
        root=tmp_path,
        label="招商银行",
        diagnostics={"quote": {"elapsed_s": 9.99, "error": "changed"}},
    )
    assert second_path == path
    assert (path / "diagnostics.json").read_text(encoding="utf-8") == first_diagnostics


def test_verify_detects_tampering(tmp_path):
    path = save_historical_source_bundle(
        stock_code="600036",
        as_of=date(2024, 6, 30),
        payloads=_payloads(),
        root=tmp_path,
    )
    sources_path = path / "sources.json"
    payload = json.loads(sources_path.read_text(encoding="utf-8"))
    payload["financials"]["latest"]["revenue"] = 1
    sources_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    result = verify_historical_source_bundle(path)
    assert not result["ok"]
    assert any("hash mismatch" in x for x in result["errors"])
