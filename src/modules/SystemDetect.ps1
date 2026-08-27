# System detection: OS build, disk style, firmware, CPU features, free space

function Test-WmuAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-WmuCpuFeatures {
    $sse42 = $false
    try {
        $def = @"
[DllImport("kernel32.dll")]
public static extern bool IsProcessorFeaturePresent(uint ProcessorFeature);
"@
        $k32 = Add-Type -MemberDefinition $def -Name "WmuKernel32_$([guid]::NewGuid().ToString('N').Substring(0,8))" -Namespace Win32 -PassThru -ErrorAction Stop
        # 38 = PF_SSE4_2_INSTRUCTIONS_AVAILABLE (proxy for POPCNT on Intel/AMD post-Nehalem)
        $sse42 = [bool]$k32::IsProcessorFeaturePresent(38)
    } catch {
        $sse42 = $null
    }
    $cpu = Get-CimInstance Win32_Processor -ErrorAction SilentlyContinue | Select-Object -First 1
    return [pscustomobject]@{
        Name            = $cpu.Name
        Cores           = $cpu.NumberOfCores
        Logical         = $cpu.NumberOfLogicalProcessors
        AddressWidth    = $cpu.AddressWidth
        Sse42Likely     = $sse42
        PopcntLikely    = $sse42  # POPCNT ships with SSE4.2 on relevant CPUs
    }
}

