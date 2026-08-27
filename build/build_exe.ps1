# Build portable Win11MagicUpgrade.exe (PyInstaller onefile with embedded engine)
$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

Write-Host "Installing PyInstaller..." -ForegroundColor Cyan
python -m pip install --upgrade pip pyinstaller -q

$dist = Join-Path $Root "dist"
$work = Join-Path $Root "build\pyi"
New-Item -ItemType Directory -Path $dist -Force | Out-Null

# Stage payload for --add-data
$payload = Join-Path $work "payload"
if (Test-Path $payload) { Remove-Item $payload -Recurse -Force }
New-Item -ItemType Directory -Path $payload -Force | Out-Null
Copy-Item (Join-Path $Root "src") $payload -Recurse -Force
Copy-Item (Join-Path $Root "vendor") $payload -Recurse -Force
Copy-Item (Join-Path $Root "i18n") $payload -Recurse -Force

$sep = ";"  # Windows PyInstaller path separator for --add-data

Write-Host "Building EXE (engine embedded)..." -ForegroundColor Cyan
python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name "Win11MagicUpgrade" `
    --distpath $dist `
    --workpath $work `
    --specpath (Join-Path $Root "build") `
    --add-data "$payload\src${sep}src" `
    --add-data "$payload\vendor${sep}vendor" `
    --add-data "$payload\i18n${sep}i18n" `
    (Join-Path $Root "python\magic_upgrade.py")

# Portable folder: standalone EXE + optional loose files for PS-only use
$portable = Join-Path $dist "Win11MagicUpgrade-Portable"
if (Test-Path $portable) { Remove-Item $portable -Recurse -Force }
New-Item -ItemType Directory -Path $portable -Force | Out-Null
Copy-Item (Join-Path $dist "Win11MagicUpgrade.exe") $portable -Force
Copy-Item (Join-Path $Root "src") $portable -Recurse -Force
Copy-Item (Join-Path $Root "vendor") $portable -Recurse -Force
Copy-Item (Join-Path $Root "i18n") $portable -Recurse -Force
Copy-Item (Join-Path $Root "Win11MagicUpgrade.cmd") $portable -Force
Copy-Item (Join-Path $Root "Diagnose.cmd") $portable -Force
Copy-Item (Join-Path $Root "README.md") $portable -Force -ErrorAction SilentlyContinue
Copy-Item (Join-Path $Root "LICENSE") $portable -Force -ErrorAction SilentlyContinue
Copy-Item (Join-Path $Root "NOTICE") $portable -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "OK: $portable\Win11MagicUpgrade.exe" -ForegroundColor Green
Write-Host "The EXE embeds src+vendor; loose copies are also next to it for .cmd use." -ForegroundColor DarkGray
