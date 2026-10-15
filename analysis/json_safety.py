"""Make numeric values in public report JSON standards-compliant.

Pandas/NumPy/LLM calculations can contain NaN or +/-Infinity. Starlette's
JSONResponse rejects those values. Unknown numeric observations must become
null, never zero or a fabricated estimate. The input is never mutated.
"""
from __future__ import annotations

import math
from decimal import Decimal
from numbers import Integral, Real
from typing import Any


def finite_json_numbers(value: Any) -> Any:
    """Recursively convert non-finite numerics to None and NumPy scalars to builtins.

    Non-numeric objects stay untouched: callers can still catch genuine data
    contract errors with a strict json.dumps(..., allow_nan=False) check.
    """
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Real):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, Decimal):
        if not value.is_finite():
            return None
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, dict):
        return {key: finite_json_numbers(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [finite_json_numbers(item) for item in value]
    # NumPy scalar values, including float32/int64, need conversion to Python.
    # Avoid making numpy a hard requirement of this response-serialization layer.
    if type(value).__module__.startswith("numpy"):
        scalar = getattr(value, "item", None)
        if callable(scalar):
            converted = scalar()
            if converted is not value:
                return finite_json_numbers(converted)
        as_list = getattr(value, "tolist", None)
        if callable(as_list):
            converted = as_list()
            if converted is not value:
                return finite_json_numbers(converted)
    # Don't stringify unsupported objects; that would conceal bad API data.
    return value


def nonfinite_number_paths(value: Any, *, limit: int = 12) -> list[str]:
    """For server-side diagnostics only. Never include values or source prose."""
    paths: list[str] = []

    def visit(node: Any, path: str) -> None:
        if len(paths) >= limit:
            return
        if isinstance(node, bool) or node is None or isinstance(node, str):
            return
        if isinstance(node, dict):
            for key, child in node.items():
                visit(child, (path + "." + str(key)) if path else str(key))
                if len(paths) >= limit:
                    break
            return
        if isinstance(node, (list, tuple)):
            for index, child in enumerate(node):
                visit(child, path + "[" + str(index) + "]")
                if len(paths) >= limit:
                    break
            return
        if isinstance(node, (Real, Decimal)):
            try:
                if not math.isfinite(float(node)):
                    paths.append(path)
            except (OverflowError, TypeError, ValueError):
                paths.append(path)

    visit(value, "")
    return paths
