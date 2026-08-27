# Known migration bug patches for Win10 -> Win11 / feature updates
# Sources: Microsoft Support, SetupDiag patterns, FlyOOBE issues, community 0xC1900101 reports

function Get-WmuKnownBlockerSoftware {
    # DisplayName patterns (Uninstall registry) that frequently hard-block or cause 0xC1900101 / 0xC1900208
    return @(
        @{ Pattern = "Logitech Gaming Software"; Reason = "Legacy LGS filter drivers -> 0xC1900101 / Memory Integrity"; Action = "WarnUninstall" },
        @{ Pattern = "Norton|McAfee|Avast|AVG|Kaspersky|Bitdefender|ESET|Sophos|Trend Micro|Webroot|Malwarebytes"; Reason = "3rd-party AV filter drivers during SafeOS"; Action = "WarnDisable" },
        @{ Pattern = "Acronis|Macrium|EaseUS Todo|AOMEI|ShadowProtect|Veeam Agent"; Reason = "Disk filter / VSS drivers block SafeOS boot"; Action = "WarnUninstall" },
        @{ Pattern = "Intel Rapid Storage|Intel Optane|IRST"; Reason = "Outdated RST/Optane drivers cause SECOND_BOOT rollback"; Action = "WarnUpdate" },
        @{ Pattern = "SentinelOne|CrowdStrike|Carbon Black|Cylance|Symantec Endpoint"; Reason = "EDR leftover .sys hard-blocks appraiser"; Action = "WarnUninstall" },
        @{ Pattern = "VPN|Cisco AnyConnect|GlobalProtect|Pulse Secure|OpenVPN Connect|NordVPN|ExpressVPN"; Reason = "Network filter drivers hang setup"; Action = "WarnDisable" },
        @{ Pattern = "VirtualBox|VMware|Hyper-V Manager"; Reason = "Virtual NIC/switch drivers may confuse setup"; Action = "Warn" },
        @{ Pattern = "iTunes|Apple Mobile Device"; Reason = "Apple USB drivers historically cause 0xC1900101"; Action = "Warn" },
        @{ Pattern = "Daemon Tools|Alcohol 120|PowerISO"; Reason = "Virtual CD filter drivers"; Action = "WarnUninstall" },
        @{ Pattern = "Old CD Burning|Nero"; Reason = "Legacy storage upper filters"; Action = "Warn" }
    )
}

function Get-WmuInstalledPrograms {
    $paths = @(
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*"
    )
    $items = @()
    foreach ($p in $paths) {
        try {
            $items += Get-ItemProperty $p -ErrorAction SilentlyContinue |
                Where-Object { $_.DisplayName } |
                Select-Object DisplayName, DisplayVersion, Publisher, UninstallString, PSChildName
        } catch { }
    }
    return $items
}

function Find-WmuBlockerSoftware {
    $progs = Get-WmuInstalledPrograms
    $rules = Get-WmuKnownBlockerSoftware
    $hits = @()
    foreach ($prog in $progs) {
        foreach ($rule in $rules) {
            if ($prog.DisplayName -match $rule.Pattern) {
                $hits += [pscustomobject]@{
                    Name    = $prog.DisplayName
                    Version = $prog.DisplayVersion
                    Reason  = $rule.Reason
                    Action  = $rule.Action
                }
                break
            }
        }
    }
    return $hits
}

function Disable-WmuRiskyServices {
    # Soften common upgrade killers without uninstalling (reversible guidance logged)
    $serviceNames = @(
        "Sense",           # leave Windows Defender alone
        "SepMasterService", "NortonSecurity", "avast! Antivirus", "AVG Antivirus",
        "MBAMService", "KDSService", "ekrn", "bdAgent",
        "Acronis*"
    )
    Write-WmuLog "Scanning third-party security/backup services (informational disable attempt)..." "STEP"
    Get-Service -ErrorAction SilentlyContinue | Where-Object {
        $_.Status -eq "Running" -and (
            $_.DisplayName -match "Norton|McAfee|Avast|AVG|Kaspersky|Bitdefender|ESET|Sophos|Malwarebytes|Acronis|Macrium|EaseUS|Sentinel"
        )
    } | ForEach-Object {
        try {
            Write-WmuLog "Stopping service for upgrade: $($_.Name) ($($_.DisplayName))" "WARN"
            Stop-Service -Name $_.Name -Force -ErrorAction Stop
            Set-Service -Name $_.Name -StartupType Manual -ErrorAction SilentlyContinue
        } catch {
            Write-WmuLog "Could not stop $($_.Name): $($_.Exception.Message)" "WARN"
        }
    }
}

