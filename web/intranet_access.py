"""Opt-in protection for sharing the investment research UI on an intranet.

Never trust X-Forwarded-For for the IP allowlist: client IP is taken from
the direct socket. When enabled, ALL routes (including costly crawl/rebuild
APIs) require both an allowed source address and HTTP Basic credentials.
Use on a trusted VPN/HTTPS network; plain HTTP Basic is not encrypted.
"""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import hmac
import ipaddress
import os
import re
from typing import Mapping

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response


@dataclass(frozen=True)
class AccessSettings:
    enabled: bool
    username: str = ""
    password: str = ""
    networks: tuple = ()
    max_concurrent: int = 2


def load_access_settings(env: Mapping[str, str] | None = None) -> AccessSettings:
    if env is None:
        env = os.environ
    flag = str(env.get("INTRANET_ACCESS", "0")).strip()
    if flag not in ("0", "1"):
        raise RuntimeError("INTRANET_ACCESS must be 0 or 1.")
    if flag != "1":
        return AccessSettings(enabled=False)

    username = str(env.get("INTRANET_USERNAME", "research")).strip()
    password = str(env.get("INTRANET_PASSWORD", ""))
    cidrs = str(env.get("INTRANET_ALLOWED_CIDRS", "")).strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", username):
        raise RuntimeError("INTRANET_USERNAME is invalid.")
    if len(password) < 20 or "\n" in password or "\r" in password or ":" in password:
        raise RuntimeError("INTRANET_PASSWORD must be at least 20 characters.")
    if not cidrs:
        raise RuntimeError("INTRANET_ALLOWED_CIDRS must list company subnet(s).")
    try:
        networks = tuple(
            ipaddress.ip_network(part.strip(), strict=True)
            for part in cidrs.split(",") if part.strip()
        )
    except ValueError as exc:
        raise RuntimeError("INTRANET_ALLOWED_CIDRS contains an invalid network.") from exc
    if not networks:
        raise RuntimeError("INTRANET_ALLOWED_CIDRS cannot be empty.")
    # Wildcard source networks would allow all of the public internet.
    if any(net.prefixlen == 0 for net in networks):
        raise RuntimeError("INTRANET_ALLOWED_CIDRS may not contain 0.0.0.0/0 or ::/0.")

    try:
        max_concurrent = int(env.get("INTRANET_MAX_CONCURRENT", "2"))
    except (ValueError, TypeError) as exc:
        raise RuntimeError("INTRANET_MAX_CONCURRENT must be a positive integer.") from exc
    if not 1 <= max_concurrent <= 10:
        raise RuntimeError("INTRANET_MAX_CONCURRENT must be between 1 and 10.")
    return AccessSettings(
        enabled=True, username=username, password=password,
        networks=networks, max_concurrent=max_concurrent,
    )


def source_allowed(source: str | None, networks: tuple) -> bool:
    if not source:
        return False
    try:
        ip = ipaddress.ip_address(source)
    except ValueError:
        return False
    return any(ip in net for net in networks)


def credentials_valid(header: str | None, settings: AccessSettings) -> bool:
    if not header or len(header) > 2048 or not header.lower().startswith("basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8")
        username, sep, password = decoded.partition(":")
    except (ValueError, UnicodeDecodeError, binascii.Error):
        return False
    if not sep:
        return False
    return (hmac.compare_digest(username.encode(), settings.username.encode())
            and hmac.compare_digest(password.encode(), settings.password.encode()))


class IntranetAccessMiddleware(BaseHTTPMiddleware):
    """Fail closed on misconfiguration rather than exposing paid endpoints."""

    def __init__(self, app):
        super().__init__(app)
        self.settings = load_access_settings()

    async def dispatch(self, request: Request, call_next):
        settings = self.settings
        if not settings.enabled:
            return await call_next(request)

        source = request.client.host if request.client else None
        if not source_allowed(source, settings.networks):
            return PlainTextResponse(
                "This service is only available to the configured intranet.",
                status_code=403, headers={"Cache-Control": "no-store"},
            )

        # Local service/monitoring endpoint is harmless. No auth prompts
        # or plaintext secrets are needed for systemd health checks.
        if request.url.path == "/healthz":
            return await call_next(request)

        if not credentials_valid(request.headers.get("authorization"), settings):
            return Response(
                status_code=401,
                headers={
                    "WWW-Authenticate": 'Basic realm="Investment Research", charset="UTF-8"',
                    "Cache-Control": "no-store",
                },
            )
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response
