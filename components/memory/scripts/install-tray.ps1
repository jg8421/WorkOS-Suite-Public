param(
    [string]$InstallRoot = "$env:LOCALAPPDATA\PersonalMemory"
)

$ErrorActionPreference = 'Stop'
$InstallRoot = [System.IO.Path]::GetFullPath($InstallRoot)
$trayScript = Join-Path $InstallRoot 'scripts\tray.ps1'
$stopScript = Join-Path $InstallRoot 'scripts\stop-tray.ps1'
$launcher = Join-Path $InstallRoot 'scripts\launch-tray.vbs'
if (-not (Test-Path -LiteralPath $trayScript)) { throw "Tray script not found: $trayScript" }
if (-not (Test-Path -LiteralPath $launcher)) { throw "Tray launcher not found: $launcher" }

if (Test-Path -LiteralPath $stopScript) {
    & $stopScript -InstallRoot $InstallRoot
}

$obsoleteTask = Get-ScheduledTask -TaskName 'Personal Memory Tray' -ErrorAction SilentlyContinue
if ($obsoleteTask) {
    Unregister-ScheduledTask -TaskName 'Personal Memory Tray' -Confirm:$false
}

$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
if (-not (Test-Path -LiteralPath $runKey)) { New-Item -Path $runKey | Out-Null }
$wscript = Join-Path $env:SystemRoot 'System32\wscript.exe'
$startupValue = "`"$wscript`" `"$launcher`""
New-ItemProperty -Path $runKey -Name 'PersonalMemoryTray' -Value $startupValue -PropertyType String -Force | Out-Null
Start-Process -FilePath $wscript -ArgumentList @("`"$launcher`"") -WindowStyle Hidden

$deadline = (Get-Date).AddSeconds(10)
do {
    Start-Sleep -Milliseconds 300
    $process = Get-CimInstance Win32_Process |
        Where-Object { $_.Name -in @('powershell.exe', 'pwsh.exe') -and $_.CommandLine -and $_.CommandLine.IndexOf($trayScript, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 } |
        Select-Object -First 1
} until ($process -or (Get-Date) -gt $deadline)
if (-not $process) { throw 'Personal Memory tray did not start.' }

[pscustomobject]@{
    Installed = $true
    Startup = 'HKCU Run'
    ProcessId = $process.ProcessId
    TrayProcessRunning = $true
    ConsoleWindow = $false
}
