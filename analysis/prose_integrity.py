"""Conservative integrity checks for investor-facing Chinese research prose.

The model can repeat a tail verbatim and stop in mid-sentence. Numeric sets,
financial-unit preservation, and tone scores cannot detect that corruption.
Only delete trailing text whose entire substantive content occurs EARLIER
in the same string; never guess which financial details should be removed.
"""
from __future__ import annotations

import re

# Candidate suffixes must begin at an actual sentence/paragraph boundary.
# Leading Chinese commas after a paragraph break are a common LLM defect.
_TAIL_BOUNDARY = re.compile(r"(?<=[。！？\n])[ \t\r\n]*[，,；;：:]?[ \t]*")
_LEADING_BREAK = re.compile(r"(?:^|\n)[ \t]*[，,；;：:、]")
_END_MARKS = "。！？!?…）】”’\"'"


def repeated_trailing_excerpt_start(
    text: str, *, min_chars: int = 36
) -> int | None:
    """Locate a demonstrably repeated trailing excerpt, if one exists.

    The WHOLE trailing excerpt must occur verbatim earlier in the article.
    This deliberately does not attempt semantic paraphrase deduplication:
    repeating a short market term is normal, while an exact 60-character
    copy followed by a cut-off word is a generation failure.
    """
    if not isinstance(text, str) or len(text) < min_chars * 2:
        return None
    earliest = None
    for match in _TAIL_BOUNDARY.finditer(text):
        start = match.start()
        if start == 0 or len(text) - start > 680:
            continue
        tail = text[start:].lstrip(" \t\r\n，,；;：:")
        if len(tail) < min_chars:
            continue
        # A sufficiently long exact match is proof of duplication; no
        # numerical, date or stock-specific word is altered or inferred.
        if tail in text[:start]:
            earliest = start
            break
    return earliest


def remove_repeated_trailing_excerpt(text: str) -> str:
    """Remove only an independently verifiable repeated ending."""
    start = repeated_trailing_excerpt_start(text)
    return text[:start].rstrip() if start is not None else text


def has_malformed_prose(text: str) -> bool:
    """Report obvious broken endings without pretending to score style."""
    if not isinstance(text, str) or not text.strip():
        return False
    if repeated_trailing_excerpt_start(text) is not None:
        return True
    trimmed = text.strip()
    if trimmed.endswith(("，", ",", "、", "；", ";", "：", ":")):
        return True
    if len(trimmed) >= 75 and _LEADING_BREAK.search(trimmed):
        return True
    if (len(trimmed) >= 100 or
            sum(trimmed.count(mark) for mark in "。！？") >= 2) and trimmed[-1] not in _END_MARKS:
        # A very short clause after completed sentences, with no ending
        # punctuation, resembles the clipped "属于温和" in the user report.
        fragment = re.split(r"[。！？\n]", trimmed)[-1].strip()
        if 0 < len(fragment) <= 16 and re.search(r"[\u4e00-\u9fff]", fragment):
            return True
    return False
