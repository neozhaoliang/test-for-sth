"""Regression: a completed stock report must never crash task polling on NaN.

One bad upstream observation becomes JSON null, not an entire HTTP 500 or a
fabricated 0. Include nested sector returns, macro observations, issuer facts,
NumPy scalar/array values and legacy cached task payloads.
"""
import json
from copy import deepcopy
from decimal import Decimal

import numpy as np
import pytest
from starlette.responses import JSONResponse

from analysis.json_safety import finite_json_numbers, nonfinite_number_paths
from analysis.public_report import public_stock_report


def sample_report():
    return {
        "stock_code": "SH601899",
        "stock_name": "紫金矿业",
        "knowledge_excerpts": [{
            "author": "军师祭咖啡", "distilled": "内部材料，不允许泄露"
        }],
        "market_context": {
            "indices": [
                {"name": "上证红利", "d20_pct": 1.22},
                {"name": "科创50", "d20_pct": float("-inf")},
            ],
            "sector_rotation": {
                "status": "observed",
                "sectors": [
                    {"name": "有色金属", "d20_pct": np.float64("nan")},
                    {"name": "化学制药", "d20_pct": np.float32(5.5)},
                    {"name": "银行", "d20_pct": 0.0},
                ],
                "median_return_pct": float("inf"),
                "up_count": np.int64(3),
            },
        },
        "macro_rates": {
            "us": {"us10y_yield_pct": float("inf"), "source_name": "FRED"},
            "china": {"lpr_5y_pct": 3.5},
        },
        "valuation_model": {
            "macro_context": {
                "observations": [
                    {"metric": "usdcny_midpoint_cny_per_usd", "value": 6.7253,
                     "usable": True},
                    {"metric": "us_10y_change_90d_pp", "value": float("nan"),
                     "usable": False},
                ],
                "overseas_revenue_pct": np.float64("nan"),
            },
            "style_context": {"style_regime": "mixed",
                              "private_excerpt_count": 5},
        },
        "fundamentals": {
            "facts": {"operating_cash_flow": np.float32("nan"),
                      "revenue": Decimal("123.456"), "cash": np.float64(2.2)},
        },
        "summary": {"thesis_summary": "真实事实可保留；缺失的观测应显示暂缺。"},
    }


def test_public_report_deep_clean_preserves_privacy_and_real_numbers():
    raw = sample_report()
    original = deepcopy(raw)
    public = public_stock_report(raw)
    # This is exactly the strict JSON response contract used by Starlette.
    JSONResponse(content=public)
    serialized = json.dumps(public, allow_nan=False, ensure_ascii=False)
    assert "NaN" not in serialized and "Infinity" not in serialized
    assert "军师祭咖啡" not in serialized and "private_excerpt_count" not in serialized
    assert "knowledge_excerpts" not in public
    rows = public["market_context"]["sector_rotation"]["sectors"]
    assert rows[0]["d20_pct"] is None
    assert rows[1]["d20_pct"] == 5.5
    assert rows[2]["d20_pct"] == 0.0
    assert public["market_context"]["indices"][1]["d20_pct"] is None
    assert public["macro_rates"]["us"]["us10y_yield_pct"] is None
    assert public["valuation_model"]["macro_context"]["observations"][1]["value"] is None
    assert public["fundamentals"]["facts"]["operating_cash_flow"] is None
    assert public["fundamentals"]["facts"]["revenue"] == 123.456
    assert public["market_context"]["sector_rotation"]["up_count"] == 3
    # Public projection must not change the original typed research report.
    assert str(raw) == str(original)


def test_numeric_normalizer_handles_nested_numpy_and_decimal_without_zeros():
    dirty = {
        "nested": [float("nan"), (float("inf"), -float("inf"), Decimal("NaN"))],
        "array": np.array([[1.0, np.nan], [np.inf, 4.5]], dtype=float),
        "numpy_integer": np.int64(17),
        "finite": [0, -1.25, True, False, None, Decimal("2.125")],
    }
    clean = finite_json_numbers(dirty)
    JSONResponse(content=clean)
    assert clean["nested"] == [None, [None, None, None]]
    assert clean["array"] == [[1.0, None], [None, 4.5]]
    assert clean["numpy_integer"] == 17
    assert clean["finite"] == [0, -1.25, True, False, None, 2.125]
    assert isinstance(dirty["array"], np.ndarray)
    assert np.isnan(dirty["array"][0, 1])
    assert finite_json_numbers(clean) == clean


def test_numeric_path_diagnostics_never_include_observation_values():
    report = sample_report()
    paths = nonfinite_number_paths(report)
    assert "macro_rates.us.us10y_yield_pct" in paths
    assert "market_context.sector_rotation.sectors[0].d20_pct" in paths
    assert len(nonfinite_number_paths(report, limit=2)) == 2
    assert "6.7253" not in repr(paths)


@pytest.mark.asyncio
async def test_existing_invalid_completed_task_is_safe_to_poll():
    import importlib
    from httpx import ASGITransport, AsyncClient

    web = importlib.import_module("web.app")
    tid = "regression-json-nan"
    web._tasks[tid] = {
        "status": "done", "result": sample_report(), "error": None,
    }
    try:
        async with AsyncClient(transport=ASGITransport(app=web.app),
                               base_url="http://test.local") as client:
            response = await client.get("/api/tasks/" + tid)
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "done"
        assert payload["result"]["macro_rates"]["us"]["us10y_yield_pct"] is None
        assert payload["result"]["stock_name"] == "紫金矿业"
        assert "knowledge_excerpts" not in payload["result"]
        assert web._tasks[tid]["result"]["macro_rates"]["us"]["us10y_yield_pct"] == float("inf")
    finally:
        web._tasks.pop(tid, None)


@pytest.mark.asyncio
async def test_new_task_is_marked_done_only_after_json_preflight(monkeypatch):
    import importlib
    web = importlib.import_module("web.app")

    class FakeReport:
        def model_dump(self):
            return sample_report()

    async def fake_generate_report(*args, **kwargs):
        return FakeReport()

    monkeypatch.setattr(web, "generate_report", fake_generate_report)
    tid = "regression-json-preflight"
    web._tasks[tid] = {"status": "pending", "result": None, "error": None}
    try:
        await web._run_analysis(tid, "SH601899", object())
        assert web._tasks[tid]["status"] == "done"
        JSONResponse(content=web._tasks[tid]["result"])
    finally:
        web._tasks.pop(tid, None)


@pytest.mark.asyncio
async def test_unsupported_result_type_fails_task_instead_of_get_500(monkeypatch):
    import importlib
    web = importlib.import_module("web.app")

    class BadReport:
        def model_dump(self):
            return {"stock_code": "SH601899", "market_context": {"bad": object()}}

    async def fake_generate_report(*args, **kwargs):
        return BadReport()

    monkeypatch.setattr(web, "generate_report", fake_generate_report)
    tid = "regression-unsupported-result"
    web._tasks[tid] = {"status": "pending", "result": None, "error": None}
    try:
        await web._run_analysis(tid, "SH601899", object())
        assert web._tasks[tid]["status"] == "failed"
        assert web._tasks[tid]["result"] is None
        public = await web.get_task(tid)
        assert public["status"] == "failed"
        assert "object" not in json.dumps(public)
        JSONResponse(content=public)
    finally:
        web._tasks.pop(tid, None)
