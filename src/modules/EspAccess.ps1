# Resolve the EFI/system partition without a fixed drive letter.
# Every external process is non-interactive and has a timeout. On timeout the
# process tree is killed. A path is removed only when this module added it.

$script:WmuEspGuid = '{c12a7328-f81f-11d2-ba4b-00a0c93ec93b}'
$script:WmuMounted = @()

function Invoke-WmuBoundedProcess {
    param(
        [Parameter(Mandatory)][string]$FileName,
        [string]$Arguments = '',
        [int]$TimeoutSec = 120,
        [string]$StdinText = ''
    )
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $FileName
    $psi.Arguments = $Arguments
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    if ($StdinText) { $psi.RedirectStandardInput = $true }
    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    if (-not $proc.Start()) { throw "failed to start $FileName" }
    if ($StdinText) {
        $proc.StandardInput.Write($StdinText)
        $proc.StandardInput.Close()
    }
    if (-not $proc.WaitForExit($TimeoutSec * 1000)) {
        try { & taskkill.exe /F /T /PID $proc.Id | Out-Null } catch {}
        try { $proc.Kill() } catch {}
        throw "timeout after $TimeoutSec s: $FileName"
    }
    $text = (($proc.StandardOutput.ReadToEnd()) + ($proc.StandardError.ReadToEnd()))
    return [pscustomobject]@{ Code = $proc.ExitCode; Output = $text }
}

function Get-WmuFreeDriveLetter {
    $used = @{}
    foreach ($d in @(Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue)) {
        $name = [string]$d.Name
        if ($name.Length -eq 1) { $used[$name.ToUpper()] = $true }
    }
    if ($env:SystemDrive) { $used[$env:SystemDrive.Substring(0, 1).ToUpper()] = $true }
    for ($code = [int][char]'Z'; $code -ge [int][char]'D'; $code--) {
        $ch = [string]([char]$code)
        if (-not $used.ContainsKey($ch)) { return $ch }
    }
    return $null
}

function Get-WmuEspPartition {
    if (-not (Get-Command Get-Partition -ErrorAction SilentlyContinue)) { return $null }
    $winDisk = $null
    if ($env:SystemDrive) {
        try {
            $winDisk = (Get-Partition -DriveLetter ($env:SystemDrive.TrimEnd('\').TrimEnd(':')) -ErrorAction Stop).DiskNumber
        } catch {}
    }
    $parts = @(Get-Partition -ErrorAction SilentlyContinue)
    $onBoot = @($parts | Where-Object {
        ($_.GptType -eq $script:WmuEspGuid -or $_.MbrType -eq 239 -or ($_.IsSystem -and -not $_.IsBoot)) -and
        (($null -eq $winDisk) -or ($_.DiskNumber -eq $winDisk))
    })
    if (-not $onBoot) { return $null }
    $ranked = @($onBoot | Sort-Object @{ Expression = { -not $_.IsSystem } }, @{ Expression = { $_.GptType -ne $script:WmuEspGuid } })
    if ($ranked.Count -gt 1 -and -not $ranked[0].IsSystem -and -not $ranked[1].IsSystem) {
        throw "more than one ESP matches on the Windows disk; refusing to guess"
    }
    return $ranked[0]
}

function Resolve-WmuSystemPartition {
    $part = Get-WmuEspPartition
    if (-not $part) { throw "no ESP or system partition on the Windows disk" }
    $mountedByUs = $false
    $letter = ''
    if ($part.DriveLetter) { $letter = [string]$part.DriveLetter }
    $access = $null
    if ($letter) {
        $access = ($letter + ':\')
    } else {
        $letter = Get-WmuFreeDriveLetter
        if (-not $letter) { throw "no free drive letter and no existing ESP access path" }
        Add-PartitionAccessPath -DiskNumber $part.DiskNumber -PartitionNumber $part.PartitionNumber -AccessPath ($letter + ':') -ErrorAction Stop
        $mountedByUs = $true
        $script:WmuMounted += [pscustomobject]@{ Disk = $part.DiskNumber; Partition = $part.PartitionNumber; Path = ($letter + ':\') }
        $access = ($letter + ':\')
    }
    $efi = Join-Path $access 'EFI'
    $boot = Join-Path $access 'bootmgr'
    $typed = ($part.GptType -eq $script:WmuEspGuid -or $part.MbrType -eq 239)
    if (-not (Test-Path $efi) -and -not (Test-Path $boot) -and -not $typed) {
        if ($mountedByUs) { Dismount-WmuEspPath -Disk $part.DiskNumber -Partition $part.PartitionNumber -Path ($letter + ':\') }
        throw "mounted volume is not an ESP (no EFI directory and not a typed system partition)"
    }
    return [pscustomobject]@{
        Disk = $part.DiskNumber
        Partition = $part.PartitionNumber
        Letter = $letter
        Access = $access
        BcdTarget = ($letter + ':')
        MountedByUs = $mountedByUs
    }
}

function Dismount-WmuEspPath {
    param([int]$Disk, [int]$Partition, [string]$Path)
    try {
        Remove-PartitionAccessPath -DiskNumber $Disk -PartitionNumber $Partition -AccessPath $Path -ErrorAction SilentlyContinue
    } catch {}
    $script:WmuMounted = @($script:WmuMounted | Where-Object { $_.Path -ne $Path })
}

function Invoke-WmuEspBcdboot {
    param([string]$Mode = 'UEFI')
    $resolved = $null
    try {
        $resolved = Resolve-WmuSystemPartition
        $bcdboot = Join-Path $env:SystemRoot 'System32\bcdboot.exe'
        if (-not (Test-Path $bcdboot)) { throw 'bcdboot.exe missing' }
        $args = '"' + $env:SystemRoot + '" /s ' + $resolved.BcdTarget + ' /f ' + $Mode
        $ran = Invoke-WmuBoundedProcess -FileName $bcdboot -Arguments $args -TimeoutSec 180
        if (Get-Command Write-WmuLog -ErrorAction SilentlyContinue) {
            Write-WmuLog ("bcdboot /s " + $resolved.BcdTarget + " /f $Mode -> " + $ran.Code) "INFO"
        }
        if ($ran.Code -ne 0) {
            $args = '"' + $env:SystemRoot + '" /s ' + $resolved.BcdTarget + ' /f ALL'
            $ran = Invoke-WmuBoundedProcess -FileName $bcdboot -Arguments $args -TimeoutSec 180
        }
        $check = Invoke-WmuBoundedProcess -FileName (Join-Path $env:SystemRoot 'System32\bcdedit.exe') -Arguments '/enum {bootmgr}' -TimeoutSec 90
        if ($ran.Code -ne 0 -or $check.Code -ne 0) { throw "bcdboot/bcdedit post-check failed ($($ran.Code)/$($check.Code))" }
        return $true
    } finally {
        if ($resolved -and $resolved.MountedByUs) {
            Dismount-WmuEspPath -Disk $resolved.Disk -Partition $resolved.Partition -Path ($resolved.Letter + ':\')
        }
    }
}
