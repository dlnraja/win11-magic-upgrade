@echo off
REM Win11 Magic Upgrade — Kaspersky KIS false-positive helper
REM Run as Administrator if possible. Safe if Kaspersky is not installed.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Fix-KIS.ps1" %*
exit /b %ERRORLEVEL%
