# Logging helpers for Win11 Magic Upgrade
$script:LogDir = Join-Path $env:LOCALAPPDATA "Win11MagicUpgrade\logs"
$script:StateDir = Join-Path $env:LOCALAPPDATA "Win11MagicUpgrade"
$script:LogFile = $null

function Initialize-WmuLogging {
    if (-not (Test-Path $script:StateDir)) {
        New-Item -ItemType Directory -Path $script:StateDir -Force | Out-Null
    }
    if (-not (Test-Path $script:LogDir)) {
        New-Item -ItemType Directory -Path $script:LogDir -Force | Out-Null
    }
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $script:LogFile = Join-Path $script:LogDir "upgrade-$stamp.log"
    Write-WmuLog "=== Win11 Magic Upgrade started ==="
}

function Write-WmuLog {
    param(
        [Parameter(Mandatory)][string]$Message,
        [ValidateSet("INFO", "WARN", "ERROR", "OK", "STEP")]
        [string]$Level = "INFO"
    )
    $line = "[{0}] [{1}] {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Level, $Message
    switch ($Level) {
        "ERROR" { Write-Host $line -ForegroundColor Red }
        "WARN"  { Write-Host $line -ForegroundColor Yellow }
        "OK"    { Write-Host $line -ForegroundColor Green }
        "STEP"  { Write-Host $line -ForegroundColor Cyan }
        default { Write-Host $line }
    }
    if ($script:LogFile) {
        Add-Content -Path $script:LogFile -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue
    }
}

function Get-WmuStatePath { Join-Path $script:StateDir "state.json" }

function Get-WmuState {
    $p = Get-WmuStatePath
    if (Test-Path $p) {
        try { return (Get-Content $p -Raw -Encoding UTF8 | ConvertFrom-Json) } catch { return $null }
    }
    return $null
}

function Set-WmuState {
    param([hashtable]$Data)
    $obj = [pscustomobject]$Data
    $json = $obj | ConvertTo-Json -Depth 8
    Set-Content -Path (Get-WmuStatePath) -Value $json -Encoding UTF8
}

function Update-WmuState {
    param([hashtable]$Patch)
    $cur = @{}
    $existing = Get-WmuState
    if ($existing) {
        $existing.PSObject.Properties | ForEach-Object { $cur[$_.Name] = $_.Value }
    }
    foreach ($k in $Patch.Keys) { $cur[$k] = $Patch[$k] }
    $cur["UpdatedAt"] = (Get-Date).ToString("o")
    Set-WmuState -Data $cur
}
