@echo off
:: Win11 Magic Upgrade - one-click launcher (portable, no .NET 4.8 / FlyOOBE required)
setlocal EnableExtensions
cd /d "%~dp0"

net session >nul 2>&1
if %errorlevel% neq 0 (
  echo Requesting Administrator privileges...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

set WMU_NO_PAUSE=0
title Win11 Magic Upgrade
echo.
echo   Win11 Magic Upgrade - one click
echo   Flyby11-class bypass + Fido ISO + MBR-GPT + obsolete Win10 path
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0src\Win11MagicUpgrade.ps1" -OneClick %*
set ERR=%ERRORLEVEL%
echo.
echo Exit code: %ERR%
pause
exit /b %ERR%
