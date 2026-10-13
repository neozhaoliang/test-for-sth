"""Fast, bounded A-share stock name / symbol autocomplete.

Uses the same Sina full-market quote catalog as resolve_stock_code. A 12-hour
symbol-only cache avoids downloading the live market on every keystroke; local
historical names and pinned listed symbols are fallbacks, not fake market data.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import unicodedata
from typing import Any

from analysis import realtime_price

try:
    from pypinyin import lazy_pinyin
except ImportError:
    lazy_pinyin = None

_LOG = logging.getLogger("MediaCrawler")
_VALID_CODE = re.compile(r"^(SH|SZ|BJ)[0-9]{6}$")
_LOCAL_FAVORITES = (
    ("SH600519", "贵州茅台"), ("SH601088", "中国神华"),
    ("SH600938", "中国海油"), ("SH601398", "工商银行"),
    ("SZ300308", "中际旭创"), ("SH601899", "紫金矿业"),
    ("SZ000333", "美的集团"), ("SH601919", "中远海控"),
    ("SH688256", "寒武纪"), ("SH688825", "长鑫科技"),
)
_CATALOG_TTL = 12 * 3600
_FAILURE_RETRY = 300
_cache_until = 0.0
_cache_rows: list[dict[str, str]] = []
_cache_source = "local"
_lock = asyncio.Lock()


def _clean(text: Any) -> str:
    return unicodedata.normalize("NFKC", str(text or "")).strip()


def _match_key(text: Any) -> str:
    return re.sub(r"\s+", "", _clean(text)).casefold()


def _normalize_row(raw_code: Any, raw_name: Any) -> dict[str, str] | None:
    code = _clean(raw_code).upper()
    name = _clean(raw_name)
    if not _VALID_CODE.fullmatch(code) or not name or name.lower() == "nan":
        return None
    return {"code": code, "name": name, "market": code[:2]}


def _build_catalog(spot_df, fallback: list[dict]) -> tuple[list[dict[str, str]], str]:
    rows: dict[str, dict[str, str]] = {}
    if spot_df is not None and {"代码", "名称"}.issubset(spot_df.columns):
        for code, name in spot_df[["代码", "名称"]].itertuples(index=False, name=None):
            row = _normalize_row(code, name)
            if row is not None:
                rows[row["code"]] = row
    live = bool(rows)
    # Keep fallback small and real: local backtests + current fixed watchlist.
    if not live:
        for stock in fallback:
            row = _normalize_row(stock.get("stock_code"), stock.get("stock_name"))
            if row:
                rows[row["code"]] = row
    for code, name in _LOCAL_FAVORITES:
        rows.setdefault(code, _normalize_row(code, name))
    return [r for r in rows.values() if r is not None], "spot" if live else "local"


async def _get_catalog() -> tuple[list[dict[str, str]], str]:
    global _cache_rows, _cache_source, _cache_until
    if _cache_rows and time.monotonic() < _cache_until:
        return _cache_rows, _cache_source
    async with _lock:
        if _cache_rows and time.monotonic() < _cache_until:
            return _cache_rows, _cache_source
        df = await realtime_price._get_spot_df()
        fallback: list[dict] = []
        if df is None or df.empty:
            try:
                from analysis.candidates import list_supported_stocks
                fallback = await asyncio.to_thread(list_supported_stocks)
            except Exception as exc:
                _LOG.warning("[stock_suggest] local stock fallback unavailable: %s", exc)
        rows, source = _build_catalog(df, fallback)
        if rows:
            _cache_rows, _cache_source = rows, source
            _cache_until = time.monotonic() + (
                _CATALOG_TTL if source == "spot" else _FAILURE_RETRY
            )
        return rows, source


def _pinyin_keys(name: str) -> tuple[str, str]:
    # Optional: no new mandatory dependency or uv.lock churn just for UI.
    if lazy_pinyin is None:
        return "", ""
    try:
        syllables = lazy_pinyin(name)
        return "".join(x[:1] for x in syllables).casefold(), "".join(syllables).casefold()
    except (TypeError, ValueError):
        return "", ""


def _subsequence(needle: str, haystack: str) -> bool:
    if len(needle) < 2:
        return False
    cursor = iter(haystack)
    return all(any(character == candidate for candidate in cursor) for character in needle)


def _score(query: str, row: dict[str, str]) -> int | None:
    name = _match_key(row["name"])
    code = row["code"].casefold()
    digits = code[-6:]
    if query == name or query == code or query == digits:
        return 0
    if code.startswith(query) or digits.startswith(query):
        return 1
    if name.startswith(query):
        return 2
    if query in name:
        return 3
    if query in code or query in digits:
        return 4
    if query.isascii():
        initials, full = _pinyin_keys(row["name"])
        if initials and initials.startswith(query):
            return 5
        if full and full.startswith(query):
            return 6
        if len(query) >= 2 and (query in initials or query in full):
            return 7
    if _subsequence(query, name):
        return 8
    return None


async def search_stocks(query: str, limit: int = 8) -> dict:
    """Return only public code/name suggestions, sorted by match quality."""
    q = _match_key(query)[:40]
    if not q:
        return {"items": [], "source": "none"}
    rows, source = await _get_catalog()
    matches = []
    for row in rows:
        rank = _score(q, row)
        if rank is not None:
            matches.append((rank, len(row["name"]), row["code"], row))
    matches.sort(key=lambda part: part[:3])
    return {"items": [item[3] for item in matches[:max(1, min(limit, 15))]], "source": source}


async def warm_stock_catalog() -> None:
    """Optional background preload: never block startup or report generation."""
    try:
        await _get_catalog()
    except Exception as exc:
        _LOG.warning("[stock_suggest] background catalog warming failed: %s", exc)
