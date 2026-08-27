@echo off
:: Win11 Magic Upgrade — ONE-CLICK full intelligent migration (GUI + progress)
setlocal EnableExtensions
cd /d "%~dp0"

net session >nul 2>&1
if %errorlevel% neq 0 (
  echo Auto-elevating to Administrator...
  :: Prefer PowerShell Start-Process -Verb RunAs (no mshta JS — AV-friendly)
  powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "Start-Process -LiteralPath '%~f0' -Verb RunAs -ArgumentList '%*'"
  exit /b
)

title Win11 Magic Upgrade — ONE-CLICK
echo.
echo   ONE-CLICK full migration (GUI with progress bars):
echo   AV trust - diag - preventives - bypass - patches - ISO - Setup - RunOnce
echo   No .NET Framework 4.x  ^|  No PowerShell engine
echo.

if exist "%~dp0Win11MagicUpgrade.exe" (
  :: Launch elevated GUI with --auto oneclick (visible progress, not invisible --cli)
  start "" "%~dp0Win11MagicUpgrade.exe" --auto oneclick %*
  exit /b 0
)

where py >nul 2>&1 && (
  start "" py -3 "%~dp0python\magic_upgrade.py" --auto oneclick %*
  exit /b 0
)

where python >nul 2>&1 && (
  start "" python "%~dp0python\magic_upgrade.py" --auto oneclick %*
  exit /b 0
)

echo ERROR: Win11MagicUpgrade.exe not found and Python is not installed.
pause
exit /b 1
