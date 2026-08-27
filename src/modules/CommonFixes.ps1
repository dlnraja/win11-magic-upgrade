# Recurring pre-upgrade fixes for Win10->Win11 migrations

function Invoke-WmuCommonFixes {
    Write-WmuLog "Applying common migration prep fixes..." "STEP"

    # Pending reboot detection
    $pending = $false
    $rebootKeys = @(
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending",
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired",
        "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager"
    )
    if (Test-Path $rebootKeys[0] -ErrorAction SilentlyContinue) { $pending = $true }
    if (Test-Path $rebootKeys[1] -ErrorAction SilentlyContinue) { $pending = $true }
    try {
        $pf = (Get-ItemProperty $rebootKeys[2] -ErrorAction SilentlyContinue).PendingFileRenameOperations
        if ($pf) { $pending = $true }
    } catch { }
    if ($pending) {
        Write-WmuLog "A reboot is pending. Continuing may fail - reboot recommended before upgrade." "WARN"
        Update-WmuState @{ PendingReboot = $true }
    }

    # Disable Windows Update interrupting feature upgrades mid-flight (temporary)
    try {
        Stop-Service wuauserv -Force -ErrorAction SilentlyContinue
        Write-WmuLog "Paused wuauserv for upgrade stability" "OK"
    } catch { }

    # Clear previous failed upgrade leftovers that cause 0xC1900101 / rollback loops
    $bt = 'C:\$WINDOWS.~BT'
    $ws = 'C:\$Windows.~WS'
    foreach ($junk in @($bt, $ws)) {
        if (Test-Path $junk) {
            Write-WmuLog "Found leftover $junk - attempting cleanup (failed prior upgrade)..." "WARN"
            try {
                cmd /c "rmdir /s /q `"$junk`"" 2>&1 | Out-Null
            } catch {
                Write-WmuLog "Could not remove $junk (in use). Continue; setup may clean it." "WARN"
            }
        }
    }

    # Compatibility appraiser cache
    $appraiser = Join-Path $env:SystemRoot "appcompat\appraiser"
    if (Test-Path $appraiser) {
        try {
            Get-ChildItem $appraiser -Filter "*.xml" -ErrorAction SilentlyContinue |
                Remove-Item -Force -ErrorAction SilentlyContinue
            Write-WmuLog "Cleared appraiser cache XML" "OK"
        } catch { }
    }

    # Free space pressure - setup needs ~15-25 GB
    $drive = Get-PSDrive -Name ($env:SystemDrive.TrimEnd(':').TrimEnd('\'))
    $freeGB = [math]::Round($drive.Free / 1GB, 1)
    if ($freeGB -lt 20) {
        Write-WmuLog "Only ${freeGB} GB free. Trying Disk Cleanup silent components..." "WARN"
        try {
            # Clear temp
            Remove-Item "$env:TEMP\*" -Recurse -Force -ErrorAction SilentlyContinue
            Remove-Item "$env:SystemRoot\Temp\*" -Recurse -Force -ErrorAction SilentlyContinue
            # SoftwareDistribution download cache (safe-ish)
            Stop-Service bits -Force -ErrorAction SilentlyContinue
            Remove-Item "$env:SystemRoot\SoftwareDistribution\Download\*" -Recurse -Force -ErrorAction SilentlyContinue
            Start-Service bits -ErrorAction SilentlyContinue
        } catch { }
        $drive = Get-PSDrive -Name ($env:SystemDrive.TrimEnd(':').TrimEnd('\'))
        $freeGB = [math]::Round($drive.Free / 1GB, 1)
        Write-WmuLog "Free space now ${freeGB} GB" $(if ($freeGB -ge 15) { "OK" } else { "WARN" })
        if ($freeGB -lt 12) {
            throw "Not enough free disk space (${freeGB} GB). Free at least ~20 GB then retry."
        }
    }

    # Unmount stale ISOs that confuse setup
    try {
        Get-DiskImage -ErrorAction SilentlyContinue | Where-Object { $_.Attached } | ForEach-Object {
            Write-WmuLog "Dismounting previously attached image: $($_.ImagePath)" "INFO"
            Dismount-DiskImage -ImagePath $_.ImagePath -ErrorAction SilentlyContinue
        }
    } catch { }

    # Ensure .NET Framework 3.5/4.x features not in a broken pending state (non-fatal)
    try {
        $net = Get-WindowsOptionalFeature -Online -FeatureName NetFx4-AdvSrvs -ErrorAction SilentlyContinue
        if ($net -and $net.State -eq "Disabled") {
            Write-WmuLog ".NET 4 Advanced Services disabled - leaving as-is (this tool does not need FlyOOBE/.NET GUI)." "INFO"
        }
    } catch { }

    # Network mapped drives can break setup - note only
    $maps = @(Get-PSDrive -PSProvider FileSystem | Where-Object { $_.DisplayRoot -like "\\*" })
    if ($maps.Count -gt 0) {
        Write-WmuLog "Mapped network drives detected ($($maps.Count)). Disconnect if setup hangs." "WARN"
    }

    Write-WmuLog "Common fixes done." "OK"
}

function Install-WmuRunOnceContinuation {
    param(
        [string]$ScriptPath,
        [string]$Args = "-Resume"
    )
    $cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`" $Args"
    Set-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce" -Name "Win11MagicUpgrade" -Value $cmd -Type String -Force
    Write-WmuLog "Registered RunOnce continuation for after reboot." "OK"
}

function Remove-WmuRunOnceContinuation {
    try {
        Remove-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce" -Name "Win11MagicUpgrade" -ErrorAction SilentlyContinue
    } catch { }
}
