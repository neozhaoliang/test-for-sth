export ANTHROPIC_AUTH_TOKEN='sk-5f961e6e18d0426d979969e3576e0e09'
export ANTHROPIC_BASE_URL='https://api.deepseek.com/anthropic'
export BACKTEST_LLM_MODEL='deepseek-v4-flash'

set -euo pipefail

# 本地运行，不启用用户名和密码验证
export INTRANET_ACCESS=0

exec uv run python -m uvicorn web.app:app --host 127.0.0.1 --port 8001
#uv run python -m uvicorn web.app:app --host 127.0.0.1 --port 8001
#uv run python local_share.py lan