function Clear-WmuMappedNetworkDrives {
    # Fix: IOCTL_STORAGE_QUERY_PROPERTY / mapped drives confuse setup
    Write-WmuLog "Disconnecting mapped network drives (common setup hang / 0x32 storage query)..." "STEP"
    try {
        net use * /delete /y 2>&1 | ForEach-Object { Write-WmuLog "net use: $_" }
    } catch {
        Write-WmuLog "net use cleanup: $($_.Exception.Message)" "WARN"
    }
}

function Disable-WmuPagefileOnUsbAndExternal {
    # External USB disks as pagefile cause SafeOS failures
    try {
        $pf = Get-CimInstance Win32_PageFileSetting -ErrorAction SilentlyContinue
        foreach ($p in @($pf)) {
            if ($p.Name -and $p.Name -notmatch '^[C]:') {
                Write-WmuLog "Non-C: pagefile detected: $($p.Name) - removing for upgrade safety" "WARN"
                Remove-CimInstance -InputObject $p -ErrorAction SilentlyContinue
            }
        }
    } catch { }
}

function Invoke-WmuStorageDriverPrep {
    # Prefer MS inbox storahci/stornvme over stale OEM RAID miniports when possible (AHCI systems)
    Write-WmuLog "Checking storage controller class for risky upper/lower filters..." "STEP"
    $classKey = "HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e967-e325-11ce-bfc1-08002be10318}"
    try {
        $upper = (Get-ItemProperty $classKey -ErrorAction SilentlyContinue).UpperFilters
        $lower = (Get-ItemProperty $classKey -ErrorAction SilentlyContinue).LowerFilters
        if ($upper) { Write-WmuLog "Disk UpperFilters: $($upper -join ', ')" "INFO" }
        if ($lower) { Write-WmuLog "Disk LowerFilters: $($lower -join ', ')" "INFO" }
        $risky = @("eicfg", "EhRecvr", "EhStorClass", "PartMgr") # PartMgr is OK normally
        # Flag known bad virtual CD / encryption filters often listed as UpperFilters on volume class
        $volKey = "HKLM:\SYSTEM\CurrentControlSet\Control\Class\{71a27cdd-812a-11d0-bec7-08002be2092f}"
        $vUpper = (Get-ItemProperty $volKey -ErrorAction SilentlyContinue).UpperFilters
        if ($vUpper) {
            Write-WmuLog "Volume UpperFilters: $($vUpper -join ', ')" "INFO"
            $bad = @($vUpper | Where-Object { $_ -match "dtsoftbus|Alcohol|Elby|TrueCrypt|VeraCrypt|DiskCryptor|Acronis|mfehidk|SymDS" })
            foreach ($b in $bad) {
                Write-WmuLog "Risky volume filter '$b' may cause 0xC1900101 - uninstall parent software before retry" "WARN"
            }
        }
    } catch {
        Write-WmuLog "Storage filter inspection failed: $($_.Exception.Message)" "WARN"
    }
}

function Repair-WmuComponentStore {
    # Helps 0x800F081F / corrupt CBS before feature upgrade
    Write-WmuLog "Quick component health (DISM CheckHealth)..." "STEP"
    try {
        $dism = Join-Path $env:SystemRoot "System32\Dism.exe"
        & $dism /Online /Cleanup-Image /CheckHealth 2>&1 | ForEach-Object { Write-WmuLog "DISM: $_" }
        $code = $LASTEXITCODE
        if ($code -ne 0) {
            Write-WmuLog "Component store may be unhealthy (DISM $code). Running ScanHealth (can take time)..." "WARN"
            & $dism /Online /Cleanup-Image /ScanHealth 2>&1 | ForEach-Object { Write-WmuLog "DISM: $_" }
        }
    } catch {
        Write-WmuLog "DISM skipped: $($_.Exception.Message)" "WARN"
    }
}

