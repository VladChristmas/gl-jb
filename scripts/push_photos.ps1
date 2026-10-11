#Requires -Version 5.1
<#
    Push photos from the local PC backup UP to the Render server.

    The PC is the source of truth (see GL_JB_RENDER_REPORT.md):
    this script compares local files with the server list (/admin/api/files)
    and uploads the missing ones to /admin/api/photos/restore.
    Run it after a redeploy, or on a schedule, to refill uploads/ on Render.

    Usage:
        powershell -ExecutionPolicy Bypass -File scripts\push_photos.ps1
        powershell -ExecutionPolicy Bypass -File scripts\push_photos.ps1 -DryRun
        powershell -ExecutionPolicy Bypass -File scripts\push_photos.ps1 -Overwrite
        powershell -ExecutionPolicy Bypass -File scripts\push_photos.ps1 -Verify

    Hourly task (optional):
        schtasks /Create /TN "GLJB-PushPhotos" /SC HOURLY /MO 1 ^
          /TR "powershell -ExecutionPolicy Bypass -File D:\GL_JB\project\scripts\push_photos.ps1" /F

    Exit codes: 0 = ok, 1 = fatal (token / server / auth), 2 = some files failed.
#>
param(
    [string]$BaseUrl = 'https://gl-jb.onrender.com',
    [string]$LocalDir = 'D:\GL_JB\project\uploads',
    [string]$TokenFile = 'C:\Users\vladc\OneDrive\Desktop\token_admin.txt',
    [string]$LogFile = 'D:\AI\logs\gljb_push.log',
    [int]$WakeTimeoutSec = 180,
    [int]$MaxBatchBytes = 6MB,
    [int]$BatchLimit = 25,
    [switch]$Overwrite,
    [switch]$DryRun,
    [switch]$Verify
)

$ErrorActionPreference = 'Stop'
$AllowedExt = @('.png', '.jpg', '.jpeg', '.webp')

function Write-Log([string]$msg) {
    $line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    $dir = Split-Path $LogFile -Parent
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
    Write-Host $line
}

function Get-AdminToken {
    if ($env:GLJB_ADMIN_TOKEN) { return $env:GLJB_ADMIN_TOKEN.Trim() }
    if (-not (Test-Path $TokenFile)) { return $null }
    $m = Select-String -Path $TokenFile -Pattern 'ADMIN_TOKEN[=\s]+(\S+)' | Select-Object -First 1
    if ($m) { return $m.Matches[0].Groups[1].Value }
    return $null
}

function Get-HttpErrorBody($err) {
    try {
        $resp = $err.Exception.Response
        if ($null -eq $resp) { return $err.Exception.Message }
        $code = [int]$resp.StatusCode
        $reader = New-Object System.IO.StreamReader($resp.GetResponseStream())
        $body = $reader.ReadToEnd()
        $reader.Dispose()
        return 'HTTP {0}: {1}' -f $code, $body
    } catch {
        return $err.Exception.Message
    }
}

function Invoke-JsonPost([string]$Uri, [hashtable]$Headers, [byte[]]$Body) {
    # One retry on 429: the endpoint is limited to 60 requests per minute.
    for ($attempt = 1; $attempt -le 2; $attempt++) {
        try {
            return Invoke-WebRequest -Uri $Uri -Method Post -Headers $Headers `
                -ContentType 'application/json; charset=utf-8' -Body $Body `
                -UseBasicParsing -TimeoutSec 300
        } catch {
            $status = 0
            try { $status = [int]$_.Exception.Response.StatusCode } catch { }
            if ($status -eq 429 -and $attempt -eq 1) {
                Write-Log 'Rate limited (429), waiting 61s...'
                Start-Sleep -Seconds 61
                continue
            }
            throw
        }
    }
}

$token = Get-AdminToken
if (-not $token) {
    Write-Log 'ERROR: admin token not found (env GLJB_ADMIN_TOKEN or token file)'
    exit 1
}
$auth = @{ 'X-Admin-Token' = $token }

if (-not (Test-Path $LocalDir)) {
    Write-Log "ERROR: local folder not found: $LocalDir"
    exit 1
}

# 1. Wake up the service (free tier sleeps)
$deadline = (Get-Date).AddSeconds($WakeTimeoutSec)
$awake = $false
while ((Get-Date) -lt $deadline) {
    try {
        $h = Invoke-WebRequest -Uri "$BaseUrl/health" -UseBasicParsing -TimeoutSec 30
        if ($h.StatusCode -eq 200) { $awake = $true; break }
    } catch { }
    Write-Log 'Waiting for server wake-up...'
    Start-Sleep -Seconds 10
}
if (-not $awake) {
    Write-Log "ERROR: server $BaseUrl is not responding after $WakeTimeoutSec s"
    exit 1
}

# 2. Remote file list
try {
    $resp = Invoke-WebRequest -Uri "$BaseUrl/admin/api/files" -Headers $auth -UseBasicParsing -TimeoutSec 60
    $remote = @{}
    foreach ($f in ($resp.Content | ConvertFrom-Json).files) { $remote[$f.name] = [int]$f.size }
} catch {
    Write-Log "ERROR: cannot fetch file list: $(Get-HttpErrorBody $_)"
    exit 1
}
Write-Log ("Server has {0} files" -f $remote.Count)

