param(
    [Parameter(Mandatory = $true)][string]$Source,
    [Parameter(Mandatory = $true)][string]$Output,
    [Parameter(Mandatory = $true)][ValidateSet('word', 'powerpoint')][string]$Kind
)

$ErrorActionPreference = 'Stop'

# Office apps create work files under %TEMP%.  If TEMP/TMP are missing or point
# to an invalid path (e.g. a stale 8.3 short name), Word shows a modal dialog
# ("could not create the work file") and automation blocks forever.  Always
# normalise to the real per-user temp folder before starting Office.
$normalisedTemp = $null
foreach ($candidate in @($env:TEMP, $env:TMP, (Join-Path $env:LOCALAPPDATA 'Temp'), (Join-Path $env:USERPROFILE 'AppData\Local\Temp'))) {
    if ($candidate) {
        try {
            $resolved = [System.IO.Path]::GetFullPath([System.Environment]::ExpandEnvironmentVariables($candidate))
            if (-not (Test-Path -LiteralPath $resolved -PathType Container)) {
                New-Item -ItemType Directory -Path $resolved -Force | Out-Null
            }
            $probe = Join-Path $resolved ('fw-probe-' + [guid]::NewGuid().ToString('N') + '.tmp')
            Set-Content -LiteralPath $probe -Value 'x' -ErrorAction Stop
            Remove-Item -LiteralPath $probe -Force -ErrorAction SilentlyContinue
            $normalisedTemp = $resolved
            break
        } catch {
            continue
        }
    }
}
if (-not $normalisedTemp) { throw 'No usable TEMP folder found for Office rendering.' }
$env:TEMP = $normalisedTemp
$env:TMP = $normalisedTemp

# Files downloaded from the internet carry a Zone.Identifier (Mark-of-the-Web)
# that makes Word open them in Protected View and fail COM automation with a
# misleading "file appears to be corrupted" error.  Open a temp copy instead,
# which is always treated as a local file.  The copy is removed afterwards.
$workSource = $Source
$tempCopy = $null
try {
    $zone = Get-Content -LiteralPath ($Source + ':Zone.Identifier') -ErrorAction SilentlyContinue
    if ($zone -match 'ZoneId\s*=\s*[1-7]') {
        $tempCopy = Join-Path $normalisedTemp ('fw-src-' + [guid]::NewGuid().ToString('N') + [System.IO.Path]::GetExtension($Source))
        Copy-Item -LiteralPath $Source -Destination $tempCopy -Force
        $workSource = $tempCopy
    }
} catch {
    $workSource = $Source
}

$application = $null
$document = $null
try {
    if ($Kind -eq 'word') {
        $application = New-Object -ComObject Word.Application
        $application.Visible = $false
        $application.DisplayAlerts = 0
        # ReadOnly + skip the recent-files list so a background preview leaves
        # no trace and never shows a window.
        $document = $application.Documents.Open($workSource, $false, $true, $false)
        $document.ExportAsFixedFormat($Output, 17)
    }
    else {
        $application = New-Object -ComObject PowerPoint.Application
        # Never touch Visible: PS 5.1 cannot bind MsoTriState and hiding an
        # already window-less app raises "Invalid request".  Opening with
        # WithWindow=$false keeps every slide window hidden.
        $document = $application.Presentations.Open($workSource, $true, $false, $false)
        # ExportAsFixedFormat is unreliable through PowerShell COM binding;
        # SaveAs with ppSaveAsPDF (32) produces the same native PDF.
        $document.SaveAs($Output, 32)
    }
    if (-not (Test-Path -LiteralPath $Output)) {
        throw "Export produced no output file: $Output"
    }
}
finally {
    if ($document) { try { $document.Close($false) } catch { try { $document.Close() } catch {} } }
    if ($application) { try { $application.Quit() } catch {} }
    if ($tempCopy) { try { Remove-Item -LiteralPath $tempCopy -Force -ErrorAction SilentlyContinue } catch {} }
}
