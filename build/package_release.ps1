# Package release artifacts that reduce Chrome Safe Browsing / SmartScreen friction.
# Prefer ZIP over naked EXE. ASCII-only for Windows PowerShell 5.1.
param(
    [string]$DistDir = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
if (-not $DistDir) { $DistDir = Join-Path $Root "dist" }

$exe = Join-Path $DistDir "Win11MagicUpgrade.exe"
$portable = Join-Path $DistDir "Win11MagicUpgrade-Portable"
if (-not (Test-Path -LiteralPath $exe)) { throw "Missing EXE - run build_exe.ps1 first" }
if (-not (Test-Path -LiteralPath $portable)) { throw "Missing portable folder" }

$ver = "0.0.0"
$vi = Join-Path $Root "build\version_info.txt"
if (Test-Path -LiteralPath $vi) {
    $m2 = Select-String -Path $vi -Pattern "ProductVersion',\s*'([0-9]+)\.([0-9]+)\.([0-9]+)" | Select-Object -First 1
    if ($m2) {
        $ver = "{0}.{1}.{2}" -f $m2.Matches[0].Groups[1].Value, $m2.Matches[0].Groups[2].Value, $m2.Matches[0].Groups[3].Value
    }
}

$zipName = "Win11MagicUpgrade-Portable-v$ver.zip"
$zipPath = Join-Path $DistDir $zipName

$helpLines = @(
    "Win11 Magic Upgrade - download / Chrome / SmartScreen help",
    "==========================================================",
    "Publisher: dlnraja",
    "Official releases: https://github.com/dlnraja/win11-magic-upgrade/releases",
    "This build is NOT malware. Open-source Windows upgrade helper (MIT).",
    "",
    "If Chrome says virus detected / blocks the download",
    "----------------------------------------------------",
    "1) Prefer this ZIP (not the naked .exe) from GitHub Releases.",
    "2) In Chrome: click Keep / Keep anyway (or open chrome://downloads).",
    "3) Or download with Edge / Firefox, or curl -L -o file.zip <url>",
    "4) After download, unblock (PowerShell):",
    "     Unblock-File .\Win11MagicUpgrade-Portable-v$ver.zip",
    "     Unblock-File .\Win11MagicUpgrade.exe",
    "5) Right-click EXE -> Properties -> Unblock -> OK, then Run as administrator.",
    "6) Verify SHA256 against SHA256SUMS.txt on the same Release page.",
    "7) Report false positive to Google:",
    "     https://safebrowsing.google.com/safebrowsing/report_error/",
    "",
    "SmartScreen: Windows protected your PC",
    "---------------------------------------",
    "Click More info -> Run anyway.",
    "A paid OV/EV code-signing certificate (CODESIGN_PFX_* secrets) removes most prompts.",
    "",
    "Kaspersky Internet Security (KIS) false positive",
    "------------------------------------------------",
    "1) Prefer this ZIP. Extract, then right-click Fix-KIS.cmd -> Run as administrator.",
    "2) If EXE was deleted: KIS Quarantine -> Restore -> Trusted application.",
    "3) Desktop guide: Win11MagicUpgrade-KIS-WHITELIST.txt",
    "4) Cloud FP (VirusTotal / OpenTIP) is submitted by GitHub Actions Release - not by One-Click.",
    "5) Submit portal: https://opentip.kaspersky.com/  or  newvirus@kaspersky.com",
    "",
    "Source: https://github.com/dlnraja/win11-magic-upgrade",
    "Security notes: SECURITY.md"
)

$helpPath = Join-Path $portable "DOWNLOAD-HELP.txt"
$helpLines | Set-Content -LiteralPath $helpPath -Encoding ASCII
$helpLines | Set-Content -LiteralPath (Join-Path $DistDir "DOWNLOAD.txt") -Encoding ASCII

if (Test-Path -LiteralPath $zipPath) { Remove-Item -LiteralPath $zipPath -Force }
Write-Host ("Creating {0} ..." -f $zipName) -ForegroundColor Cyan
Compress-Archive -Path (Join-Path $portable "*") -DestinationPath $zipPath -CompressionLevel Optimal
if (-not (Test-Path -LiteralPath $zipPath) -or ((Get-Item -LiteralPath $zipPath).Length -lt 1000000)) {
    throw ("ZIP creation failed or too small: {0}" -f $zipPath)
}

$sumPath = Join-Path $DistDir "SHA256SUMS.txt"
$lines = @()
foreach ($f in @($exe, $zipPath)) {
    $h = (Get-FileHash -LiteralPath $f -Algorithm SHA256).Hash.ToLowerInvariant()
    $lines += ("{0}  {1}" -f $h, (Split-Path $f -Leaf))
    Write-Host ("SHA256 {0} = {1}" -f (Split-Path $f -Leaf), $h) -ForegroundColor DarkGray
}
$pub = Join-Path $DistDir "PUBLISHER.txt"
if (Test-Path -LiteralPath $pub) {
    $h = (Get-FileHash -LiteralPath $pub -Algorithm SHA256).Hash.ToLowerInvariant()
    $lines += ("{0}  PUBLISHER.txt" -f $h)
}
($lines -join "`n") + "`n" | Set-Content -LiteralPath $sumPath -Encoding ASCII -NoNewline
Copy-Item -LiteralPath $sumPath -Destination (Join-Path $portable "SHA256SUMS.txt") -Force -ErrorAction SilentlyContinue

Write-Host ("OK: {0}" -f $zipPath) -ForegroundColor Green
Write-Host ("OK: {0}" -f $sumPath) -ForegroundColor Green

$meta = @{
    version = $ver
    zip = $zipName
    exe = "Win11MagicUpgrade.exe"
    sha256sums = "SHA256SUMS.txt"
    publisher = "dlnraja"
    prefer_download = $zipName
    chrome_note = "Prefer ZIP; naked EXE often triggers Chrome Safe Browsing on new builds"
}
($meta | ConvertTo-Json) | Set-Content -LiteralPath (Join-Path $DistDir "RELEASE-ASSETS.json") -Encoding ASCII
