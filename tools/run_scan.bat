@echo off
call "%~dp0_run_tool.bat" scan --cli
set "VOICE_RC=%errorlevel%"
pause
exit /b %VOICE_RC%
