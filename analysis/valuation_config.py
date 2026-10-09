"""Load user-reviewed, per-stock valuation projection cases from a local JSON file.

This config is intentionally ignored by git. It is NOT a scraping pipeline,
not an issuer filing and not proof that a forecast will be realized.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Optional


CONFIG_FILE = Path(__file__).resolve().parents[1] / "config" / "valuation_cases.local.json"
MAX_BYTES = 2_000_000


def load_local_valuation_case(stock_code: str, *, as_of: Optional[date] = None):
    """Safely load the user's forecast and provenance for a specific stock."""
    if not CONFIG_FILE.is_file():
        return {"status": "not_configured", "context": {}}
    if CONFIG_FILE.stat().st_size > MAX_BYTES:
        return {"status": "invalid_config", "error": "local valuation config exceeds 2MB",
                "context": {}}
    try:
        contents = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError, UnicodeError) as exc:
        return {"status": "invalid_config", "error": type(exc).__name__, "context": {}}
    stocks = contents.get("stocks") if isinstance(contents, dict) else None
    if not isinstance(stocks, dict):
        return {"status": "invalid_config", "error": "stocks must be an object",
                "context": {}}
    code = "".join(c for c in str(stock_code) if c.isdigit())[-6:]
    stock = stocks.get(code)
    if not isinstance(stock, dict):
        return {"status": "not_configured", "context": {}}
    if not stock.get("explicit_user_approval"):
        return {"status": "not_approved", "context": {}}
    base_date = stock.get("as_of")
    try:
        observed = date.fromisoformat(str(base_date)[:10])
    except (TypeError, ValueError):
        return {"status": "invalid_config", "error": "date missing or malformed",
                "context": {}}
    reference = as_of or date.today()
    if observed > reference:
        return {"status": "future_config", "context": {}}
    # Historical analysis must have an expressly matching as-of case; merely
    # copying today's scenario backwards causes hindsight bias.
    if as_of is not None and observed != as_of:
        return {"status": "asof_mismatch", "context": {}}
    payload = stock.get("inputs")
    provenance = stock.get("provenance")
    if not isinstance(payload, dict) or not isinstance(provenance, dict):
        return {"status": "invalid_config", "context": {}}
    return {"status": "configured", "context": {
        "industry_valuation_inputs": payload,
        "industry_valuation_provenance": provenance,
        "scenario_context": stock.get("scenario_context") or {},
    }}
