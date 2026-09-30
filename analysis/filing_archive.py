# -*- coding: utf-8 -*-
"""
Archive exact CNINFO periodic-report PDF versions available at an as-of date.

Why this exists:
Today's F10/history tables can contain later corrections.  Strict historical reconstruction
needs the original filing version that investors could actually read on that date.

This module only freezes the source documents + metadata + hashes.  It does not yet parse
full financial statements into fundamentals/profitability metrics.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from analysis.filing_calendar import (
    get_financial_filing_calendar,
    latest_available_filing_by_period,
)


_MAX_PDF_BYTES = 50 * 1024 * 1024
_DOWNLOAD_TIMEOUT_S = 45
_PDF_CACHE_MAX_ENTRIES = 16
_pdf_cache: Dict[str, Optional[bytes]] = {}
_pdf_inflight: Dict[str, asyncio.Task] = {}


class ArchivedFiling(BaseModel):
    period: str
    report_type: str
    published_at: str
    title: str
    url: str
    announcement_id: str = ""
    filename: str
    sha256: str
    bytes: int


class FilingArchiveManifest(BaseModel):
    stock_code: str
    as_of: str
    entries: List[ArchivedFiling] = Field(default_factory=list)


def _safe_filename(period: str, announcement_id: str) -> str:
    suffix = announcement_id.strip() or "filing"
    return f"{period}_{suffix}.pdf".replace("/", "-")


async def _download_pdf_uncached(url: str) -> Optional[bytes]:
    import httpx

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=_DOWNLOAD_TIMEOUT_S,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.cninfo.com.cn/",
                "Accept": "application/pdf,*/*",
                "Accept-Encoding": "gzip, deflate",
            },
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            body = response.content
    except Exception:
        return None

    if not body.startswith(b"%PDF"):
        return None
    if len(body) > _MAX_PDF_BYTES:
        return None
    return body


def _remember_pdf(url: str, body: Optional[bytes]) -> None:
    _pdf_cache[url] = body
    while len(_pdf_cache) > _PDF_CACHE_MAX_ENTRIES:
        oldest = next(iter(_pdf_cache))
        _pdf_cache.pop(oldest, None)


async def _download_pdf(url: str) -> Optional[bytes]:
    """
    Download a filing PDF once per process and share the same in-flight request.

    Historical financials, ownership and shareholder-count parsers commonly need the exact
    same periodic report.  Without this cache a single report can be downloaded three times
    concurrently, slowing acceptance runs and increasing CNINFO load.
    """
    if url in _pdf_cache:
        return _pdf_cache[url]

    task = _pdf_inflight.get(url)
    if task is None:
        task = asyncio.create_task(_download_pdf_uncached(url))
        _pdf_inflight[url] = task

    try:
        body = await task
    finally:
        if _pdf_inflight.get(url) is task:
            _pdf_inflight.pop(url, None)

    _remember_pdf(url, body)
    return body


async def archive_financial_filings(
    stock_code: str,
    as_of: date,
    *,
    root: str | Path = "data/investment_snapshots",
    lookback_years: int = 6,
    max_filings: int = 12,
) -> Path:
    """
    Freeze latest filing version per report period that was published by as_of.

    Returns archive directory.  Missing individual PDFs are skipped rather than replaced
    with a newer version.
    """
    calendar = await get_financial_filing_calendar(
        stock_code,
        as_of=as_of,
        lookback_years=lookback_years,
    )
    selected = latest_available_filing_by_period(calendar, as_of)
    rows = sorted(
        selected.values(),
        key=lambda x: (str(x.get("period") or ""), str(x.get("published_at") or "")),
        reverse=True,
    )[:max_filings]

    target = Path(root) / stock_code / as_of.isoformat() / "filings"
    target.mkdir(parents=True, exist_ok=True)

    entries: List[ArchivedFiling] = []
    for item in rows:
        url = str(item.get("url") or "")
        if not url:
            continue
        body = await _download_pdf(url)
        if not body:
            continue
        filename = _safe_filename(
            str(item.get("period") or "unknown"),
            str(item.get("announcement_id") or ""),
        )
        path = target / filename

        digest = hashlib.sha256(body).hexdigest()
        if path.exists():
            existing = hashlib.sha256(path.read_bytes()).hexdigest()
            if existing != digest:
                # Same historical filing id returning different bytes is a provenance event.
                # Preserve both versions instead of overwriting the old frozen artifact.
                filename = filename[:-4] + f"_{digest[:10]}.pdf"
                path = target / filename
        if not path.exists():
            path.write_bytes(body)

        entries.append(
            ArchivedFiling(
                period=str(item.get("period") or ""),
                report_type=str(item.get("report_type") or ""),
                published_at=str(item.get("published_at") or ""),
                title=str(item.get("title") or ""),
                url=url,
                announcement_id=str(item.get("announcement_id") or ""),
                filename=filename,
                sha256=digest,
                bytes=len(body),
            )
        )

    manifest = FilingArchiveManifest(
        stock_code=stock_code,
        as_of=as_of.isoformat(),
        entries=entries,
    )
    manifest_path = target / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target


def load_filing_archive_manifest(
    path: str | Path,
) -> FilingArchiveManifest:
    p = Path(path)
    if p.is_dir():
        p = p / "manifest.json"
    return FilingArchiveManifest.model_validate_json(
        p.read_text(encoding="utf-8")
    )


def verify_filing_archive(path: str | Path) -> Dict[str, object]:
    p = Path(path)
    manifest = load_filing_archive_manifest(p)
    base = p if p.is_dir() else p.parent
    errors: List[str] = []
    checked = 0
    for entry in manifest.entries:
        file_path = base / entry.filename
        if not file_path.exists():
            errors.append(f"missing: {entry.filename}")
            continue
        digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
        checked += 1
        if digest != entry.sha256:
            errors.append(f"hash mismatch: {entry.filename}")

    return {
        "ok": not errors,
        "stock_code": manifest.stock_code,
        "as_of": manifest.as_of,
        "checked": checked,
        "errors": errors,
    }
