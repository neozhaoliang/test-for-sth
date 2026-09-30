# -*- coding: utf-8 -*-
"""Deterministic comparison for reports replayed from the same frozen snapshot."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


_STANCE_FIELDS = ("stance", "company_quality_stance", "current_odds_stance")


def _summary(payload: Dict[str, Any]) -> Dict[str, Any]:
    value = payload.get("summary") or {}
    return value if isinstance(value, dict) else {}


def _dimension_scores(payload: Dict[str, Any]) -> Dict[str, float]:
    rows = _summary(payload).get("dimension_scores") or []
    out: Dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = str(row.get("key") or row.get("dimension") or "").strip()
        if not key:
            continue
        try:
            out[key] = float(row.get("score"))
        except (TypeError, ValueError):
            continue
    return out


def _replay_provenance(payload: Dict[str, Any]) -> Dict[str, Any]:
    validation = payload.get("validation") or {}
    if not isinstance(validation, dict):
        return {}
    replay = validation.get("replay") or {}
    return replay if isinstance(replay, dict) else {}


def _prompt_version(payload: Dict[str, Any]) -> str:
    return str(
        payload.get("prompt_version")
        or _replay_provenance(payload).get("replay_prompt_version")
        or ""
    )


def _assert_same_snapshot(a: Dict[str, Any], b: Dict[str, Any]) -> None:
    pa = _replay_provenance(a)
    pb = _replay_provenance(b)
    ida = str(pa.get("snapshot_id") or "")
    idb = str(pb.get("snapshot_id") or "")
    if not ida or not idb:
        raise ValueError("both reports must contain replay snapshot provenance")
    if ida != idb:
        raise ValueError(f"reports come from different snapshots: {ida} != {idb}")

    for field in ("stock_code", "as_of", "research_mode"):
        va = str(a.get(field) or "")
        vb = str(b.get(field) or "")
        if va and vb and va != vb:
            raise ValueError(f"reports differ on {field}: {va} != {vb}")


def compare_replayed_reports(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """Compare two replay outputs without invoking any model or data adapter."""
    _assert_same_snapshot(a, b)

    sa = _summary(a)
    sb = _summary(b)
    stance_changes: Dict[str, Dict[str, str]] = {}
    for field in _STANCE_FIELDS:
        va = str(sa.get(field) or "")
        vb = str(sb.get(field) or "")
        if va != vb:
            stance_changes[field] = {"a": va, "b": vb}

    try:
        confidence_a = float(sa.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence_a = 0.0
    try:
        confidence_b = float(sb.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence_b = 0.0

    scores_a = _dimension_scores(a)
    scores_b = _dimension_scores(b)
    score_deltas: Dict[str, Dict[str, float]] = {}
    for key in sorted(set(scores_a) | set(scores_b)):
        av = scores_a.get(key)
        bv = scores_b.get(key)
        if av is None or bv is None:
            continue
        delta = round(bv - av, 6)
        score_deltas[key] = {"a": av, "b": bv, "delta_b_minus_a": delta}

    changed_dimensions = [
        key for key, row in score_deltas.items() if row["delta_b_minus_a"] != 0
    ]
    max_abs_delta = max(
        (abs(row["delta_b_minus_a"]) for row in score_deltas.values()),
        default=0.0,
    )

    pa = _replay_provenance(a)
    pb = _replay_provenance(b)
    return {
        "same_frozen_input": True,
        "snapshot_id": str(pa.get("snapshot_id") or pb.get("snapshot_id") or ""),
        "stock_code": str(a.get("stock_code") or b.get("stock_code") or ""),
        "as_of": str(a.get("as_of") or b.get("as_of") or ""),
        "a": {
            "prompt_version": _prompt_version(a),
            "generated_at": a.get("generated_at"),
            "source_prompt_version": pa.get("source_prompt_version"),
        },
        "b": {
            "prompt_version": _prompt_version(b),
            "generated_at": b.get("generated_at"),
            "source_prompt_version": pb.get("source_prompt_version"),
        },
        "stance_changes": stance_changes,
        "confidence": {
            "a": confidence_a,
            "b": confidence_b,
            "delta_b_minus_a": round(confidence_b - confidence_a, 6),
        },
        "dimension_scores": score_deltas,
        "changed_dimensions": changed_dimensions,
        "changed_dimension_count": len(changed_dimensions),
        "max_abs_dimension_delta": round(max_abs_delta, 6),
    }


def load_report_json(path: str | Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
