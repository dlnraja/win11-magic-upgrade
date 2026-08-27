# Prepare Authenticode material for CI/CD and local SmartScreen-ready signing.
# Priority:
#   1) MAGIC_CODESIGN_PFX already points to an existing .pfx file
#   2) Decode CODESIGN_PFX_BASE64 / MAGIC_CODESIGN_PFX_BASE64 into a temp .pfx
#   3) Else leave unset -> sign_exe.ps1 falls back to self-signed CN=dlnraja
#
# Passwords: MAGIC_CODESIGN_PASSWORD / CODESIGN_PASSWORD
param(
    [string]$OutDir = ""
)

$ErrorActionPreference = "Stop"

if (-not $OutDir) {
    if ($env:RUNNER_TEMP) {
        $OutDir = Join-Path $env:RUNNER_TEMP "codesign"
    } else {
        $OutDir = Join-Path ([System.IO.Path]::GetTempPath()) "win11magic-codesign"
    }
}
New-Item -ItemType Directory -Path $OutDir -Force | Out-Null

$githubEnv = $env:GITHUB_ENV
function Set-JobEnv([string]$Name, [string]$Value) {
    if ($githubEnv) {
        Add-Content -LiteralPath $githubEnv -Value ("{0}={1}" -f $Name, $Value)
    }
    Set-Item -Path "Env:$Name" -Value $Value
}

# Password (may be empty)
$pass = $env:MAGIC_CODESIGN_PASSWORD
if ($null -eq $pass -or "$pass" -eq "") { $pass = $env:CODESIGN_PASSWORD }
if ($null -eq $pass) { $pass = "" }

# 1) Existing path
$existing = $env:MAGIC_CODESIGN_PFX
if (-not $existing) { $existing = $env:CODESIGN_PFX }
if ($existing -and (Test-Path -LiteralPath $existing)) {
    $full = (Resolve-Path -LiteralPath $existing).Path
    Set-JobEnv "MAGIC_CODESIGN_PFX" $full
    Set-JobEnv "MAGIC_CODESIGN_PASSWORD" $pass
    Write-Host ("Using existing PFX path: {0}" -f $full) -ForegroundColor Green
    Write-Host "codesign_mode=pfx_path"
    exit 0
}

# 2) Base64 secret
$b64 = $env:CODESIGN_PFX_BASE64
if (-not $b64) { $b64 = $env:MAGIC_CODESIGN_PFX_BASE64 }

if ($b64 -and $b64.Trim().Length -gt 80) {
    $pfx = Join-Path $OutDir "dlnraja-codesign.pfx"
    Write-Host ("Decoding CODESIGN_PFX_BASE64 -> {0}" -f $pfx) -ForegroundColor Cyan
    try {
        [IO.File]::WriteAllBytes($pfx, [Convert]::FromBase64String($b64.Trim()))
    } catch {
        throw ("Invalid CODESIGN_PFX_BASE64: {0}" -f $_.Exception.Message)
    }
    if (-not (Test-Path -LiteralPath $pfx) -or ((Get-Item $pfx).Length -lt 100)) {
        throw "Decoded PFX is missing or too small"
    }
    # Quick open probe
    try {
        $secure = ConvertTo-SecureString -String $pass -AsPlainText -Force
        $flags = [System.Security.Cryptography.X509Certificates.X509KeyStorageFlags]::Exportable `
            -bor [System.Security.Cryptography.X509Certificates.X509KeyStorageFlags]::UserKeySet
        $probe = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new($pfx, $secure, $flags)
        Write-Host ("PFX subject: {0}" -f $probe.Subject) -ForegroundColor DarkGray
        Write-Host ("PFX issuer:  {0}" -f $probe.Issuer) -ForegroundColor DarkGray
        if ($probe.Subject -eq $probe.Issuer) {
            Write-Host "WARN: decoded PFX looks self-signed (not ideal for SmartScreen)." -ForegroundColor Yellow
        }
    } catch {
        throw ("Decoded PFX cannot be opened with provided password: {0}" -f $_.Exception.Message)
    }
    Set-JobEnv "MAGIC_CODESIGN_PFX" $pfx
    Set-JobEnv "MAGIC_CODESIGN_PASSWORD" $pass
    Write-Host "Trusted/CI PFX ready for Authenticode (dlnraja publisher)." -ForegroundColor Green
    Write-Host "codesign_mode=pfx_base64"
    exit 0
}

Write-Host "No MAGIC_CODESIGN_PFX / CODESIGN_PFX_BASE64 - sign_exe.ps1 will use self-signed CN=dlnraja." -ForegroundColor Yellow
Write-Host "For SmartScreen: set MAGIC_CODESIGN_PFX + MAGIC_CODESIGN_PASSWORD (local) or CODESIGN_PFX_BASE64 secret (CI)." -ForegroundColor DarkGray
Write-Host "codesign_mode=selfsigned"
if ($githubEnv) {
    Add-Content -LiteralPath $githubEnv -Value 'MAGIC_CODESIGN_PFX='
}
