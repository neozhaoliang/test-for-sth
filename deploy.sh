#!/usr/bin/env bash
# Linux (Ubuntu/Debian + systemd): deploy the investment research WebUI.
# Usage: bash deploy.sh [deploy|update|start|restart|stop|status|logs|lan-enable|lan-disable]
# API keys stay in the server-local .env (mode 0600, gitignored).
set -Eeuo pipefail
umask 077

# SSH over 443 bypasses the unstable HTTPS proxy shown in the server logs.
# SSH requires a read-only deploy key registered with this GitHub repository.
REPO_URL_SSH="ssh://git@ssh.github.com:443/neozhaoliang/test-for-sth.git"
REPO_URL_HTTPS="https://github.com/neozhaoliang/test-for-sth.git"
GIT_TRANSPORT="${GIT_TRANSPORT:-ssh}"
REPO_URL="$REPO_URL_SSH"
BRANCH="main"
SERVICE_NAME="${SERVICE_NAME:-investment-agent}"
APP_PORT="${APP_PORT:-8000}"
BIND_HOST="127.0.0.1"  # Private by default; LAN exposure is opt-in and authenticated.
ACTION="${1:-deploy}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

say() { printf '\n\033[1;34m[deploy]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[注意]\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31m[失败]\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'HELP'
投资研究助手 · Ubuntu/Debian 服务器部署

  bash deploy.sh           初次安装/后续更新并启动（默认）
  bash deploy.sh update    拉取 main、同步依赖并重启
  bash deploy.sh start     启动已有服务
  bash deploy.sh restart   重启已有服务
  bash deploy.sh stop      停止服务
  bash deploy.sh status    查看服务状态
  bash deploy.sh logs      查看最近 100 行服务日志
  bash deploy.sh lan-enable   启用内网访问，不重新下载项目
  bash deploy.sh lan-disable  关闭内网访问，恢复仅本机监听

可选环境变量：
  APP_DIR=/opt/investment-agent   安装路径（默认在运行用户家目录）
  APP_PORT=8000                  本机监听端口
  SERVICE_NAME=investment-agent  systemd 服务名
  DEPLOY_USER=investment-agent   仅 root 直接执行时指定服务运行用户
  SKIP_BROWSER_INSTALL=1        跳过浏览器安装（部分雪球功能可能失效）
  SKIP_NODE_UPGRADE=1           暂跳过 Node.js 升级（Node 12 下采集功能可能异常）
  GIT_TRANSPORT=ssh             默认：SSH via ssh.github.com:443
  GIT_TRANSPORT=https           可选：原 HTTPS 模式（当前代理不稳定）
  APP_ARCHIVE=/app/test-for-sth-main.zip   本地 ZIP 部署，不访问 GitHub
  LAN_BIND_IP=192.168.10.20   服务器自身的企业内网 IPv4（必须指定）
  LAN_ALLOWED_CIDRS=192.168.10.0/24   允许连接的客户端网段
  LAN_USERNAME=research       内网登录用户名（默认）
  LAN_MAX_CONCURRENT=2        同时运行的付费分析任务上限（1～10）

安全说明：默认仅绑定 127.0.0.1。内网模式只绑定指定私网网卡，
所有页面/API 均有密码及客户端网段限制，不监听公网网卡。
HTTP Basic 经普通 HTTP 传输不加密，请通过可信企业 VPN 或 HTTPS 使用。
VPS 若未加入企业内网/VPN，企业同事无法直接访问其内网 IP。
HELP
}

case "$ACTION" in
  -h|--help|help) usage; exit 0 ;;
  deploy|update|start|restart|stop|status|logs|lan-enable|lan-disable) ;;
  *) usage; die "不认识的命令：$ACTION" ;;
esac

