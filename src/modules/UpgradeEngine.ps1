# Upgrade engine: intermediate Win10 + final Win11 inplace setup

function Start-WmuSetupFromRoot {
    param(
        [Parameter(Mandatory)][string]$SetupRoot,
        [switch]$UseServerProduct,
        [switch]$Quiet
    )
    $setup = Join-Path $SetupRoot "setup.exe"
    if (-not (Test-Path $setup)) {
        throw "setup.exe not found under $SetupRoot"
    }

    # Prefer sources\setupprep.exe /product server on some builds (community tip)
    $setupPrep = Join-Path $SetupRoot "sources\setupprep.exe"
    $exe = $setup
    $argList = @()

    if ($UseServerProduct) {
        Invoke-WmuHardwareBypass | Out-Null
        if (Test-Path $setupPrep) {
            $exe = $setupPrep
            Write-WmuLog "Using setupprep.exe /product server (Flyby11 method)" "STEP"
        } else {
            Write-WmuLog "Using setup.exe /product server (Flyby11 method)" "STEP"
        }
        $argList = Get-WmuSetupBypassArgs -Quiet:$Quiet
    } else {
        Write-WmuLog "Starting standard inplace upgrade (keep apps/files)..." "STEP"
        $argList = @(
            "/auto", "upgrade",
            "/compat", "IgnoreWarning",
            "/dynamicupdate", "disable",
            "/eula", "accept"
        )
        if ($Quiet) { $argList += @("/quiet", "/showoobe", "none") }
    }

    Write-WmuLog "Launch: `"$exe`" $($argList -join ' ')" "INFO"
    Update-WmuState @{
        Phase       = "SetupRunning"
        SetupExe    = $exe
        SetupArgs   = ($argList -join ' ')
        StartedAt   = (Get-Date).ToString("o")
    }

    $p = Start-Process -FilePath $exe -ArgumentList $argList -PassThru -Wait
    Write-WmuLog "Setup process exited with code $($p.ExitCode)" $(if ($p.ExitCode -eq 0) { "OK" } else { "WARN" })
    return $p.ExitCode
}

function Invoke-WmuIntermediateWin10Upgrade {
    param(
        [Parameter(Mandatory)]$Report,
        [string]$LocalIso,
        [switch]$Quiet
    )
    Write-WmuLog "=== Phase: Intermediate Windows 10 22H2 upgrade (obsolete build $($Report.OS.Build)) ===" "STEP"

    $edition = Resolve-WmuFidoEdition $Report.OS.EditionId
    $lang = Resolve-WmuFidoLang $Report.OS.SystemLocale
    $arch = if ($Report.OS.Architecture -eq "x64") { "x64" } else { "x86" }

    $iso = $LocalIso
    if (-not $iso) {
        $iso = Invoke-WmuIsoDownload -Win "10" -Release "22H2" -Edition $edition -Lang $lang -Arch $arch
    }
    Update-WmuState @{
        Phase = "IntermediateIsoReady"
        IntermediateIso = $iso
    }

    $root = Mount-WmuIso -IsoPath $iso
    Install-WmuRunOnceContinuation -ScriptPath $script:WmuMainScript -Args "-Resume"
    Update-WmuState @{ Phase = "IntermediateSetup"; AfterReboot = "ContinueToWin11" }

    $code = Start-WmuSetupFromRoot -SetupRoot $root -UseServerProduct:$false -Quiet:$Quiet
    return [pscustomobject]@{ ExitCode = $code; Iso = $iso; MountRoot = $root }
}

function Invoke-WmuWindows11Upgrade {
    param(
        [Parameter(Mandatory)]$Report,
        [string]$LocalIso,
        [switch]$Quiet
    )
    Write-WmuLog "=== Phase: Windows 11 latest inplace upgrade (Flyby11 /product server) ===" "STEP"

    if ($Report.Cpu.Sse42Likely -eq $false) {
        throw "CPU lacks SSE4.2/POPCNT - Windows 11 24H2+ cannot boot on this processor. Stopped."
    }

    $edition = Resolve-WmuFidoEdition $Report.OS.EditionId
    $lang = Resolve-WmuFidoLang $Report.OS.SystemLocale
    $arch = "x64"
    if ($Report.OS.Architecture -ne "x64") {
        throw "Windows 11 requires 64-bit Windows. This OS is $($Report.OS.Architecture)."
    }

    $iso = $LocalIso
    if (-not $iso) {
        $iso = Invoke-WmuIsoDownload -Win "11" -Release "Latest" -Edition $edition -Lang $lang -Arch $arch
    }
    Update-WmuState @{ Phase = "Win11IsoReady"; Win11Iso = $iso }

    $root = Mount-WmuIso -IsoPath $iso
    $code = Start-WmuSetupFromRoot -SetupRoot $root -UseServerProduct -Quiet:$Quiet
    return [pscustomobject]@{ ExitCode = $code; Iso = $iso; MountRoot = $root }
}

function Invoke-WmuFullPipeline {
    param(
        [switch]$SkipMbrConvert,
        [switch]$SkipIntermediate,
        [string]$Win10Iso,
        [string]$Win11Iso,
        [switch]$Quiet,
        [switch]$WhatIf
    )

    $report = Get-WmuFullReport
    Write-WmuReport -Report $report
    Update-WmuState @{
        Phase = "Detected"
        Report = $report
    }

    if ($report.OS.IsWindows11 -and $report.OS.Build -ge 26100) {
        Write-WmuLog "Already on Windows 11 24H2+. Nothing mandatory to do." "OK"
        Update-WmuState @{ Phase = "Done"; Message = "Already latest-class Win11" }
        return
    }

    if ($report.Cpu.Sse42Likely -eq $false) {
        Write-WmuLog "Hard stop: CPU incompatible with Win11 24H2+ (no SSE4.2/POPCNT)." "ERROR"
        Update-WmuState @{ Phase = "Blocked"; Reason = "NoSSE42" }
        throw "CPU incompatible"
    }

    if ($report.OS.Architecture -ne "x64") {
        throw "32-bit Windows cannot upgrade to Windows 11."
    }

    if ($WhatIf) {
        Write-WmuLog "WhatIf: pipeline would continue with fixes -> MBR->GPT -> intermediate? -> Win11." "WARN"
        return
    }

    Invoke-WmuCommonFixes
    Invoke-WmuMigrationPatches
    Invoke-WmuHardwareBypass | Out-Null

    # MBR -> GPT (only if mbr2gpt exists; else after intermediate)
    if (-not $SkipMbrConvert -and $report.Disk.PartitionStyle -eq "MBR") {
        if ($report.OS.Mbr2gptAvailable) {
            $r = Invoke-WmuMbrToGpt -DiskNumber $report.Disk.Number -ForcePrepare
            if (-not $r.Success) {
                Write-WmuLog "MBR conversion failed. Will still try upgrade; Win11 may require UEFI/GPT." "WARN"
            } elseif ($r.NeedsUefi) {
                Write-WmuLog ">>> ACTION REQUIRED AFTER REBOOT: Enter firmware settings, disable CSM/Legacy, enable UEFI boot. <<<" "WARN"
            }
        } else {
            Write-WmuLog "mbr2gpt not available on this build - will convert after intermediate Win10 upgrade." "WARN"
            Update-WmuState @{ PendingMbrConvert = $true }
        }
    }

    # Intermediate path for obsolete Win10 (1511, 1607, 1703, 1709, ...)
    if (-not $SkipIntermediate -and $report.OS.NeedsIntermediate) {
        $null = Invoke-WmuIntermediateWin10Upgrade -Report $report -LocalIso $Win10Iso -Quiet:$Quiet
        Write-WmuLog "Intermediate upgrade launched. After reboot the tool will continue to Windows 11." "OK"
        return
    }

    # If we just came back from intermediate and still MBR
    $state = Get-WmuState
    if ($state -and $state.PendingMbrConvert -eq $true -and $report.Disk.PartitionStyle -eq "MBR") {
        if ($report.OS.Mbr2gptAvailable) {
            $null = Invoke-WmuMbrToGpt -DiskNumber $report.Disk.Number -ForcePrepare
            Update-WmuState @{ PendingMbrConvert = $false }
        }
    }

    # Already Win11 but older feature update -> still use Win11 latest ISO
    $null = Invoke-WmuWindows11Upgrade -Report $report -LocalIso $Win11Iso -Quiet:$Quiet
    Update-WmuState @{ Phase = "Win11SetupLaunched" }
    Write-WmuLog "Windows 11 setup launched. Follow on-screen steps - keep files and apps." "OK"
}