function Get-WmuFirmwareInfo {
    $biosMode = $null
    try {
        $biosMode = (Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control" -Name "PEFirmwareType" -ErrorAction Stop).PEFirmwareType
        # 1 = BIOS, 2 = UEFI
    } catch { }

    # Fallback: GPT system disk almost always means UEFI boot on modern installs
    if ($null -eq $biosMode) {
        try {
            $bcd = bcdedit /enum firmware 2>&1 | Out-String
            if ($bcd -match "firmware") { $biosMode = 2 }
        } catch { }
    }
    if ($null -eq $biosMode) {
        try {
            $secure = Confirm-SecureBootUEFI -ErrorAction Stop
            $biosMode = 2
        } catch {
            if ($_.Exception.Message -match "Cmdlet not supported on this platform") {
                $biosMode = 1
            }
        }
    }

    $secureBoot = $null
    try {
        $secureBoot = [bool](Confirm-SecureBootUEFI -ErrorAction Stop)
    } catch {
        $secureBoot = $false
    }

    return [pscustomobject]@{
        FirmwareTypeCode = $biosMode
        IsUefi           = ($biosMode -eq 2)
        IsLegacyBios     = ($biosMode -eq 1)
        SecureBootOn     = $secureBoot
    }
}

function Get-WmuSystemDisk {
    $sysDrive = $env:SystemDrive.TrimEnd('\')
    $part = Get-Partition -DriveLetter ($sysDrive.Substring(0, 1)) -ErrorAction SilentlyContinue
    if (-not $part) {
        # Fallback for older PowerShell without Get-Partition (Win10 1511 may lack Storage module fully)
        $diskIndex = $null
        try {
            $ld = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$sysDrive'"
            $part2 = Get-CimInstance -Query "ASSOCIATORS OF {Win32_LogicalDisk.DeviceID='$sysDrive'} WHERE AssocClass=Win32_LogicalDiskToPartition"
            if ($part2) {
                if ($part2.DeviceID -match 'Disk #(\d+)') { $diskIndex = [int]$Matches[1] }
            }
        } catch { }
        $style = "Unknown"
        if ($null -ne $diskIndex) {
            try {
                $d = Get-CimInstance Win32_DiskPartition -Filter "DiskIndex=$diskIndex" | Select-Object -First 1
                # Type not reliable for GPT; use Get-Disk if available
            } catch { }
        }
        try {
            if (Get-Command Get-Disk -ErrorAction SilentlyContinue) {
                $sysDisk = Get-Disk | Where-Object { $_.IsSystem -or $_.IsBoot } | Select-Object -First 1
                if ($sysDisk) {
                    return [pscustomobject]@{
                        Number           = $sysDisk.Number
                        PartitionStyle   = $sysDisk.PartitionStyle.ToString()
                        SizeGB           = [math]::Round($sysDisk.Size / 1GB, 1)
                        PartitionCount   = @(Get-Partition -DiskNumber $sysDisk.Number -ErrorAction SilentlyContinue).Count
                        SystemDrive      = $sysDrive
                    }
                }
            }
        } catch { }
        return [pscustomobject]@{
            Number         = 0
            PartitionStyle = "Unknown"
            SizeGB         = $null
            PartitionCount = $null
            SystemDrive    = $sysDrive
        }
    }

    $disk = Get-Disk -Number $part.DiskNumber
    $parts = @(Get-Partition -DiskNumber $disk.Number -ErrorAction SilentlyContinue)
    return [pscustomobject]@{
        Number           = $disk.Number
        PartitionStyle   = $disk.PartitionStyle.ToString()
        SizeGB           = [math]::Round($disk.Size / 1GB, 1)
        PartitionCount   = $parts.Count
        PrimaryLikeCount = @($parts | Where-Object { $_.Type -match 'Basic|IFS|NTFS|System|Reserved' -or $_.DriveLetter }).Count
        SystemDrive      = $sysDrive
    }
}

function Get-WmuOsInfo {
    $cv = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion"
    $build = [int]$cv.CurrentBuildNumber
    $ubr = 0
    try { $ubr = [int]$cv.UBR } catch { }
    $display = $cv.DisplayVersion
    if (-not $display) { $display = $cv.ReleaseId }
    $productName = $cv.ProductName
    $editionId = $cv.EditionID
    $arch = if ([Environment]::Is64BitOperatingSystem) { "x64" } else { "x86" }

    # Map common build -> marketing version
    $verMap = @{
        10240 = "1507"; 10586 = "1511"; 14393 = "1607"; 15063 = "1703"
        16299 = "1709"; 17134 = "1803"; 17763 = "1809"; 18362 = "1903"
        18363 = "1909"; 19041 = "2004"; 19042 = "20H2"; 19043 = "21H1"
        19044 = "21H2"; 19045 = "22H2"
        22000 = "21H2"; 22621 = "22H2"; 22631 = "23H2"
        26100 = "24H2"; 26200 = "25H2"
    }
    $mapped = $verMap[$build]
    if (-not $mapped) { $mapped = $display }

    $isWin11 = ($build -ge 22000)
    $isWin10 = (-not $isWin11) -and (($productName -match "Windows 10") -or ($build -ge 10240 -and $build -lt 22000))
    if ($isWin11 -and ($productName -notmatch "Windows 11")) {
        $productName = ($productName -replace "Windows 10", "Windows 11")
        if ($productName -notmatch "Windows 11") { $productName = "Windows 11 ($($cv.ProductName))" }
    }

    # Direct Win11 setup is unreliable below ~1809; mbr2gpt needs 1703+
    $needsIntermediate = $isWin10 -and ($build -lt 17763)
    $mbr2gptAvailable = $build -ge 15063  # 1703

    $ramGB = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)
    $sysDrive = Get-PSDrive -Name ($env:SystemDrive.TrimEnd(':').TrimEnd('\')) -ErrorAction SilentlyContinue
    $freeGB = if ($sysDrive) { [math]::Round($sysDrive.Free / 1GB, 1) } else { $null }

    $lang = (Get-Culture).Name
    try {
        $uiLang = (Get-WinSystemLocale).Name
    } catch {
        $uiLang = $lang
    }

    return [pscustomobject]@{
        ProductName       = $productName
        EditionId         = $editionId
        DisplayVersion    = $display
        MappedVersion     = $mapped
        Build             = $build
        Ubr               = $ubr
        IsWindows10       = $isWin10
        IsWindows11       = $isWin11
        Architecture      = $arch
        NeedsIntermediate = $needsIntermediate
        Mbr2gptAvailable  = $mbr2gptAvailable
        RamGB             = $ramGB
        FreeGB            = $freeGB
        Culture           = $lang
        SystemLocale      = $uiLang
        InstallDate       = $cv.InstallDate
    }
}

function Get-WmuFullReport {
    $os = Get-WmuOsInfo
    $disk = Get-WmuSystemDisk
    $fw = Get-WmuFirmwareInfo
    # GPT system disk strongly implies UEFI even if PEFirmwareType is missing
    if (($null -eq $fw.FirmwareTypeCode) -and ($disk.PartitionStyle -eq "GPT")) {
        $fw = [pscustomobject]@{
            FirmwareTypeCode = 2
            IsUefi           = $true
            IsLegacyBios     = $false
            SecureBootOn     = $fw.SecureBootOn
        }
    }
    $cpu = Get-WmuCpuFeatures
    $tpm = $null
    try {
        $t = Get-CimInstance -Namespace "root\cimv2\Security\MicrosoftTpm" -ClassName Win32_Tpm -ErrorAction Stop
        $tpm = [pscustomobject]@{
            Present     = $true
            IsEnabled   = $t.IsEnabled_InitialValue
            IsActivated = $t.IsActivated_InitialValue
            SpecVersion = $t.SpecVersion
        }
    } catch {
        $tpm = [pscustomobject]@{ Present = $false; IsEnabled = $false; IsActivated = $false; SpecVersion = $null }
    }

    return [pscustomobject]@{
        OS       = $os
        Disk     = $disk
        Firmware = $fw
        Cpu      = $cpu
        Tpm      = $tpm
        Timestamp = (Get-Date).ToString("o")
    }
}

function Write-WmuReport {
    param($Report)
    Write-WmuLog "OS: $($Report.OS.ProductName) $($Report.OS.MappedVersion) build $($Report.OS.Build).$($Report.OS.Ubr) ($($Report.OS.Architecture))" "STEP"
    Write-WmuLog "Edition: $($Report.OS.EditionId) | Locale: $($Report.OS.SystemLocale) | RAM: $($Report.OS.RamGB) GB | Free: $($Report.OS.FreeGB) GB"
    Write-WmuLog "Disk #$($Report.Disk.Number): $($Report.Disk.PartitionStyle) | ~$($Report.Disk.SizeGB) GB | partitions=$($Report.Disk.PartitionCount)"
    Write-WmuLog "Firmware: UEFI=$($Report.Firmware.IsUefi) SecureBoot=$($Report.Firmware.SecureBootOn)"
    Write-WmuLog "CPU: $($Report.Cpu.Name) | SSE4.2/POPCNT likely=$($Report.Cpu.Sse42Likely)"
    Write-WmuLog "TPM present=$($Report.Tpm.Present) version=$($Report.Tpm.SpecVersion)"
    if ($Report.OS.NeedsIntermediate) {
        Write-WmuLog "Obsolete Windows 10 detected - intermediate upgrade to Win10 22H2 required before Win11." "WARN"
    }
    if ($Report.Disk.PartitionStyle -eq "MBR") {
        Write-WmuLog "System disk is MBR - will auto-convert to GPT (no data wipe) when possible." "WARN"
    }
    if ($Report.Cpu.Sse42Likely -eq $false) {
        Write-WmuLog "CPU appears to lack SSE4.2/POPCNT - Windows 11 24H2+ will NOT boot. Abort recommended." "ERROR"
    }
}
