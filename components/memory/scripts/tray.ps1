param(
    [string]$InstallRoot = "$env:LOCALAPPDATA\PersonalMemory"
)

$ErrorActionPreference = 'Stop'
$InstallRoot = [System.IO.Path]::GetFullPath($InstallRoot)
$configPath = Join-Path $InstallRoot 'config.json'
$startService = Join-Path $InstallRoot 'scripts\start-service.ps1'
$stopService = Join-Path $InstallRoot 'scripts\stop-service.ps1'
$pausePath = Join-Path $InstallRoot 'state\service.paused'

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$createdNew = $false
$mutex = [System.Threading.Mutex]::new($true, 'Local\PersonalMemoryTray', [ref]$createdNew)
if (-not $createdNew) {
    $mutex.Dispose()
    exit 0
}

$notifyIcon = [System.Windows.Forms.NotifyIcon]::new()
$notifyIcon.Icon = [System.Drawing.SystemIcons]::Information
$notifyIcon.Text = 'Personal Memory'
$notifyIcon.Visible = $true

$menu = [System.Windows.Forms.ContextMenuStrip]::new()
$statusItem = [System.Windows.Forms.ToolStripMenuItem]::new('Checking Personal Memory...')
$statusItem.Enabled = $false
$openMemoryItem = [System.Windows.Forms.ToolStripMenuItem]::new('Open OneDrive Memory')
$openSummaryItem = [System.Windows.Forms.ToolStripMenuItem]::new('Open MEMORY.md')
$refreshItem = [System.Windows.Forms.ToolStripMenuItem]::new('Refresh status')
$pauseItem = [System.Windows.Forms.ToolStripMenuItem]::new('Pause Memory service')
$resumeItem = [System.Windows.Forms.ToolStripMenuItem]::new('Resume Memory service')
$restartItem = [System.Windows.Forms.ToolStripMenuItem]::new('Restart Memory service')
$exitItem = [System.Windows.Forms.ToolStripMenuItem]::new('Exit tray')
[void]$menu.Items.Add($statusItem)
[void]$menu.Items.Add([System.Windows.Forms.ToolStripSeparator]::new())
[void]$menu.Items.Add($openMemoryItem)
[void]$menu.Items.Add($openSummaryItem)
[void]$menu.Items.Add([System.Windows.Forms.ToolStripSeparator]::new())
[void]$menu.Items.Add($refreshItem)
[void]$menu.Items.Add($pauseItem)
[void]$menu.Items.Add($resumeItem)
[void]$menu.Items.Add($restartItem)
[void]$menu.Items.Add([System.Windows.Forms.ToolStripSeparator]::new())
[void]$menu.Items.Add($exitItem)
$notifyIcon.ContextMenuStrip = $menu

$script:dataRoot = $null
$script:memoryFile = $null
$script:lastAutoStart = [DateTime]::MinValue

function Update-MemoryStatus {
    $paused = Test-Path -LiteralPath $pausePath
    $pauseItem.Enabled = -not $paused
    $resumeItem.Enabled = $paused
    try {
        $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
        $script:dataRoot = [System.IO.Path]::GetFullPath($config.dataRoot)
        $script:memoryFile = Join-Path $script:dataRoot 'memory\MEMORY.md'
        $headers = @{ Authorization = "Bearer $($config.token)" }
        $apiHost = if ($config.localApiHost) { $config.localApiHost } else { '127.0.0.1' }
        $stats = Invoke-RestMethod -Uri "http://${apiHost}:$($config.port)/api/stats" -Headers $headers -TimeoutSec 3
        $oneDriveRunning = [bool](Get-Process OneDrive -ErrorAction SilentlyContinue)
        $syncText = if ($oneDriveRunning) { 'OneDrive running' } else { 'OneDrive offline' }
        $statusItem.Text = "Online | $($stats.active) entries | $syncText"
        $notifyIcon.Text = "Memory | $($stats.active) | $syncText"
        $notifyIcon.Icon = [System.Drawing.SystemIcons]::Information
        $openMemoryItem.Enabled = Test-Path -LiteralPath $script:dataRoot
        $openSummaryItem.Enabled = Test-Path -LiteralPath $script:memoryFile
    } catch {
        $statusItem.Text = if ($paused) { 'Memory service paused' } else { 'Memory service offline - retrying automatically' }
        $notifyIcon.Text = if ($paused) { 'Personal Memory | paused' } else { 'Personal Memory | reconnecting' }
        $notifyIcon.Icon = [System.Drawing.SystemIcons]::Error
        $openMemoryItem.Enabled = [bool]($script:dataRoot -and (Test-Path -LiteralPath $script:dataRoot))
        $openSummaryItem.Enabled = [bool]($script:memoryFile -and (Test-Path -LiteralPath $script:memoryFile))
        if (-not $paused -and ((Get-Date) - $script:lastAutoStart).TotalSeconds -ge 60) {
            $script:lastAutoStart = Get-Date
            try { & $startService -InstallRoot $InstallRoot -Automatic } catch {}
        }
    }
}

$openMemoryItem.Add_Click({
    if ($script:dataRoot -and (Test-Path -LiteralPath $script:dataRoot)) {
        Start-Process -FilePath 'explorer.exe' -ArgumentList @($script:dataRoot)
    }
})
$openSummaryItem.Add_Click({
    if ($script:memoryFile -and (Test-Path -LiteralPath $script:memoryFile)) {
        Start-Process -FilePath $script:memoryFile
    }
})
$refreshItem.Add_Click({ Update-MemoryStatus })
$pauseItem.Add_Click({
    try { & $stopService -InstallRoot $InstallRoot } catch {}
    Update-MemoryStatus
})
$resumeItem.Add_Click({
    try { & $startService -InstallRoot $InstallRoot } catch {}
    Update-MemoryStatus
})
$restartItem.Add_Click({
    $statusItem.Text = 'Restarting Memory service...'
    try {
        & $stopService -InstallRoot $InstallRoot
        & $startService -InstallRoot $InstallRoot
    } catch {}
    $script:restartTimer.Start()
})
$exitItem.Add_Click({
    $notifyIcon.Visible = $false
    $notifyIcon.Dispose()
    [System.Windows.Forms.Application]::Exit()
})
$notifyIcon.Add_DoubleClick({
    if ($script:dataRoot -and (Test-Path -LiteralPath $script:dataRoot)) {
        Start-Process -FilePath 'explorer.exe' -ArgumentList @($script:dataRoot)
    }
})

$timer = [System.Windows.Forms.Timer]::new()
$timer.Interval = 20000
$timer.Add_Tick({ Update-MemoryStatus })
$timer.Start()
$script:restartTimer = [System.Windows.Forms.Timer]::new()
$script:restartTimer.Interval = 1800
$script:restartTimer.Add_Tick({
    $script:restartTimer.Stop()
    Update-MemoryStatus
})
Update-MemoryStatus

try {
    [System.Windows.Forms.Application]::Run()
} finally {
    $timer.Stop()
    $timer.Dispose()
    $script:restartTimer.Stop()
    $script:restartTimer.Dispose()
    $notifyIcon.Visible = $false
    $notifyIcon.Dispose()
    try { $mutex.ReleaseMutex() } catch {}
    $mutex.Dispose()
}
