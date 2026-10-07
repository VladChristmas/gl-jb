#Requires -Version 5.1
<#
    Синхронизация фото с Render на локальный ПК.

    Скачивает в папку проекта недостающие файлы из uploads/ на сервере.
    Запуск вручную:
        powershell -ExecutionPolicy Bypass -File scripts\sync_photos.ps1
    По расписанию (ежечасно):
        schtasks /Create /TN "GLJB-SyncPhotos" /SC HOURLY /MO 1 ^
          /TR "powershell -ExecutionPolicy Bypass -File D:\GL_JB\project\scripts\sync_photos.ps1" /F
#>
param(
    [string]$BaseUrl = 'https://gl-jb.onrender.com',
    [string]$LocalDir = 'D:\GL_JB\project\uploads',
    [string]$TokenFile = 'C:\Users\vladc\OneDrive\Desktop\token_admin.txt',
    [string]$LogFile = 'D:\AI\logs\gljb_sync.log',
    [int]$WakeTimeoutSec = 180
)

$ErrorActionPreference = 'Stop'

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

$token = Get-AdminToken
if (-not $token) {
    Write-Log 'ERROR: admin token not found (env GLJB_ADMIN_TOKEN or token file)'
    exit 1
}

if (-not (Test-Path $LocalDir)) {
    New-Item -ItemType Directory -Path $LocalDir -Force | Out-Null
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
    $resp = Invoke-WebRequest -Uri "$BaseUrl/admin/api/files" -Headers @{ 'X-Admin-Token' = $token } -UseBasicParsing -TimeoutSec 60
    $remote = ($resp.Content | ConvertFrom-Json).files
} catch {
    Write-Log "ERROR: cannot fetch file list: $($_.Exception.Message)"
    exit 1
}

# 3. Download missing or size-mismatched files
$downloaded = 0
$skipped = 0
$failed = 0

foreach ($f in $remote) {
    $target = Join-Path $LocalDir $f.name
    $need = $true
    if (Test-Path $target) {
        $localSize = (Get-Item $target).Length
        if ($localSize -eq $f.size) { $need = $false }
    }

    if (-not $need) { $skipped++; continue }

    $tmp = "$target.part"
    try {
        $url = "$BaseUrl/uploads/$([uri]::EscapeDataString($f.name))"
        Invoke-WebRequest -Uri $url -OutFile $tmp -Headers @{ 'X-Admin-Token' = $token } -UseBasicParsing -TimeoutSec 180
        $gotSize = (Get-Item $tmp).Length
        if ($gotSize -ne $f.size) {
            throw "size mismatch: got $gotSize, expected $($f.size)"
        }
        Move-Item -Path $tmp -Destination $target -Force
        $downloaded++
        Write-Log "OK  $($f.name) ($gotSize bytes)"
    } catch {
        $failed++
        Write-Log "FAIL $($f.name): $($_.Exception.Message)"
        if (Test-Path $tmp) { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
    }
}

$localCount = (Get-ChildItem -Path $LocalDir -File -ErrorAction SilentlyContinue | Measure-Object).Count
Write-Log "DONE remote=$($remote.Count) local=$localCount downloaded=$downloaded skipped=$skipped failed=$failed"

if ($failed -gt 0) { exit 2 }
exit 0