function Clear-WmuUpgradeCaches {
    Write-WmuLog "Clearing Windows Update / MoSetup caches that stale-block upgrades..." "STEP"
    $paths = @(
        "$env:SystemRoot\SoftwareDistribution\Download",
        "$env:SystemRoot\SoftwareDistribution\DataStore\Logs",
        "$env:SystemRoot\Logs\MoSetup",
        "$env:SystemRoot\Logs\WindowsUpdate"
    )
    try {
        Stop-Service wuauserv, bits, usosvc, cryptsvc -Force -ErrorAction SilentlyContinue
    } catch { }
    foreach ($p in $paths) {
        if (Test-Path $p) {
            try {
                Get-ChildItem $p -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
            } catch { }
        }
    }
    # Soft-reset appraiser databank (forces re-evaluation; bypasses also reapplied later)
    $appraiser = Join-Path $env:SystemRoot "appcompat\appraiser"
    if (Test-Path $appraiser) {
        Get-ChildItem $appraiser -Include *.sdb,*.xml,*.cab -Recurse -ErrorAction SilentlyContinue |
            Remove-Item -Force -ErrorAction SilentlyContinue
    }
    try {
        Start-Service cryptsvc, bits, wuauserv -ErrorAction SilentlyContinue
    } catch { }
}

function Invoke-WmuSetupDiagIfPresent {
    # Parse prior failure if SetupDiag results exist
    $xml = Join-Path $env:SystemRoot "Logs\SetupDiag\SetupDiagResults.xml"
    $reg = "HKLM:\SYSTEM\Setup\SetupDiag\Results"
    if (Test-Path $xml) {
        Write-WmuLog "Found previous SetupDiag results: $xml" "STEP"
        try {
            [xml]$doc = Get-Content $xml -Raw -ErrorAction Stop
            $failure = $doc.SetupDiag.Failure
            if ($failure) {
                Write-WmuLog "Last SetupDiag failure: $($failure.FailureName) / $($failure.FailureType)" "WARN"
                Write-WmuLog "Detail: $($failure.FailureDetails)" "WARN"
                Update-WmuState @{ LastSetupDiag = $failure.FailureName; LastSetupDiagDetail = [string]$failure.FailureDetails }
            }
        } catch {
            Write-WmuLog "Could not parse SetupDiag XML" "WARN"
        }
    }
    if (Test-Path $reg) {
        try {
            $props = Get-ItemProperty $reg -ErrorAction SilentlyContinue
            if ($props.FailureName) {
                Write-WmuLog "Registry SetupDiag: $($props.FailureName)" "WARN"
            }
        } catch { }
    }

    # Also skim setuperr.log for famous codes
    $errLogs = @(
        'C:\$WINDOWS.~BT\Sources\Panther\setuperr.log',
        'C:\$WINDOWS.~BT\Sources\Rollback\setuperr.log',
        "$env:SystemRoot\Panther\setuperr.log"
    )
    foreach ($log in $errLogs) {
        if (Test-Path $log) {
            Write-WmuLog "Scanning $log for known error codes..." "INFO"
            $hits = Select-String -Path $log -Pattern "0xC1900101|0xC1900208|0x80070070|0x8007001F|0x800F081F|0xC1900200|SECOND_BOOT|SafeOS" -ErrorAction SilentlyContinue |
                Select-Object -Last 8
            foreach ($h in $hits) { Write-WmuLog "LOG: $($h.Line.Trim())" "WARN" }
        }
    }
}

function Disable-WmuHibernationTemporarily {
    # Frees disk + avoids some resume-from-hibernation upgrade glitches
    try {
        $freeBefore = (Get-PSDrive $env:SystemDrive[0]).Free
        & powercfg.exe /hibernate off 2>&1 | Out-Null
        $freeAfter = (Get-PSDrive $env:SystemDrive[0]).Free
        if ($freeAfter -gt $freeBefore) {
            Write-WmuLog "Hibernation disabled; freed ~$([math]::Round(($freeAfter-$freeBefore)/1GB,1)) GB" "OK"
        }
    } catch { }
}

