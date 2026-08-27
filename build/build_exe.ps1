# Build portable Win11MagicUpgrade.exe — embeds pure Python engine (no .NET 4.x runtime needed on target)
# AV-hardened: NO UPX, UAC manifest, version resource (reduces Kaspersky Trojan.PDF heuristics)
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

$sep = ";"
$manifest = Join-Path $Root "build\app.manifest"
$version = Join-Path $Root "build\version_info.txt"

Write-Host "Building EXE (no UPX, UAC admin manifest, version info)..." -ForegroundColor Cyan
python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --uac-admin `
    --noupx `
    --version-file $version `
    --manifest $manifest `
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
    --hidden-import engine.enrich `
    --hidden-import engine.support `
    --hidden-import engine.preventive `
    --hidden-import engine.autonomy `
    --hidden-import engine.compat `
    --hidden-import engine.media_bypass `
    --hidden-import engine.errfix `
    --hidden-import engine.bootmgr `
    --hidden-import engine.hybrid_uefi `
    --hidden-import engine.sysreserved `
    --hidden-import engine.logutil `
    --hidden-import engine.progress `
    --hidden-import engine.av_trust `
    --hidden-import engine.av_cloud `
    --hidden-import engine.iso_inspect `
    --hidden-import engine.diskpart_safe `
    --hidden-import engine.sanitize `
    --hidden-import engine.gh_report `
    --hidden-import engine.errors `
    --hidden-import engine.uia_guard `
    --hidden-import engine.boot_safe `
    --hidden-import engine.boot_emergency `
    --hidden-import engine.boot_partition_backup `
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

# Authenticode sign as publisher "dlnraja"
# Prefers MAGIC_CODESIGN_PFX (+ MAGIC_CODESIGN_PASSWORD) for SmartScreen-ready CA certs.
$exe = Join-Path $dist "Win11MagicUpgrade.exe"
$prep = Join-Path $Root "build\ci_prepare_codesign.ps1"
if (Test-Path $prep) {
    powershell -NoProfile -ExecutionPolicy Bypass -File $prep | Out-Host
}
$signScript = Join-Path $Root "build\sign_exe.ps1"
if (Test-Path $signScript) {
    Write-Host "Signing EXE as dlnraja (PFX if MAGIC_CODESIGN_PFX set)..." -ForegroundColor Cyan
    powershell -NoProfile -ExecutionPolicy Bypass -File $signScript -ExePath $exe -Publisher "dlnraja"
    if ($LASTEXITCODE -ne 0) { throw "sign_exe.ps1 failed" }
    Copy-Item $exe (Join-Path $portable "Win11MagicUpgrade.exe") -Force
    foreach ($n in @("PUBLISHER.txt", "PUBLISHER.json")) {
        $p = Join-Path $dist $n
        if (Test-Path $p) { Copy-Item $p $portable -Force }
    }
}

# ZIP + SHA256 (Chrome prefers ZIP over naked EXE downloads)
$pkg = Join-Path $Root "build\package_release.ps1"
if (Test-Path $pkg) {
    Write-Host "Packaging ZIP + SHA256SUMS for release downloads..." -ForegroundColor Cyan
    & powershell -NoProfile -ExecutionPolicy Bypass -File $pkg -DistDir $dist
    if ($LASTEXITCODE -ne 0) { throw "package_release.ps1 failed with exit $LASTEXITCODE" }
    $zips = Get-ChildItem -LiteralPath $dist -Filter "*.zip" -ErrorAction SilentlyContinue
    if (-not $zips) { throw "No ZIP produced in dist - Chrome-friendly package missing" }
}

Write-Host ""
Write-Host "OK: $portable\Win11MagicUpgrade.exe" -ForegroundColor Green
Write-Host "Prefer downloading the Portable ZIP from GitHub Releases (Chrome-friendly)." -ForegroundColor Yellow
Write-Host "Publisher: dlnraja | AV notes: UPX off + UAC manifest + version resource + Authenticode + ZIP." -ForegroundColor DarkGray
Write-Host "Target PCs need NO .NET Framework 4.x and NO working PowerShell for the upgrade engine." -ForegroundColor DarkGray
