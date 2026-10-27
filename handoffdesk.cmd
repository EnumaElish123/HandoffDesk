@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if defined HANDOFFDESK_PYTHON (
  "%HANDOFFDESK_PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 2)"
  if errorlevel 1 goto unsupported
  "%HANDOFFDESK_PYTHON%" -s -B -X utf8 -m handoffdesk %*
  exit /b
)
where py >nul 2>&1
if not errorlevel 1 (
  py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 2)"
  if errorlevel 1 goto unsupported
  py -3 -s -B -X utf8 -m handoffdesk %*
  exit /b
)
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 2)"
if errorlevel 1 goto unsupported
python -s -B -X utf8 -m handoffdesk %*
exit /b %errorlevel%
:unsupported
echo HandoffDesk requires Python 3.10 or newer. Set HANDOFFDESK_PYTHON to its executable.
exit /b 2