function Set-WmuTargetReleaseOverride {
    # Prevent WU from fighting our ISO path with a different target
    $path = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate"
    if (-not (Test-Path $path)) { New-Item -Path $path -Force | Out-Null }
    # Do not force TargetReleaseVersion permanently; only clear blockers
    Remove-ItemProperty -Path $path -Name "TargetReleaseVersion" -ErrorAction SilentlyContinue
    Remove-ItemProperty -Path $path -Name "TargetReleaseVersionInfo" -ErrorAction SilentlyContinue
    # Allow upgrades on "unsupported" hardware via policy used by some enterprises
    New-ItemProperty -Path $path -Name "DisableWUfBSafeguards" -PropertyType DWord -Value 1 -Force -ErrorAction SilentlyContinue | Out-Null
    Write-WmuLog "WU safeguards policy softened (DisableWUfBSafeguards=1)" "OK"
}

function Invoke-WmuMigrationPatches {
    <#
    .SYNOPSIS
      Applies researched pre-flight patches for recurring Win11 migration failures.
    #>
    Write-WmuLog "=== Migration bug patches (researched) ===" "STEP"

    Invoke-WmuSetupDiagIfPresent

    $blockers = Find-WmuBlockerSoftware
    if ($blockers.Count -gt 0) {
        Write-WmuLog "Potentially blocking software detected:" "WARN"
        foreach ($b in $blockers) {
            Write-WmuLog " - $($b.Name) [$($b.Action)] :: $($b.Reason)" "WARN"
        }
        Update-WmuState @{ BlockerSoftware = @($blockers | ForEach-Object { $_.Name }) }
    } else {
        Write-WmuLog "No known blocker software patterns matched." "OK"
    }

    Clear-WmuMappedNetworkDrives
    Disable-WmuRiskyServices
    Disable-WmuPagefileOnUsbAndExternal
    Invoke-WmuStorageDriverPrep
    Clear-WmuUpgradeCaches
    Disable-WmuHibernationTemporarily
    Set-WmuTargetReleaseOverride

    # Language pack mismatch -> "Keep apps" greyed out
    try {
        $langs = Get-WinUserLanguageList -ErrorAction SilentlyContinue
        Write-WmuLog "UI languages: $(($langs | ForEach-Object { $_.LanguageTag }) -join ', ')" "INFO"
        Write-WmuLog "Install ISO language should match system locale for Keep apps/files." "INFO"
    } catch { }

    # Pending device installs
    try {
        $pnputil = Join-Path $env:SystemRoot "System32\pnputil.exe"
        & $pnputil /enum-devices /problem 2>&1 | Select-Object -First 20 | ForEach-Object {
            if ($_ -match "Problem|Error|Status") { Write-WmuLog "PNP: $_" "WARN" }
        }
    } catch { }

    Write-WmuLog "Migration patches applied." "OK"
}

function Get-WmuErrorCodeHelp {
    param([string]$Code)
    $map = @{
        "0xC1900101" = "Driver/SafeOS rollback. Update chipset/storage/GPU, remove AV/backup filters, disconnect USB, clean boot, retry ISO /product server."
        "0xC1900208" = "Incompatible app hard-block. Uninstall listed app from Setup screen / Appraiser, then retry."
        "0x80070070" = "Not enough disk space. Free 20+ GB, disable hibernation, empty recycle bin."
        "0x8007001F" = "Device not functioning. Disconnect peripherals, update drivers, chkdsk."
        "0x800F081F" = "Component store / source files. DISM RestoreHealth, SFC /scannow, then retry."
        "0xC1900200" = "System does not meet requirements (or appraiser cache). Re-apply bypasses, clear appraiser, /product server."
        "0xC1900107" = "Previous upgrade still pending cleanup. Remove `$WINDOWS.~BT / `$Windows.~WS, reboot, retry."
    }
    foreach ($k in $map.Keys) {
        if ($Code -match [regex]::Escape($k)) { return $map[$k] }
    }
    return $null
}
