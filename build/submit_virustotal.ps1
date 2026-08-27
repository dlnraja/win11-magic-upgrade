# Submit signed EXE to VirusTotal (optional CI secret VIRUSTOTAL_API_KEY / MAGIC_VT_API_KEY).
# Builds reputation / community votes; does not replace a trusted OV/EV code-signing cert.
param(
    [Parameter(Mandatory = $true)][string]$ExePath,
    [switch]$VoteHarmless
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $ExePath)) { throw "EXE missing: $ExePath" }

$key = $env:VIRUSTOTAL_API_KEY
if (-not $key) { $key = $env:MAGIC_VT_API_KEY }
if (-not $key -or $key.Trim().Length -lt 32) {
    Write-Host "No VIRUSTOTAL_API_KEY — skip VT submit (optional)." -ForegroundColor Yellow
    Write-Host "vt_submit=skipped"
    exit 0
}

$sha = (Get-FileHash -LiteralPath $ExePath -Algorithm SHA256).Hash.ToLowerInvariant()
Write-Host "VirusTotal submit sha256=$sha" -ForegroundColor Cyan
$headers = @{ "x-apikey" = $key.Trim() }

# Prefer large-file upload URL
$uploadUrl = "https://www.virustotal.com/api/v3/files"
try {
    $u = Invoke-RestMethod -Method GET -Uri "https://www.virustotal.com/api/v3/files/upload_url" -Headers $headers
    if ($u.data) { $uploadUrl = [string]$u.data }
} catch {
    Write-Host "upload_url fallback to /files: $($_.Exception.Message)" -ForegroundColor DarkGray
}

$form = @{ file = Get-Item -LiteralPath $ExePath }
try {
    $resp = Invoke-RestMethod -Method POST -Uri $uploadUrl -Headers $headers -Form $form
    Write-Host "VT upload OK: $($resp.data.id)" -ForegroundColor Green
} catch {
    Write-Host "VT upload failed: $($_.Exception.Message)" -ForegroundColor Yellow
    Write-Host "vt_submit=upload_fail"
    exit 0  # do not fail the release
}

if ($VoteHarmless) {
    $voteBody = '{"data":{"type":"vote","attributes":{"verdict":"harmless"}}}'
    try {
        Invoke-RestMethod -Method POST `
            -Uri "https://www.virustotal.com/api/v3/files/$sha/votes" `
            -Headers (@{ "x-apikey" = $key.Trim(); "Content-Type" = "application/json" }) `
            -Body $voteBody | Out-Null
        Write-Host "VT voted harmless" -ForegroundColor Green
    } catch {
        Write-Host "VT vote skipped: $($_.Exception.Message)" -ForegroundColor DarkGray
    }
    $comment = @"
{"data":{"type":"comment","attributes":{"text":"Win11 Magic Upgrade by dlnraja — open-source MIT Windows 11 migration helper (PyInstaller). Not malware. Source: https://github.com/dlnraja/win11-magic-upgrade — false positive report welcome."}}}
"@
    try {
        Invoke-RestMethod -Method POST `
            -Uri "https://www.virustotal.com/api/v3/files/$sha/comments" `
            -Headers (@{ "x-apikey" = $key.Trim(); "Content-Type" = "application/json" }) `
            -Body $comment | Out-Null
        Write-Host "VT FP comment posted" -ForegroundColor Green
    } catch {
        Write-Host "VT comment skipped: $($_.Exception.Message)" -ForegroundColor DarkGray
    }
}

Write-Host "https://www.virustotal.com/gui/file/$sha"
Write-Host "vt_submit=ok"
