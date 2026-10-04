param(
    [string]$ProjectRoot = (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)),
    [string]$ToolsRoot = ''
)
$ErrorActionPreference = 'Stop'
$ProjectRoot = [IO.Path]::GetFullPath($ProjectRoot)
if (-not $ToolsRoot) { $ToolsRoot = Join-Path (Split-Path -Parent $ProjectRoot) 'tools' }
$jdkRoot = (Get-ChildItem -LiteralPath (Join-Path $ToolsRoot 'jdk') -Directory | Select-Object -First 1).FullName
$classes = Join-Path $PSScriptRoot ('.build\privacy-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $classes -Force | Out-Null
& (Join-Path $jdkRoot 'bin\javac.exe') -encoding UTF-8 -d $classes `
    (Join-Path $ProjectRoot 'android\app\src\main\java\com\personalmemory\app\CapturePrivacy.java') `
    (Join-Path $PSScriptRoot 'CapturePrivacyChecks.java')
if ($LASTEXITCODE -ne 0) { throw 'Privacy test compilation failed.' }
& (Join-Path $jdkRoot 'bin\java.exe') -classpath $classes com.personalmemory.app.CapturePrivacyChecks
if ($LASTEXITCODE -ne 0) { throw 'Privacy checks failed.' }
