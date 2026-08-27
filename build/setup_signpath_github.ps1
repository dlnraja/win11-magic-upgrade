# Push SignPath Foundation secrets/vars to GitHub (dlnraja/win11-magic-upgrade).
# Run AFTER SignPath OSS approval when you have org id + API token.
# ASCII-only for Windows PowerShell 5.1.
param(
    [Parameter(Mandatory = $true)][string]$ApiToken,
    [Parameter(Mandatory = $true)][string]$OrganizationId,
    [string]$ProjectSlug = "win11-magic-upgrade",
    [string]$SigningPolicySlug = "release-signing",
    [string]$Repo = "dlnraja/win11-magic-upgrade"
)

$ErrorActionPreference = "Stop"

Write-Host "Setting SignPath secret/vars on $Repo ..." -ForegroundColor Cyan
gh secret set SIGNPATH_API_TOKEN --repo $Repo --body $ApiToken
gh variable set SIGNPATH_ORGANIZATION_ID --repo $Repo --body $OrganizationId
gh variable set SIGNPATH_PROJECT_SLUG --repo $Repo --body $ProjectSlug
gh variable set SIGNPATH_SIGNING_POLICY_SLUG --repo $Repo --body $SigningPolicySlug

Write-Host ""
Write-Host "OK. Verify:" -ForegroundColor Green
gh secret list --repo $Repo
gh variable list --repo $Repo
Write-Host ""
Write-Host "Next: tag a new release (e.g. vX.Y.Z) so Release workflow signs via SignPath." -ForegroundColor Yellow
Write-Host "Docs: docs/SIGNPATH_APPLICATION.md  docs/CODE_SIGNING_POLICY.md"
