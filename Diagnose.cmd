@echo off
cd /d "%~dp0"
if exist "%~dp0Win11MagicUpgrade.exe" (
  "%~dp0Win11MagicUpgrade.exe" --cli --diagnose
) else if exist "%~dp0python\magic_upgrade.py" (
  where py >nul 2>&1 && py -3 "%~dp0python\magic_upgrade.py" --cli --diagnose && goto :done
  python "%~dp0python\magic_upgrade.py" --cli --diagnose
) else (
  echo EXE / python engine missing.
)
:done
pause
