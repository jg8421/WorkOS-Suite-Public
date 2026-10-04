# Independent hardware-only sidecar. Requires normal administrator consent.
# Uses unmodified official LibreHardwareMonitor CI binaries in this directory.
# To stop gracefully, create a file named sensor-bridge.stop here.
param([string]$MonitorDirectory = $PSScriptRoot)
$ErrorActionPreference = 'Stop'
$root = [System.IO.Path]::GetFullPath($MonitorDirectory)
$snapshot = [System.IO.Path]::GetFullPath((Join-Path $root 'sensor-snapshot.json'))
$temp = [System.IO.Path]::GetFullPath((Join-Path $root 'sensor-snapshot.tmp.json'))
$backup = [System.IO.Path]::GetFullPath((Join-Path $root 'sensor-snapshot.previous.json'))
if ((Split-Path $backup) -ne $root) { throw 'Unexpected backup path' }
$stop = Join-Path $root 'sensor-bridge.stop'
$log = Join-Path $root 'sensor-bridge-error.txt'
# Verify atomic-write source and target remain in the intended independent tool directory.
if ((Split-Path $snapshot) -ne $root -or (Split-Path $temp) -ne $root -or
    (Split-Path $snapshot -Leaf) -ne 'sensor-snapshot.json' -or
    (Split-Path $temp -Leaf) -ne 'sensor-snapshot.tmp.json') { throw 'Unexpected snapshot path' }
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($id)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Administrator permission is required for actual CPU temperature and RAPL MSR access.'
}
$sha = [System.Security.Cryptography.SHA256]::Create()
$pathHash = ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($root.ToLowerInvariant())))).Replace('-', '').Substring(0, 24)
$sha.Dispose()
$mutex = [System.Threading.Mutex]::new($false, ('Global\FastProcessManagerHardwareSensorBridge_' + $pathHash))
$owned = $false
$computer = $null
try {
    try { $owned = $mutex.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $owned = $true }
    if (-not $owned) { exit 0 }
    if (Test-Path -LiteralPath $stop) { exit 0 }
    [System.Reflection.Assembly]::LoadFrom((Join-Path $root 'LibreHardwareMonitorLib.dll')) | Out-Null
    $computer = [LibreHardwareMonitor.Hardware.Computer]::new()
    foreach ($name in @('IsCpuEnabled','IsGpuEnabled','IsMotherboardEnabled','IsMemoryEnabled','IsStorageEnabled','IsBatteryEnabled')) {
        if ($computer.PSObject.Properties[$name]) { $computer.$name = $true }
    }
    $computer.Open()
    function Read-HardwareTree($hardware, [string]$parent) {
        $hardware.Update()
        $script:hardwareRecords.Add([pscustomobject]@{
            Identifier = $hardware.Identifier.ToString()
            Parent = $parent
            HardwareType = $hardware.HardwareType.ToString()
            Name = $hardware.Name
        })
        foreach ($sensor in $hardware.Sensors) {
            $script:sensorRecords.Add([pscustomobject]@{
                Name = $sensor.Name
                Identifier = $sensor.Identifier.ToString()
                Parent = $sensor.Hardware.Identifier.ToString()
                SensorType = $sensor.SensorType.ToString()
                Value = $sensor.Value
            })
        }
        foreach ($child in $hardware.SubHardware) { Read-HardwareTree $child $hardware.Identifier.ToString() }
    }
    $utf8 = [System.Text.UTF8Encoding]::new($false)
    # Let cumulative energy counters settle; do not publish the initial zero sample.
    foreach ($hardware in $computer.Hardware) { $hardware.Update() }
    Start-Sleep -Milliseconds 1100
    while (-not (Test-Path -LiteralPath $stop)) {
        $tick = [System.Diagnostics.Stopwatch]::StartNew()
        $script:hardwareRecords = [System.Collections.Generic.List[object]]::new()
        $script:sensorRecords = [System.Collections.Generic.List[object]]::new()
        foreach ($hardware in $computer.Hardware) { Read-HardwareTree $hardware '' }
        $payload = [pscustomobject]@{
            Timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
            Source = 'LibreHardwareMonitor' + [char]0x5b98 + [char]0x65b9 + [char]0x5e93
            Hardware = @($script:hardwareRecords.ToArray())
            Sensors = @($script:sensorRecords.ToArray())
        }
        $json = $payload | ConvertTo-Json -Depth 8 -Compress
        [System.IO.File]::WriteAllText($temp, $json, $utf8)
        if ([System.IO.File]::Exists($snapshot)) {
            [System.IO.File]::Replace($temp, $snapshot, $backup)
        } else {
            [System.IO.File]::Move($temp, $snapshot)
        }
        $remaining = 1000 - [int]$tick.ElapsedMilliseconds
        if ($remaining -gt 0) { Start-Sleep -Milliseconds $remaining }
    }
} catch {
    # Only hardware-sidecar errors are recorded; no environment or credential dumps.
    [System.IO.File]::WriteAllText($log, ((Get-Date -Format o) + ' ' + $_.Exception.Message), [System.Text.UTF8Encoding]::new($false))
    exit 1
} finally {
    if ($computer) { $computer.Close() }
    if ($owned) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
