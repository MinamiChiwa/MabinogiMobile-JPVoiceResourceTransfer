@echo off
call "%~dp0_run_tool.bat" transfer --gui
set "VOICE_RC=%errorlevel%"
if not "%VOICE_RC%"=="0" echo Operation failed. See run_transfer.log.
pause
exit /b %VOICE_RC%
