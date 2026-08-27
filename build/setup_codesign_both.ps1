# Dual Authenticode path: Option A (your OV/EV .pfx) + Option B (SignPath Foundation).
#
# Status check (no secrets printed):
#   .\build\setup_codesign_both.ps1
#
# After you buy a .pfx (Option A):
#   .\build\upload_codesign_github.ps1 -PfxPath C:\path\to\codesign.pfx -Password '***' -RequireTrustedChain
#
# After SignPath OSS approval (Option B):
#   .\build\setup_signpath_github.ps1 -ApiToken '...' -OrganizationId '...'
#
# Release workflow uses A during build, then B re-signs when configured (final SmartScreen cert).
# ASCII-only for Windows PowerShell 5.1.

param(
    [string]$Repo = "dlnraja/win11-magic-upgrade"
)

$ErrorActionPreference = "Continue"
Write-Host "=== Option A + B code-signing status ($Repo) ===" -ForegroundColor Cyan

$secrets = @()
try { $secrets = @(gh secret list --repo $Repo 2>$null) } catch {}
$vars = @()
try { $vars = @(gh variable list --repo $Repo 2>$null) } catch {}

$hasA = ($secrets | Out-String) -match "CODESIGN_PFX_BASE64"
$hasBToken = ($secrets | Out-String) -match "SIGNPATH_API_TOKEN"
$hasBOrg = ($vars | Out-String) -match "SIGNPATH_ORGANIZATION_ID"
$hasBProj = ($vars | Out-String) -match "SIGNPATH_PROJECT_SLUG"
$hasBPol = ($vars | Out-String) -match "SIGNPATH_SIGNING_POLICY_SLUG"
$hasB = $hasBToken -and $hasBOrg -and $hasBProj -and $hasBPol

Write-Host ("Option A (PFX secrets):     {0}" -f $(if ($hasA) { "CONFIGURED" } else { "MISSING - buy OV/EV .pfx then upload_codesign_github.ps1" }))
Write-Host ("Option B (SignPath):        {0}" -f $(if ($hasB) { "CONFIGURED" } else { "MISSING - apply https://signpath.org/apply.html then setup_signpath_github.ps1" }))
Write-Host ("  SIGNPATH_API_TOKEN:       {0}" -f $(if ($hasBToken) { "yes" } else { "no" }))
Write-Host ("  SIGNPATH_ORGANIZATION_ID: {0}" -f $(if ($hasBOrg) { "yes" } else { "no" }))
Write-Host ("  SIGNPATH_PROJECT_SLUG:    {0}" -f $(if ($hasBProj) { "yes" } else { "no" }))
Write-Host ("  SIGNPATH_SIGNING_POLICY:  {0}" -f $(if ($hasBPol) { "yes" } else { "no" }))

$localPfx = [Environment]::GetEnvironmentVariable("MAGIC_CODESIGN_PFX", "User")
Write-Host ("Local MAGIC_CODESIGN_PFX:   {0}" -f $(if ($localPfx) { $localPfx } else { "(not set)" }))

Write-Host ""
Write-Host "Buy Option A (examples):" -ForegroundColor Yellow
Write-Host "  https://www.ssl.com/certificates/code-signing/"
Write-Host "  https://sectigo.com/ssl-certificates-tls/code-signing"
Write-Host "  https://www.digicert.com/signing/code-signing-certificates"
Write-Host "Apply Option B: https://signpath.org/apply.html"
Write-Host "Docs: docs/CODESIGN.md  docs/SIGNPATH_APPLICATION.md  docs/CODE_SIGNING_POLICY.md"

if ($hasA -and $hasB) {
    Write-Host ""
    Write-Host "Both configured. Tag a v* release: CI signs with A then re-signs with B." -ForegroundColor Green
    exit 0
}
if (-not $hasA -and -not $hasB) {
    Write-Host ""
    Write-Host "Neither path ready - releases stay self-signed (SmartScreen warns)." -ForegroundColor Yellow
    exit 2
}
exit 1
