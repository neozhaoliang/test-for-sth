#!/usr/bin/env bash
# Linux (Ubuntu/Debian + systemd): deploy the investment research WebUI.
# Usage: bash deploy.sh [deploy|update|start|restart|stop|status|logs]
# API keys stay in the server-local .env (mode 0600, gitignored).
set -Eeuo pipefail
umask 077

REPO_URL="https://github.com/neozhaoliang/test-for-sth.git"
BRANCH="main"
SERVICE_NAME="${SERVICE_NAME:-investment-agent}"
APP_PORT="${APP_PORT:-8000}"
BIND_HOST="127.0.0.1"  # Deliberately private: this app has no login/rate-limit layer.
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

可选环境变量：
  APP_DIR=/opt/investment-agent   安装路径（默认在运行用户家目录）
  APP_PORT=8000                  本机监听端口
  SERVICE_NAME=investment-agent  systemd 服务名
  DEPLOY_USER=investment-agent   仅 root 直接执行时指定服务运行用户
  SKIP_BROWSER_INSTALL=1        跳过浏览器安装（部分雪球功能可能失效）

安全说明：服务仅绑定 127.0.0.1，不会将 API Key 暴露给网页。
远程浏览器访问请使用 SSH 端口转发或带身份认证的 HTTPS 反向代理。
HELP
}

case "$ACTION" in
  -h|--help|help) usage; exit 0 ;;
  deploy|update|start|restart|stop|status|logs) ;;
  *) usage; die "不认识的命令：$ACTION" ;;
esac

[[ "$(uname -s)" == Linux ]] || die "这个脚本只支持 Linux。"
command -v systemctl >/dev/null 2>&1 || die "服务器需要 systemd。"
[[ "$SERVICE_NAME" =~ ^[A-Za-z0-9_-]+$ ]] || die "SERVICE_NAME 只能包含字母、数字、-、_。"
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

service_command() {
  [[ -f "$UNIT_FILE" ]] || die "服务未安装，请先运行 bash deploy.sh。"
  case "$ACTION" in
    start|restart|stop) as_root systemctl "$ACTION" "$SERVICE_NAME"; say "$ACTION 完成：$SERVICE_NAME" ;;
    status) as_root systemctl status "$SERVICE_NAME" --no-pager -l || true ;;
    logs) as_root journalctl -u "$SERVICE_NAME" -n 100 --no-pager ;;
  esac
}
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
   ! command -v xauth >/dev/null; then
  as_root apt-get update
  as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y \
    git curl ca-certificates nodejs xvfb xauth
fi
if command -v node >/dev/null; then
  NODE_MAJOR="$(node -p 'parseInt(process.versions.node.split(".")[0],10)' 2>/dev/null || echo 0)"
  if (( NODE_MAJOR < 16 )); then
    warn "Node.js 版本较旧（$(node -v)）；部分爬虫 JavaScript 可能需要升级到 Node.js 18+。"
  fi
fi

say "安装/更新项目：$APP_DIR"
if [[ ! -d "$APP_DIR/.git" ]]; then
  if [[ -d "$APP_DIR" && -n "$(ls -A "$APP_DIR")" ]]; then
    die "$APP_DIR 已存在且不是空 Git 仓库；为保护数据，拒绝覆盖。"
  fi
  as_root install -d -m 0750 -o "$RUN_USER" -g "$RUN_GROUP" "$APP_DIR"
  as_user git clone --branch "$BRANCH" --single-branch "$REPO_URL" "$APP_DIR"
else
  [[ "$(as_user git -C "$APP_DIR" branch --show-current)" == "$BRANCH" ]] || \
    die "当前项目不在 $BRANCH 分支；为保护本地修改，不会强行切换或重置。"
  as_user git -C "$APP_DIR" fetch origin "$BRANCH"
  as_user git -C "$APP_DIR" merge --ff-only FETCH_HEAD || \
    die "Git 更新失败。请先处理本地修改或分支分叉；不会执行 reset/clean。"
fi
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

say "注册 systemd 服务（单进程，任务状态保存在进程内存）"
# No paid API on a public listener. Front this with authenticated HTTPS or SSH.
# The browser uses Xvfb because upstream launches a headed browser even on Linux.
TEMP_UNIT="$(mktemp)"
cat > "$TEMP_UNIT" <<EOF
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
ExecStart=/usr/bin/xvfb-run -a -s "-screen 0 1920x1080x24" $APP_DIR/.venv/bin/python -m uvicorn web.app:app --host $BIND_HOST --port $APP_PORT --workers 1
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
as_root install -m 0644 "$TEMP_UNIT" "$UNIT_FILE"
rm -f "$TEMP_UNIT"
as_root systemctl daemon-reload
as_root systemctl enable "$SERVICE_NAME"
as_root systemctl restart "$SERVICE_NAME"

say "检查 Web 服务启动"
ready=0
for _ in {1..20}; do
  if as_root systemctl is-active --quiet "$SERVICE_NAME" && \
     curl -fsS --max-time 3 "http://127.0.0.1:${APP_PORT}/" >/dev/null 2>&1; then
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
printf '本地电脑访问：ssh -L %s:127.0.0.1:%s 用户名@服务器IP\n' \
  "$APP_PORT" "$APP_PORT"
printf '然后浏览器打开：http://127.0.0.1:%s\n' "$APP_PORT"
printf '查看日志：bash deploy.sh logs；更新并重启：bash deploy.sh update\n'
warn '只启动投资助手，不代表雪球已登录。无桌面的 Xvfb 不能人工完成 WAF 滑块，需要另配可交互浏览器会话。'
