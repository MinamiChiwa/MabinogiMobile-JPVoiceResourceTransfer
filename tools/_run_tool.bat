@echo off
setlocal
chcp 65001 >nul
set "VOICE_ROOT=%~dp0"
if exist "%VOICE_ROOT%JPVoiceTransfer.exe" (
  "%VOICE_ROOT%JPVoiceTransfer.exe" %*
  exit /b
)
if exist "%VOICE_ROOT%.venv\Scripts\python.exe" (
  "%VOICE_ROOT%.venv\Scripts\python.exe" -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul
  if not errorlevel 1 (
    "%VOICE_ROOT%.venv\Scripts\python.exe" -X utf8 "%VOICE_ROOT%resource_transfer_launcher.py" %*
    exit /b
  )
)
python -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul
if not errorlevel 1 (
  python -X utf8 "%VOICE_ROOT%resource_transfer_launcher.py" %*
  exit /b
)
py -3 -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul
if not errorlevel 1 (
  py -3 -X utf8 "%VOICE_ROOT%resource_transfer_launcher.py" %*
  exit /b
)
echo Python 3.11+ was not found for this source package.
echo Use the portable Windows x64 package to run without installing Python.
exit /b 2
