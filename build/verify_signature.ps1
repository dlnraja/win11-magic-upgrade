# Verify Authenticode signature on the built EXE (CI gate).
# With -RequireTrusted / MAGIC_REQUIRE_TRUSTED_CODESIGN=1: Status must be Valid
# (CA-trusted chain) - rejects self-signed NotTrusted for SmartScreen releases.
param(
    [Parameter(Mandatory = $true)][string]$ExePath,
    [string]$ExpectPublisher = "dlnraja",
    # Extra CN substrings accepted (e.g. SignPath Foundation after OSS signing)
    [string[]]$AlsoAllowCn = @(),
    # Skip CN check; only Status / trusted-chain gates apply
    [switch]$AnyPublisher,
    [switch]$RequireTrusted
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $ExePath)) {
    throw "EXE not found: $ExePath"
}

$envTrusted = $env:MAGIC_REQUIRE_TRUSTED_CODESIGN
if ($envTrusted -and $envTrusted.Trim().ToLower() -in @("1", "true", "yes")) {
    $RequireTrusted = $true
}

$sig = Get-AuthenticodeSignature -FilePath $ExePath
Write-Host ("Status={0}" -f $sig.Status)
Write-Host ("Subject={0}" -f $sig.SignerCertificate.Subject)
Write-Host ("Issuer={0}" -f $sig.SignerCertificate.Issuer)
Write-Host ("Thumbprint={0}" -f $sig.SignerCertificate.Thumbprint)
if ($sig.TimeStamperCertificate) {
    Write-Host ("Timestamp={0}" -f $sig.TimeStamperCertificate.Subject)
}

if (-not $sig.SignerCertificate) {
    throw "EXE is not Authenticode-signed"
}
if (-not $AnyPublisher) {
    $subj = [string]$sig.SignerCertificate.Subject
    $cnOk = ($ExpectPublisher -and ($subj -match [regex]::Escape("CN=$ExpectPublisher")))
    foreach ($alt in $AlsoAllowCn) {
        if ($alt -and ($subj -match [regex]::Escape($alt))) { $cnOk = $true }
    }
    if (-not $cnOk) {
        throw ("Signer CN mismatch - expected CN={0} (or AlsoAllowCn), got {1}" -f $ExpectPublisher, $subj)
    }
}
if ($sig.Status -eq "HashMismatch" -or $sig.Status -eq "NotSigned") {
    throw ("Bad Authenticode status: {0}" -f $sig.Status)
}

$selfSigned = ($sig.SignerCertificate.Subject -eq $sig.SignerCertificate.Issuer)
$smartReady = (($sig.Status -eq "Valid") -and (-not $selfSigned))

if ($RequireTrusted) {
    if ($selfSigned) {
        throw "RequireTrusted: signer is self-signed - set MAGIC_CODESIGN_PFX to a real OV/EV .pfx"
    }
    if ($sig.Status -ne "Valid") {
        throw ("RequireTrusted: want Status=Valid for SmartScreen, got {0}" -f $sig.Status)
    }
    Write-Host "SmartScreen-ready: Valid CA-trusted Authenticode signature." -ForegroundColor Green
} elseif ($sig.Status -eq "NotTrusted") {
    Write-Host "WARN: NotTrusted (self-signed root) - signature present for CN=$ExpectPublisher" -ForegroundColor Yellow
    Write-Host "      SmartScreen will warn until a trusted PFX is configured (docs/CODESIGN.md)." -ForegroundColor DarkGray
} elseif ($sig.Status -notin @("Valid", "UnknownError")) {
    Write-Host ("WARN: status {0} - continuing if signer CN matches" -f $sig.Status) -ForegroundColor Yellow
}

# Mirror into PUBLISHER.json if present
$jsonPath = Join-Path (Split-Path $ExePath -Parent) "PUBLISHER.json"
if (Test-Path -LiteralPath $jsonPath) {
    try {
        $obj = Get-Content -LiteralPath $jsonPath -Raw | ConvertFrom-Json
        $obj | Add-Member -NotePropertyName verify_status -NotePropertyValue ([string]$sig.Status) -Force
        $obj | Add-Member -NotePropertyName verify_smartscreen_ready -NotePropertyValue $smartReady -Force
        ($obj | ConvertTo-Json) | Set-Content -LiteralPath $jsonPath -Encoding ASCII
    } catch { }
}

Write-Host ("Authenticode verify OK (publisher_gate={0}, smartscreen_ready={1})" -f $(if ($AnyPublisher) { "any" } else { $ExpectPublisher }), $smartReady) -ForegroundColor Green
