"""Regression guard for F10 retrieval behind a SOCKS proxy.

HTTPX reads HTTP_PROXY/HTTPS_PROXY/ALL_PROXY from the environment by default.
If any of those uses socks5://, plain httpx installs raise ImportError
before the F10 HTTP requests even start.
"""

from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def test_httpx_socks_extra_is_declared_in_both_install_methods():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    assert "httpx[socks]==0.28.1" in requirements

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "httpx[socks]==0.28.1" in project["project"]["dependencies"]
