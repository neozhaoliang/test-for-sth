#!/usr/bin/env bash
# 启动一个开启远程调试端口的 Chrome，供 CDP 模式的爬虫/分析任务连接。
# 若该端口已被占用 (常见于上次退出未清理)，先杀掉占用进程再重新启动。
#
# 用法: bash run.sh

set -euo pipefail

PORT=9222
CHROME_PATHS=(
    "/c/Program Files/Google/Chrome/Application/chrome.exe"
    "/c/Program Files (x86)/Google/Chrome/Application/chrome.exe"
    "${LOCALAPPDATA:-}/Google/Chrome/Application/chrome.exe"
)
USER_DATA_DIR="$(pwd)/browser_data/cdp_chrome_user_data_dir"

CHROME_BIN=""
for path in "${CHROME_PATHS[@]}"; do
    if [ -n "$path" ] && [ -f "$path" ]; then
        CHROME_BIN="$path"
        break
    fi
done

if [ -z "$CHROME_BIN" ]; then
    echo "未找到 Chrome，请检查安装路径或手动编辑 run.sh 中的 CHROME_PATHS" >&2
    exit 1
fi

existing_pids="$(netstat -ano | grep -E "LISTENING" | grep ":${PORT} " | awk '{print $NF}' | sort -u || true)"
if [ -n "$existing_pids" ]; then
    echo "端口 ${PORT} 已被占用，杀掉现有进程: ${existing_pids}"
    for pid in $existing_pids; do
        taskkill //PID "$pid" //F >/dev/null 2>&1 || true
    done
    sleep 1
fi

mkdir -p "$USER_DATA_DIR"

echo "启动 Chrome (远程调试端口 ${PORT}) ..."
echo "  路径: $CHROME_BIN"
echo "  用户数据目录: $USER_DATA_DIR"

"$CHROME_BIN" \
    "--remote-debugging-port=${PORT}" \
    "--user-data-dir=${USER_DATA_DIR}" \
    "--no-first-run" \
    "--no-default-browser-check" \
    "https://xueqiu.com" &

disown

echo "已启动，浏览器窗口打开后可正常使用 (登录/滑块验证等); 完成后保持窗口开启，供后台任务连接 ${PORT} 端口。"
