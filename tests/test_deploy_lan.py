"""Deploy script invariants and a no-systemd simulation of local secrets."""
from pathlib import Path
import os
import subprocess


SCRIPT = Path(__file__).resolve().parents[1] / "deploy.sh"


def test_deploy_script_bash_parses_and_uses_private_bind_only():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True, capture_output=True)
    text = SCRIPT.read_text(encoding="utf-8")
    assert "lan-enable|lan-disable" in text
    assert "write_systemd_unit \"$BIND_HOST\"" in text
    assert "write_systemd_unit \"$host\"" in text
    assert "BIND_HOST=\"127.0.0.1\"" in text
    assert "validate_lan_ip \"$host\"" in text
    assert "INTRANET_ALLOWED_CIDRS" in text
    assert "--no-proxy-headers" in text
    assert "127.0.0.1" in text
    assert "INTRANET_PASSWORD" in text
    assert "APP_ARCHIVE" in text
    assert "--host 0.0.0.0" not in text
    assert "-o StrictHostKeyChecking=no" not in text


def test_env_updates_do_not_erase_api_key_and_retain_mode(tmp_path):
    original = tmp_path / ".env"
    original.write_text(
        "ANTHROPIC_AUTH_TOKEN=keep-private-01234\n"
        "BACKTEST_LLM_MODEL=deepseek-flash\n"
        "INTRANET_ACCESS=0\n",
        encoding="utf-8",
    )
    source = SCRIPT.read_text(encoding="utf-8")
    # Source only the small shell helper section, never run apt, SSH, Git or
    # systemctl during this unit-level test.
    begin = source.index("env_get() {")
    end = source.index("validate_lan_ip() {", begin)
    helpers = source[begin:end]
    runner = (
        "set -Eeuo pipefail\n"
        "APP_DIR=\"$1\"\n"
        "ENV_FILE=\"$APP_DIR/.env\"\n"
        "RUN_USER=\"$(id -un)\"\n"
        "RUN_GROUP=\"$(id -gn)\"\n"
        "die() { echo \"error: $*\" >&2; exit 1; }\n"
        "as_root() { \"$@\"; }\n"
        + helpers + "\n"
        "env_set INTRANET_ACCESS 1\n"
        "env_set INTRANET_ALLOWED_CIDRS 192.168.20.0/24\n"
        "env_set INTRANET_PASSWORD a123456789012345678901234567890\n"
        "env_set INTRANET_ACCESS 0\n"
        "test \"$(env_get INTRANET_ACCESS)\" = 0\n"
    )
    result = subprocess.run(
        ["bash", "-c", runner, "test", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    output = original.read_text(encoding="utf-8")
    assert output.count("ANTHROPIC_AUTH_TOKEN=keep-private-01234") == 1
    assert output.count("INTRANET_ACCESS=0") == 1
    assert "INTRANET_ACCESS=1" not in output
    assert "INTRANET_PASSWORD=a123456789012345678901234567890" in output
    assert "INTRANET_ALLOWED_CIDRS=192.168.20.0/24" in output
    assert oct(original.stat().st_mode & 0o777) == "0o600"
    assert "keep-private" not in result.stdout


def test_generated_systemd_unit_binds_exact_private_interface(tmp_path):
    text = SCRIPT.read_text(encoding="utf-8")
    start = text.index("write_systemd_unit() {")
    end = text.index("verify_service_health() {", start)
    helper = text[start:end]
    unit = tmp_path / "investment-agent.service"
    script = (
        "set -Eeuo pipefail\n"
        "APP_DIR=\"$1\"\n"
        "ENV_FILE=\"$APP_DIR/.env\"\n"
        "UNIT_FILE=\"$2\"\n"
        "RUN_USER=\"$(id -un)\"\n"
        "RUN_GROUP=\"$(id -gn)\"\n"
        "APP_PORT=8000\n"
        "as_root() { if [[ \"$1\" == systemctl ]]; then return 0; fi; \"$@\"; }\n"
        + helper + "\n"
        "write_systemd_unit 192.168.50.20\n"
    )
    result = subprocess.run(
        ["bash", "-c", script, "test", str(tmp_path), str(unit)],
        text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    content = unit.read_text(encoding="utf-8")
    assert "--host 192.168.50.20 --port 8000" in content
    assert "--no-proxy-headers" in content
    assert "EnvironmentFile=" + str(tmp_path / ".env") in content
    assert "--host 0.0.0.0" not in content
    assert "INTRANET_PASSWORD" not in content
