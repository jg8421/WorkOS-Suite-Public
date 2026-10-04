param(
    [string]$Serial = '',
    [int]$MaxSize = 1280,
    [int]$MaxFps = 60,
    [switch]$NoAudio,
    [switch]$Fullscreen,
    [switch]$List
)
$ErrorActionPreference = 'Continue'

# Legacy source helper: use the toolkit layout, explicit local configuration, or PATH.
$Tools = $PSScriptRoot
if (-not $Tools) { $Tools = Split-Path -Parent $MyInvocation.MyCommand.Definition }
$Toolkit = Split-Path -Parent $Tools
$adb    = Join-Path $Toolkit 'bin\platform-tools\adb.exe'
$scrcpy = Join-Path $Toolkit 'bin\scrcpy\scrcpy.exe'
if ($env:WORKOS_SUITE_ADB) { $adb = $env:WORKOS_SUITE_ADB }
if ($env:WORKOS_SUITE_SCRCPY) { $scrcpy = $env:WORKOS_SUITE_SCRCPY }
if (-not (Test-Path -LiteralPath $adb)) {
    $command = Get-Command adb.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command) { $adb = $command.Source }
    elseif ($env:LOCALAPPDATA) { $adb = Join-Path $env:LOCALAPPDATA 'Android\Sdk\platform-tools\adb.exe' }
}
if (-not (Test-Path -LiteralPath $scrcpy)) {
    $command = Get-Command scrcpy.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command) { $scrcpy = $command.Source }
    elseif ($env:LOCALAPPDATA) { $scrcpy = Join-Path $env:LOCALAPPDATA 'scrcpy\scrcpy.exe' }
}

function Say($m,$c='Gray'){ Write-Host $m -ForegroundColor $c }
if (-not (Test-Path $scrcpy)) { Say ('scrcpy missing: ' + $scrcpy) Red; Read-Host 'Enter'; exit 1 }
if (-not (Test-Path $adb))    { Say ('adb missing: ' + $adb) Red;    Read-Host 'Enter'; exit 1 }

$env:ADB = $adb
& $adb start-server 2>&1 | Out-Null

function Get-Dev {
    # returns a STRING array of device serials (never collapses to one string)
    $raw = (& $adb devices 2>&1 | Out-String)
    $res = New-Object System.Collections.ArrayList
    foreach ($ln in ($raw -split "`r?`n")) {
        if ($ln -match '\tdevice' -and $ln -notmatch 'no serial') {
            $sn = ($ln -split '\t')[0].Trim()
            if ($sn) { [void]$res.Add([string]$sn) }
        }
    }
    $arr = New-Object string[] $res.Count
    for ($i = 0; $i -lt $res.Count; $i++) { $arr[$i] = [string]$res[$i] }
    return $arr
}

# drop stale wireless transports (USB devices cannot be disconnected this way)
$raw_all = (& $adb devices 2>&1 | Out-String)
foreach ($ln in ($raw_all -split "`r?`n")) {
    if ($ln -match '\toffline') {
        $sn = ($ln -split '\t')[0].Trim()
        if ($sn -and $sn -match ':') { Say ('  dropping offline: ' + $sn) DarkGray; & $adb disconnect $sn 2>&1 | Out-Null }
    }
}

$dev = Get-Dev
if ($dev.Count -eq 0) {
    Say 'No authorized phone.' Yellow
    Say 'Run the Scan-to-Mirror helper first.' Cyan
    & $adb devices -l
    Read-Host 'Enter'
    exit 2
}

$target = [string]$Serial

if (-not $target -and $dev.Count -gt 1) {
    # several devices: let the user choose instead of guessing
    Say ''
    Say ('  ' + $dev.Count + ' devices connected. Pick one:') Yellow
    Say ''
    $models = @()
    for ($i = 0; $i -lt $dev.Count; $i++) {
        $m = (& $adb -s $dev[$i] shell getprop ro.product.model 2>&1 | Out-String).Trim()
        $models += $m
        Say ('    [' + ($i+1) + ']  ' + $dev[$i]) White
        Say ('         ' + $m) DarkGray
    }
    Say ''
    $pick = Read-Host '  Enter number (default 1)'
    $idx = 0
    if ($pick -match '^\d+$') { $idx = [int]$pick - 1 }
    if ($idx -lt 0 -or $idx -ge $dev.Count) { $idx = 0 }
    $target = [string]$dev[$idx]
}

if (-not $target) { $target = [string]$dev[0] }

if ($List) { Say ('Target: ' + $target) Cyan; & $adb devices -l; Read-Host 'Enter'; exit 0 }
Say ('Connected: ' + $target) Green

$a = @('--serial', $target, '--stay-awake', '--max-size', [string]$MaxSize, '--max-fps', [string]$MaxFps, '--window-title', 'Phone Mirror')
if ($NoAudio)    { $a += '--no-audio' }
if ($Fullscreen) { $a += '--fullscreen' }

Say 'Starting mirror. Close the window to stop.' Cyan
Say 'Left click = tap | Wheel = scroll | Keyboard = type | Drag file = install' DarkGray
Say ''
& $scrcpy @a
if ($LASTEXITCODE -ne 0) { Say ('scrcpy exit: ' + $LASTEXITCODE) Yellow; Read-Host 'Enter' }