[[ "$(uname -s)" == Linux ]] || die "这个脚本只支持 Linux。"
command -v systemctl >/dev/null 2>&1 || die "服务器需要 systemd。"
[[ "$SERVICE_NAME" =~ ^[A-Za-z0-9_-]+$ ]] || die "SERVICE_NAME 只能包含字母、数字、-、_。"
[[ "$GIT_TRANSPORT" == ssh || "$GIT_TRANSPORT" == https ]] || die "GIT_TRANSPORT 只能是 ssh 或 https。"
if [[ "$GIT_TRANSPORT" == https ]]; then REPO_URL="$REPO_URL_HTTPS"; fi
# Offline here means only the source checkout: uv, PyPI and Chromium still
# need their normal package mirrors unless installed or cached already.
APP_ARCHIVE="${APP_ARCHIVE:-}"
if [[ -n "$APP_ARCHIVE" ]]; then
  [[ "$APP_ARCHIVE" == /* ]] || die "APP_ARCHIVE 必须是绝对路径。"
  [[ -f "$APP_ARCHIVE" && -r "$APP_ARCHIVE" ]] || die "本地 ZIP 文件不存在或无法读取：$APP_ARCHIVE"
  [[ "$APP_ARCHIVE" == *.zip ]] || die "APP_ARCHIVE 目前只支持 GitHub 下载的 .zip 文件。"
fi
[[ "$APP_PORT" =~ ^[0-9]+$ ]] && (( 10#$APP_PORT >= 1 && 10#$APP_PORT <= 65535 )) || die "APP_PORT 必须在 1～65535 之间。"
if [[ "${BIND_HOST_OVERRIDE:-127.0.0.1}" != "127.0.0.1" ]]; then
  die "为避免陌生人调用你的付费 API，脚本只支持本机监听；请在前面加带认证的反向代理。"
fi

as_root() {
  if (( EUID == 0 )); then "$@"; else sudo "$@"; fi
}

# With a root-only VPS, automatically create a dedicated unprivileged service
# account instead of letting a web crawler run as root.
if (( EUID == 0 )); then
  RUN_USER="${DEPLOY_USER:-${SUDO_USER:-investment-agent}}"
  [[ "$RUN_USER" != root ]] || die "请指定非 root 的 DEPLOY_USER。"
  if ! id "$RUN_USER" >/dev/null 2>&1; then
    [[ "$ACTION" == deploy || "$ACTION" == update ]] || die "用户 $RUN_USER 不存在，请先运行 deploy。"
    useradd --create-home --shell /bin/bash "$RUN_USER"
  fi
else
  RUN_USER="$(id -un)"
  command -v sudo >/dev/null 2>&1 || die "需要 sudo 权限来安装系统依赖与注册 systemd 服务。"
fi
RUN_HOME="$(getent passwd "$RUN_USER" | cut -d: -f6)"
RUN_GROUP="$(id -gn "$RUN_USER")"
[[ -n "$RUN_HOME" && "$RUN_HOME" == /* ]] || die "无法找到运行用户的家目录。"

as_user() {
  if [[ "$(id -un)" == "$RUN_USER" ]]; then
    "$@"
  else
    runuser -u "$RUN_USER" -- env HOME="$RUN_HOME" PATH="$RUN_HOME/.local/bin:$PATH" "$@"
  fi
}

if [[ -z "${APP_DIR:-}" ]]; then
  if (( EUID != 0 )) && [[ -f "$SCRIPT_DIR/pyproject.toml" && -f "$SCRIPT_DIR/web/app.py" ]]; then
    APP_DIR="$SCRIPT_DIR"
  else
    APP_DIR="$RUN_HOME/investment-agent"
  fi
fi
[[ "$APP_DIR" == /* ]] || die "APP_DIR 必须是绝对路径。"
[[ ! "$APP_DIR" =~ [[:space:]] ]] || die "APP_DIR 暂不支持含空格的路径。"
[[ "$APP_DIR" != /root/* ]] || die "不能把非 root Web 服务部署在 /root 下。"
UNIT_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
ENV_FILE="$APP_DIR/.env"
[[ ! -L "$ENV_FILE" ]] || die "为保护服务器密钥，.env 不能是符号链接。"


# Safely preserve all API credentials while updating only INTRANET_* keys.
env_get() {
  local name="$1" line
  [[ -f "$ENV_FILE" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ "$line" == "$name="* ]]; then
      printf '%s' "${line#*=}"
      return 0
    fi
  done < "$ENV_FILE"
}

env_set() {
  local name="$1" value="$2" line seen=0 tmp
  [[ "$name" =~ ^INTRANET_[A-Z_]+$ ]] || die "非法内网配置键。"
  [[ "$value" != *$'\n'* && "$value" != *$'\r'* ]] ||
    die "内网配置不得包含换行符。"
  tmp="$(mktemp "$APP_DIR/.env.XXXXXXXX")"
  {
    if [[ -f "$ENV_FILE" ]]; then
      while IFS= read -r line || [[ -n "$line" ]]; do
        if [[ "$line" == "$name="* ]]; then
          if (( ! seen )); then
            printf '%s=%s\n' "$name" "$value"
            seen=1
          fi
        else
          printf '%s\n' "$line"
        fi
      done < "$ENV_FILE"
    fi
    if (( ! seen )); then printf '%s=%s\n' "$name" "$value"; fi
  } > "$tmp"
  as_root chown "$RUN_USER:$RUN_GROUP" "$tmp"
  as_root chmod 600 "$tmp"
  mv -f -- "$tmp" "$ENV_FILE"
}

validate_lan_ip() {
  local host="$1"
  [[ -x "$APP_DIR/.venv/bin/python" ]] || die "请先部署服务；没有 Python 虚拟环境。"
  if ! "$APP_DIR/.venv/bin/python" - "$host" <<'PY'
import ipaddress, sys
try:
    addr = ipaddress.IPv4Address(sys.argv[1])
except ValueError:
    sys.exit(1)
allowed = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10")
sys.exit(0 if any(addr in ipaddress.ip_network(n) for n in allowed) else 1)
PY
  then
    die "LAN_BIND_IP 必须是服务器真实配置的内网 IPv4；不能用 0.0.0.0 或公网 IP。"
  fi
  if command -v ip >/dev/null 2>&1; then
    if ! ip -o -4 addr show | awk '{print $4}' | cut -d/ -f1 | grep -Fxq "$host"; then
      die "本机没有 IP $host。请先运行 ip -4 addr 查看网卡的内网 IP。"
    fi
  fi
}

validate_lan_ranges() {
  if ! "$APP_DIR/.venv/bin/python" - "$1" "$2" <<'PY'
import ipaddress, sys
try:
    networks = [ipaddress.ip_network(part.strip(), strict=True)
                for part in sys.argv[1].split(",") if part.strip()]
    n = int(sys.argv[2])
    assert networks and all(net.prefixlen > 0 for net in networks)
    assert 1 <= n <= 10
except (AssertionError, ValueError):
    sys.exit(1)
PY
  then
    die "LAN_ALLOWED_CIDRS 或 LAN_MAX_CONCURRENT 无效。示例：192.168.10.0/24"
  fi
}

# The same unit is used for first deployment and LAN toggles. Uvicorn proxy
# headers are disabled to prevent forged X-Forwarded-For bypass of source IP.
write_systemd_unit() {
  local host="$1" tmp
  tmp="$(mktemp)"
  cat > "$tmp" <<EOF
[Unit]
Description=Private Investment Research Agent
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=$RUN_USER
Group=$RUN_GROUP
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONIOENCODING=utf-8
Environment=TZ=Asia/Shanghai
UMask=0077
ExecStart=/usr/bin/xvfb-run -a -s "-screen 0 1920x1080x24" $APP_DIR/.venv/bin/python -m uvicorn web.app:app --host $host --port $APP_PORT --workers 1 --no-proxy-headers
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
  as_root install -m 0644 "$tmp" "$UNIT_FILE"
  rm -f -- "$tmp"
  as_root systemctl daemon-reload
}



# Confirm the new systemd worker has bound its socket before printing success.
# /healthz returns only a status and has no login prompt, but source CIDRs
# still apply. This is safe even when requests are routed through an HTTP proxy.
verify_service_health() {
  local host="$1" success=0
  for _ in {1..15}; do
    if as_root systemctl is-active --quiet "$SERVICE_NAME" &&
       curl --noproxy '*' --silent --fail --max-time 3 \
         "http://$host:$APP_PORT/healthz" >/dev/null 2>&1; then
      success=1
      break
    fi
    sleep 1
  done
  if (( ! success )); then
    as_root journalctl -u "$SERVICE_NAME" -n 35 --no-pager >&2 || true
    die "服务没有正常启动。请检查日志：bash deploy.sh logs"
  fi
}

configure_lan_mode() {
  [[ -f "$UNIT_FILE" && -f "$ENV_FILE" ]] ||
    die "请先部署并启动服务，然后切换企业内网访问。"
  [[ -f "$APP_DIR/web/app.py" ]] || die "Web 服务入口不存在。"
  if [[ "$ACTION" == lan-disable ]]; then
    env_set INTRANET_ACCESS 0
    env_set INTRANET_BIND_IP 127.0.0.1
    write_systemd_unit 127.0.0.1
    as_root systemctl restart "$SERVICE_NAME"
    verify_service_health 127.0.0.1
    say "内网访问已关闭；恢复仅本机监听：http://127.0.0.1:$APP_PORT"
    return
  fi
  [[ -f "$APP_DIR/web/intranet_access.py" ]] ||
    die "服务器尚未安装新版内网鉴权代码。请先通过 ZIP 或 Git 更新项目，再运行 lan-enable。"
  grep -Fq 'IntranetAccessMiddleware' "$APP_DIR/web/app.py" ||
    die "应用入口仍然是旧版，拒绝向内网开放未经认证的服务。"

  local host ranges max username password new_password
  host="${LAN_BIND_IP:-$(env_get INTRANET_BIND_IP)}"
  [[ -n "$host" && "$host" != 127.0.0.1 ]] ||
    die "请指定 LAN_BIND_IP=服务器私网IP，例如 LAN_BIND_IP=192.168.10.20 bash deploy.sh lan-enable"
  validate_lan_ip "$host"
  ranges="${LAN_ALLOWED_CIDRS:-$(env_get INTRANET_ALLOWED_CIDRS)}"
  if [[ -z "$ranges" ]]; then
    ranges="$("$APP_DIR/.venv/bin/python" - "$host" <<'PY'
import ipaddress, sys
print(ipaddress.ip_network(sys.argv[1] + "/24", strict=False))
PY
)"
    warn "未给出公司内网网段，暂以 $ranges 作为允许网段。"
  fi
  max="${LAN_MAX_CONCURRENT:-$(env_get INTRANET_MAX_CONCURRENT)}"
  max="${max:-2}"

  validate_lan_ranges "$ranges" "$max"
  # Always allow the server's own private interface to reach /healthz,
  # including when admin narrows the client allowlist to another VLAN.
  ranges="$("$APP_DIR/.venv/bin/python" - "$host" "$ranges" <<'PY'
import ipaddress, sys
host = ipaddress.ip_address(sys.argv[1])
parts = [ipaddress.ip_network(x.strip()) for x in sys.argv[2].split(",")]
print(sys.argv[2] if any(host in net for net in parts)
      else sys.argv[2] + "," + str(host) + "/32")
PY
)"
  username="${LAN_USERNAME:-$(env_get INTRANET_USERNAME)}"
  username="${username:-research}"
  [[ "$username" =~ ^[A-Za-z0-9_.-]{1,40}$ ]] ||
    die "登录用户名只能由字母、数字、下划线、点、短横线构成。"

  password="$(env_get INTRANET_PASSWORD)"
  new_password=0
  if [[ ${#password} -lt 20 ]]; then
    password="$("$APP_DIR/.venv/bin/python" -c 'import secrets; print(secrets.token_hex(24))')"
    new_password=1
  fi

  # Store credentials before exposing the listener, fail closed on startup.
  env_set INTRANET_ACCESS 1
  env_set INTRANET_BIND_IP "$host"
  env_set INTRANET_USERNAME "$username"
  env_set INTRANET_PASSWORD "$password"
  env_set INTRANET_ALLOWED_CIDRS "$ranges"
  env_set INTRANET_MAX_CONCURRENT "$max"
  write_systemd_unit "$host"
  as_root systemctl restart "$SERVICE_NAME"
  verify_service_health "$host"

  say "内网访问地址：http://$host:$APP_PORT"
  printf '登录用户名：%s\n' "$username"
  if (( new_password )); then
    printf '首次生成的访问密码：%s\n' "$password"
    warn "仅在当前终端显示一次密码，请通过可信渠道分享，切勿上传 GitHub。"
  else
    printf '密码继续使用服务器 .env 中的原有配置。\n'
  fi
  warn "允许来源网段：$ranges。请在企业防火墙/云安全组中只对内网开放端口 $APP_PORT。"
  warn "HTTP Basic 在普通 HTTP 上不加密；通过企业 VPN 或 HTTPS 使用。"
}

service_command() {
  [[ -f "$UNIT_FILE" ]] || die "服务未安装，请先运行 bash deploy.sh。"
  case "$ACTION" in
    start|restart|stop) as_root systemctl "$ACTION" "$SERVICE_NAME"; say "$ACTION 完成：$SERVICE_NAME" ;;
    status) as_root systemctl status "$SERVICE_NAME" --no-pager -l || true ;;
    logs) as_root journalctl -u "$SERVICE_NAME" -n 100 --no-pager ;;
  esac
}
if [[ "$ACTION" == lan-enable || "$ACTION" == lan-disable ]]; then
  configure_lan_mode
  exit 0
fi
if [[ "$ACTION" != deploy && "$ACTION" != update ]]; then
  service_command
  exit 0
fi

[[ -f /etc/os-release ]] || die "无法识别 Linux 发行版。"
# shellcheck disable=SC1091
source /etc/os-release
case "${ID:-}" in
  ubuntu|debian) ;;
  *) die "自动安装目前只支持 Ubuntu / Debian；当前为 ${ID:-unknown}。" ;;
esac

say "准备系统依赖（git、curl、Node.js、Xvfb 等）"
if ! command -v git >/dev/null || ! command -v curl >/dev/null || \
   ! command -v node >/dev/null || ! command -v xvfb-run >/dev/null || \
   ! command -v xauth >/dev/null || ! command -v ssh >/dev/null || ! command -v ssh-keygen >/dev/null || \
   [[ -n "$APP_ARCHIVE" && ( ! -x /usr/bin/unzip || ! -x /usr/bin/rsync ) ]]; then
  as_root apt-get update
  as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y \
    git curl ca-certificates nodejs xvfb xauth openssh-client unzip rsync
fi
node_major() {
  if ! command -v node >/dev/null 2>&1; then echo 0; return; fi
  node -p 'parseInt(process.versions.node.split(".")[0],10)' 2>/dev/null || echo 0
}

NODE_MAJOR="$(node_major)"
if (( NODE_MAJOR < 18 )) && [[ "${SKIP_NODE_UPGRADE:-0}" != 1 ]]; then
  say "Node.js $(node -v 2>/dev/null || echo 未安装) 过旧，自动升级到 Node.js 22 LTS"
  NODE_SETUP="$(mktemp)"
  if ! curl --http1.1 -fLsS --retry 3 --retry-delay 3 --connect-timeout 20 https://deb.nodesource.com/setup_22.x -o "$NODE_SETUP"; then
    rm -f "$NODE_SETUP"
    die "无法下载 NodeSource 安装程序，请先检查网络或手动安装 Node.js 22。"
  fi
  if ! as_root bash "$NODE_SETUP"; then
    rm -f "$NODE_SETUP"
    die "NodeSource 仓库配置失败，请检查 APT 源。"
  fi
  rm -f "$NODE_SETUP"
  as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs
  NODE_MAJOR="$(node_major)"
  (( NODE_MAJOR >= 18 )) || die "Node.js 升级失败，请检查 node -v。"
fi

# Official GitHub ED25519 host key for github.com / ssh.github.com:443
# https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints
# Exact pin prevents a proxy/MITM from substituting its own SSH host identity.
GITHUB_ED25519="AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"
SSH_DIR="$RUN_HOME/.ssh"
SSH_KEY="$SSH_DIR/id_ed25519_investment_agent"
SSH_KNOWN_HOSTS="$SSH_DIR/known_hosts_investment_agent"
GIT_SSH_COMMAND=""

configure_git_ssh() {
  as_user install -d -m 0700 "$SSH_DIR"
  if [[ ! -f "$SSH_KEY" ]]; then
    say "为 $RUN_USER 生成只用于部署的 SSH 密钥"
    as_user ssh-keygen -q -t ed25519 -N "" \
      -C "investment-agent-deploy" -f "$SSH_KEY"
  fi
  [[ -f "$SSH_KEY.pub" ]] || die "存在 SSH 私钥但缺少公钥：$SSH_KEY.pub，请人工检查。"
  as_user chmod 0600 "$SSH_KEY"
  as_user chmod 0644 "$SSH_KEY.pub"
  as_user sh -c 'printf "[ssh.github.com]:443 ssh-ed25519 %s\n" "$1" > "$2"' \
    _ "$GITHUB_ED25519" "$SSH_KNOWN_HOSTS"
  as_user chmod 0600 "$SSH_KNOWN_HOSTS"

  # Only affect THIS deployment. No global SSH config changes, no insecure
  # StrictHostKeyChecking=no, and no secret/private-key output to terminal.
  GIT_SSH_COMMAND="ssh -F /dev/null -i $SSH_KEY -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$SSH_KNOWN_HOSTS -o ConnectTimeout=12 -o ServerAliveInterval=10 -o ServerAliveCountMax=2"
  say "检查 SSH 到 GitHub 443 端口及仓库只读权限"
  local verify_output=""
  if ! verify_output="$(as_user env GIT_SSH_COMMAND="$GIT_SSH_COMMAND" \
      git ls-remote "$REPO_URL_SSH" HEAD 2>&1)"; then
    if [[ "$verify_output" == *"Permission denied (publickey)"* ||
          "$verify_output" == *"Could not read from remote repository"* ]]; then
      warn "服务器公钥还没有在 GitHub 上获得仓库读取权限。"
      printf '\n===== 请复制这一整行公钥（不是私钥） =====\n'
      as_user cat "$SSH_KEY.pub"
      printf '==========================================\n'
      printf '请进入 https://github.com/neozhaoliang/test-for-sth/settings/keys\n'
      printf '点击 Add deploy key，粘贴上面公钥，保持 Allow write access 未勾选。\n'
      die "添加 GitHub Deploy Key 后，直接重新运行 bash deploy.sh。"
    fi
    warn "SSH 连接或 GitHub 仓库访问失败：$verify_output"
    die "请检查 ssh.github.com 的 TCP 443 能否直连；如果代理阻断 SSH，请从其他电脑上传代码。"
  fi
  say "SSH 443 已连通，仓库读取权限正常。"
}

if [[ "$GIT_TRANSPORT" == ssh && -z "$APP_ARCHIVE" ]]; then
  configure_git_ssh
fi

git_safe() {
  if [[ "$GIT_TRANSPORT" == ssh ]]; then
    as_user env GIT_SSH_COMMAND="$GIT_SSH_COMMAND" git "$@"
  else
    # Original HTTPS fallback, forced to HTTP/1.1 rather than HTTP/2.
    as_user git -c http.version=HTTP/1.1 \
      -c http.lowSpeedLimit=1024 -c http.lowSpeedTime=90 "$@"
  fi
}
retry_git() {
  local attempt
  for attempt in 1 2 3 4; do
    if git_safe "$@"; then return 0; fi
    if (( attempt < 4 )); then
      warn "Git 网络错误，第 $attempt 次；$((attempt * 3)) 秒后重试（$GIT_TRANSPORT）。"
      sleep $((attempt * 3))
    fi
  done
  return 1
}

# If the VPS cannot reach GitHub, upload the official "Download ZIP" from
# another machine and run APP_ARCHIVE=/app/test-for-sth-main.zip bash deploy.sh.
# Source files are local; only package/dependency installations may use network.
install_local_zip() {
  local archive="$1" staging source_root candidate
  say "从本地 ZIP 部署源码（不访问 GitHub）：$archive"
  local parent
  parent="$(dirname "$APP_DIR")"
  [[ -d "$parent" ]] || as_root install -d -m 0755 -o "$RUN_USER" -g "$RUN_GROUP" "$parent"
  staging="$(as_root mktemp -d "$parent/.$SERVICE_NAME-archive.XXXXXXXX")"
  # mktemp creates a root-owned 0700 directory. Let the restricted service
  # user traverse it before rsync, without opening it to other users.
  as_root chown "$RUN_USER:$RUN_GROUP" "$staging"
  cleanup_archive_staging() {
    if [[ -n "$staging" && -d "$staging" ]]; then
      as_root rm -rf -- "$staging"
    fi
  }
  trap cleanup_archive_staging EXIT

  as_root unzip -q "$archive" -d "$staging" || die "ZIP 解压失败，请重新从 GitHub 下载完整 ZIP。"
  candidate="$(find "$staging" -maxdepth 3 -type f -name pyproject.toml -print -quit)"
  [[ -n "$candidate" ]] || die "ZIP 缺少 pyproject.toml：请下载项目的 Code → Download ZIP。"
  source_root="$(dirname "$candidate")"
  [[ -f "$source_root/web/app.py" && -f "$source_root/uv.lock" ]] ||
    die "ZIP 缺少 web/app.py 或 uv.lock，不能部署。"

  # Local data, secrets, virtualenv and browser sessions must survive future
  # uploads. Existing source files are replaced, but no user files deleted.
  if [[ -L "$APP_DIR" ]]; then die "APP_DIR 不支持符号链接，请检查安装目录。"; fi
  if [[ -d "$APP_DIR/.git" ]] &&
     ! git_safe -C "$APP_DIR" rev-parse --verify HEAD >/dev/null 2>&1; then
    local backup="$APP_DIR.incomplete.$(date -u +%Y%m%dT%H%M%SZ)"
    as_root mv -- "$APP_DIR" "$backup"
    warn "上次 Git 克隆不完整，旧目录已备份到：$backup"
  fi

  if [[ ! -d "$APP_DIR" ]]; then
    as_root install -d -m 0750 -o "$RUN_USER" -g "$RUN_GROUP" "$APP_DIR"
  fi
  as_root chown -R "$RUN_USER:$RUN_GROUP" "$source_root"
  # ZIP timestamps are coarse; --checksum also updates same-size files
  # changed within a minute, so repeated uploads never silently keep old code.
  as_user rsync -a --checksum \
    --exclude='/.git/' --exclude='/.env' --exclude='/.venv/' \
    --exclude='/data/' --exclude='/browser_data/' \
    --exclude='/cache/' --exclude='/logs/' --exclude='/instance/' \
    --exclude='/database/*.db' \
    --exclude='/config/valuation_cases.local.json' \
    "$source_root/" "$APP_DIR/" ||
    die "本地 ZIP 复制失败，请检查目录权限。"
  as_root chown "$RUN_USER:$RUN_GROUP" "$APP_DIR"
  cleanup_archive_staging
  trap - EXIT
  say "本地 ZIP 源码部署成功；本地密钥、数据目录和浏览器会话没有被覆盖。"
}

say "安装/更新项目：$APP_DIR"
if [[ -n "$APP_ARCHIVE" ]]; then
  install_local_zip "$APP_ARCHIVE"
else
# An older deploy.sh may have left behind .git before its HTTP/2 download
# failed. Keep that entire directory as a timestamped backup, don't delete
# local data, then retry cleanly with the HTTP/1.1 shallow clone.
if [[ -d "$APP_DIR/.git" ]] &&
   ! git_safe -C "$APP_DIR" rev-parse --verify HEAD >/dev/null 2>&1; then
  backup="$APP_DIR.incomplete.$(date -u +%Y%m%dT%H%M%SZ)"
  as_root mv -- "$APP_DIR" "$backup"
  warn "检测到上次不完整的 Git 克隆，已备份至 $backup，继续重新下载。"
fi
if [[ ! -d "$APP_DIR/.git" ]]; then
  if [[ -d "$APP_DIR" && -n "$(ls -A "$APP_DIR")" ]]; then
    die "$APP_DIR 不是完整 Git 仓库且含有文件；为避免删除用户数据，请先备份或移走。"
  fi
  # Clone into a scratch directory and move it into place only after success.
  # Failed downloads never become misleading half-installed applications.
  parent="$(dirname "$APP_DIR")"
  [[ -d "$parent" ]] || as_root install -d -m 0755 -o "$RUN_USER" -g "$RUN_GROUP" "$parent"
  stage="$(as_root mktemp -d "$parent/.$SERVICE_NAME-clone.XXXXXXXX")"
  as_root chown "$RUN_USER:$RUN_GROUP" "$stage"
  cleanup_stage() {
    if [[ -n "$stage" && -d "$stage" ]]; then as_root rm -rf -- "$stage"; fi
  }
  trap cleanup_stage EXIT
  success=0
  for attempt in 1 2 3 4; do
    # Only remove the scratch clone belonging to this deployment.
    as_root rm -rf -- "$stage/repository"
    say "GitHub 浅克隆，第 $attempt/4 次（$GIT_TRANSPORT）"
    if git_safe clone --depth 1 --single-branch --no-tags --branch "$BRANCH" "$REPO_URL" "$stage/repository"; then
      success=1
      break
    fi
    if (( attempt < 4 )); then
      warn "GitHub 网络断开，$((attempt * 3)) 秒后重试。"
      sleep $((attempt * 3))
    fi
  done
  (( success )) || die "GitHub 克隆失败；请检查 SSH 443 线路及 GitHub Deploy Key（HTTPS 备用模式检查代理）。"
  [[ -f "$stage/repository/pyproject.toml" && -f "$stage/repository/web/app.py" ]] || die "代码下载不完整。"
  if [[ -d "$APP_DIR" ]]; then
    rmdir "$APP_DIR" || die "安装目录非空，请先检查：$APP_DIR"
  fi
  as_root mv -- "$stage/repository" "$APP_DIR"
  cleanup_stage
  trap - EXIT
else
  [[ "$(git_safe -C "$APP_DIR" branch --show-current)" == "$BRANCH" ]] || die "项目分支不是 $BRANCH，拒绝强制重置。"
  say "Git 拉取更新（$GIT_TRANSPORT，失败自动重试）"
  # Switch previously-cloned HTTPS worktrees to SSH after new key setup.
  # Preserve the repo history and local edits; change only origin remote.
  current_origin="$(git_safe -C "$APP_DIR" remote get-url origin)"
  if [[ "$current_origin" != "$REPO_URL" ]]; then
    say "切换 origin 远程地址为 $GIT_TRANSPORT"
    git_safe -C "$APP_DIR" remote set-url origin "$REPO_URL"
  fi
  retry_git -C "$APP_DIR" fetch --depth=200 --no-tags origin "$BRANCH" || die "GitHub 更新失败，请检查网络或 SSH 密钥。"
  git_safe -C "$APP_DIR" merge --ff-only FETCH_HEAD || die "更新无法快进，请检查本地修改；不会执行 reset/clean。"
fi
fi # APP_ARCHIVE bypasses Git/SSH/HTTPS entirely
[[ -f "$APP_DIR/pyproject.toml" && -f "$APP_DIR/web/app.py" ]] || die "仓库不包含预期的投资助手入口。"

# The existing, server-local key is never overwritten by a git update.
if [[ ! -f "$ENV_FILE" ]]; then
  token="${ANTHROPIC_AUTH_TOKEN:-${ANTHROPIC_API_KEY:-}}"
  if [[ -z "$token" && -t 0 ]]; then
    printf '请输入模型 API Key（不回显，默认使用 DeepSeek 兼容接口）：' >&2
    IFS= read -r -s token
    printf '\n' >&2
  fi
  if [[ "$token" == *$'\n'* || "$token" == *$'\r'* || "$token" == *" "* ]]; then
    die "API Key 不能包含空格或换行。"
  fi
  {
    printf '# 服务器私有配置，请勿提交到 GitHub。\n'
    printf 'ANTHROPIC_BASE_URL=%s\n' "${ANTHROPIC_BASE_URL:-https://api.deepseek.com/anthropic}"
    printf 'BACKTEST_LLM_MODEL=%s\n' "${BACKTEST_LLM_MODEL:-deepseek-flash}"
    printf 'BACKTEST_LLM_MAX_TOKENS=65536\n'
    printf 'ANTHROPIC_AUTH_TOKEN=%s\n' "$token"
  } > "$ENV_FILE"
fi
as_root chown "$RUN_USER:$RUN_GROUP" "$ENV_FILE"
as_root chmod 600 "$ENV_FILE"

# Never print the token, even on missing-credential errors.
if ! as_user sh -c 'grep -Eq "^[[:space:]]*(ANTHROPIC_AUTH_TOKEN|ANTHROPIC_API_KEY)[[:space:]]*=[[:space:]]*[^[:space:]#<]+" "$1"' _ "$ENV_FILE"; then
  die "已创建 $ENV_FILE，但尚无 API Key。请用 nano $ENV_FILE 填写后重运行 bash deploy.sh。"
fi

say "安装 uv 并按 uv.lock 同步 Python 3.11 依赖"
UV_BIN="$RUN_HOME/.local/bin/uv"
if [[ ! -x "$UV_BIN" ]]; then
  if command -v uv >/dev/null 2>&1; then
    UV_BIN="$(command -v uv)"
  else
    as_user env UV_INSTALL_DIR="$RUN_HOME/.local/bin" sh -c \
      'curl -LsSf https://astral.sh/uv/install.sh | sh'
  fi
fi
[[ -x "$UV_BIN" ]] || die "uv 安装失败：$UV_BIN"
as_user "$UV_BIN" sync --frozen --python 3.11 --project "$APP_DIR"
[[ -x "$APP_DIR/.venv/bin/python" ]] || die "Python 虚拟环境创建失败。"

if [[ "${SKIP_BROWSER_INSTALL:-0}" != 1 ]]; then
  say "安装 Playwright Chromium 与 Linux 浏览器系统依赖（首次会下载浏览器）"
  as_root "$APP_DIR/.venv/bin/python" -m playwright install-deps chromium
  as_user "$APP_DIR/.venv/bin/python" -m playwright install chromium
else
  warn "已跳过浏览器安装；雪球/直播抓取等依赖浏览器的功能可能不可用。"
fi


# Preserve LAN settings when updating from Git or uploaded ZIP.
if [[ "$(env_get INTRANET_ACCESS)" == 1 ]]; then
  BIND_HOST="$(env_get INTRANET_BIND_IP)"
  validate_lan_ip "$BIND_HOST"
  validate_lan_ranges "$(env_get INTRANET_ALLOWED_CIDRS)" \
    "$(env_get INTRANET_MAX_CONCURRENT)"
  [[ -f "$APP_DIR/web/intranet_access.py" ]] ||
    die "更新不包含内网鉴权模块，拒绝启动不安全的监听。"
fi
say "注册 systemd 服务（监听 $BIND_HOST，内网模式需登录）"
write_systemd_unit "$BIND_HOST"
as_root systemctl enable "$SERVICE_NAME"
as_root systemctl restart "$SERVICE_NAME"

say "检查 Web 服务启动"
ready=0
for _ in {1..20}; do
  if as_root systemctl is-active --quiet "$SERVICE_NAME" && \
     curl --noproxy '*' -fsS --max-time 3 "http://${BIND_HOST}:${APP_PORT}/healthz" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 2
done
if (( ! ready )); then
  as_root journalctl -u "$SERVICE_NAME" -n 35 --no-pager >&2 || true
  die "服务未通过启动检查。请运行 bash deploy.sh logs 查看完整错误。"
fi

say "部署成功"
printf '项目目录：%s\n服务名称：%s\n本机地址：http://127.0.0.1:%s\n' \
  "$APP_DIR" "$SERVICE_NAME" "$APP_PORT"

if [[ "$BIND_HOST" != 127.0.0.1 ]]; then
  printf '企业内网地址：http://%s:%s（需要浏览器账号/密码）\n' "$BIND_HOST" "$APP_PORT"
  printf '访问白名单：%s\n' "$(env_get INTRANET_ALLOWED_CIDRS)"
  warn "如有服务器安全组/防火墙，只允许公司内网网段访问端口 $APP_PORT。"
else
  printf '本地电脑访问：ssh -L %s:127.0.0.1:%s 用户名@服务器IP\n' \
    "$APP_PORT" "$APP_PORT"
  printf '然后浏览器打开：http://127.0.0.1:%s\n' "$APP_PORT"
fi

printf '查看日志：bash deploy.sh logs；更新并重启：bash deploy.sh update\n'
warn '只启动投资助手，不代表雪球已登录。无桌面的 Xvfb 不能人工完成 WAF 滑块，需要另配可交互浏览器会话。'
