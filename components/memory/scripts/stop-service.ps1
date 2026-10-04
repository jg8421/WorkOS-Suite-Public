param(
    [string]$InstallRoot = "$env:LOCALAPPDATA\PersonalMemory"
)

$ErrorActionPreference = 'Stop'
$InstallRoot = [System.IO.Path]::GetFullPath($InstallRoot)
$statePath = Join-Path $InstallRoot 'state'
New-Item -ItemType Directory -Path $statePath -Force | Out-Null
Set-Content -LiteralPath (Join-Path $statePath 'service.paused') -Value (Get-Date -Format o) -Encoding ascii
$servicePath = [System.IO.Path]::GetFullPath((Join-Path $InstallRoot 'src\service.mjs'))
$targets = Get-CimInstance Win32_Process -Filter "Name = 'node.exe'" |
    Where-Object { $_.CommandLine -and $_.CommandLine.IndexOf($servicePath, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 }
foreach ($target in $targets) {
    Stop-Process -Id $target.ProcessId -Force
}
