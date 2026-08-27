# Win11 Magic Upgrade — Kaspersky Internet Security (KIS) false-positive helper
# Does NOT require the EXE to already be trusted (runs as plain PowerShell).
# Prefer: right-click -> Run with PowerShell, or Fix-KIS.cmd as Administrator.

$ErrorActionPreference = "Continue"
$Root = $PSScriptRoot
$Exe = Join-Path $Root "Win11MagicUpgrade.exe"
$State = Join-Path $env:LOCALAPPDATA "Win11MagicUpgrade"
$Desktop = [Environment]::GetFolderPath("Desktop")

Write-Host "Win11 Magic Upgrade — KIS / antivirus false-positive helper" -ForegroundColor Cyan
Write-Host "Publisher: dlnraja | Official: https://github.com/dlnraja/win11-magic-upgrade/releases" -ForegroundColor DarkGray
Write-Host ""

# 1) Unblock MOTW on ZIP / EXE / this folder
$targets = @(
    $Root,
    $Exe,
    (Join-Path $Root "*.zip"),
    (Join-Path $Root "Win11MagicUpgrade*.exe")
)
foreach ($t in $targets) {
    Get-Item -LiteralPath $t -ErrorAction SilentlyContinue | ForEach-Object {
        try {
            Unblock-File -LiteralPath $_.FullName -ErrorAction Stop
            Write-Host "Unblocked: $($_.FullName)" -ForegroundColor Green
        } catch {
            Get-ChildItem -LiteralPath $_.FullName -ErrorAction SilentlyContinue | ForEach-Object {
                try { Unblock-File -LiteralPath $_.FullName -ErrorAction SilentlyContinue } catch {}
            }
        }
    }
}
Get-ChildItem -LiteralPath $Root -Filter "*.exe" -ErrorAction SilentlyContinue | ForEach-Object {
    try {
        Unblock-File -LiteralPath $_.FullName -ErrorAction Stop
        Write-Host "Unblocked: $($_.FullName)" -ForegroundColor Green
    } catch {}
}

# 2) Defender path exclusions (best-effort)
$excl = @($Root, $State)
if (Test-Path -LiteralPath $Exe) { $excl += $Exe }
foreach ($p in $excl) {
    try {
        Add-MpPreference -ExclusionPath $p -ErrorAction Stop
        Write-Host "Defender exclusion: $p" -ForegroundColor Green
    } catch {
        Write-Host "Defender exclusion note: $($_.Exception.Message)" -ForegroundColor Yellow
    }
}

# 3) Kaspersky avp.com ADD / SET (best-effort)
$avp = Get-ChildItem -Path @(
    "${env:ProgramFiles}\Kaspersky Lab",
    "${env:ProgramFiles(x86)}\Kaspersky Lab"
) -Filter "avp.com" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1

if ($avp) {
    Write-Host "Kaspersky CLI: $($avp.FullName)" -ForegroundColor Cyan
    $paths = @($Root)
    if (Test-Path -LiteralPath $Exe) { $paths += $Exe }
    foreach ($p in $paths) {
        & $avp.FullName ADD $p 2>&1 | Out-Null
        & $avp.FullName SET "TrustedZone.TrustedApplications.Path=$p" 2>&1 | Out-Null
        Write-Host "Kaspersky trust attempt: $p" -ForegroundColor Green
    }
    & $avp.FullName RESTORE /REPLACE "Win11MagicUpgrade.exe" 2>&1 | Out-Null
    & $avp.FullName RESTORE "Win11MagicUpgrade.exe" 2>&1 | Out-Null
} else {
    Write-Host "Kaspersky avp.com not found — use KIS GUI Trusted applications." -ForegroundColor Yellow
}

# 4) Write Desktop guide
$guide = @"
Win11 Magic Upgrade — Kaspersky Internet Security (KIS) false positive
======================================================================

This build is NOT malware (open source MIT, publisher dlnraja).
False positives are common on new PyInstaller EXEs (Trojan.PDF / HEUR).

If KIS deleted or blocked Win11MagicUpgrade.exe:
1. Open Kaspersky -> More tools -> Quarantine -> Restore
2. Settings -> Additional -> Threats and Exclusions
3. Manage exclusions -> Add -> Trusted application
4. Select Win11MagicUpgrade.exe and its folder:
   $Root
5. Re-run Fix-KIS.cmd as Administrator, then the EXE as Administrator
6. Or: Win11MagicUpgrade.exe --cli --declare-av

Submit FP: https://opentip.kaspersky.com/
Email: newvirus@kaspersky.com
Official ZIP: https://github.com/dlnraja/win11-magic-upgrade/releases/latest
"@
New-Item -ItemType Directory -Path $State -Force | Out-Null
$guide | Set-Content -LiteralPath (Join-Path $State "KIS-WHITELIST.txt") -Encoding UTF8
$desk = Join-Path $Desktop "Win11MagicUpgrade-KIS-WHITELIST.txt"
$guide | Set-Content -LiteralPath $desk -Encoding UTF8
Write-Host "Wrote guide: $desk" -ForegroundColor Green

# 5) Launch declare-av if EXE present
if (Test-Path -LiteralPath $Exe) {
    Write-Host ""
    Write-Host "Launching EXE --cli --declare-av (accept UAC if prompted)..." -ForegroundColor Cyan
    Start-Process -FilePath $Exe -ArgumentList "--cli","--declare-av" -Verb RunAs -Wait -ErrorAction SilentlyContinue
} else {
    Write-Host "EXE not in this folder yet — restore from Quarantine, then re-run Fix-KIS." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Done. If still blocked: restore from KIS Quarantine + Trusted application (see Desktop guide)." -ForegroundColor Cyan
exit 0
