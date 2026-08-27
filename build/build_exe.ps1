# Build portable Win11MagicUpgrade.exe — embeds pure Python engine (no .NET 4.x runtime needed on target)
$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

Write-Host "Installing PyInstaller..." -ForegroundColor Cyan
python -m pip install --upgrade pip pyinstaller -q

$dist = Join-Path $Root "dist"
$work = Join-Path $Root "build\pyi"
New-Item -ItemType Directory -Path $dist -Force | Out-Null

$payload = Join-Path $work "payload"
if (Test-Path $payload) { Remove-Item $payload -Recurse -Force }
New-Item -ItemType Directory -Path $payload -Force | Out-Null
Copy-Item (Join-Path $Root "python\engine") (Join-Path $payload "engine") -Recurse -Force
Copy-Item (Join-Path $Root "i18n") $payload -Recurse -Force
# Optional legacy PS kept out of runtime path; still ship docs only

$sep = ";"

Write-Host "Building EXE (pure Python engine, no PowerShell at runtime)..." -ForegroundColor Cyan
python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name "Win11MagicUpgrade" `
    --distpath $dist `
    --workpath $work `
    --specpath (Join-Path $Root "build") `
    --paths (Join-Path $Root "python") `
    --hidden-import engine `
    --hidden-import engine.pipeline `
    --hidden-import engine.detect `
    --hidden-import engine.bypass `
    --hidden-import engine.iso `
    --hidden-import engine.virtdisk `
    --hidden-import engine.mbrgpt `
    --hidden-import engine.patches `
    --hidden-import engine.chain `
    --add-data "$payload\engine${sep}engine" `
    --add-data "$payload\i18n${sep}i18n" `
    (Join-Path $Root "python\magic_upgrade.py")

$portable = Join-Path $dist "Win11MagicUpgrade-Portable"
if (Test-Path $portable) { Remove-Item $portable -Recurse -Force }
New-Item -ItemType Directory -Path $portable -Force | Out-Null
Copy-Item (Join-Path $dist "Win11MagicUpgrade.exe") $portable -Force
Copy-Item (Join-Path $Root "python") $portable -Recurse -Force
Copy-Item (Join-Path $Root "i18n") $portable -Recurse -Force
Copy-Item (Join-Path $Root "Win11MagicUpgrade.cmd") $portable -Force
Copy-Item (Join-Path $Root "Diagnose.cmd") $portable -Force
Copy-Item (Join-Path $Root "README.md") $portable -Force -ErrorAction SilentlyContinue
Copy-Item (Join-Path $Root "LICENSE") $portable -Force -ErrorAction SilentlyContinue
Copy-Item (Join-Path $Root "NOTICE") $portable -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "OK: $portable\Win11MagicUpgrade.exe" -ForegroundColor Green
Write-Host "Target PCs need NO .NET Framework 4.x and NO working PowerShell for the upgrade engine." -ForegroundColor DarkGray
