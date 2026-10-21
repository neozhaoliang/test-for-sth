@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 (
  echo [Error] uv is not installed or is not on PATH. Install uv and run uv sync first.
  pause
  exit /b 1
)
uv run python local_share.py lan %*
if errorlevel 1 pause
endlocal
