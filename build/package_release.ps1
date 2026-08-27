# Package release artifacts that reduce Chrome Safe Browsing / SmartScreen friction.
# - Prefer ZIP over naked EXE for downloads (Chrome is much less aggressive on GitHub ZIPs)
# - SHA256SUMS for integrity
# - DOWNLOAD.txt with unblock / reputation guidance
param(
    [string]$DistDir = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
if (-not $DistDir) { $DistDir = Join-Path $Root "dist" }

$exe = Join-Path $DistDir "Win11MagicUpgrade.exe"
$portable = Join-Path $DistDir "Win11MagicUpgrade-Portable"
if (-not (Test-Path -LiteralPath $exe)) { throw "Missing $exe — run build_exe.ps1 first" }
if (-not (Test-Path -LiteralPath $portable)) { throw "Missing $portable" }

# Read version from version_info for zip name
$ver = "0.0.0"
$vi = Join-Path $Root "build\version_info.txt"
if (Test-Path $vi) {
    $m = Select-String -Path $vi -Pattern "FileVersion',\s*'([0-9.]+)'" | Select-Object -First 1
    if ($m) { $ver = ($m.Matches[0].Groups[1].Value -replace '\.0$', '') }
    # Prefer ProductVersion 1.29.0.0 -> 1.29.0
    $m2 = Select-String -Path $vi -Pattern "ProductVersion',\s*'([0-9]+)\.([0-9]+)\.([0-9]+)" | Select-Object -First 1
    if ($m2) {
        $ver = "{0}.{1}.{2}" -f $m2.Matches[0].Groups[1].Value, $m2.Matches[0].Groups[2].Value, $m2.Matches[0].Groups[3].Value
    }
}

$zipName = "Win11MagicUpgrade-Portable-v$ver.zip"
$zipPath = Join-Path $DistDir $zipName

# Help file inside portable before zip
$help = @"
Win11 Magic Upgrade — download / Chrome / SmartScreen help
==========================================================
Publisher: dlnraja
Official releases: https://github.com/dlnraja/win11-magic-upgrade/releases
This build is NOT malware. It is an open-source Windows upgrade helper (MIT).

If Chrome says "virus detected" / blocks the download
----------------------------------------------------
1) Prefer this ZIP (not the naked .exe) from GitHub Releases.
2) In Chrome: click Keep / Keep anyway (or open chrome://downloads).
3) Or download with Edge / Firefox, or: winget is N/A — use curl:
     curl -L -o Win11MagicUpgrade-Portable.zip "<release-zip-url>"
4) After download, unblock the zip/exe (PowerShell as user):
     Unblock-File .\Win11MagicUpgrade-Portable-v$ver.zip
     Unblock-File .\Win11MagicUpgrade.exe
5) Right-click EXE → Properties → Unblock → OK, then Run as administrator.
6) Verify SHA256 against SHA256SUMS.txt on the same Release page.
7) Report false positive to Google (helps everyone):
     https://safebrowsing.google.com/safebrowsing/report_error/
8) Optional: upload the EXE to VirusTotal and compare community comments.

SmartScreen "Windows protected your PC"
---------------------------------------
Click More info → Run anyway. A paid OV/EV code-signing certificate
(repo secrets CODESIGN_PFX_*) removes most of these prompts over time.

Source code: https://github.com/dlnraja/win11-magic-upgrade
Security notes: SECURITY.md in the repo
"@
$helpPath = Join-Path $portable "DOWNLOAD-HELP.txt"
Set-Content -LiteralPath $helpPath -Value $help -Encoding UTF8

# Also top-level DOWNLOAD.txt in dist
Set-Content -LiteralPath (Join-Path $DistDir "DOWNLOAD.txt") -Value $help -Encoding UTF8

# Create zip (Compress-Archive needs folder contents)
if (Test-Path -LiteralPath $zipPath) { Remove-Item -LiteralPath $zipPath -Force }
Write-Host "Creating $zipName ..." -ForegroundColor Cyan
Compress-Archive -Path (Join-Path $portable "*") -DestinationPath $zipPath -CompressionLevel Optimal
if (-not (Test-Path -LiteralPath $zipPath) -or ((Get-Item $zipPath).Length -lt 1000000)) {
    throw "ZIP creation failed or too small: $zipPath"
}

# SHA256 sums
$sumPath = Join-Path $DistDir "SHA256SUMS.txt"
$lines = @()
foreach ($f in @($exe, $zipPath)) {
    $h = (Get-FileHash -LiteralPath $f -Algorithm SHA256).Hash.ToLowerInvariant()
    $lines += ("{0}  {1}" -f $h, (Split-Path $f -Leaf))
    Write-Host ("SHA256 {0} = {1}" -f (Split-Path $f -Leaf), $h) -ForegroundColor DarkGray
}
$pub = Join-Path $DistDir "PUBLISHER.txt"
if (Test-Path $pub) {
    $h = (Get-FileHash -LiteralPath $pub -Algorithm SHA256).Hash.ToLowerInvariant()
    $lines += ("{0}  {1}" -f $h, "PUBLISHER.txt")
}
Set-Content -LiteralPath $sumPath -Value ($lines -join "`n") -Encoding ASCII
Copy-Item $sumPath (Join-Path $portable "SHA256SUMS.txt") -Force -ErrorAction SilentlyContinue

Write-Host "OK: $zipPath" -ForegroundColor Green
Write-Host "OK: $sumPath" -ForegroundColor Green

# Sidecar JSON for CI / VT
$meta = @{
    version = $ver
    zip = $zipName
    exe = "Win11MagicUpgrade.exe"
    sha256sums = "SHA256SUMS.txt"
    publisher = "dlnraja"
    prefer_download = $zipName
    chrome_note = "Prefer ZIP; naked EXE often triggers Chrome Safe Browsing on new builds"
}
($meta | ConvertTo-Json) | Set-Content -LiteralPath (Join-Path $DistDir "RELEASE-ASSETS.json") -Encoding UTF8
