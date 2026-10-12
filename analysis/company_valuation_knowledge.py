"""Point-in-time retrieval of company-specific investment valuation theses.

Private source material is fed ONLY to synthesis. No author identity, post ID,
URL, raw text, or verbatim quote is part of the public report. A researcher's
numerical thesis is a dated *hypothesis*, never audited issuer evidence.
"""
from __future__ import annotations

from datetime import date, datetime
from html import unescape
from pathlib import Path
import glob
import json
import re
from typing import Any
from zoneinfo import ZoneInfo

# These source archives already exist in data/xueqiu/jsonl. Loading a specific
# company's relevant posts avoids prompting an LLM to distill entire large
# historical corpora on every request.
_PRIVATE_SOURCES = ("5270498878", "6262681062")
_VALUE_WORDS = re.compile(r"估值|合理价|价值|目标价|值多少|市值|市盈率|市净率|PE|PB|ROE|净利润|预期利润", re.I)
_TAGS = re.compile(r"<[^>]{0,400}>")
_SPACE = re.compile(r"\s+")
_ROOT = Path(__file__).resolve().parents[1] / "data" / "xueqiu" / "jsonl"


def _when(raw: Any) -> date | None:
    try:
        ts = float(raw)
        if ts > 1e11:
            ts /= 1000
        if ts > 1e9:
            return datetime.fromtimestamp(ts, tz=ZoneInfo("Asia/Shanghai")).date()
    except (ValueError, TypeError, OverflowError, OSError):
        pass
    try:
        return date.fromisoformat(str(raw)[:10])
    except (ValueError, TypeError):
        return None


def _clean(value: Any) -> str:
    # Remove HTML markup and potential post attribution/URLs, not valuation digits.
    text = unescape(_TAGS.sub(" ", str(value or "")))
    text = re.sub(r"https?://[^\\s]+", "（链接省略）", text, flags=re.I)
    for name in ("双木林叔", "陈chensir", "军师祭咖啡", "买股票的老木匠"):
        text = text.replace(name, "（匿名）")
    return _SPACE.sub(" ", text).strip()


def load_company_valuation_views(
    *, stock_name: str, stock_code: str, as_of: str,
    directory: str | Path | None = None, limit: int = 5,
) -> list[dict]:
    """Select recent directly relevant, source-dated theses with valuation claims.

    The input archive can lag behind today's posts. Never invent missing new
    articles; no source returned means it wasn't found in local dated archives.
    """
    cutoff = _when(as_of)
    if cutoff is None:
        return []
    name = str(stock_name or "").strip()
    code = re.sub(r"\D", "", stock_code or "")[-6:]
    if not name and len(code) != 6:
        return []
    tokens = [x for x in (name, code) if x and len(x) >= 2]
    root = Path(directory) if directory is not None else _ROOT
    by_key: dict[tuple[str, str], dict] = {}
    for uid in _PRIVATE_SOURCES:
        pattern = str(root / f"creator_{uid}_contents_*.jsonl")
        for filename in sorted(glob.glob(pattern)):
            try:
                with open(filename, encoding="utf-8") as handle:
                    for rawline in handle:
                        try:
                            item = json.loads(rawline)
                        except json.JSONDecodeError:
                            continue
                        posted = _when(item.get("created_at"))
                        if posted is None or posted > cutoff:
                            continue
                        body = _clean(item.get("description"))
                        if not body or not any(t in body for t in tokens):
                            continue
                        if not _VALUE_WORDS.search(body):
                            continue
                        entry_id = str(item.get("status_id") or "").strip()
                        if not entry_id:
                            continue
                        key = (uid, entry_id)
                        candidate = {
                            "published_at": posted.isoformat(),
                            "private_source": uid,
                            "private_entry_id": entry_id,
                            "hypothesis": body[:2200],
                        }
                        previous = by_key.get(key)
                        if previous is None or len(body) > len(previous["hypothesis"]):
                            by_key[key] = candidate
            except OSError:
                continue
    return sorted(
        by_key.values(),
        key=lambda x: (x["published_at"], x["private_entry_id"]),
        reverse=True,
    )[:max(0, min(limit, 8))]


def company_valuation_prompt(views: list[dict], *, stock_name: str) -> str:
    """Internal LLM prompt only; not suitable for publication or API return."""
    if not views:
        return (
            f"未在本地截至研究日的归档里找到{stock_name}的直接估值帖子。"
            "不得声称已经读过某人最新帖子或虚构其具体价格。"
            "应尝试公司中周期盈利与保守定价敏感性分析，标明假设与缺口。"
        )
    blocks = []
    for item in views[:5]:
        # Deliberately omit private source identities, IDs and URLs.
        blocks.append(
            f"归档观点（发布日期 {item['published_at']}；未核验的投资假设）："
            + item["hypothesis"]
        )
    return (
        "## 对本公司的定向估值研究线索（仅内部使用）\n"
        + "\n".join(blocks)
        + "\n这些材料只是有经验投资者的主观看法。先识别其产量、利润、"
        "估值倍数、资本开支及周期假设；不能直接把其价格认定为事实。"
        "针对本公司逐项核对财报和同一研究日股价，检验盈利预测是否过于乐观。"
        "若有可复算的利润×估值倍数计算，请明确写出你的独立情景计算与反例。"
        "若不能复算，诚实说明缺失数据，不得抄录原帖或暴露作者、账户、链接、"
        "原文及其他股票案例。"
    )
