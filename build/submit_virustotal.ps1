# Submit signed EXE to VirusTotal (optional CI secret VIRUSTOTAL_API_KEY / MAGIC_VT_API_KEY).
# Compatible with Windows PowerShell 5.1 (no -Form; uses multipart/.NET).
# ASCII-only source for Windows PowerShell 5.1 parsers.
param(
    [Parameter(Mandatory = $true)][string]$ExePath,
    [switch]$VoteHarmless
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $ExePath)) { throw "EXE missing: $ExePath" }

$key = $env:VIRUSTOTAL_API_KEY
if (-not $key) { $key = $env:MAGIC_VT_API_KEY }
if (-not $key -or $key.Trim().Length -lt 32) {
    Write-Host "No VIRUSTOTAL_API_KEY - skip VT submit (optional)." -ForegroundColor Yellow
    Write-Host "vt_submit=skipped"
    exit 0
}

$sha = (Get-FileHash -LiteralPath $ExePath -Algorithm SHA256).Hash.ToLowerInvariant()
Write-Host ("VirusTotal submit sha256={0}" -f $sha) -ForegroundColor Cyan
$apiKey = $key.Trim()
$headers = @{ "x-apikey" = $apiKey }

function Invoke-VtMultipartUpload {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string]$ApiKey
    )
    # PS 5.1 has no Invoke-RestMethod -Form; build multipart manually
    $boundary = [guid]::NewGuid().ToString("N")
    $fileName = [IO.Path]::GetFileName($FilePath)
    $fileBytes = [IO.File]::ReadAllBytes($FilePath)
    $enc = [Text.Encoding]::GetEncoding("iso-8859-1")
    $lf = "`r`n"
    $header = "--$boundary$lf" +
        "Content-Disposition: form-data; name=`"file`"; filename=`"$fileName`"$lf" +
        "Content-Type: application/octet-stream$lf$lf"
    $footer = "$lf--$boundary--$lf"
    $headerBytes = $enc.GetBytes($header)
    $footerBytes = $enc.GetBytes($footer)
    $body = New-Object byte[] ($headerBytes.Length + $fileBytes.Length + $footerBytes.Length)
    [Buffer]::BlockCopy($headerBytes, 0, $body, 0, $headerBytes.Length)
    [Buffer]::BlockCopy($fileBytes, 0, $body, $headerBytes.Length, $fileBytes.Length)
    [Buffer]::BlockCopy($footerBytes, 0, $body, $headerBytes.Length + $fileBytes.Length, $footerBytes.Length)

    $req = [Net.HttpWebRequest]::Create($Uri)
    $req.Method = "POST"
    $req.Headers.Add("x-apikey", $ApiKey)
    $req.Accept = "application/json"
    $req.ContentType = "multipart/form-data; boundary=$boundary"
    $req.Timeout = 600000
    $req.ReadWriteTimeout = 600000
    $req.ContentLength = $body.Length
    $stream = $req.GetRequestStream()
    $stream.Write($body, 0, $body.Length)
    $stream.Close()
    try {
        $resp = $req.GetResponse()
    } catch [Net.WebException] {
        if ($_.Exception.Response) {
            $sr = New-Object IO.StreamReader($_.Exception.Response.GetResponseStream())
            $err = $sr.ReadToEnd()
            $sr.Close()
            throw ("HTTP {0}: {1}" -f [int]$_.Exception.Response.StatusCode, $err.Substring(0, [Math]::Min(240, $err.Length)))
        }
        throw
    }
    $reader = New-Object IO.StreamReader($resp.GetResponseStream())
    $jsonText = $reader.ReadToEnd()
    $reader.Close()
    $resp.Close()
    return ($jsonText | ConvertFrom-Json)
}

$uploadUrl = "https://www.virustotal.com/api/v3/files"
try {
    $u = Invoke-RestMethod -Method GET -Uri "https://www.virustotal.com/api/v3/files/upload_url" -Headers $headers
    if ($u.data) { $uploadUrl = [string]$u.data }
} catch {
    Write-Host ("upload_url fallback to /files: {0}" -f $_.Exception.Message) -ForegroundColor DarkGray
}

$analysisId = $null
try {
    $resp = Invoke-VtMultipartUpload -Uri $uploadUrl -FilePath $ExePath -ApiKey $apiKey
    if ($resp.data.id) { $analysisId = [string]$resp.data.id }
    Write-Host ("VT upload OK: {0}" -f $analysisId) -ForegroundColor Green
} catch {
    Write-Host ("VT upload failed: {0}" -f $_.Exception.Message) -ForegroundColor Yellow
    Write-Host "vt_submit=upload_fail"
    exit 0
}

# Wait briefly for file object to exist before vote/comment (avoids 404 NotFound)
if ($analysisId) {
    for ($i = 0; $i -lt 12; $i++) {
        try {
            $an = Invoke-RestMethod -Method GET -Uri ("https://www.virustotal.com/api/v3/analyses/{0}" -f $analysisId) -Headers $headers
            $st = [string]$an.data.attributes.status
            Write-Host ("VT analysis status: {0}" -f $st) -ForegroundColor DarkGray
            if ($st -eq "completed") { break }
        } catch { }
        Start-Sleep -Seconds 5
    }
}

if ($VoteHarmless) {
    $voteObj = @{ data = @{ type = "vote"; attributes = @{ verdict = "harmless" } } }
    $voteBody = $voteObj | ConvertTo-Json -Compress -Depth 5
    try {
        Invoke-RestMethod -Method POST `
            -Uri ("https://www.virustotal.com/api/v3/files/{0}/votes" -f $sha) `
            -Headers @{ "x-apikey" = $apiKey; "Content-Type" = "application/json" } `
            -Body $voteBody | Out-Null
        Write-Host "VT voted harmless" -ForegroundColor Green
    } catch {
        Write-Host ("VT vote skipped: {0}" -f $_.Exception.Message) -ForegroundColor DarkGray
    }

    $text = "FALSE POSITIVE. Win11 Magic Upgrade by dlnraja - open-source MIT Windows 11 migration helper (PyInstaller onefile). NOT Trojan.Python.Agent / NOT malware. Downloads official Microsoft ISOs only. Source: https://github.com/dlnraja/win11-magic-upgrade Prefer ZIP from Releases. #false_positive"
    $commentObj = @{ data = @{ type = "comment"; attributes = @{ text = $text } } }
    $commentBody = $commentObj | ConvertTo-Json -Compress -Depth 5
    try {
        Invoke-RestMethod -Method POST `
            -Uri ("https://www.virustotal.com/api/v3/files/{0}/comments" -f $sha) `
            -Headers @{ "x-apikey" = $apiKey; "Content-Type" = "application/json" } `
            -Body $commentBody | Out-Null
        Write-Host "VT FP comment posted" -ForegroundColor Green
    } catch {
        Write-Host ("VT comment skipped: {0}" -f $_.Exception.Message) -ForegroundColor DarkGray
    }
}

Write-Host ("https://www.virustotal.com/gui/file/{0}" -f $sha)
Write-Host "vt_submit=ok"
