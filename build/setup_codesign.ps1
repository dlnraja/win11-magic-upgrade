# Setup / validate a real code-signing .pfx for SmartScreen.
# Never commits the PFX. Can print base64 for GitHub Actions secrets.
#
# Examples:
#   .\build\setup_codesign.ps1 -PfxPath C:\certs\dlnraja.pfx -Password '***'
#   .\build\setup_codesign.ps1 -PfxPath C:\certs\dlnraja.pfx -Password '***' -ExportBase64
#   .\build\setup_codesign.ps1 -PfxPath C:\certs\dlnraja.pfx -Password '***' -SetUserEnv
param(
    [Parameter(Mandatory = $true)][string]$PfxPath,
    [string]$Password = "",
    [switch]$ExportBase64,
    [switch]$SetUserEnv,
    [switch]$RequireTrustedChain
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $PfxPath)) {
    throw ("PFX not found: {0}" -f $PfxPath)
}

$full = (Resolve-Path -LiteralPath $PfxPath).Path
$secure = ConvertTo-SecureString -String $Password -AsPlainText -Force
$flags = [System.Security.Cryptography.X509Certificates.X509KeyStorageFlags]::Exportable `
    -bor [System.Security.Cryptography.X509Certificates.X509KeyStorageFlags]::UserKeySet

try {
    $cert = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new($full, $secure, $flags)
} catch {
    throw ("Cannot open PFX (wrong password?): {0}" -f $_.Exception.Message)
}

Write-Host "=== Code-signing PFX check ===" -ForegroundColor Cyan
Write-Host ("Path:        {0}" -f $full)
Write-Host ("Subject:     {0}" -f $cert.Subject)
Write-Host ("Issuer:      {0}" -f $cert.Issuer)
Write-Host ("Thumbprint:  {0}" -f $cert.Thumbprint)
Write-Host ("NotAfter:    {0}" -f $cert.NotAfter)
Write-Host ("HasPrivKey:  {0}" -f $cert.HasPrivateKey)

if (-not $cert.HasPrivateKey) { throw "PFX has no private key" }
if ($cert.NotAfter -le (Get-Date)) { throw "PFX is expired" }

$self = ($cert.Subject -eq $cert.Issuer)
$chain = New-Object System.Security.Cryptography.X509Certificates.X509Chain
$chain.ChainPolicy.RevocationMode = [System.Security.Cryptography.X509Certificates.X509RevocationMode]::NoCheck
$chainOk = $chain.Build($cert)
$smart = ((-not $self) -and $chainOk -and $cert.HasPrivateKey)

Write-Host ("SelfSigned:  {0}" -f $self)
Write-Host ("ChainOK:     {0}" -f $chainOk)
Write-Host ("SmartScreen: {0}" -f $(if ($smart) { "READY (CA-trusted PFX)" } else { "NOT READY (buy OV/EV from DigiCert/Sectigo/SSL.com)" }))

if ($RequireTrustedChain -and -not $smart) {
    throw "RequireTrustedChain failed - PFX is not CA-trusted"
}

if ($SetUserEnv) {
    [Environment]::SetEnvironmentVariable("MAGIC_CODESIGN_PFX", $full, "User")
    [Environment]::SetEnvironmentVariable("MAGIC_CODESIGN_PASSWORD", $Password, "User")
    $env:MAGIC_CODESIGN_PFX = $full
    $env:MAGIC_CODESIGN_PASSWORD = $Password
    Write-Host "User env set: MAGIC_CODESIGN_PFX + MAGIC_CODESIGN_PASSWORD (new terminals inherit)." -ForegroundColor Green
}

if ($ExportBase64) {
    $b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($full))
    Write-Host ""
    Write-Host "GitHub Actions secrets (Settings -> Secrets -> Actions):" -ForegroundColor Cyan
    Write-Host "  Name: CODESIGN_PFX_BASE64"
    Write-Host "  Value: <paste base64 below - do NOT commit>"
    Write-Host "  Name: CODESIGN_PASSWORD"
    Write-Host "  Value: <your PFX password>"
    Write-Host ""
    Write-Host "-----BEGIN CODESIGN_PFX_BASE64-----"
    Write-Host $b64
    Write-Host "-----END CODESIGN_PFX_BASE64-----"
    $out = Join-Path ([IO.Path]::GetTempPath()) "codesign-pfx-base64.txt"
    Set-Content -LiteralPath $out -Value $b64 -Encoding ASCII
    Write-Host ("Also wrote: {0} (delete after pasting into GitHub)" -f $out) -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Local build:" -ForegroundColor Cyan
Write-Host ('  $env:MAGIC_CODESIGN_PFX = "{0}"' -f $full)
Write-Host '  $env:MAGIC_CODESIGN_PASSWORD = "***"'
Write-Host "  powershell -File .\build\build_exe.ps1"
Write-Host ""
Write-Host "Docs: docs/CODESIGN.md"
