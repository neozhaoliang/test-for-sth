#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
export DISPLAY="${DISPLAY:-:99}"
mkdir -p /app/browser_data /app/data /app/logs /app/instance
Xvfb "${DISPLAY}" -screen 0 1600x900x24 -nolisten tcp -ac > /app/logs/xvfb.log 2>&1 &
xvfb_pid=$!
sleep 2
if ! kill -0 "$xvfb_pid" 2>/dev/null; then
  echo "Xvfb startup failed; see /app/logs/xvfb.log" >&2
  exit 1
fi

if [[ "${VNC_ENABLE:-0}" == "1" ]]; then
  if [[ -z "${VNC_PASSWORD:-}" || ${#VNC_PASSWORD} -lt 8 ]]; then
    echo "VNC_ENABLE=1 requires VNC_PASSWORD with at least 8 characters" >&2
    exit 2
  fi
  # Legacy VNC auth only uses 8 significant bytes. SSH tunnel is mandatory.
  passfile="$(mktemp /tmp/xueqiu-vnc.XXXXXX)"
  chmod 600 "$passfile"
  x11vnc -storepasswd "$VNC_PASSWORD" "$passfile" >/dev/null 2>&1
  unset VNC_PASSWORD
  x11vnc -display "$DISPLAY" -localhost -rfbport 5900 -rfbauth "$passfile" \
    -forever -shared -noxdamage -quiet >/app/logs/x11vnc.log 2>&1 &
  sleep 1
  # Docker must publish on HOST loopback ONLY: -p 127.0.0.1:6080:6080
  websockify --web=/usr/share/novnc 0.0.0.0:6080 127.0.0.1:5900 \
    >/app/logs/novnc.log 2>&1 &
  echo "[Xueqiu] noVNC ready on container port 6080 (use SSH tunnel)"
fi
echo "[Xueqiu] Xvfb ready; browser state saved in /app/browser_data"
exec /app/.venv/bin/python -m uvicorn web.app:app \
  --host 0.0.0.0 --port "${APP_PORT:-8000}" \
  --workers 1 --no-proxy-headers
