# Sign Win11MagicUpgrade.exe as publisher "dlnraja" (Authenticode).
# Intelligent priority for SmartScreen-ready signatures:
#   1) MAGIC_CODESIGN_PFX (+ MAGIC_CODESIGN_PASSWORD) - real trusted .pfx path
#   2) CODESIGN_PFX / CODESIGN_PASSWORD aliases
#   3) PFX prepared by ci_prepare_codesign.ps1 from CODESIGN_PFX_BASE64
#   4) Self-signed CurrentUser CN=dlnraja (NOT SmartScreen-trusted)
#
# Env:
#   MAGIC_REQUIRE_CODESIGN=1           -> must be signed (Valid/UnknownError/NotTrusted OK for self-signed)
#   MAGIC_REQUIRE_TRUSTED_CODESIGN=1   -> must use CA-trusted PFX with Status=Valid (SmartScreen path)
param(
    [Parameter(Mandatory = $true)][string]$ExePath,
    [string]$Publisher = "dlnraja"
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $ExePath)) {
    throw "EXE not found: $ExePath"
}

function Resolve-PfxPath {
    foreach ($cand in @($env:MAGIC_CODESIGN_PFX, $env:CODESIGN_PFX)) {
        if ($cand -and $cand.Trim().Length -gt 0 -and (Test-Path -LiteralPath $cand.Trim())) {
            return (Resolve-Path -LiteralPath $cand.Trim()).Path
        }
    }
    return $null
}

function Resolve-PfxPassword {
    if ($env:MAGIC_CODESIGN_PASSWORD) { return [string]$env:MAGIC_CODESIGN_PASSWORD }
    if ($env:CODESIGN_PASSWORD) { return [string]$env:CODESIGN_PASSWORD }
    return ""
}

function Test-IsCodeSigningCert([System.Security.Cryptography.X509Certificates.X509Certificate2]$Cert) {
    if (-not $Cert) { return $false }
    if (-not $Cert.HasPrivateKey) { return $false }
    if ($Cert.NotAfter -le (Get-Date)) { return $false }
    # EKU: code signing 1.3.6.1.5.5.7.3.3 OR no EKU (often treated as all-purpose)
    foreach ($ext in $Cert.Extensions) {
        if ($ext.Oid.Value -eq "2.5.29.37") {
            $eku = New-Object System.Security.Cryptography.X509Certificates.X509EnhancedKeyUsageExtension $ext, $false
            $oids = @($eku.EnhancedKeyUsages | ForEach-Object { $_.Value })
            if ($oids.Count -eq 0) { return $true }
            if ($oids -contains "1.3.6.1.5.5.7.3.3") { return $true }
            return $false
        }
    }
    return $true
}

function Get-CertTrustInfo([System.Security.Cryptography.X509Certificates.X509Certificate2]$Cert) {
    $info = [ordered]@{
        chain_ok = $false
        self_signed = $false
        smartscreen_ready = $false
        chain_status = @()
        issuer = $Cert.Issuer
        subject = $Cert.Subject
    }
    try {
        $chain = New-Object System.Security.Cryptography.X509Certificates.X509Chain
        $chain.ChainPolicy.RevocationMode = [System.Security.Cryptography.X509Certificates.X509RevocationMode]::NoCheck
        $ok = $chain.Build($Cert)
        $info.chain_ok = [bool]$ok
        $info.chain_status = @($chain.ChainStatus | ForEach-Object { $_.Status.ToString() })
        if ($Cert.Subject -eq $Cert.Issuer) {
            $info.self_signed = $true
        }
        # SmartScreen-ready: private key + not self-signed + chain builds (CA present)
        $info.smartscreen_ready = [bool]($Cert.HasPrivateKey -and (-not $info.self_signed) -and $info.chain_ok)
    } catch {
        $info.chain_status = @("ChainError")
    }
    return $info
}

