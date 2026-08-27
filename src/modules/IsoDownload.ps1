# ISO download via bundled Fido (official Microsoft CDN) - same approach as FlyOOBE / Rufus

function Get-WmuVendorFidoPath {
    $root = $script:WmuRoot
    if (-not $root) {
        if ($PSScriptRoot -match '[\\/]modules$') {
            $root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
        } else {
            $root = Split-Path $PSScriptRoot -Parent
        }
    }
    $candidates = @(
        (Join-Path $root "vendor\Fido.ps1"),
        (Join-Path (Split-Path $PSScriptRoot -Parent) "..\vendor\Fido.ps1"),
        (Join-Path $PSScriptRoot "..\..\vendor\Fido.ps1"),
        (Join-Path $script:StateDir "Fido.ps1")
    )
    foreach ($c in $candidates) {
        $full = [System.IO.Path]::GetFullPath($c)
        if (Test-Path $full) { return $full }
    }
    return $null
}

function Ensure-WmuFido {
    $existing = Get-WmuVendorFidoPath
    if ($existing) { return $existing }
    $dest = Join-Path $script:StateDir "Fido.ps1"
    Write-WmuLog "Downloading Fido.ps1 (official Microsoft ISO helper by pbatard)..." "STEP"
    Invoke-WebRequest -Uri "https://raw.githubusercontent.com/pbatard/Fido/master/Fido.ps1" -OutFile $dest -UseBasicParsing -TimeoutSec 120
    return $dest
}

function Resolve-WmuFidoEdition {
    param([string]$EditionId)
    switch -Regex ($EditionId) {
        'Enterprise' { return "Enterprise" }
        'Education'  { return "Education" }
        'Professional|Pro' { return "Pro" }
        'Core|Home'  { return "Home" }
        default      { return "Pro" }
    }
}

function Resolve-WmuFidoLang {
    param([string]$Locale)
    # Fido accepts friendly names; map common locales
    $map = @{
        "fr-FR" = "French"; "fr-CA" = "French"
        "en-US" = "English"; "en-GB" = "English International"
        "de-DE" = "German"; "es-ES" = "Spanish"; "it-IT" = "Italian"
        "pt-BR" = "Brazilian Portuguese"; "pt-PT" = "Portuguese"
        "nl-NL" = "Dutch"; "pl-PL" = "Polish"; "ru-RU" = "Russian"
        "ja-JP" = "Japanese"; "zh-CN" = "Simplified Chinese"; "zh-TW" = "Traditional Chinese"
        "ar-SA" = "Arabic"; "tr-TR" = "Turkish"; "cs-CZ" = "Czech"
        "sv-SE" = "Swedish"; "ko-KR" = "Korean"
    }
    if ($map.ContainsKey($Locale)) { return $map[$Locale] }
    if ($Locale -like "fr*") { return "French" }
    if ($Locale -like "en*") { return "English" }
    return "English"
}

function Get-WmuIsoWorkDir {
    $d = Join-Path $script:StateDir "iso"
    New-Item -ItemType Directory -Path $d -Force | Out-Null
    return $d
}

