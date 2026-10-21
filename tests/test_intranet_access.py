"""Access control for company LAN deployment.

Run with: pytest -q tests/test_intranet_access.py
"""
import base64

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from web.intranet_access import (
    IntranetAccessMiddleware,
    credentials_valid,
    load_access_settings,
    source_allowed,
)


def settings(**overrides):
    values = {
        "INTRANET_ACCESS": "1",
        "INTRANET_USERNAME": "research",
        "INTRANET_PASSWORD": "a-long-unique-intranet-password-2026",
        "INTRANET_ALLOWED_CIDRS": "192.168.50.0/24,10.10.0.0/16",
    }
    values.update(overrides)
    return values


def auth(user="research", password="a-long-unique-intranet-password-2026"):
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture
def make_app(monkeypatch):
    def create(**env):
        values = settings(**env)
        for key, value in values.items():
            monkeypatch.setenv(key, value)
        app = Starlette(routes=[
            Route("/", lambda request: JSONResponse({"page": True})),
            Route("/api/analyze", lambda request: JSONResponse({"started": True}), methods=["POST"]),
            Route("/api/digest/rebuild", lambda request: JSONResponse({"rebuilt": True}), methods=["POST"]),
            Route("/assets/research_desk.css", lambda request: JSONResponse({"asset": True})),
            Route("/healthz", lambda request: JSONResponse({"status": "ok"})),
        ])
        app.add_middleware(IntranetAccessMiddleware)
        return app
    return create


@pytest.mark.asyncio
async def test_intranet_blocks_every_sensitive_route_without_password(make_app):
    app = make_app()
    transport = httpx.ASGITransport(app=app, client=("192.168.50.17", 54321))
    async with httpx.AsyncClient(transport=transport, base_url="http://lan") as client:
        for method, path in (
            ("GET", "/"), ("POST", "/api/analyze"),
            ("POST", "/api/digest/rebuild"), ("GET", "/assets/research_desk.css"),
        ):
            response = await client.request(method, path)
            assert response.status_code == 401
            assert "Basic" in response.headers["www-authenticate"]
            assert response.headers["cache-control"] == "no-store"
            wrong = await client.request(method, path, headers=auth(password="incorrect-secret"))
            assert wrong.status_code == 401
            good = await client.request(method, path, headers=auth())
            assert good.status_code == 200
            assert good.headers["cache-control"] == "no-store"
        healthy = await client.get("/healthz")
        assert healthy.status_code == 200


@pytest.mark.asyncio
async def test_public_network_is_denied_even_with_correct_password_or_spoofed_forwarded_for(make_app):
    app = make_app()
    transport = httpx.ASGITransport(app=app, client=("198.51.100.42", 54321))
    async with httpx.AsyncClient(transport=transport, base_url="http://lan") as client:
        response = await client.post(
            "/api/analyze",
            headers={**auth(), "X-Forwarded-For": "192.168.50.17"},
        )
        assert response.status_code == 403
        assert response.headers["cache-control"] == "no-store"
        health = await client.get("/healthz")
        assert health.status_code == 403


@pytest.mark.asyncio
async def test_default_local_mode_does_not_demand_password(monkeypatch, make_app):
    app = make_app(INTRANET_ACCESS="0")
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 9999))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
        assert (await client.get("/")).status_code == 200
        assert (await client.post("/api/analyze")).status_code == 200


def test_invalid_password_subnet_or_concurrency_fail_closed():
    for updates in (
        {"INTRANET_PASSWORD": ""},
        {"INTRANET_PASSWORD": "short"},
        {"INTRANET_USERNAME": "research:admin"},
        {"INTRANET_ALLOWED_CIDRS": ""},
        {"INTRANET_ALLOWED_CIDRS": "0.0.0.0/0"},
        {"INTRANET_ALLOWED_CIDRS": "192.168.50.1/24"},
        {"INTRANET_MAX_CONCURRENT": "0"},
        {"INTRANET_MAX_CONCURRENT": "11"},
        {"INTRANET_ACCESS": "maybe"},
    ):
        with pytest.raises(RuntimeError):
            load_access_settings(settings(**updates))


def test_malformed_basic_header_rejected():
    cfg = load_access_settings(settings())
    assert credentials_valid(auth()["Authorization"], cfg)
    for value in (None, "", "Basic @@@", "Bearer token", "Basic abcdef",
                  "Basic " + "A" * 3000,
                  auth(password="wrong")["Authorization"]):
        assert not credentials_valid(value, cfg)
    assert source_allowed("192.168.50.98", cfg.networks)
    assert not source_allowed("172.17.0.1", cfg.networks)
    assert not source_allowed("not-an-ip", cfg.networks)
