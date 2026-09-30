# -*- coding: utf-8 -*-
"""
Immutable source bundles for strict historical/as-of acceptance.

These bundles sit one layer below full report snapshots. They freeze the raw point-in-time
public-data payloads before any LLM synthesis, so parser/API regressions can be reproduced
without depending on the provider returning the same historical representation later.

A source bundle is intentionally NOT an AnalysisReport snapshot. It contains no stance or
LLM conclusion and therefore cannot be mistaken for a completed investment report.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional


_SCHEMA_VERSION = 2

# Date keys are source-aware on purpose. Generic "date" cannot be scanned globally because
# ownership payloads may legitimately contain future unlock dates already known at the
# cutoff. Those are future events, not future knowledge.
_DEFAULT_TIME_FIELDS = {
    "available_at",
    "published_at",
    "latest_published_at",
}
_SOURCE_TIME_FIELDS = {
    "quote": {"date", "timestamp"},
    "valuation_history": {"as_of", "date"},
    "market_context": {
        "latest_date",
        "w52_high_date",
        "w52_low_date",
        "hist_high_date",
        "hist_low_date",
        "hist_start",
    },
    "margin": {"as_of", "latest_date", "date"},
    "macro_rates": {"as_of", "date"},
    "primary_evidence": {"published_at"},
    "filing_calendar": {"period", "published_at"},
    "financials": {
        "period",
        "published_at",
        "latest_period",
        "latest_published_at",
    },
    "fundamentals": {"available_at", "finance_period"},
    "profitability": {"as_of", "available_at", "period", "published_at"},
    # Do not include generic "date": unlock_supply may contain known future unlock dates.
    "ownership": {
        "report_period",
        "previous_report_period",
        "available_at",
        "published_at",
    },
    "shareholder_count": {
        "as_of",
        "period",
        "published_at",
        "available_at",
    },
    "dividends": {"announce_date", "available_at", "published_at"},
    "buybacks": {"announce_date", "available_at", "published_at"},
}


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _semantic_sha256(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _write_json(path: Path, value: Any) -> str:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _time_fields_for_source(name: str) -> set[str]:
    return _DEFAULT_TIME_FIELDS | _SOURCE_TIME_FIELDS.get(name, set())


def _collect_dates(value: Any, *, fields: set[str]) -> List[date]:
    dates: List[date] = []

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            for key, child in obj.items():
                if key in fields and child:
                    try:
                        dates.append(date.fromisoformat(str(child)[:10]))
                    except (TypeError, ValueError):
                        pass
                walk(child)
        elif isinstance(obj, list):
            for child in obj:
                walk(child)

    walk(value)
    return dates


def source_vintages(payloads: Dict[str, Any]) -> Dict[str, str]:
    """
    Newest audited observation/publication date inside each source payload.

    This is source-aware rather than a blind scan so scheduled future events (for example
    a disclosed unlock date) do not masquerade as the data vintage.
    """
    out: Dict[str, str] = {}
    for name, value in payloads.items():
        dates = _collect_dates(
            value,
            fields=_time_fields_for_source(name),
        )
        if dates:
            out[name] = max(dates).isoformat()
    return out


def future_source_dates(payloads: Dict[str, Any], as_of: date) -> List[str]:
    """
    Return source-level future-observation/future-knowledge violations.

    Known future event dates intentionally are not audited unless that source defines the
    date field as an observation/publication timestamp.
    """
    violations: List[str] = []
    for name, value in payloads.items():
        dates = _collect_dates(
            value,
            fields=_time_fields_for_source(name),
        )
        future = [d for d in dates if d > as_of]
        if future:
            violations.append(f"{name}:{max(future).isoformat()}")
    return violations


def save_historical_source_bundle(
    *,
    stock_code: str,
    as_of: date,
    payloads: Dict[str, Any],
    root: str | Path,
    label: str = "",
    diagnostics: Optional[Dict[str, Any]] = None,
) -> Path:
    """
    Persist an immutable point-in-time public-data bundle.

    Directory:
      <root>/<stock>/<as_of>/<stock>_<as_of>_<sources-hash-prefix>/
        sources.json
        diagnostics.json
        manifest.json
      <root>/<stock>/<as_of>/latest.json
    """
    root = Path(root)
    source_hash = _semantic_sha256(payloads)
    bundle_id = (
        f"{stock_code}_{as_of.isoformat()}_v{_SCHEMA_VERSION}_{source_hash[:12]}"
    )
    target = root / stock_code / as_of.isoformat() / bundle_id
    sources_path = target / "sources.json"
    diagnostics_path = target / "diagnostics.json"
    manifest_path = target / "manifest.json"

    # Content-addressed bundle directories are immutable. Re-running the same source
    # payload must reuse the existing bundle rather than changing diagnostics/manifest
    # under an already published bundle_id.
    if target.exists():
        if not manifest_path.exists():
            raise RuntimeError(f"incomplete existing source bundle: {target}")
        integrity = verify_historical_source_bundle(target)
        if not integrity.get("ok"):
            raise RuntimeError(
                "existing source bundle failed integrity verification: "
                + "; ".join(integrity.get("errors") or [])
            )
        latest_path = root / stock_code / as_of.isoformat() / "latest.json"
        latest_path.write_text(
            json.dumps(
                {
                    "bundle_id": bundle_id,
                    "path": str(target),
                    "sources_sha256": source_hash,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return target

    target.mkdir(parents=True, exist_ok=False)
    diagnostics_payload = diagnostics or {}
    source_file_hash = _write_json(sources_path, payloads)
    diagnostics_file_hash = _write_json(diagnostics_path, diagnostics_payload)

    manifest = {
        "schema_version": _SCHEMA_VERSION,
        "bundle_type": "historical_public_sources",
        "bundle_id": bundle_id,
        "stock_code": stock_code,
        "label": label,
        "as_of": as_of.isoformat(),
        "captured_at": int(time.time()),
        "git_sha": os.getenv("GITHUB_SHA", ""),
        "sources_sha256": source_hash,
        "file_sha256": {
            "sources": source_file_hash,
            "diagnostics": diagnostics_file_hash,
        },
        "source_vintages": source_vintages(payloads),
        "future_source_dates": future_source_dates(payloads, as_of),
        "files": {
            "sources": sources_path.name,
            "diagnostics": diagnostics_path.name,
            "manifest": manifest_path.name,
        },
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    latest_path = root / stock_code / as_of.isoformat() / "latest.json"
    latest_path.write_text(
        json.dumps(
            {
                "bundle_id": bundle_id,
                "path": str(target),
                "sources_sha256": source_hash,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return target


def load_historical_source_manifest(path: str | Path) -> Dict[str, Any]:
    p = Path(path)
    if p.is_dir():
        p = p / "manifest.json"
    return json.loads(p.read_text(encoding="utf-8"))


def verify_historical_source_bundle(path: str | Path) -> Dict[str, Any]:
    p = Path(path)
    manifest = load_historical_source_manifest(p)
    base = p if p.is_dir() else p.parent
    errors: List[str] = []
    checks: Dict[str, bool] = {}

    files = manifest.get("files") or {}
    expected_file_hashes = manifest.get("file_sha256") or {}
    for logical in ("sources", "diagnostics"):
        filename = files.get(logical)
        if not filename:
            checks[f"{logical}_file"] = False
            errors.append(f"manifest missing file entry: {logical}")
            continue
        file_path = base / filename
        if not file_path.exists():
            checks[f"{logical}_file"] = False
            errors.append(f"missing file: {filename}")
            continue
        expected = expected_file_hashes.get(logical)
        if expected:
            actual = hashlib.sha256(file_path.read_bytes()).hexdigest()
            ok = actual == expected
            checks[f"{logical}_file"] = ok
            if not ok:
                errors.append(f"file hash mismatch: {filename}")

    sources_name = files.get("sources", "sources.json")
    sources_path = base / sources_name
    if sources_path.exists():
        try:
            payloads = json.loads(sources_path.read_text(encoding="utf-8"))
            actual_semantic = _semantic_sha256(payloads)
            ok = actual_semantic == manifest.get("sources_sha256")
            checks["sources_semantic"] = ok
            if not ok:
                errors.append("semantic hash mismatch: sources.json")

            try:
                as_of = date.fromisoformat(str(manifest.get("as_of") or ""))
            except ValueError:
                as_of = None
                errors.append("manifest as_of is invalid")
            if as_of is not None:
                future = future_source_dates(payloads, as_of)
                checks["no_future_source_dates"] = not future
                if future:
                    errors.append("future source dates: " + "; ".join(future))
        except Exception as exc:
            checks["sources_semantic"] = False
            errors.append(f"invalid sources.json: {exc}")

    return {
        "ok": bool(checks) and all(checks.values()) and not errors,
        "bundle_id": manifest.get("bundle_id", ""),
        "checks": checks,
        "errors": errors,
    }
