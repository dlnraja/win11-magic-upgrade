# MBR -> GPT conversion without data loss (mbr2gpt) + common layout fixes

function Test-WmuBitLockerOnSystem {
    try {
        if (-not (Get-Command Get-BitLockerVolume -ErrorAction SilentlyContinue)) { return $false }
        $v = Get-BitLockerVolume -MountPoint $env:SystemDrive -ErrorAction Stop
        return ($v.ProtectionStatus -eq "On" -or $v.VolumeStatus -ne "FullyDecrypted")
    } catch { return $false }
}

function Suspend-WmuBitLockerIfNeeded {
    if (Test-WmuBitLockerOnSystem) {
        Write-WmuLog "Suspending BitLocker on system drive for conversion/upgrade..." "STEP"
        try {
            Suspend-BitLocker -MountPoint $env:SystemDrive -RebootCount 2 -ErrorAction Stop
            Write-WmuLog "BitLocker suspended." "OK"
            return $true
        } catch {
            Write-WmuLog "BitLocker suspend failed: $($_.Exception.Message)" "ERROR"
            throw
        }
    }
    return $false
}

function Repair-WmuPartitionLayoutForMbr2Gpt {
    <#
    .SYNOPSIS
      Attempts common fixes when mbr2gpt validation fails:
      - Shrink C: to free ~300MB for EFI if needed
      - Disable/relocate WinRE if recovery partition blocks layout
      - Warn if >3 primary partitions
    #>
    param([int]$DiskNumber)

    Write-WmuLog "Preparing disk layout for MBR2GPT (disk $DiskNumber)..." "STEP"

    # Ensure WinRE is known (reduces "Cannot find OS partition" / recovery GUID issues)
    try {
        $reagent = Join-Path $env:SystemRoot "System32\reagentc.exe"
        if (Test-Path $reagent) {
            & $reagent /info 2>&1 | ForEach-Object { Write-WmuLog "reagentc: $_" }
        }
    } catch { }

    if (Get-Command Get-Partition -ErrorAction SilentlyContinue) {
        $parts = @(Get-Partition -DiskNumber $DiskNumber -ErrorAction SilentlyContinue)
        $primaryish = @($parts | Where-Object {
            $_.Type -notin @('Unknown') -and -not $_.IsHidden -or $_.DriveLetter -or $_.IsSystem -or $_.IsBoot -or $_.IsActive
        })
        # MBR max 4 primary; mbr2gpt needs at most 3 so it can create EFI
        $mbrPrimaries = @($parts | Where-Object { $_.DriveLetter -or $_.IsActive -or $_.IsSystem -or $_.IsBoot -or ($_.Size -gt 100MB) })
        if ($mbrPrimaries.Count -gt 3) {
            Write-WmuLog "Disk has $($mbrPrimaries.Count) sizable partitions. MBR2GPT needs <=3. Attempting WinRE disable to free a slot..." "WARN"
            try {
                & "$env:SystemRoot\System32\reagentc.exe" /disable 2>&1 | ForEach-Object { Write-WmuLog "reagentc disable: $_" }
            } catch {
                Write-WmuLog "Could not disable WinRE automatically. You may need to delete/merge an OEM/recovery partition." "WARN"
            }
        }

        # Shrink OS volume a bit if no free space for EFI (~260-300 MB)
        $sysLetter = $env:SystemDrive.Substring(0, 1)
        try {
            $osPart = Get-Partition -DriveLetter $sysLetter -ErrorAction Stop
            $supported = Get-PartitionSupportedSize -DriveLetter $sysLetter -ErrorAction SilentlyContinue
            if ($supported -and ($osPart.Size - $supported.SizeMin) -gt 400MB) {
                $target = $osPart.Size - 350MB
                if ($target -ge $supported.SizeMin -and $target -lt $osPart.Size) {
                    Write-WmuLog "Shrinking ${sysLetter}: by ~350MB to make room for EFI system partition..." "STEP"
                    Resize-Partition -DriveLetter $sysLetter -Size $target -ErrorAction Stop
                    Write-WmuLog "Shrink OK." "OK"
                }
            }
        } catch {
            Write-WmuLog "Optional shrink skipped/failed: $($_.Exception.Message)" "WARN"
        }
    }

    # chkdsk soft schedule not forced (too slow); run quick dirty check
    try {
        Write-WmuLog "Running quick volume health check..." "STEP"
        $vol = Get-Volume -DriveLetter $env:SystemDrive.TrimEnd(':').TrimEnd('\') -ErrorAction SilentlyContinue
        if ($vol -and $vol.HealthStatus -ne "Healthy") {
            Write-WmuLog "Volume health is $($vol.HealthStatus) - consider chkdsk before upgrade." "WARN"
        }
    } catch { }
}

function Invoke-WmuMbrToGpt {
    param(
        [Parameter(Mandatory)][int]$DiskNumber,
        [switch]$ForcePrepare
    )

    $mbr2gpt = Join-Path $env:SystemRoot "System32\mbr2gpt.exe"
    if (-not (Test-Path $mbr2gpt)) {
        Write-WmuLog "mbr2gpt.exe not found. Available only on Windows 10 1703+. Run intermediate Win10 upgrade first." "ERROR"
        return [pscustomobject]@{ Success = $false; Code = -1; Message = "mbr2gpt missing (need Win10 1703+)" }
    }

    Suspend-WmuBitLockerIfNeeded | Out-Null
    if ($ForcePrepare) { Repair-WmuPartitionLayoutForMbr2Gpt -DiskNumber $DiskNumber }

    $logDir = Join-Path $script:StateDir "mbr2gpt-logs"
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null

    Write-WmuLog "Validating disk $DiskNumber for MBR->GPT..." "STEP"
    $validateOut = & $mbr2gpt /validate /disk:$DiskNumber /allowFullOS /logs:"$logDir" 2>&1
    $validateOut | ForEach-Object { Write-WmuLog "mbr2gpt validate: $_" }
    $vCode = $LASTEXITCODE
    if ($vCode -ne 0) {
        Write-WmuLog "Validation failed (code $vCode). Applying layout repairs and retrying once..." "WARN"
        Repair-WmuPartitionLayoutForMbr2Gpt -DiskNumber $DiskNumber
        $validateOut = & $mbr2gpt /validate /disk:$DiskNumber /allowFullOS /logs:"$logDir" 2>&1
        $validateOut | ForEach-Object { Write-WmuLog "mbr2gpt validate retry: $_" }
        $vCode = $LASTEXITCODE
        if ($vCode -ne 0) {
            $msg = Get-WmuMbr2GptMessage $vCode
            Write-WmuLog "MBR2GPT validation failed: $msg" "ERROR"
            return [pscustomobject]@{ Success = $false; Code = $vCode; Message = $msg }
        }
    }

    Write-WmuLog "Converting disk $DiskNumber MBR -> GPT (data preserved)..." "STEP"
    $convOut = & $mbr2gpt /convert /disk:$DiskNumber /allowFullOS /logs:"$logDir" 2>&1
    $convOut | ForEach-Object { Write-WmuLog "mbr2gpt convert: $_" }
    $cCode = $LASTEXITCODE
    # 0 = success, 100 = success but some BCD entries not restored
    if ($cCode -eq 0 -or $cCode -eq 100) {
        Write-WmuLog "Conversion succeeded (code $cCode). IMPORTANT: set firmware to UEFI (disable CSM) before reboot if prompted." "OK"
        Update-WmuState @{
            Phase            = "MbrConverted"
            NeedsUefiFirmware = $true
            Mbr2gptCode      = $cCode
        }
        # Fix boot files
        try {
            $sys = $env:SystemDrive
            & bcdboot "$sys\Windows" /f UEFI 2>&1 | ForEach-Object { Write-WmuLog "bcdboot: $_" }
        } catch {
            Write-WmuLog "bcdboot warning: $($_.Exception.Message)" "WARN"
        }
        return [pscustomobject]@{ Success = $true; Code = $cCode; Message = "Converted"; NeedsUefi = $true }
    }

    $msg = Get-WmuMbr2GptMessage $cCode
    Write-WmuLog "MBR2GPT convert failed: $msg" "ERROR"
    return [pscustomobject]@{ Success = $false; Code = $cCode; Message = $msg }
}

function Get-WmuMbr2GptMessage {
    param([int]$Code)
    $map = @{
        0   = "Success"
        1   = "Canceled by user"
        2   = "Internal error"
        3   = "Initialization error"
        4   = "Invalid command-line parameters"
        5   = "Error reading disk geometry/layout"
        6   = "Volume encrypted (BitLocker) - suspend/decrypt first"
        7   = "Disk layout does not meet requirements (<=3 partitions, active system partition)"
        8   = "Error creating EFI system partition"
        9   = "Error installing boot files"
        10  = "Error applying GPT layout"
        100 = "GPT OK but some BCD entries not restored"
    }
    if ($map.ContainsKey($Code)) { return $map[$Code] }
    return "Unknown mbr2gpt code $Code - see logs under $script:StateDir\mbr2gpt-logs"
}
