param(
    [string]$InstallRoot = "$env:LOCALAPPDATA\PersonalMemory"
)

$ErrorActionPreference = 'Stop'
$trayPath = [System.IO.Path]::GetFullPath((Join-Path $InstallRoot 'scripts\tray.ps1'))
$targets = Get-CimInstance Win32_Process |
    Where-Object { $_.Name -in @('powershell.exe', 'pwsh.exe') -and $_.CommandLine -and $_.CommandLine.IndexOf($trayPath, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 }
foreach ($target in $targets) {
    Stop-Process -Id $target.ProcessId -Force
}
