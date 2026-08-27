@echo off
:: Win11 Magic Upgrade — fully autonomous One-Click (NO .NET 4.x / NO PowerShell)
setlocal EnableExtensions
cd /d "%~dp0"

net session >nul 2>&1
if %errorlevel% neq 0 (
  echo Auto-elevating to Administrator...
  mshta "javascript:var s=new ActiveXObject('Shell.Application');s.ShellExecute('%~f0','%*','','runas',1);close();"
  exit /b
)

title Win11 Magic Upgrade (autonomous)
echo.
echo   Win11 Magic Upgrade — autonomous One-Click
echo   Preventives + runtime + quiet Setup + RunOnce resume
echo   No .NET Framework 4.x  ^|  No PowerShell  ^|  No FlyOOBE
echo.

if exist "%~dp0Win11MagicUpgrade.exe" (
  "%~dp0Win11MagicUpgrade.exe" --cli --oneclick %*
  set ERR=%ERRORLEVEL%
  echo Exit code: %ERR%
  if %ERR%==0 exit /b 0
  if %ERR%==3010 exit /b 0
  echo.
  echo Non-zero exit — see Desktop MigrationReport.txt / SupportGuide.txt
  pause
  exit /b %ERR%
)

where py >nul 2>&1 && (
  py -3 "%~dp0python\magic_upgrade.py" --cli --oneclick %*
  set ERR=%ERRORLEVEL%
  echo Exit code: %ERR%
  if %ERR%==0 exit /b 0
  if %ERR%==3010 exit /b 0
  pause
  exit /b %ERR%
)

where python >nul 2>&1 && (
  python "%~dp0python\magic_upgrade.py" --cli --oneclick %*
  set ERR=%ERRORLEVEL%
  echo Exit code: %ERR%
  if %ERR%==0 exit /b 0
  if %ERR%==3010 exit /b 0
  pause
  exit /b %ERR%
)

echo ERROR: Win11MagicUpgrade.exe not found and Python is not installed.
echo Copy the Portable folder built on another PC (EXE embeds Python — no system .NET needed).
pause
exit /b 1
