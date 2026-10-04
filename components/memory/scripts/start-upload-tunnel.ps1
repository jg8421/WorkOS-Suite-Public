param([string]$TunnelConfig = (Join-Path $env:USERPROFILE '.cloudflared\personal-memory.yml'))
$ErrorActionPreference = 'Stop'
$connector = 'C:\Program Files (x86)\cloudflared\cloudflared.exe'
if (-not (Test-Path -LiteralPath $TunnelConfig) -or -not (Test-Path -LiteralPath $connector)) { throw 'Missing tunnel configuration or Cloudflare connector.' }
$log = Join-Path $env:LOCALAPPDATA 'PersonalMemory\logs\upload-tunnel.log'
while ($true) {
    $existing = Get-CimInstance Win32_Process -Filter "Name='cloudflared.exe'" |
        Where-Object { $_.CommandLine -and $_.CommandLine.Contains($TunnelConfig) }
    if ($existing) { Start-Sleep -Seconds 15; continue }
    $process = Start-Process -FilePath $connector -ArgumentList @('tunnel','--config',('"'+$TunnelConfig+'"'),'--logfile',('"'+$log+'"'),'run') -WindowStyle Hidden -PassThru
    $process.WaitForExit()
    Start-Sleep -Seconds 10
}
