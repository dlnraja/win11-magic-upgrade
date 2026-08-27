# Prepare Authenticode material for CI/CD.
# Prefer GitHub Actions secrets (never commit PFX files):
#   CODESIGN_PFX_BASE64   - base64 of the .pfx (trusted CA recommended)
#   CODESIGN_PASSWORD     - PFX password
# Aliases: MAGIC_CODESIGN_PFX_BASE64 / MAGIC_CODESIGN_PASSWORD
#
# If no secret is set, leaves MAGIC_CODESIGN_PFX unset so sign_exe.ps1
# creates/reuses a self-signed CN=dlnraja cert (still signed, not SmartScreen-trusted).
param(
    [string]$OutDir = ""
)

$ErrorActionPreference = "Stop"

if (-not $OutDir) {
    $OutDir = Join-Path $env:RUNNER_TEMP "codesign"
    if (-not $OutDir -or $OutDir -eq "codesign") {
        $OutDir = Join-Path ([System.IO.Path]::GetTempPath()) "win11magic-codesign"
    }
}
New-Item -ItemType Directory -Path $OutDir -Force | Out-Null

$b64 = $env:CODESIGN_PFX_BASE64
if (-not $b64) { $b64 = $env:MAGIC_CODESIGN_PFX_BASE64 }
$pass = $env:CODESIGN_PASSWORD
if (-not $pass) { $pass = $env:MAGIC_CODESIGN_PASSWORD }

$githubEnv = $env:GITHUB_ENV
function Set-JobEnv([string]$Name, [string]$Value) {
    if ($githubEnv) {
        Add-Content -LiteralPath $githubEnv -Value ("{0}={1}" -f $Name, $Value)
    }
    Set-Item -Path "Env:$Name" -Value $Value
}

if ($b64 -and $b64.Trim().Length -gt 80) {
    $pfx = Join-Path $OutDir "dlnraja-codesign.pfx"
    Write-Host ("Decoding CODESIGN_PFX_BASE64 -> {0}" -f $pfx) -ForegroundColor Cyan
    [IO.File]::WriteAllBytes($pfx, [Convert]::FromBase64String($b64.Trim()))
    if (-not (Test-Path -LiteralPath $pfx) -or ((Get-Item $pfx).Length -lt 100)) {
        throw "Decoded PFX is missing or too small"
    }
    Set-JobEnv "MAGIC_CODESIGN_PFX" $pfx
    if ($pass) {
        Set-JobEnv "MAGIC_CODESIGN_PASSWORD" $pass
    }
    Write-Host "Trusted/CI PFX ready for Authenticode (dlnraja publisher)." -ForegroundColor Green
    Write-Host "codesign_mode=pfx"
} else {
    Write-Host "No CODESIGN_PFX_BASE64 secret - sign_exe.ps1 will use self-signed CN=dlnraja." -ForegroundColor Yellow
    Write-Host "codesign_mode=selfsigned"
    # Clear stale paths (empty value)
    if ($githubEnv) {
        Add-Content -LiteralPath $githubEnv -Value 'MAGIC_CODESIGN_PFX='
    }
}
