# Synchronise semver across version_info.txt and tag hint.
# Usage:
#   .\build\sync_semver.ps1 -Version 1.38.0
# ASCII-only for Windows PowerShell 5.1.
param(
    [Parameter(Mandatory = $true)][string]$Version
)

$ErrorActionPreference = "Stop"
if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    throw "Version must be X.Y.Z"
}
$parts = $Version.Split('.')
$tuple = "($($parts[0]), $($parts[1]), $($parts[2]), 0)"
$dot = "$Version.0"
$Root = Split-Path $PSScriptRoot -Parent
$vi = Join-Path $Root "build\version_info.txt"
if (-not (Test-Path $vi)) { throw "Missing $vi" }

$c = Get-Content -LiteralPath $vi -Raw
$c = [regex]::Replace($c, 'filevers=\([^)]+\)', "filevers=$tuple")
$c = [regex]::Replace($c, 'prodvers=\([^)]+\)', "prodvers=$tuple")
$c = [regex]::Replace($c, "FileVersion', '[^']+'", "FileVersion', '$dot'")
$c = [regex]::Replace($c, "ProductVersion', '[^']+'", "ProductVersion', '$dot'")
Set-Content -LiteralPath $vi -Value $c -Encoding UTF8
Write-Host "Updated $vi -> $Version"
Write-Host "Next: git tag v$Version && git push origin v$Version"
