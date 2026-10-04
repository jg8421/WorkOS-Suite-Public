param(
    [string]$SourceRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$InstallRoot = "$env:LOCALAPPDATA\PersonalMemory",
    [switch]$SkipScheduledTask
)

$ErrorActionPreference = 'Stop'
$SourceRoot = [System.IO.Path]::GetFullPath($SourceRoot)
$InstallRoot = [System.IO.Path]::GetFullPath($InstallRoot)
$nodeCommand = Get-Command node -ErrorAction Stop
$pnpmCommand = Get-Command pnpm -ErrorAction Stop

New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
foreach ($name in @('src', 'scripts')) {
    $target = Join-Path $InstallRoot $name
    New-Item -ItemType Directory -Path $target -Force | Out-Null
    Copy-Item -Path (Join-Path $SourceRoot "$name\*") -Destination $target -Recurse -Force
}
Copy-Item -LiteralPath (Join-Path $SourceRoot 'package.json') -Destination $InstallRoot -Force
if (Test-Path -LiteralPath (Join-Path $SourceRoot 'pnpm-lock.yaml')) {
    Copy-Item -LiteralPath (Join-Path $SourceRoot 'pnpm-lock.yaml') -Destination $InstallRoot -Force
}
Copy-Item -LiteralPath (Join-Path $SourceRoot 'README.md') -Destination $InstallRoot -Force

Push-Location $InstallRoot
try {
    # A hoisted layout avoids broken package junctions observed under LocalAppData on Windows.
    & $pnpmCommand.Source install --prod --frozen-lockfile --config.node-linker=hoisted
    if ($LASTEXITCODE -ne 0) { throw "pnpm install failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}

$runtime = [ordered]@{
    nodePath = $nodeCommand.Source
    installedAt = (Get-Date).ToUniversalTime().ToString('o')
}
$runtime | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $InstallRoot 'runtime.json') -Encoding utf8

$serviceScript = Join-Path $InstallRoot 'scripts\start-service.ps1'
$startupRegistered = $false

if (-not $SkipScheduledTask) {
    # Retain the old switch name for command-line compatibility; startup now uses HKCU Run.
    $runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
    if (-not (Test-Path -LiteralPath $runKey)) { New-Item -Path $runKey | Out-Null }
    $launcher = Join-Path $InstallRoot 'scripts\launch-service.vbs'
    $wscript = Join-Path $env:SystemRoot 'System32\wscript.exe'
    New-ItemProperty -Path $runKey -Name 'PersonalMemoryService' -Value "`"$wscript`" `"$launcher`"" -PropertyType String -Force | Out-Null
    $startupRegistered = $true
}

$configPath = Join-Path $InstallRoot 'config.json'
$config = if (Test-Path -LiteralPath $configPath) { Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json } else { $null }
$servicePort = if ($config.port) { $config.port } else { 18765 }
$apiHost = if ($config.localApiHost) { $config.localApiHost } else { '127.0.0.1' }
& $serviceScript -InstallRoot $InstallRoot
$deadline = (Get-Date).AddSeconds(15)
do {
    Start-Sleep -Milliseconds 400
    try {
        $health = Invoke-RestMethod -Uri "http://${apiHost}:$servicePort/health" -TimeoutSec 2
    } catch {
        $health = $null
    }
} until ($health -or (Get-Date) -gt $deadline)
if (-not $health) { throw "Personal Memory service did not become healthy on port $servicePort." }
$config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
$stats = Invoke-RestMethod -Uri "http://${apiHost}:$servicePort/api/stats" -Headers @{ Authorization = "Bearer $($config.token)" } -TimeoutSec 3
if ($startupRegistered) {
    $legacyTask = Get-ScheduledTask -TaskName 'Personal Memory Service' -ErrorAction SilentlyContinue
    if ($legacyTask) {
        Stop-ScheduledTask -TaskName 'Personal Memory Service' -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName 'Personal Memory Service' -Confirm:$false
    }
}

[pscustomobject]@{
    Installed = $true
    InstallRoot = $InstallRoot
    Health = $health.ok
    Startup = if ($startupRegistered) { 'HKCU Run (hidden VBS)' } else { 'Not changed' }
    EventCount = $stats.active
    ConfigPath = (Join-Path $InstallRoot 'config.json')
    TokenPrinted = $false
}
