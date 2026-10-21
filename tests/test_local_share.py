"""Platform-independent checks for the local Windows/LAN launcher."""
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "local_share", Path(__file__).resolve().parents[1] / "local_share.py"
)
local_share = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(local_share)


def test_local_mode_does_not_bind_lan(monkeypatch):
    monkeypatch.setenv("INTRANET_ACCESS", "1")
    host, password = local_share.configure(local_share.parse_args([]))
    assert host == "127.0.0.1"
    assert password is None
    assert local_share.os.environ["INTRANET_ACCESS"] == "0"


def test_private_ranges_and_public_ranges():
    for ip in ("192.168.1.2", "10.2.0.9", "172.20.3.2", "100.88.0.11"):
        assert local_share.is_lan_ip(ip)
    for ip in ("0.0.0.0", "127.0.0.1", "8.8.8.8", "2001:db8::1"):
        assert not local_share.is_lan_ip(ip)
    for value in ("0.0.0.0/0", "8.8.8.0/24", "192.168.1.1/24", "::/0"):
        with pytest.raises(ValueError):
            local_share.validate_cidrs([value])


def test_lan_config_uses_exact_lan_ip_with_auth_and_restricted_source(monkeypatch):
    from web.intranet_access import load_access_settings
    monkeypatch.setattr(local_share, "check_host_is_local", lambda host: None)
    monkeypatch.setenv("INTRANET_PASSWORD", "super-long-stable-password-12345")
    for key in ("INTRANET_ALLOWED_CIDRS", "INTRANET_MAX_CONCURRENT", "INTRANET_USERNAME"):
        monkeypatch.delenv(key, raising=False)
    opts = local_share.parse_args([
        "lan", "--host", "192.168.55.21", "--allow", "192.168.55.0/24",
        "--max-concurrent", "3",
    ])
    host, generated = local_share.configure(opts)
    cfg = load_access_settings()
    assert host == "192.168.55.21" and generated is None
    assert cfg.enabled and cfg.max_concurrent == 3
    assert str(cfg.networks[0]) == "192.168.55.0/24"
    assert local_share.os.environ["INTRANET_PASSWORD"] == "super-long-stable-password-12345"


def test_other_vlan_gets_local_health_access(monkeypatch):
    monkeypatch.setattr(local_share, "check_host_is_local", lambda host: None)
    monkeypatch.setenv("INTRANET_PASSWORD", "super-long-stable-password-12345")
    opts = local_share.parse_args(["lan", "--host", "10.8.1.2", "--allow", "10.9.0.0/16"])
    local_share.configure(opts)
    assert local_share.os.environ["INTRANET_ALLOWED_CIDRS"] == "10.9.0.0/16,10.8.1.2/32"


def test_never_bind_a_nonlocal_interface(monkeypatch):
    class DummySocket:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def bind(self, address): raise OSError("not assigned")
    monkeypatch.setattr(local_share.socket, "socket", lambda *_: DummySocket())
    with pytest.raises(ValueError, match="不是这台电脑"):
        local_share.check_host_is_local("192.168.55.21")


def test_default_mode_rejects_any_lan_configuration():
    with pytest.raises(ValueError):
        local_share.configure(local_share.parse_args(["local", "--host", "10.1.2.3"]))
