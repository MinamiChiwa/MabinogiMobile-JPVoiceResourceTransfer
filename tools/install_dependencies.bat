@echo off
echo Portable package: offline runtime check. Source package: local virtual environment setup.
call "%~dp0_run_tool.bat" dependencies
set "VOICE_RC=%errorlevel%"
pause
exit /b %VOICE_RC%
