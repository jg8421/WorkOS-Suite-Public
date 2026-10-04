$ErrorActionPreference = 'Stop'
$installRoot = Join-Path $env:LOCALAPPDATA 'PersonalMemory'
$runtime = Get-Content -LiteralPath (Join-Path $installRoot 'runtime.json') -Raw | ConvertFrom-Json
$env:PERSONAL_MEMORY_HOME = $installRoot
& $runtime.nodePath (Join-Path $installRoot 'src\mcp.mjs')
exit $LASTEXITCODE
