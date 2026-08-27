# CI/CD antivirus trust - VirusTotal + Kaspersky OpenTIP (Release workflow only).
# Does NOT run inside the app / One-Click. Optional secrets:
#   VIRUSTOTAL_API_KEY or MAGIC_VT_API_KEY
#   MAGIC_KASPERSKY_OPENTIP_KEY or KASPERSKY_OPENTIP_KEY
# ASCII-only for Windows PowerShell 5.1.
param(
    [Parameter(Mandatory = $true)][string]$ExePath,
    [string]$OutDir = ""
)

$ErrorActionPreference = "Continue"
if (-not (Test-Path -LiteralPath $ExePath)) { throw "EXE missing: $ExePath" }

$Root = Split-Path $PSScriptRoot -Parent
$py = Join-Path $Root "python"
if (-not $OutDir) { $OutDir = Join-Path (Split-Path $ExePath -Parent) "fp_ci" }
New-Item -ItemType Directory -Path $OutDir -Force | Out-Null

# Map common CI secret names
if (-not $env:MAGIC_VT_API_KEY -and $env:VIRUSTOTAL_API_KEY) {
    $env:MAGIC_VT_API_KEY = $env:VIRUSTOTAL_API_KEY
}
if (-not $env:MAGIC_KASPERSKY_OPENTIP_KEY -and $env:KASPERSKY_OPENTIP_KEY) {
    $env:MAGIC_KASPERSKY_OPENTIP_KEY = $env:KASPERSKY_OPENTIP_KEY
}

# Headless CI: never open browser / mailto
$env:MAGIC_AV_OPEN_BROWSER = "0"
$env:MAGIC_AV_OPEN_MAIL = "0"
$env:LOCALAPPDATA = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { $env:TEMP }
$state = Join-Path $env:LOCALAPPDATA "Win11MagicUpgrade"
New-Item -ItemType Directory -Path $state -Force | Out-Null

$sha = (Get-FileHash -LiteralPath $ExePath -Algorithm SHA256).Hash.ToLowerInvariant()
Write-Host "=== CI AV TRUST (VirusTotal + Kaspersky OpenTIP) ===" -ForegroundColor Cyan
Write-Host ("EXE: {0}" -f $ExePath)
Write-Host ("SHA256: {0}" -f $sha)

$hasVt = [bool]($env:MAGIC_VT_API_KEY -and $env:MAGIC_VT_API_KEY.Trim().Length -ge 32)
$hasKp = [bool]($env:MAGIC_KASPERSKY_OPENTIP_KEY -and $env:MAGIC_KASPERSKY_OPENTIP_KEY.Trim().Length -ge 8)
Write-Host ("VirusTotal key: {0}" -f $(if ($hasVt) { "present" } else { "missing (skip upload)" }))
Write-Host ("Kaspersky OpenTIP key: {0}" -f $(if ($hasKp) { "present" } else { "missing (skip upload)" }))

# Always run PowerShell VT submit when key present (fast, no Python deps beyond stdlib already used)
$vtScript = Join-Path $PSScriptRoot "submit_virustotal.ps1"
if (Test-Path -LiteralPath $vtScript) {
    powershell -NoProfile -ExecutionPolicy Bypass -File $vtScript -ExePath $ExePath -VoteHarmless
}

# Python cloud declare (VT + OpenTIP + FP package) when python engine is available
$engine = Join-Path $py "engine\av_cloud.py"
if (Test-Path -LiteralPath $engine) {
    $env:MAGIC_CI_AV_EXE = (Resolve-Path -LiteralPath $ExePath).Path
    $tmpPy = Join-Path $OutDir "_ci_declare_av.py"
    @'
import json, os, sys
from pathlib import Path
sys.path.insert(0, os.environ.get("MAGIC_CI_AV_PYTHON", "."))
os.environ["MAGIC_AV_OPEN_BROWSER"] = "0"
os.environ["MAGIC_AV_OPEN_MAIL"] = "0"
from engine.av_cloud import declare_virustotal_and_kaspersky
exe = Path(os.environ["MAGIC_CI_AV_EXE"])
r = declare_virustotal_and_kaspersky(exe)
print("CI_AV_RESULT=" + json.dumps(r))
'@ | Set-Content -LiteralPath $tmpPy -Encoding ASCII
    $env:MAGIC_CI_AV_PYTHON = $py
    try {
        python $tmpPy
        Write-Host "Python cloud AV declare finished." -ForegroundColor Green
    } catch {
        Write-Host ("Python cloud AV declare note: {0}" -f $_.Exception.Message) -ForegroundColor Yellow
    }
} else {
    Write-Host "engine/av_cloud.py not found - VT-only path used." -ForegroundColor Yellow
}

# Summary artifact for the release job
$summary = @{
    sha256 = $sha
    exe = (Split-Path $ExePath -Leaf)
    virustotal_key = $hasVt
    kaspersky_opentip_key = $hasKp
    virustotal_url = ("https://www.virustotal.com/gui/file/{0}" -f $sha)
    opentip_url = ("https://opentip.kaspersky.com/{0}/results" -f $sha)
    note = "AV trust runs in GitHub Actions Release only; not in the desktop app / One-Click."
}
$summaryPath = Join-Path $OutDir "AV_TRUST_CI.json"
($summary | ConvertTo-Json) | Set-Content -LiteralPath $summaryPath -Encoding ASCII
Copy-Item -LiteralPath $summaryPath -Destination (Join-Path (Split-Path $ExePath -Parent) "AV_TRUST_CI.json") -Force
Write-Host ("Wrote {0}" -f $summaryPath) -ForegroundColor Green
Write-Host ("VT: {0}" -f $summary.virustotal_url)
Write-Host ("OpenTIP: {0}" -f $summary.opentip_url)
Write-Host "ci_av_trust=done"
exit 0
