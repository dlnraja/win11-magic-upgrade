<#
.SYNOPSIS
  Win11 Magic Upgrade - one-click portable upgrader (Flyby11/FlyOOBE inspired)
.DESCRIPTION
  Upgrades Windows 10 (including obsolete builds like 1511) and older Windows 11
  to the latest Windows 11 without wiping apps/files.

  - No FlyOOBE/.NET 4.8 GUI dependency (works when FlyOOBE fails on old .NET)
  - Official ISO download via Fido (Microsoft CDN)
  - Hardware bypass: /product server + LabConfig + MoSetup + 24H2 HwReqChk
  - Auto MBR->GPT via mbr2gpt (no data loss) when possible
  - Intermediate Win10 22H2 path for obsolete builds before Win11

.PARAMETER OneClick
  Run full automatic pipeline (default when launched from .cmd/.exe)
.PARAMETER Resume
  Continue after reboot (RunOnce)
.PARAMETER DiagnoseOnly
  Print system report and exit
.PARAMETER SkipMbrConvert
.PARAMETER SkipIntermediate
.PARAMETER Win10Iso / Win11Iso
  Use local ISO paths instead of downloading
.PARAMETER Quiet
  Pass quiet flags to setup.exe
.PARAMETER WhatIf
#>
[CmdletBinding()]
param(
    [switch]$OneClick,
    [switch]$Resume,
    [switch]$DiagnoseOnly,
    [switch]$SkipMbrConvert,
    [switch]$SkipIntermediate,
    [string]$Win10Iso,
    [string]$Win11Iso,
    [switch]$Quiet,
    [switch]$WhatIf,
    [switch]$ApplyBypassOnly,
    [switch]$ConvertMbrOnly
)

$ErrorActionPreference = "Stop"
$script:WmuMainScript = $MyInvocation.MyCommand.Path
$script:WmuRoot = Split-Path $PSScriptRoot -Parent
if (-not (Test-Path (Join-Path $script:WmuRoot "vendor"))) {
    $script:WmuRoot = $PSScriptRoot
}

$moduleDir = Join-Path $PSScriptRoot "modules"
foreach ($m in @(
    "Logging.ps1",
    "SystemDetect.ps1",
    "BypassChecks.ps1",
    "MbrToGpt.ps1",
    "CommonFixes.ps1",
    "MigrationPatches.ps1",
    "IsoDownload.ps1",
    "UpgradeEngine.ps1"
)) {
    $path = Join-Path $moduleDir $m
    if (-not (Test-Path $path)) { throw "Missing module: $path" }
    . $path
}

Initialize-WmuLogging

Write-Host ""
Write-Host "  Win11 Magic Upgrade  |  Flyby11/FlyOOBE-class one-click  |  portable" -ForegroundColor Magenta
Write-Host "  Keeps files & apps  |  TPM/SecureBoot bypass  |  MBR->GPT  |  Fido ISO" -ForegroundColor DarkGray
Write-Host ""

try {
    if ($DiagnoseOnly) {
        $r = Get-WmuFullReport
        Write-WmuReport -Report $r
        $r | ConvertTo-Json -Depth 6 | Set-Content (Join-Path $script:StateDir "last-diagnose.json") -Encoding UTF8
        Write-WmuLog "Diagnosis written to $script:StateDir\last-diagnose.json" "OK"
        exit 0
    }

    if (-not (Test-WmuAdmin)) {
        Write-WmuLog "Not elevated - relaunching as Administrator..." "WARN"
        $arg = "-NoProfile -ExecutionPolicy Bypass -File `"$($MyInvocation.MyCommand.Path)`""
        foreach ($a in $PSBoundParameters.GetEnumerator()) {
            if ($a.Value -is [switch]) {
                if ($a.Value) { $arg += " -$($a.Key)" }
            } elseif ($a.Value) {
                $arg += " -$($a.Key) `"$($a.Value)`""
            }
        }
        if (-not $PSBoundParameters.ContainsKey("OneClick") -and -not $ApplyBypassOnly -and -not $ConvertMbrOnly) {
            $arg += " -OneClick"
        }
        Start-Process -FilePath "powershell.exe" -ArgumentList $arg -Verb RunAs
        exit 0
    }

    if ($ApplyBypassOnly) {
        Invoke-WmuHardwareBypass | Out-Null
        exit 0
    }

    if ($ConvertMbrOnly) {
        $r = Get-WmuFullReport
        if ($r.Disk.PartitionStyle -ne "MBR") {
            Write-WmuLog "Disk is already $($r.Disk.PartitionStyle)." "OK"
            exit 0
        }
        $res = Invoke-WmuMbrToGpt -DiskNumber $r.Disk.Number -ForcePrepare
        if (-not $res.Success) { exit 1 }
        exit 0
    }

    if ($Resume) {
        Write-WmuLog "Resuming after reboot..." "STEP"
        Remove-WmuRunOnceContinuation
        $OneClick = $true
        # After intermediate upgrade, skip intermediate again
        $st = Get-WmuState
        if ($st -and $st.AfterReboot -eq "ContinueToWin11") {
            $SkipIntermediate = $true
            Update-WmuState @{ AfterReboot = $null }
        }
    }

    # Default action = one-click full pipeline
    if ($OneClick -or $Resume -or (-not $DiagnoseOnly)) {
        if (-not $OneClick -and -not $Resume -and $MyInvocation.Line -match 'Win11MagicUpgrade') {
            # Interactive confirm when double-clicked without flags
            Write-Host "This will prepare and upgrade this PC to Windows 11 latest." -ForegroundColor Yellow
            Write-Host "Files and apps are kept (inplace upgrade). Unsupported hardware checks are bypassed." -ForegroundColor Yellow
            Write-Host ""
            $ans = Read-Host "Continue? (O/N)"
            if ($ans -notmatch '^(O|o|Y|y|Oui|oui|Yes|yes)$') {
                Write-WmuLog "Canceled by user." "WARN"
                exit 2
            }
            $OneClick = $true
        }

        Invoke-WmuFullPipeline `
            -SkipMbrConvert:$SkipMbrConvert `
            -SkipIntermediate:$SkipIntermediate `
            -Win10Iso $Win10Iso `
            -Win11Iso $Win11Iso `
            -Quiet:$Quiet `
            -WhatIf:$WhatIf
    }
}
catch {
    Write-WmuLog "FATAL: $($_.Exception.Message)" "ERROR"
    Write-WmuLog $_.ScriptStackTrace "ERROR"
    Update-WmuState @{ Phase = "Failed"; Error = $_.Exception.Message }
    if (-not $env:WMU_NO_PAUSE) {
        Write-Host ""
        Write-Host "Press Enter to close..." -ForegroundColor DarkGray
        [void][Console]::ReadLine()
    }
    exit 1
}

Write-WmuLog "Log file: $($script:LogFile)" "INFO"
if (-not $env:WMU_NO_PAUSE -and -not $Quiet) {
    Write-Host ""
    Write-Host "Press Enter to close..." -ForegroundColor DarkGray
    [void][Console]::ReadLine()
}
