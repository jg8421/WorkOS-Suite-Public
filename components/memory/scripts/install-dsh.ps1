param(
    [string]$InstallRoot = "$env:LOCALAPPDATA\PersonalMemory",
    [string]$DshHome = "$env:USERPROFILE\.dsh",
    [string]$Profile = 'web'
)

$ErrorActionPreference = 'Stop'
$nodePath = (Get-Command node -ErrorAction Stop).Source
$mcpPath = Join-Path $InstallRoot 'src\mcp.mjs'
$patchPath = Join-Path $DshHome "profiles\$Profile\cordis.patch.yml"
$skillSource = Join-Path $PSScriptRoot '..\plugin\skills\personal-memory'
$skillTarget = Join-Path $DshHome 'skills\personal-memory'

if (-not (Test-Path -LiteralPath $mcpPath)) { throw "Memory MCP not installed: $mcpPath" }
if (-not (Test-Path -LiteralPath $patchPath)) { throw "DSH profile patch not found: $patchPath" }

$existing = Get-Content -LiteralPath $patchPath -Raw
if ($existing -notmatch '(?m)^\s*- id: mcp-personal-memory\s*$') {
    $yaml = @(
        '',
        '- insert:',
        '    - id: mcp-personal-memory',
        '      name: "@deepseek-ai/dsh-mcp-client"',
        '      config:',
        '        serverName: personal_memory',
        '        transport: stdio',
        "        command: '$($nodePath.Replace("'", "''"))'",
        '        args:',
        "          - '$($mcpPath.Replace("'", "''"))'",
        '        failOnStartupError: false',
        '        toolCallTimeoutMs: 60000'
    ) -join [Environment]::NewLine
    [System.IO.File]::AppendAllText($patchPath, $yaml + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
}

New-Item -ItemType Directory -Path $skillTarget -Force | Out-Null
Copy-Item -Path (Join-Path $skillSource '*') -Destination $skillTarget -Recurse -Force

[pscustomobject]@{
    Installed = $true
    Profile = $Profile
    PatchPath = $patchPath
    SkillPath = Join-Path $skillTarget 'SKILL.md'
    ServerName = 'personal_memory'
    TokenPrinted = $false
}
