@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "APPDIR=%~dp0"
set "EXE=%~dp0Win11MagicUpgrade.exe"

if exist "%EXE%" goto :exe
goto :python

:exe
rem The exe manifest asks for administrator, so UAC appears without PowerShell.
if "%~1"=="" (
  start "" "%EXE%" --auto oneclick
) else (
  start "" "%EXE%" %*
)
exit /b 0

:python
net session >nul 2>&1
if not errorlevel 1 goto :runpy
echo Auto-elevating to Administrator...
where powershell >nul 2>&1
if errorlevel 1 (
  echo PowerShell is unavailable. Right-click Win11MagicUpgrade.cmd and choose Run as administrator.
  exit /b 1
)
set "MAGIC_ELEVATE_TARGET=%~f0"
set "MAGIC_ELEVATE_DIR=%APPDIR%"
set "MAGIC_ELEVATE_ARGS=%*"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath $env:MAGIC_ELEVATE_TARGET -Verb RunAs -WorkingDirectory $env:MAGIC_ELEVATE_DIR -ArgumentList ($env:MAGIC_ELEVATE_ARGS -split ' ' | Where-Object { $_ })"
exit /b 0

:runpy
if "%~1"=="" goto :pynone
where py >nul 2>&1 && (
  start "" py -3 "%~dp0python\magic_upgrade.py" %*
  exit /b 0
)
where python >nul 2>&1 && (
  start "" python "%~dp0python\magic_upgrade.py" %*
  exit /b 0
)
goto :pynomatch

:pynone
where py >nul 2>&1 && (
  start "" py -3 "%~dp0python\magic_upgrade.py" --auto oneclick
  exit /b 0
)
where python >nul 2>&1 && (
  start "" python "%~dp0python\magic_upgrade.py" --auto oneclick
  exit /b 0
)

:pynomatch
echo ERROR: Win11MagicUpgrade.exe not found and Python is not installed.
exit /b 1
