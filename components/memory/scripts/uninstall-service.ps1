param(
    [string]$InstallRoot = "$env:LOCALAPPDATA\PersonalMemory",
    [switch]$KeepData
)

$ErrorActionPreference = 'Stop'
$taskName = 'Personal Memory Service'
$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'

& (Join-Path $InstallRoot 'scripts\stop-tray.ps1') -InstallRoot $InstallRoot
Remove-ItemProperty -LiteralPath $runKey -Name 'PersonalMemoryTray' -ErrorAction SilentlyContinue
Remove-ItemProperty -LiteralPath $runKey -Name 'PersonalMemoryService' -ErrorAction SilentlyContinue

$legacyTrayTask = Get-ScheduledTask -TaskName 'Personal Memory Tray' -ErrorAction SilentlyContinue
if ($legacyTrayTask) {
    Stop-ScheduledTask -TaskName 'Personal Memory Tray' -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName 'Personal Memory Tray' -Confirm:$false
}

$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}
& (Join-Path $InstallRoot 'scripts\stop-service.ps1') -InstallRoot $InstallRoot
if (-not $KeepData) {
    Write-Host "Data remains at $InstallRoot. Delete it manually only after making a backup."
}
