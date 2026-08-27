# Hardware check bypasses (Flyby11/FlyOOBE style + 24H2 registry patches)

function Set-WmuRegistryDword {
    param(
        [string]$Path,
        [string]$Name,
        [int]$Value
    )
    if (-not (Test-Path $Path)) {
        New-Item -Path $Path -Force | Out-Null
    }
    New-ItemProperty -Path $Path -Name $Name -PropertyType DWord -Value $Value -Force | Out-Null
}

function Invoke-WmuHardwareBypass {
    <#
    .SYNOPSIS
      Applies all known in-place upgrade bypasses for unsupported Win11 hardware.
      Combines:
        - MoSetup AllowUpgradesWithUnsupportedTPMOrCPU (Microsoft documented)
        - LabConfig BypassTPM/SecureBoot/RAM/Storage/CPU checks
        - 24H2 HwReqChkVars AppCompatFlags spoof
        - Clearing stale AppCompat upgrade blockers
    #>
    Write-WmuLog "Applying Windows 11 hardware requirement bypasses..." "STEP"

    # Microsoft-documented MoSetup bypass (TPM/CPU)
    Set-WmuRegistryDword "HKLM:\SYSTEM\Setup\MoSetup" "AllowUpgradesWithUnsupportedTPMOrCPU" 1
    Write-WmuLog "MoSetup\\AllowUpgradesWithUnsupportedTPMOrCPU = 1" "OK"

    # LabConfig (used by setup media / PE paths; harmless on full OS)
    $lab = "HKLM:\SYSTEM\Setup\LabConfig"
    Set-WmuRegistryDword $lab "BypassTPMCheck" 1
    Set-WmuRegistryDword $lab "BypassSecureBootCheck" 1
    Set-WmuRegistryDword $lab "BypassRAMCheck" 1
    Set-WmuRegistryDword $lab "BypassStorageCheck" 1
    Set-WmuRegistryDword $lab "BypassCPUCheck" 1
    Write-WmuLog "LabConfig BypassTPM/SecureBoot/RAM/Storage/CPU = 1" "OK"

    # Clear AppCompat markers that block "This PC can't run Windows 11"
    $compatRoots = @(
        "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\CompatMarkers",
        "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Shared",
        "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\TargetVersionUpgradeExperienceIndicators"
    )
    foreach ($r in $compatRoots) {
        if (Test-Path $r) {
            try {
                Remove-Item -Path $r -Recurse -Force -ErrorAction Stop
                Write-WmuLog "Removed $r" "OK"
            } catch {
                Write-WmuLog "Could not remove $r : $($_.Exception.Message)" "WARN"
            }
        }
    }

    # 24H2+ hardware requirement check spoof (gHacks / community method)
    $hwReq = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\HwReqChk"
    if (-not (Test-Path $hwReq)) {
        New-Item -Path $hwReq -Force | Out-Null
    }
    $multi = @(
        "SQ_SecureBootCapable=TRUE",
        "SQ_SecureBootEnabled=TRUE",
        "SQ_TpmVersion=2",
        "SQ_RamMB=8192"
    )
    New-ItemProperty -Path $hwReq -Name "HwReqChkVars" -PropertyType MultiString -Value $multi -Force | Out-Null
    Write-WmuLog "HwReqChkVars spoofed for 24H2 checks" "OK"

    # Soft block dismissal used by some upgrade assistants
    Set-WmuRegistryDword "HKLM:\SYSTEM\Setup\MoSetup" "AllowUpgradesWithUnsupportedTPMOrCPU" 1

    # Prevent UpgradeEligibility soft-block UI on some builds
    try {
        $upg = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\UpgradeEligibility"
        if (-not (Test-Path $upg)) { New-Item -Path $upg -Force | Out-Null }
        Set-WmuRegistryDword $upg "UpgradedSystem" 1
    } catch { }

    Write-WmuLog "All registry bypasses applied." "OK"
    return $true
}

function Get-WmuSetupBypassArgs {
    <#
    .SYNOPSIS
      Returns setup.exe argument list using Flyby11 method: /product server
      plus silent-friendly keep-apps upgrade flags.
    #>
    param(
        [switch]$Quiet,
        [switch]$NoReboot
    )
    # /product server = Windows Server setup path -> skips TPM/SecureBoot/CPU list checks
    # Still installs client Windows 11 (Home/Pro) from a client ISO.
    $args = @(
        "/product", "server",
        "/auto", "upgrade",
        "/compat", "IgnoreWarning",
        "/dynamicupdate", "disable",
        "/eula", "accept",
        "/telemetry", "disable"
    )
    if ($Quiet) {
        $args += @("/quiet", "/showoobe", "none")
    }
    if ($NoReboot) {
        $args += "/noreboot"
    }
    return $args
}
