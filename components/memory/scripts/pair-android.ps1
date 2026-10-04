param(
    [Parameter(Mandatory = $true)][string]$ApkPath,
    [string]$Endpoint = 'http://127.0.0.1:18765',
    [string]$InstallRoot = "$env:LOCALAPPDATA\PersonalMemory",
    [string]$Device,
    [switch]$UseAdbReverse,
    [switch]$SkipInstall
)

$ErrorActionPreference = 'Stop'
$adb = (Get-Command adb -ErrorAction Stop).Source
$apk = (Resolve-Path -LiteralPath $ApkPath).Path
$config = Get-Content -LiteralPath (Join-Path $InstallRoot 'config.json') -Raw | ConvertFrom-Json
$deviceArgs = @()
if ($Device) { $deviceArgs = @('-s', $Device) }
if ($UseAdbReverse) {
    & $adb @deviceArgs reverse tcp:18765 tcp:18765 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "ADB reverse failed with exit code $LASTEXITCODE" }
    $Endpoint = 'http://127.0.0.1:18765'
}
if (-not $SkipInstall) {
    & $adb @deviceArgs install -r $apk
    if ($LASTEXITCODE -ne 0) { throw "APK installation failed with exit code $LASTEXITCODE" }
}
& $adb @deviceArgs shell am start -n com.personalmemory.app/com.personalmemory.app.MainActivity --es endpoint $Endpoint --es token $config.token | Out-Null
[pscustomobject]@{
    Installed = $true
    Endpoint = $Endpoint
    TokenPrinted = $false
}
