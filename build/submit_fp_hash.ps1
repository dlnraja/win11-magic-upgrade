# Vote HARMLESS + post FP comment on VirusTotal for an existing sample hash.
# Uses MAGIC_VT_API_KEY / VIRUSTOTAL_API_KEY or %LOCALAPPDATA%\Win11MagicUpgrade\av_keys.json
# ASCII-only for Windows PowerShell 5.1.
param(
    [Parameter(Mandatory = $true)][string]$Sha256
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
$env:MAGIC_AV_OPEN_BROWSER = if ($env:MAGIC_AV_OPEN_BROWSER) { $env:MAGIC_AV_OPEN_BROWSER } else { "0" }
$env:MAGIC_AV_OPEN_MAIL = "0"

$sha = $Sha256.Trim().ToLowerInvariant()
if ($sha.Length -ne 64) { throw "SHA256 must be 64 hex chars" }

Write-Host ("VirusTotal FP by hash: {0}" -f $sha) -ForegroundColor Cyan
Write-Host ("https://www.virustotal.com/gui/file/{0}" -f $sha)

$py = Join-Path $Root "python"
$tmp = Join-Path $env:TEMP ("vt_fp_hash_" + [guid]::NewGuid().ToString("N") + ".py")
@'
import os, sys, json
sys.path.insert(0, os.environ["MAGIC_FP_PYTHON"])
os.environ["MAGIC_AV_OPEN_BROWSER"] = os.environ.get("MAGIC_AV_OPEN_BROWSER", "0")
os.environ["MAGIC_AV_OPEN_MAIL"] = "0"
from engine.av_cloud import declare_virustotal_by_hash
sha = os.environ["MAGIC_FP_SHA256"]
print("RESULT=" + declare_virustotal_by_hash(sha))
'@ | Set-Content -LiteralPath $tmp -Encoding ASCII

$env:MAGIC_FP_PYTHON = $py
$env:MAGIC_FP_SHA256 = $sha
try {
    python $tmp
} finally {
    Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
}
Write-Host "fp_hash=done"