# 3. Local candidates -> missing or size-mismatched
$skippedExt = 0
$diffSize = 0
$todo = New-Object System.Collections.ArrayList
foreach ($file in (Get-ChildItem -Path $LocalDir -File | Sort-Object Name)) {
    if ($AllowedExt -notcontains $file.Extension.ToLower()) { $skippedExt++; continue }
    if ($remote.ContainsKey($file.Name) -and $remote[$file.Name] -eq $file.Length) { continue }
    if ($remote.ContainsKey($file.Name) -and -not $Overwrite) {
        # present but different size: only -Overwrite may replace it
        $diffSize++
        continue
    }
    [void]$todo.Add($file)
}
if ($skippedExt -gt 0) { Write-Log "Skipped $skippedExt file(s) with unsupported extension" }
if ($diffSize -gt 0) {
    Write-Log "Skipped $diffSize file(s) with different size on the server (use -Overwrite to replace)"
}
Write-Log ("To upload: {0} file(s), {1} bytes" -f $todo.Count, (($todo | Measure-Object Length -Sum).Sum))

if ($todo.Count -eq 0) {
    Write-Log 'Nothing to do: server already has every local photo.'
    exit 0
}

if ($DryRun) {
    foreach ($f in $todo) { Write-Log ("DRY-RUN would upload {0} ({1} bytes)" -f $f.Name, $f.Length) }
    Write-Log 'DRY-RUN finished, no files were sent.'
    exit 0
}

# 4. Upload in batches (raw bytes per batch -> base64 grows ~4/3, stay under 16 MB)
$totalUploaded = 0
$totalFailed = 0
$batch = New-Object System.Collections.ArrayList
$batchBytes = 0

function Send-Batch {
    param([System.Collections.ArrayList]$Items)
    if ($Items.Count -eq 0) { return }
    $payloadFiles = foreach ($it in $Items) { @{ name = $it.Name; data = $it.Data } }
    $payload = @{ files = @($payloadFiles); overwrite = [bool]$Overwrite }
    $json = $payload | ConvertTo-Json -Depth 5 -Compress
    $body = [System.Text.Encoding]::UTF8.GetBytes($json)

    try {
        $r = Invoke-JsonPost -Uri "$BaseUrl/admin/api/photos/restore" -Headers $auth -Body $body
        $res = $r.Content | ConvertFrom-Json
        Write-Log ("Batch: uploaded={0} skipped={1} conflicts={2} failed={3} bytes={4}" -f `
            $res.uploaded, $res.skipped, $res.conflicts.Count, $res.failed.Count, $res.bytes)
        foreach ($f in $res.failed) { Write-Log ("  FAIL {0}: {1}" -f $f.name, $f.reason) }
        foreach ($c in $res.conflicts) { Write-Log ("  CONFLICT {0}: {1}" -f $c.name, $c.reason) }
        $script:totalUploaded += [int]$res.uploaded
        $script:totalFailed += [int]$res.failed.Count + [int]$res.conflicts.Count
    } catch {
        $script:totalFailed += $Items.Count
        Write-Log ("Batch failed: {0}" -f (Get-HttpErrorBody $_))
    }
    $Items.Clear()
}

foreach ($file in $todo) {
    $size = [int]$file.Length
    if ($batch.Count -gt 0 -and (($batchBytes + $size) -gt $MaxBatchBytes -or $batch.Count -ge $BatchLimit)) {
        Send-Batch -Items $batch
        $batchBytes = 0
    }
    $data = [Convert]::ToBase64String([IO.File]::ReadAllBytes($file.FullName))
    [void]$batch.Add([pscustomobject]@{ Name = $file.Name; Data = $data })
    $batchBytes += $size
}
Send-Batch -Items $batch

# 5. Optional verification: re-list the server and compare sizes
if ($Verify) {
    try {
        $resp = Invoke-WebRequest -Uri "$BaseUrl/admin/api/files" -Headers $auth -UseBasicParsing -TimeoutSec 60
        $after = @{}
        foreach ($f in ($resp.Content | ConvertFrom-Json).files) { $after[$f.name] = [int]$f.size }
        $missing = 0
        foreach ($file in (Get-ChildItem -Path $LocalDir -File)) {
            if ($AllowedExt -notcontains $file.Extension.ToLower()) { continue }
            if (-not $after.ContainsKey($file.Name) -or $after[$file.Name] -ne $file.Length) { $missing++ }
        }
        Write-Log ("Verify: {0} local photo(s) still not matching on the server" -f $missing)
        if ($missing -gt 0) { $totalFailed += $missing }
    } catch {
        Write-Log ("Verify failed: {0}" -f (Get-HttpErrorBody $_))
    }
}

$localCount = (Get-ChildItem -Path $LocalDir -File | Where-Object { $AllowedExt -contains $_.Extension.ToLower() } | Measure-Object).Count
Write-Log ("DONE local={0} server_before={1} uploaded={2} failed={3}" -f $localCount, $remote.Count, $totalUploaded, $totalFailed)

if ($totalFailed -gt 0) { exit 2 }
exit 0