function Get-PublisherCert {
    param([string]$Name)

    $pfxPath = Resolve-PfxPath
    $pfxPass = Resolve-PfxPassword
    if ($pfxPath) {
        Write-Host ("Loading trusted/CI code-signing PFX: {0}" -f $pfxPath) -ForegroundColor Cyan
        $secure = ConvertTo-SecureString -String $pfxPass -AsPlainText -Force
        $flags = [System.Security.Cryptography.X509Certificates.X509KeyStorageFlags]::Exportable `
            -bor [System.Security.Cryptography.X509Certificates.X509KeyStorageFlags]::UserKeySet
        try {
            $cert = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new($pfxPath, $secure, $flags)
        } catch {
            throw ("Failed to open PFX '{0}': {1}. Check MAGIC_CODESIGN_PFX / MAGIC_CODESIGN_PASSWORD." -f $pfxPath, $_.Exception.Message)
        }
        if (-not (Test-IsCodeSigningCert $cert)) {
            throw ("PFX is not a usable code-signing certificate (EKU/private key/expiry): {0}" -f $cert.Subject)
        }
        $trust = Get-CertTrustInfo $cert
        if ($trust.self_signed) {
            Write-Host "WARN: PFX appears self-signed - SmartScreen will still warn." -ForegroundColor Yellow
        } elseif ($trust.smartscreen_ready) {
            Write-Host "PFX chain OK - SmartScreen-ready publisher certificate." -ForegroundColor Green
        } else {
            Write-Host ("WARN: PFX chain status: {0}" -f ($trust.chain_status -join ",")) -ForegroundColor Yellow
        }
        return @{ Cert = $cert; Mode = "pfx"; Trust = $trust }
    }

    $requireTrusted = $env:MAGIC_REQUIRE_TRUSTED_CODESIGN
    if ($requireTrusted -and $requireTrusted.Trim().ToLower() -in @("1", "true", "yes")) {
        throw "MAGIC_REQUIRE_TRUSTED_CODESIGN=1 but MAGIC_CODESIGN_PFX is missing. Place a real OV/EV .pfx and set MAGIC_CODESIGN_PFX + MAGIC_CODESIGN_PASSWORD."
    }

    $existing = Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Subject -match [regex]::Escape("CN=$Name") -and
            $_.NotAfter -gt (Get-Date) -and
            $_.HasPrivateKey
        } |
        Select-Object -First 1
    if ($existing) {
        Write-Host ("Reusing local code-signing cert: {0}" -f $existing.Subject) -ForegroundColor DarkGray
        $trust = Get-CertTrustInfo $existing
        $mode = if ($trust.self_signed) { "selfsigned" } else { "store" }
        return @{ Cert = $existing; Mode = $mode; Trust = $trust }
    }

    Write-Host ("Creating self-signed code-signing certificate CN={0} (NOT SmartScreen-trusted)..." -f $Name) -ForegroundColor Yellow
    $created = New-SelfSignedCertificate `
        -Type CodeSigningCert `
        -Subject ("CN={0}, O={0}, C=BE" -f $Name) `
        -KeyExportPolicy Exportable `
        -KeySpec Signature `
        -KeyLength 2048 `
        -HashAlgorithm SHA256 `
        -CertStoreLocation "Cert:\CurrentUser\My" `
        -NotAfter (Get-Date).AddYears(5) `
        -FriendlyName ("Win11 Magic Upgrade ({0})" -f $Name)
    $trust = Get-CertTrustInfo $created
    return @{ Cert = $created; Mode = "selfsigned"; Trust = $trust }
}

$bundle = Get-PublisherCert -Name $Publisher
$cert = $bundle.Cert
$mode = $bundle.Mode
$trust = $bundle.Trust

$tsServers = @(
    "http://timestamp.digicert.com",
    "http://timestamp.sectigo.com",
    "http://timestamp.globalsign.com/tsa/r6advanced1",
    "http://timestamp.apple.com/ts01"
)
$sig = $null
$usedTs = ""
foreach ($ts in $tsServers) {
    try {
        Write-Host ("Signing with timestamp: {0}" -f $ts) -ForegroundColor DarkGray
        $sig = Set-AuthenticodeSignature -FilePath $ExePath -Certificate $cert -TimestampServer $ts -HashAlgorithm SHA256
        if ($sig.Status -eq "Valid") {
            $usedTs = $ts
            break
        }
        Write-Host ("Status {0} with {1}" -f $sig.Status, $ts) -ForegroundColor Yellow
    } catch {
        Write-Host ("Timestamp server failed: {0} - {1}" -f $ts, $_.Exception.Message) -ForegroundColor Yellow
    }
}
if (-not $sig -or $sig.Status -ne "Valid") {
    Write-Host "Retrying Authenticode without timestamp..." -ForegroundColor Yellow
    $sig = Set-AuthenticodeSignature -FilePath $ExePath -Certificate $cert -HashAlgorithm SHA256
}

$smartReady = [bool]($trust.smartscreen_ready -and $sig.Status -eq "Valid" -and $usedTs)
Write-Host ("Authenticode status: {0} | mode={1} | smartscreen_ready={2} | Publisher: {3}" -f `
    $sig.Status, $mode, $smartReady, $sig.SignerCertificate.Subject) -ForegroundColor $(if ($smartReady) { "Green" } else { "Yellow" })

$require = $env:MAGIC_REQUIRE_CODESIGN
$requireTrusted = $env:MAGIC_REQUIRE_TRUSTED_CODESIGN
$signedOk = $sig.SignerCertificate -and ($sig.Status -in @("Valid", "UnknownError", "NotTrusted"))

if ($requireTrusted -and $requireTrusted.Trim().ToLower() -in @("1", "true", "yes")) {
    if ($mode -eq "selfsigned" -or -not $trust.smartscreen_ready) {
        throw "MAGIC_REQUIRE_TRUSTED_CODESIGN=1 requires a CA-trusted PFX (MAGIC_CODESIGN_PFX). Current mode=$mode"
    }
    if ($sig.Status -ne "Valid") {
        throw ("MAGIC_REQUIRE_TRUSTED_CODESIGN=1 but signature status is {0} (want Valid)" -f $sig.Status)
    }
}

if ($require -and $require.Trim().ToLower() -in @("1", "true", "yes")) {
    if (-not $signedOk) {
        throw ("MAGIC_REQUIRE_CODESIGN=1 but signature status is {0}" -f $sig.Status)
    }
    if ($sig.SignerCertificate.Subject -notmatch [regex]::Escape("CN=$Publisher")) {
        throw ("MAGIC_REQUIRE_CODESIGN=1 but signer CN is {0}" -f $sig.SignerCertificate.Subject)
    }
} elseif (-not $signedOk) {
    Write-Host ("WARN: signature status {0}" -f $sig.Status) -ForegroundColor Yellow
}

$meta = Join-Path (Split-Path $ExePath -Parent) "PUBLISHER.txt"
$tsSubject = ""
if ($sig.TimeStamperCertificate) { $tsSubject = $sig.TimeStamperCertificate.Subject }
@(
    "Publisher: $Publisher"
    "GitHub: https://github.com/dlnraja/win11-magic-upgrade"
    "Signed: $(Get-Date -Format o)"
    "Mode: $mode"
    "SmartScreenReady: $smartReady"
    "Thumbprint: $($cert.Thumbprint)"
    "Subject: $($cert.Subject)"
    "Issuer: $($cert.Issuer)"
    "Status: $($sig.Status)"
    "Timestamp: $tsSubject"
    "TimestampServer: $usedTs"
    "PFX: $(if (Resolve-PfxPath) { 'yes' } else { 'no' })"
) | Set-Content -LiteralPath $meta -Encoding ASCII

$jsonPath = Join-Path (Split-Path $ExePath -Parent) "PUBLISHER.json"
$payload = [ordered]@{
    publisher = $Publisher
    mode = $mode
    status = [string]$sig.Status
    smartscreen_ready = $smartReady
    self_signed = [bool]$trust.self_signed
    chain_ok = [bool]$trust.chain_ok
    thumbprint = $cert.Thumbprint
    subject = $cert.Subject
    issuer = $cert.Issuer
    timestamp_server = $usedTs
    pfx_configured = [bool](Resolve-PfxPath)
    exe = (Resolve-Path -LiteralPath $ExePath).Path
}
($payload | ConvertTo-Json) | Set-Content -LiteralPath $jsonPath -Encoding ASCII

if (-not $smartReady) {
    Write-Host "Tip: for SmartScreen, set MAGIC_CODESIGN_PFX + MAGIC_CODESIGN_PASSWORD to a real OV/EV .pfx (see docs/CODESIGN.md)." -ForegroundColor DarkGray
}
