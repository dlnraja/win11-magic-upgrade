# Push VirusTotal + Kaspersky OpenTIP secrets to GitHub (Release AV trust).
# ASCII-only, no BOM: Windows PowerShell 5.1 parses this file as the ANSI
# code page, and a UTF-8 em dash turns into a stray quote that fails ParseFile.
#
# Get keys:
#   VirusTotal:  https://www.virustotal.com/gui/my-apikey  (free API key)
#   OpenTIP:     https://opentip.kaspersky.com/  (API token after account)
#
# Usage:
#   .\build\setup_av_github.ps1 -VirusTotalApiKey "<vt>" -KasperskyOpenTipKey "<kp>"
#   # or from env:
#   $env:VIRUSTOTAL_API_KEY="..."; $env:MAGIC_KASPERSKY_OPENTIP_KEY="..."
#   .\build\setup_av_github.ps1
#   # optional: also write local av_keys.json + re-run last Release job
#   .\build\setup_av_github.ps1 -VirusTotalApiKey "..." -KasperskyOpenTipKey "..." -WriteLocal -RerunRelease
param(
    [string]$VirusTotalApiKey = "",
    [string]$KasperskyOpenTipKey = "",
    [string]$Repo = "dlnraja/win11-magic-upgrade",
    [switch]$WriteLocal,
    [switch]$RerunRelease,
    [switch]$NewPatchTag
)

$ErrorActionPreference = "Stop"

if (-not $VirusTotalApiKey) { $VirusTotalApiKey = $env:VIRUSTOTAL_API_KEY }
if (-not $VirusTotalApiKey) { $VirusTotalApiKey = $env:MAGIC_VT_API_KEY }
if (-not $KasperskyOpenTipKey) { $KasperskyOpenTipKey = $env:MAGIC_KASPERSKY_OPENTIP_KEY }
if (-not $KasperskyOpenTipKey) { $KasperskyOpenTipKey = $env:KASPERSKY_OPENTIP_KEY }

function Test-RealKey([string]$Value, [string]$Name, [int]$MinLen) {
    $v = ($Value | ForEach-Object { $_ }) 
    if (-not $v -or $v.Trim().Length -lt $MinLen) {
        throw "$Name missing or too short (min $MinLen). Get it from the portal, then re-run."
    }
    if ($v -match 'paste|<|>|your.?key|example|TODO|template') {
        throw "$Name still looks like a template placeholder - paste the real key."
    }
}

Test-RealKey $VirusTotalApiKey "VirusTotal API key" 32
Test-RealKey $KasperskyOpenTipKey "Kaspersky OpenTIP key" 8

Write-Host "Setting AV secrets on $Repo ..." -ForegroundColor Cyan
# Primary names used by release.yml + aliases accepted by ci_declare_av.ps1
gh secret set VIRUSTOTAL_API_KEY --repo $Repo --body $VirusTotalApiKey
gh secret set MAGIC_VT_API_KEY --repo $Repo --body $VirusTotalApiKey
gh secret set MAGIC_KASPERSKY_OPENTIP_KEY --repo $Repo --body $KasperskyOpenTipKey
gh secret set KASPERSKY_OPENTIP_KEY --repo $Repo --body $KasperskyOpenTipKey

if ($WriteLocal) {
    $state = Join-Path $env:LOCALAPPDATA "Win11MagicUpgrade"
    New-Item -ItemType Directory -Path $state -Force | Out-Null
    $keysPath = Join-Path $state "av_keys.json"
    $obj = @{
        virustotal         = $VirusTotalApiKey
        kaspersky_opentip  = $KasperskyOpenTipKey
        _comment           = "Local keys for optional offline declare; CI uses GitHub secrets."
    }
    ($obj | ConvertTo-Json) | Set-Content -LiteralPath $keysPath -Encoding UTF8
    Write-Host ("Wrote local keys: {0}" -f $keysPath) -ForegroundColor Green
}

Write-Host ""
Write-Host "OK. Secrets present (values hidden):" -ForegroundColor Green
gh secret list --repo $Repo

if ($NewPatchTag) {
    Push-Location (Split-Path $PSScriptRoot -Parent)
    try {
        $latest = (git tag -l "v*" --sort=-v:refname | Select-Object -First 1)
        if (-not $latest) { throw "No v* tag found" }
        if ($latest -notmatch '^v(\d+)\.(\d+)\.(\d+)$') { throw "Unexpected tag: $latest" }
        $maj = [int]$Matches[1]; $min = [int]$Matches[2]; $pat = [int]$Matches[3] + 1
        $next = "v$maj.$min.$pat"
        Write-Host "Creating tag $next (was $latest) ..." -ForegroundColor Yellow
        powershell -NoProfile -ExecutionPolicy Bypass -File .\build\sync_semver.ps1 -Version "$maj.$min.$pat"
        git add build/version_info.txt
        git commit -m "Bump version to $maj.$min.$pat for AV-trust release."
        git push origin HEAD
        git tag $next
        git push origin $next
        Write-Host "Pushed $next - Release workflow will submit VT + OpenTIP." -ForegroundColor Green
    } finally {
        Pop-Location
    }
}
elseif ($RerunRelease) {
    Write-Host "Re-running latest Release workflow..." -ForegroundColor Yellow
    $rid = gh run list --repo $Repo --workflow release.yml --limit 1 --json databaseId -q ".[0].databaseId"
    if (-not $rid) { throw "No release.yml run found" }
    gh run rerun $rid --repo $Repo
    Write-Host ("Reran run {0}. Watch: gh run watch {0} --repo {1}" -f $rid, $Repo) -ForegroundColor Green
}

Write-Host ""
Write-Host "VT portal: https://www.virustotal.com/gui/my-apikey"
Write-Host "OpenTIP:   https://opentip.kaspersky.com/"
Write-Host "Docs:      SECURITY.md"
