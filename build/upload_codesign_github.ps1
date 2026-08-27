# Upload a real code-signing .pfx to GitHub Actions secrets using your logged-in gh account.
# Never commits the PFX. Requires: gh auth login (repo admin on the target).
#
# Example:
#   gh auth status
#   .\build\upload_codesign_github.ps1 -PfxPath C:\certs\codesign.pfx -Password '***'
#
# Then tag a release so CI signs with CODESIGN_PFX_BASE64 (SmartScreen path).
param(
    [Parameter(Mandatory = $true)][string]$PfxPath,
    [Parameter(Mandatory = $true)][string]$Password,
    [string]$Repo = "dlnraja/win11-magic-upgrade",
    [switch]$RequireTrustedChain
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "gh CLI not found. Install GitHub CLI and run: gh auth login"
}

$ghAuth = & gh auth status 2>&1 | Out-String
if ($LASTEXITCODE -ne 0) {
    throw "Not logged in to GitHub. Run: gh auth login"
}
Write-Host $ghAuth.Trim() -ForegroundColor DarkGray

$setup = Join-Path $PSScriptRoot "setup_codesign.ps1"
$setupArgs = @{
    PfxPath = $PfxPath
    Password = $Password
}
if ($RequireTrustedChain) { $setupArgs.RequireTrustedChain = $true }
& $setup @setupArgs
if ($LASTEXITCODE -ne 0) { throw "setup_codesign.ps1 failed" }

$full = (Resolve-Path -LiteralPath $PfxPath).Path
$b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($full))
if ($b64.Length -lt 80) { throw "PFX base64 too short - file looks empty" }

Write-Host ("Uploading secrets to {0} via your GitHub account..." -f $Repo) -ForegroundColor Cyan
$b64 | & gh secret set CODESIGN_PFX_BASE64 -R $Repo --body -
if ($LASTEXITCODE -ne 0) { throw "gh secret set CODESIGN_PFX_BASE64 failed" }

$Password | & gh secret set CODESIGN_PASSWORD -R $Repo --body -
if ($LASTEXITCODE -ne 0) { throw "gh secret set CODESIGN_PASSWORD failed" }

Write-Host "Secrets set: CODESIGN_PFX_BASE64 + CODESIGN_PASSWORD" -ForegroundColor Green
Write-Host "Next: tag a new release (or re-run the Release workflow) so the EXE is signed with this PFX." -ForegroundColor Cyan
Write-Host "Docs: docs/CODESIGN.md"
