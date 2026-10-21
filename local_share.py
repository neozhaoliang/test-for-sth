"""Run the investment research web app on THIS computer (Windows/macOS/Linux).

    uv run python local_share.py                 # localhost only
    uv run python local_share.py lan             # authenticated company LAN
    uv run python local_share.py lan --host 192.168.10.20 --allow 192.168.10.0/24

No systemd, containers, /app directory or remote deployment is involved.
"""
from __future__ import annotations

import argparse
import ipaddress
import os
from pathlib import Path
import secrets
import socket
import sys

PROJECT_ROOT = Path(__file__).resolve().parent
PRIVATE_NETWORKS = tuple(ipaddress.ip_network(block) for block in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10",
))


def is_lan_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return isinstance(address, ipaddress.IPv4Address) and any(
        address in network for network in PRIVATE_NETWORKS
    )


def autodetect_lan_ip() -> str:
    """Pick the default-route interface without sending a network packet."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("192.0.2.1", 80))  # TEST-NET-1; no UDP payload sent
            ip = sock.getsockname()[0]
            if is_lan_ip(ip):
                return ip
        except OSError:
            pass

    addresses: set[str] = set()
    try:
        for entry in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = entry[4][0]
            if is_lan_ip(ip):
                addresses.add(ip)
    except OSError:
        pass
    if len(addresses) == 1:
        return addresses.pop()
    if addresses:
        raise ValueError(
            "检测到多个内网地址（" + ", ".join(sorted(addresses)) +
            "）。请通过 ipconfig 查看正在使用的网卡，并用 --host 指定本机 IP。"
        )
    raise ValueError("无法自动检测内网 IP；请运行 ipconfig，然后用 --host 指定本机 IPv4。")


def check_host_is_local(host: str) -> None:
    if not is_lan_ip(host):
        raise ValueError("--host 必须是本机的企业内网 IPv4，不能使用公网 IP、127.0.0.1 或 0.0.0.0。")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((host, 0))
    except OSError as exc:
        raise ValueError(
            f"{host} 不是这台电脑当前可监听的地址。请用 ipconfig 找本机 IPv4（而不是同事的 IP）：{exc}"
        ) from exc


def validate_cidrs(raw_values: list[str]) -> str:
    values = [part.strip() for item in raw_values for part in item.split(",") if part.strip()]
    if not values:
        raise ValueError("允许访问的内网网段不能为空。")
    networks = []
    for value in values:
        try:
            net = ipaddress.ip_network(value, strict=True)
        except ValueError as exc:
            raise ValueError(f"无效网段 {value!r}，例如 192.168.10.0/24。") from exc
        if not isinstance(net, ipaddress.IPv4Network) or not any(
            net.subnet_of(private_net) for private_net in PRIVATE_NETWORKS
        ):
            raise ValueError(f"禁止公网或全网段访问：{value!r}。请只填写企业私网网段。")
        networks.append(str(net))
    return ",".join(dict.fromkeys(networks))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="在本地电脑上运行股票投资助手；lan 模式允许企业内网同事通过浏览器使用。"
    )
    parser.add_argument("mode", choices=("local", "lan"), nargs="?", default="local",
                        help="local 仅本机（默认）；lan 开启密码保护的内网共享")
    parser.add_argument("--host", help="本机网卡上的 IPv4 地址；不填时自动检测")
    parser.add_argument("--port", type=int, default=8000, help="Web 服务端口，默认 8000")
    parser.add_argument("--allow", action="append", default=[], metavar="CIDR",
                        help="允许访问的客户端网段，如 192.168.10.0/24；可重复或逗号分隔")
    parser.add_argument("--username", default=None, help="内网登录用户名，默认 research")
    parser.add_argument("--max-concurrent", type=int, default=None,
                        help="同时运行的付费研究任务上限（1～10），默认 2")
    return parser.parse_args(argv)


def configure(args: argparse.Namespace) -> tuple[str, str | None]:
    """Set child-app environment before importing web.app; return listener IP and new password."""
    if not 1 <= args.port <= 65535:
        raise ValueError("--port 必须在 1～65535 之间。")
    if args.mode == "local":
        if args.host or args.allow or args.username or args.max_concurrent is not None:
            raise ValueError("--host/--allow/--username/--max-concurrent 仅用于 lan 模式。")
        os.environ["INTRANET_ACCESS"] = "0"
        return "127.0.0.1", None

    host = args.host or autodetect_lan_ip()
    check_host_is_local(host)
    # Default to same /24, not the entire private 10/8. Use --allow for routed VLANs.
    local_subnet = str(ipaddress.ip_network(f"{host}/24", strict=False))
    raw_allow = args.allow or [os.environ.get("INTRANET_ALLOWED_CIDRS") or local_subnet]
    cidrs = validate_cidrs(raw_allow)
    # Ensure this machine can run diagnostics even if its clients are on a remote VLAN.
    if not any(ipaddress.ip_address(host) in ipaddress.ip_network(net) for net in cidrs.split(",")):
        cidrs += f",{host}/32"

    username = args.username or os.environ.get("INTRANET_USERNAME") or "research"
    max_concurrent = (args.max_concurrent if args.max_concurrent is not None
                      else os.environ.get("INTRANET_MAX_CONCURRENT", "2"))
    password = os.environ.get("INTRANET_PASSWORD", "")
    generated = None
    if not password:
        generated = secrets.token_urlsafe(24)
        password = generated

    os.environ.update({
        "INTRANET_ACCESS": "1",
        "INTRANET_USERNAME": username,
        "INTRANET_PASSWORD": password,
        "INTRANET_ALLOWED_CIDRS": cidrs,
        "INTRANET_MAX_CONCURRENT": str(max_concurrent),
        "INTRANET_BIND_IP": host,
    })
    # Fail before starting the listener if auth settings are bad.
    from web.intranet_access import load_access_settings
    load_access_settings()
    return host, generated


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    os.chdir(PROJECT_ROOT)  # Data, caches and relative paths always resolve locally.
    # Load optional model credentials / stable LAN password from the local gitignored .env.
    try:
        from dotenv import load_dotenv
        load_dotenv(PROJECT_ROOT / ".env", override=False)
    except ImportError:
        pass

    try:
        host, generated_password = configure(args)
    except (ValueError, RuntimeError) as exc:
        print(f"\n[启动失败] {exc}", file=sys.stderr)
        return 2

    print("\n股票投资助手 · 本地电脑启动", flush=True)
    if args.mode == "lan":
        print(f"内网共享地址：http://{host}:{args.port}")
        print(f"登录账号：{os.environ['INTRANET_USERNAME']}")
        if generated_password:
            print(f"本次启动的访问密码：{generated_password}")
            print("提示：重启后会生成新密码。想固定密码，可在本地 .env 设置 INTRANET_PASSWORD（至少20字符）。")
        print(f"允许访问的客户端网段：{os.environ['INTRANET_ALLOWED_CIDRS']}")
        print(f"付费分析最大并发数：{os.environ['INTRANET_MAX_CONCURRENT']}")
        print("同事不能访问时，请检查 Windows 防火墙入站规则和企业网络隔离。")
        print("安全提醒：普通 HTTP 不加密，请只在可信内网/VPN 中使用；不要将服务端口映射到公网。")
    else:
        print(f"仅本机访问：http://127.0.0.1:{args.port}")
    print("按 Ctrl+C 停止服务。\n", flush=True)

    try:
        import uvicorn
    except ImportError:
        print("[启动失败] 未安装依赖。请先在项目目录运行 uv sync。", file=sys.stderr)
        return 2
    # Single process: analysis/crawler task registries are in memory, not distributed.
    uvicorn.run(
        "web.app:app", host=host, port=args.port, workers=1,
        reload=False, proxy_headers=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
