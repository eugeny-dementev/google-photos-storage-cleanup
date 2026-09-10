@echo off
setlocal EnableExtensions
set "ROOT=%~dp0"

if exist "%ROOT%.venv\Scripts\python.exe" (
  cd /d "%ROOT%"
  "%ROOT%.venv\Scripts\python.exe" -m gpsync %*
  exit /b %ERRORLEVEL%
)

if exist "%ROOT%.venv\bin\python" (
  where wsl >nul 2>&1
  if not errorlevel 1 (
    wsl --cd "%ROOT%." -e .venv/bin/python -m gpsync %*
    exit /b %ERRORLEVEL%
  )
)

echo No project .venv found. From this folder, once:
echo   python -m venv .venv
echo   .venv\Scripts\pip install -e .
echo   .venv\Scripts\playwright install chromium
echo If you use WSL instead:
echo   python3 -m venv .venv ^&^& .venv/bin/pip install -e . ^&^& .venv/bin/playwright install chromium
exit /b 1
