param([int]$OwnerPid)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes
Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class MemoryForeground {
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint id);
 [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder text, int max);
 [DllImport("user32.dll")] static extern IntPtr OpenInputDesktop(uint flags, bool inherit, uint access);
 [DllImport("user32.dll")] static extern bool CloseDesktop(IntPtr h);
 [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern bool GetUserObjectInformation(IntPtr h, int index, StringBuilder name, int length, out int needed);
 public static bool Unlocked() {
  IntPtr h = OpenInputDesktop(0,false,1); if(h==IntPtr.Zero) return false;
  try { var name=new StringBuilder(256); int needed; return GetUserObjectInformation(h,2,name,512,out needed) && name.ToString()=="Default"; }
  finally { CloseDesktop(h); }
 }
 public static string Title(IntPtr h) { var b=new StringBuilder(1024); GetWindowText(h,b,b.Capacity); return b.ToString(); }
}
'@
$sensitive = '(?i)(password|passcode|verification|one.time|\botp\b|sign.?in|log.?in|checkout|payment|incognito|inprivate|private browsing|authenticator|bitwarden|1password|lastpass|\u9a8c\u8bc1\u7801|\u5bc6\u7801|\u53e3\u4ee4|\u767b\u5f55|\u767b\u5165|\u4ed8\u6b3e|\u652f\u4ed8|\u8f6c\u8d26|\u94f6\u884c|\u65e0\u75d5|\u79c1\u5bc6\u6d4f\u89c8|\u5b89\u5168\u7801)'
$blockedProcesses = '^(?i)(LockApp|LogonUI|CredentialUIBroker|SystemSettings|KeePass.*|Bitwarden.*|1Password.*|LastPass.*|WindowsTerminal|powershell|pwsh|cmd|conhost|mstsc|msrdc)$'
function Clean-Label([string]$Value) {
    $Value = $Value -replace '(?i)\b(?:sk|rk|pk|ghp|github_pat)_[A-Za-z0-9_\-]{12,}\b','[REDACTED]'
    $Value = $Value -replace '(?i)\bBearer\s+[A-Za-z0-9._~+/-]{12,}=*','Bearer [REDACTED]'
    $Value = $Value -replace '(?i)\b(token|secret|api[_ -]?key)\s*[:=]\s*[^\s,;]+','$1=[REDACTED]'
    $Value = $Value -replace '(?<!\d)\d{12,19}(?!\d)','[REDACTED_NUMBER]'
    return ($Value -replace '\s+',' ').Trim()
}
while (Get-Process -Id $OwnerPid -ErrorAction SilentlyContinue) {
    try {
        if ([MemoryForeground]::Unlocked()) {
            $handle = [MemoryForeground]::GetForegroundWindow()
            [uint32]$foregroundPid = 0
            [void][MemoryForeground]::GetWindowThreadProcessId($handle,[ref]$foregroundPid)
            $foregroundProcess = Get-Process -Id $foregroundPid -ErrorAction SilentlyContinue
            $title = [MemoryForeground]::Title($handle)
            if ($foregroundProcess -and $foregroundProcess.ProcessName -notmatch $blockedProcesses -and $title -and $title -notmatch $sensitive) {
                $labels = [System.Collections.Generic.List[string]]::new()
                $filtered = $false
                try {
                    $root = [System.Windows.Automation.AutomationElement]::FromHandle($handle)
                    $queue = [System.Collections.Generic.Queue[System.Windows.Automation.AutomationElement]]::new()
                    $queue.Enqueue($root)
                    $watch = [Diagnostics.Stopwatch]::StartNew()
                    $visited = 0
                    while ($queue.Count -gt 0) {
                        if (++$visited -gt 256 -or $watch.ElapsedMilliseconds -gt 250) { $labels.Clear(); break }
                        $node = $queue.Dequeue(); $current = $node.Current
                        if ($current.IsOffscreen) { continue }
                        if ($current.IsPassword) { $filtered=$true; break }
                        if ($current.ControlType -eq [System.Windows.Automation.ControlType]::Edit) { continue }
                        $name = $current.Name
                        if ($name -match $sensitive) { $filtered=$true; break }
                        if ($name -and $name.Length -lt 2000 -and $labels.Count -lt 40) {
                            $clean=Clean-Label $name
                            if ($clean -and -not $labels.Contains($clean)) { $labels.Add($clean.Substring(0,[Math]::Min(240,$clean.Length))) }
                        }
                        $child=[System.Windows.Automation.TreeWalker]::ControlViewWalker.GetFirstChild($node)
                        while ($child) {
                            if ($queue.Count -gt 256) { $labels.Clear(); break }
                            $queue.Enqueue($child)
                            $child=[System.Windows.Automation.TreeWalker]::ControlViewWalker.GetNextSibling($child)
                        }
                    }
                } catch { $labels.Clear() }
                if ($filtered) { [Console]::WriteLine('{"heartbeat":true,"status":"filtered-page"}') }
                if (-not $filtered -and [MemoryForeground]::GetForegroundWindow() -eq $handle -and [MemoryForeground]::Unlocked()) {
                    $safeTitle=Clean-Label $title
                    $text='Foreground: ' + $foregroundProcess.ProcessName + ' | ' + $safeTitle
                    if ($labels.Count) { $text+=' | ' + ($labels -join ' | ') }
                    [Console]::WriteLine(([ordered]@{title=$safeTitle.Substring(0,[Math]::Min(300,$safeTitle.Length)); text=$text.Substring(0,[Math]::Min(1600,$text.Length)); process=$foregroundProcess.ProcessName} | ConvertTo-Json -Compress))
                }
            } else { [Console]::WriteLine('{"heartbeat":true,"status":"excluded-window"}') }
        } else { [Console]::WriteLine('{"heartbeat":true,"status":"screen-locked"}') }
    } catch {
        if (-not (Get-Process -Id $OwnerPid -ErrorAction SilentlyContinue)) { break }
        try { [Console]::WriteLine('{"heartbeat":true,"status":"read-error"}') } catch { break }
    }
    Start-Sleep -Seconds 5
}
