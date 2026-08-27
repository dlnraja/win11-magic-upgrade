@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0src\Win11MagicUpgrade.ps1" -DiagnoseOnly
pause
