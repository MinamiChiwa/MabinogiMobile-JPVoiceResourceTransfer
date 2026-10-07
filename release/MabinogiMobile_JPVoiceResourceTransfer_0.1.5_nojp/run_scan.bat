@echo off
setlocal
chcp 65001 >nul
set "ROOT=%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Python 3.11 or newer was not found.
  pause
  exit /b 2
)
py -3 -X utf8 "%ROOT%resource_transfer_launcher.py" scan
set "RC=%errorlevel%"
if not "%RC%"=="0" echo Operation failed with code %RC%. Full log: run_scan.log
pause
exit /b %RC%
