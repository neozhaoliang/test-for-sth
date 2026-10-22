#!/usr/bin/env bash
# 本地启动股票投资助手（无需用户名或密码）。
# Docker 镜像使用独立的 tools/docker-entrypoint.sh；不受此脚本影响。
# 用法：bash run.sh

set -euo pipefail

# 无论从哪个目录执行，都从项目根目录启动。
cd "$(dirname "${BASH_SOURCE[0]}")"

# 本地仅监听环回地址，不开启内网 Basic Auth。
export INTRANET_ACCESS=0

exec uv run python -m uvicorn web.app:app --host 127.0.0.1 --port 8001