function Invoke-WmuIsoDownload {
    <#
    .SYNOPSIS
      Downloads official Windows ISO using Fido CLI (Microsoft software-download API).
    #>
    param(
        [ValidateSet("10", "11")][string]$Win = "11",
        [string]$Release = "Latest",
        [string]$Edition,
        [string]$Lang,
        [string]$Arch = "x64",
        [string]$OutDir
    )

    if (-not $OutDir) { $OutDir = Get-WmuIsoWorkDir }
    New-Item -ItemType Directory -Path $OutDir -Force | Out-Null

    $fido = Ensure-WmuFido
    Write-WmuLog "Using Fido: $fido" "INFO"
    Write-WmuLog "Requesting Windows $Win $Release $Edition $Lang $Arch ISO URL from Microsoft..." "STEP"

    $prev = Get-Location
    try {
        Set-Location $OutDir
        # -GetUrl prints URL; then we BITS download for resume support
        $url = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $fido `
            -Win $Win -Rel $Release -Ed $Edition -Lang $Lang -Arch $Arch -GetUrl 2>&1 |
            Where-Object { $_ -match '^https://' } |
            Select-Object -Last 1

        if (-not $url) {
            # Fallback: let Fido download itself into OutDir
            Write-WmuLog "GetUrl empty - letting Fido download directly..." "WARN"
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $fido `
                -Win $Win -Rel $Release -Ed $Edition -Lang $Lang -Arch $Arch
            $iso = Get-ChildItem $OutDir -Filter "*.iso" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
            if (-not $iso) { throw "Fido did not produce an ISO in $OutDir" }
            Write-WmuLog "ISO ready: $($iso.FullName)" "OK"
            return $iso.FullName
        }

        $url = $url.ToString().Trim()
        Write-WmuLog "Microsoft CDN URL obtained." "OK"
        $name = [regex]::Match($url, '([^/\\?]+\.iso)').Groups[1].Value
        if (-not $name) { $name = "Windows${Win}_${Release}_${Arch}.iso" }
        $dest = Join-Path $OutDir $name

        if ((Test-Path $dest) -and ((Get-Item $dest).Length -gt 3GB)) {
            Write-WmuLog "Reusing existing ISO: $dest" "OK"
            return $dest
        }

        Write-WmuLog "Downloading $name (BITS, resumable)..." "STEP"
        # Prefer BITS for large files / resume
        try {
            Start-BitsTransfer -Source $url -Destination $dest -DisplayName "Win11MagicUpgrade-ISO" -ErrorAction Stop
        } catch {
            Write-WmuLog "BITS failed ($($_.Exception.Message)); falling back to Invoke-WebRequest..." "WARN"
            Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
        }

        if (-not (Test-Path $dest) -or ((Get-Item $dest).Length -lt 1GB)) {
            throw "ISO download incomplete: $dest"
        }
        Write-WmuLog "Download complete: $dest ($([math]::Round((Get-Item $dest).Length/1GB,2)) GB)" "OK"
        return $dest
    }
    finally {
        Set-Location $prev
    }
}

function Mount-WmuIso {
    param([Parameter(Mandatory)][string]$IsoPath)
    Write-WmuLog "Mounting ISO: $IsoPath" "STEP"
    if (-not (Get-Command Mount-DiskImage -ErrorAction SilentlyContinue)) {
        throw "Mount-DiskImage unavailable. Open the ISO manually and pass -SetupRoot."
    }
    $img = Mount-DiskImage -ImagePath $IsoPath -PassThru -ErrorAction Stop
    Start-Sleep -Seconds 2
    $vol = $img | Get-Volume -ErrorAction SilentlyContinue
    if (-not $vol) {
        # Alternate path
        $diskImage = Get-DiskImage -ImagePath $IsoPath
        $vol = Get-Volume -DiskImage $diskImage -ErrorAction SilentlyContinue
    }
    $letter = $null
    if ($vol) {
        if ($vol -is [array]) { $letter = $vol[0].DriveLetter } else { $letter = $vol.DriveLetter }
    }
    if (-not $letter) {
        # Fallback: find new drive with setup.exe
        Get-PSDrive -PSProvider FileSystem | ForEach-Object {
            if (Test-Path (Join-Path $_.Root "setup.exe")) { $letter = $_.Name }
        }
    }
    if (-not $letter) { throw "Could not determine mounted ISO drive letter." }
    $root = "${letter}:\"
    Write-WmuLog "ISO mounted at $root" "OK"
    return $root
}

function Dismount-WmuIso {
    param([string]$IsoPath)
    if ($IsoPath -and (Test-Path $IsoPath)) {
        try { Dismount-DiskImage -ImagePath $IsoPath -ErrorAction SilentlyContinue | Out-Null } catch { }
    }
}
