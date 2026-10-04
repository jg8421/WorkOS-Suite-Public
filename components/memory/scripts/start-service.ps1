param(
    [string]$InstallRoot = "$env:LOCALAPPDATA\PersonalMemory",
    [switch]$Foreground,
    [switch]$Automatic
)

$ErrorActionPreference = 'Stop'
$InstallRoot = [System.IO.Path]::GetFullPath($InstallRoot)
$statePath = Join-Path $InstallRoot 'state'
$pausePath = Join-Path $statePath 'service.paused'
New-Item -ItemType Directory -Path $statePath -Force | Out-Null
if ($Automatic -and (Test-Path -LiteralPath $pausePath)) { return }
if (-not $Automatic) { Remove-Item -LiteralPath $pausePath -ErrorAction SilentlyContinue }
$runtimePath = Join-Path $InstallRoot 'runtime.json'
if (-not (Test-Path -LiteralPath $runtimePath)) {
    throw "Missing runtime configuration: $runtimePath"
}
$runtime = Get-Content -LiteralPath $runtimePath -Raw | ConvertFrom-Json
$servicePath = [System.IO.Path]::GetFullPath((Join-Path $InstallRoot 'src\service.mjs'))
if (-not $Foreground) {
    $launcher = Join-Path $InstallRoot 'scripts\launch-service.vbs'
    Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\wscript.exe') -ArgumentList @("`"$launcher`"") -WindowStyle Hidden
    return
}

$createdNew = $false
$mutex = [System.Threading.Mutex]::new($true, 'Local\PersonalMemoryService', [ref]$createdNew)
if (-not $createdNew) {
    $mutex.Dispose()
    return
}
try {
    if ($Automatic -and (Test-Path -LiteralPath $pausePath)) { return }
    $existing = Get-CimInstance Win32_Process -Filter "Name = 'node.exe'" |
        Where-Object { $_.CommandLine -and $_.CommandLine.IndexOf($servicePath, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 }
    if ($existing) { return }
    $logPath = Join-Path $InstallRoot 'logs'
    New-Item -ItemType Directory -Path $logPath -Force | Out-Null
    $env:PERSONAL_MEMORY_HOME = $InstallRoot
    $service = Start-Process -FilePath $runtime.nodePath -ArgumentList @("`"$servicePath`"") `
        -WorkingDirectory $InstallRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $logPath 'launcher-stdout.log') `
        -RedirectStandardError (Join-Path $logPath 'launcher-stderr.log')
    $service.WaitForExit()
    if ($service.ExitCode -ne 0) {
        Add-Content -LiteralPath (Join-Path $logPath 'launcher.log') -Value "$(Get-Date -Format o) Service exited with code $($service.ExitCode)"
    }
} finally {
    try { $mutex.ReleaseMutex() } catch {}
    $mutex.Dispose()
}
