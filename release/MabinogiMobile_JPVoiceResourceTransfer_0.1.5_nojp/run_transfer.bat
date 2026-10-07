@echo off
setlocal
chcp 65001 >nul
set "ROOT=%~dp0"
title Mabinogi Mobile JP Voice Resource Transfer
echo ============================================================
echo Mabinogi Mobile JP Voice Resource Transfer
echo GUI / Language / 语言 / 語言; browse folders; live log
echo Sources are read-only; default scope: Aranwen / 阿蘭雯; output goes to a new folder.
echo Close the game and keep this window open / 请关闭游戏并保持窗口开启。
echo ============================================================
where py >nul 2>nul
if errorlevel 1 (
  echo Python 3.11 or newer was not found.
  echo Run install_dependencies.bat after installing Python.
  pause
  exit /b 2
)
py -3 -X utf8 "%ROOT%resource_transfer_launcher.py" transfer --gui
set "RC=%errorlevel%"
if "%RC%"=="0" (
  echo [完成] 替换流程已结束。请检查输出目录 appdata\patch-report.json。
) else (
  echo [失败] 操作退出码：%RC%。请查看 run_transfer.log 和输出目录中的 patch-report.json。
)
pause
exit /b %RC%
