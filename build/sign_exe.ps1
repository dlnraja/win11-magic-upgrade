# Sign Win11MagicUpgrade.exe as publisher "dlnraja" (Authenticode).
# Priority:
#   1) MAGIC_CODESIGN_PFX (+ MAGIC_CODESIGN_PASSWORD) - trusted/CI PFX
#   2) CODESIGN_PFX_BASE64 decoded by ci_prepare_codesign.ps1 into MAGIC_CODESIGN_PFX
#   3) Self-signed CurrentUser cert CN=dlnraja
#
# MAGIC_REQUIRE_CODESIGN=1 -> fail if signature is not Valid.
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
$tsServers = @(
    "http://timestamp.digicert.com",
    "http://timestamp.sectigo.com",
    "http://timestamp.globalsign.com/tsa/r6advanced1"
)
$sig = $null
foreach ($ts in $tsServers) {
    try {
        Write-Host "Signing with timestamp: $ts" -ForegroundColor DarkGray
        $sig = Set-AuthenticodeSignature -FilePath $ExePath -Certificate $cert -TimestampServer $ts -HashAlgorithm SHA256
        if ($sig.Status -eq "Valid") { break }
        Write-Host "Status $($sig.Status) with $ts" -ForegroundColor Yellow
    } catch {
        Write-Host "Timestamp server failed: $ts - $($_.Exception.Message)" -ForegroundColor Yellow
    }
}
if (-not $sig -or $sig.Status -ne "Valid") {
    Write-Host "Retrying Authenticode without timestamp..." -ForegroundColor Yellow
    $sig = Set-AuthenticodeSignature -FilePath $ExePath -Certificate $cert -HashAlgorithm SHA256
}

$mode = if ($env:MAGIC_CODESIGN_PFX -and (Test-Path -LiteralPath $env:MAGIC_CODESIGN_PFX)) { "pfx" } else { "selfsigned" }
Write-Host "Authenticode status: $($sig.Status) | mode=$mode | Publisher: $($sig.SignerCertificate.Subject)" -ForegroundColor Green

$require = $env:MAGIC_REQUIRE_CODESIGN
$signedOk = $sig.SignerCertificate -and ($sig.Status -in @("Valid", "UnknownError", "NotTrusted"))
if ($require -and $require.Trim().ToLower() -in @("1", "true", "yes")) {
    if (-not $signedOk) {
        throw "MAGIC_REQUIRE_CODESIGN=1 but signature status is $($sig.Status)"
    }
    if ($sig.SignerCertificate.Subject -notmatch [regex]::Escape("CN=$Publisher")) {
        throw "MAGIC_REQUIRE_CODESIGN=1 but signer CN is $($sig.SignerCertificate.Subject)"
    }
} elseif (-not $signedOk) {
    Write-Host "WARN: signature status $($sig.Status)" -ForegroundColor Yellow
}

$meta = Join-Path (Split-Path $ExePath -Parent) "PUBLISHER.txt"
$tsSubject = ""
if ($sig.TimeStamperCertificate) { $tsSubject = $sig.TimeStamperCertificate.Subject }
@(
    "Publisher: $Publisher"
    "GitHub: https://github.com/dlnraja/win11-magic-upgrade"
    "Signed: $(Get-Date -Format o)"
    "Mode: $mode"
    "Thumbprint: $($cert.Thumbprint)"
    "Subject: $($cert.Subject)"
    "Status: $($sig.Status)"
    "Timestamp: $tsSubject"
) | Set-Content -LiteralPath $meta -Encoding UTF8

$jsonPath = Join-Path (Split-Path $ExePath -Parent) "PUBLISHER.json"
$payload = [ordered]@{
    publisher = $Publisher
    mode = $mode
    status = [string]$sig.Status
    thumbprint = $cert.Thumbprint
    subject = $cert.Subject
    exe = (Resolve-Path -LiteralPath $ExePath).Path
}
($payload | ConvertTo-Json) | Set-Content -LiteralPath $jsonPath -Encoding UTF8
