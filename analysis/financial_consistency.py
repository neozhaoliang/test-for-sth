# -*- coding: utf-8 -*-
"""Pure financial-value consistency helpers with no crawler/runtime dependencies."""

from __future__ import annotations

from typing import Optional


def coherent_yoy_pct(
    current: Optional[float],
    previous: Optional[float],
    reported: Optional[float],
) -> Optional[float]:
    """Repair a lost YoY sign when current/previous values prove the opposite direction.

    Some F10 tables render only the percentage magnitude while the direction word lives in
    presentation text outside the parsed cell.  We flip only when the value-derived change
    has essentially the same magnitude, so genuinely different calculation bases are not
    overwritten.
    """
    if current is None or previous in (None, 0):
        return reported

    derived = (current - previous) / abs(previous) * 100
    if reported is None:
        return round(derived, 2)

    tolerance = max(0.25, abs(derived) * 0.03)
    if (
        reported != 0
        and derived != 0
        and reported * derived < 0
        and abs(abs(reported) - abs(derived)) <= tolerance
    ):
        return round(derived, 2)
    return reported
