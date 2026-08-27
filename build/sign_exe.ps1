# Sign Win11MagicUpgrade.exe as publisher "dlnraja" (Authenticode).
# Uses MAGIC_CODESIGN_PFX + MAGIC_CODESIGN_PASSWORD if set (recommended for trusted CA certs).
# Otherwise creates/reuses a CurrentUser code-signing cert: CN=dlnraja.
param(
    [Parameter(Mandatory = $true)][string]$ExePath,
    [string]$Publisher = "dlnraja"
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $ExePath)) {
    throw "EXE not found: $ExePath"
}

function Get-PublisherCert {
    param([string]$Name)

    $pfxPath = $env:MAGIC_CODESIGN_PFX
    $pfxPass = $env:MAGIC_CODESIGN_PASSWORD
    if ($pfxPath -and (Test-Path -LiteralPath $pfxPath)) {
        Write-Host "Loading code-signing PFX: $pfxPath" -ForegroundColor Cyan
        $secure = if ($pfxPass) {
            ConvertTo-SecureString -String $pfxPass -AsPlainText -Force
        } else {
            New-Object System.Security.SecureString
        }
        return [System.Security.Cryptography.X509Certificates.X509Certificate2]::new(
            (Resolve-Path -LiteralPath $pfxPath).Path,
            $secure,
            [System.Security.Cryptography.X509Certificates.X509KeyStorageFlags]::Exportable
        )
    }

    $existing = Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Subject -match [regex]::Escape("CN=$Name") -and
            $_.NotAfter -gt (Get-Date) -and
            $_.HasPrivateKey
        } |
        Select-Object -First 1
    if ($existing) {
        Write-Host "Reusing code-signing cert: $($existing.Subject)" -ForegroundColor DarkGray
        return $existing
    }

    Write-Host "Creating self-signed code-signing certificate CN=$Name ..." -ForegroundColor Cyan
    return New-SelfSignedCertificate `
        -Type CodeSigningCert `
        -Subject "CN=$Name, O=$Name, C=BE" `
        -KeyExportPolicy Exportable `
        -KeySpec Signature `
        -KeyLength 2048 `
        -HashAlgorithm SHA256 `
        -CertStoreLocation "Cert:\CurrentUser\My" `
        -NotAfter (Get-Date).AddYears(5) `
        -FriendlyName "Win11 Magic Upgrade ($Name)"
}

$cert = Get-PublisherCert -Name $Publisher
$sig = Set-AuthenticodeSignature -FilePath $ExePath -Certificate $cert -TimestampServer "http://timestamp.digicert.com" -HashAlgorithm SHA256
if ($sig.Status -ne "Valid" -and $sig.Status -ne "UnknownError") {
    # UnknownError sometimes when timestamp server unreachable — retry without timestamp
    Write-Host "Timestamp may have failed ($($sig.Status)); retrying without timestamp..." -ForegroundColor Yellow
    $sig = Set-AuthenticodeSignature -FilePath $ExePath -Certificate $cert -HashAlgorithm SHA256
}

Write-Host "Authenticode status: $($sig.Status) | Publisher: $($sig.SignerCertificate.Subject)" -ForegroundColor Green
if ($sig.Status -notin @("Valid", "UnknownError")) {
    Write-Host "WARN: signature status $($sig.Status) — EXE still built; install a trusted PFX via MAGIC_CODESIGN_PFX for SmartScreen." -ForegroundColor Yellow
}

# Sidecar identity file for portable package
$meta = Join-Path (Split-Path $ExePath -Parent) "PUBLISHER.txt"
@"
Publisher: $Publisher
GitHub: https://github.com/dlnraja/win11-magic-upgrade
Signed: $(Get-Date -Format o)
Thumbprint: $($cert.Thumbprint)
Subject: $($cert.Subject)
Status: $($sig.Status)
"@ | Set-Content -LiteralPath $meta -Encoding UTF8
