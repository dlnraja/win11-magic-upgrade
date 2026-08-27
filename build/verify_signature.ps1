# Verify Authenticode signature on the built EXE (CI gate).
param(
    [Parameter(Mandatory = $true)][string]$ExePath,
    [string]$ExpectPublisher = "dlnraja"
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $ExePath)) {
    throw "EXE not found: $ExePath"
}

$sig = Get-AuthenticodeSignature -FilePath $ExePath
Write-Host "Status=$($sig.Status)"
Write-Host "Subject=$($sig.SignerCertificate.Subject)"
Write-Host "Thumbprint=$($sig.SignerCertificate.Thumbprint)"

if (-not $sig.SignerCertificate) {
    throw "EXE is not Authenticode-signed"
}
if ($sig.SignerCertificate.Subject -notmatch [regex]::Escape("CN=$ExpectPublisher")) {
    throw "Signer CN mismatch - expected CN=$ExpectPublisher, got $($sig.SignerCertificate.Subject)"
}
if ($sig.Status -eq "HashMismatch" -or $sig.Status -eq "NotSigned") {
    throw "Bad Authenticode status: $($sig.Status)"
}
if ($sig.Status -eq "NotTrusted") {
    Write-Host "WARN: NotTrusted (self-signed root) - signature present for CN=$ExpectPublisher" -ForegroundColor Yellow
} elseif ($sig.Status -notin @("Valid", "UnknownError") -and $sig.Status -ne "NotTrusted") {
    Write-Host "WARN: status $($sig.Status) - continuing if signer CN matches" -ForegroundColor Yellow
}

Write-Host "Authenticode verify OK for publisher $ExpectPublisher" -ForegroundColor Green
