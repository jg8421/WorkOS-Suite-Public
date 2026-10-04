param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$ToolsRoot = (Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) 'tools'),
    [string]$OutputApk = (Join-Path (Split-Path -Parent $PSScriptRoot) 'dist\PersonalMemory-debug.apk')
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
$ToolsRoot = [System.IO.Path]::GetFullPath($ToolsRoot)
$androidRoot = Join-Path $ProjectRoot 'android'
$buildRoot = Join-Path $androidRoot '.manual-build'
if (-not $buildRoot.StartsWith($androidRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'Unsafe Android build path.'
}
if (Test-Path -LiteralPath $buildRoot) {
    Remove-Item -LiteralPath $buildRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $buildRoot -Force | Out-Null

$jdkRoot = (Get-ChildItem -LiteralPath (Join-Path $ToolsRoot 'jdk') -Directory | Select-Object -First 1).FullName
$sdkRoot = Join-Path $ToolsRoot 'android-sdk'
$buildTools = Join-Path $sdkRoot 'build-tools\35.0.0'
$androidJar = Join-Path $sdkRoot 'platforms\android-35\android.jar'
$aapt2 = Join-Path $buildTools 'aapt2.exe'
$d8 = Join-Path $buildTools 'd8.bat'
$zipalign = Join-Path $buildTools 'zipalign.exe'
$apksigner = Join-Path $buildTools 'apksigner.bat'
$javac = Join-Path $jdkRoot 'bin\javac.exe'
$jar = Join-Path $jdkRoot 'bin\jar.exe'
$keytool = Join-Path $jdkRoot 'bin\keytool.exe'
foreach ($required in @($androidJar, $aapt2, $d8, $zipalign, $apksigner, $javac, $jar, $keytool)) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Missing build tool: $required" }
}
$env:JAVA_HOME = $jdkRoot
$env:ANDROID_SDK_ROOT = $sdkRoot

$compiled = Join-Path $buildRoot 'compiled-resources.zip'
$generated = Join-Path $buildRoot 'generated'
$classes = Join-Path $buildRoot 'classes'
$dex = Join-Path $buildRoot 'dex'
$unsigned = Join-Path $buildRoot 'unsigned.apk'
$aligned = Join-Path $buildRoot 'aligned.apk'
$classJar = Join-Path $buildRoot 'classes.jar'
New-Item -ItemType Directory -Path $generated,$classes,$dex -Force | Out-Null

& $aapt2 compile --dir (Join-Path $androidRoot 'app\src\main\res') -o $compiled
if ($LASTEXITCODE -ne 0) { throw 'aapt2 compile failed.' }
& $aapt2 link -o $unsigned -I $androidJar --manifest (Join-Path $androidRoot 'app\src\main\AndroidManifest.xml') --java $generated --min-sdk-version 26 --target-sdk-version 35 --version-code 5 --version-name '0.4.0' --auto-add-overlay -R $compiled
if ($LASTEXITCODE -ne 0) { throw 'aapt2 link failed.' }

$sources = @(
    Get-ChildItem -LiteralPath (Join-Path $androidRoot 'app\src\main\java') -Recurse -File -Filter '*.java' | ForEach-Object FullName
    Get-ChildItem -LiteralPath $generated -Recurse -File -Filter '*.java' | ForEach-Object FullName
)
$javacArgs = @('-encoding', 'UTF-8', '-source', '17', '-target', '17', '-classpath', $androidJar, '-d', $classes) + $sources
& $javac @javacArgs
if ($LASTEXITCODE -ne 0) { throw 'javac failed.' }
& $jar --create --file $classJar -C $classes .
if ($LASTEXITCODE -ne 0) { throw 'jar creation failed.' }
& $d8 --lib $androidJar --min-api 26 --output $dex $classJar
if ($LASTEXITCODE -ne 0) { throw 'd8 failed.' }
& $jar --update --file $unsigned -C $dex classes.dex
if ($LASTEXITCODE -ne 0) { throw 'Adding classes.dex failed.' }
& $zipalign -p -f 4 $unsigned $aligned
if ($LASTEXITCODE -ne 0) { throw 'zipalign failed.' }

$keystore = Join-Path $ProjectRoot '.local-debug.keystore'
if (-not (Test-Path -LiteralPath $keystore)) {
    & $keytool -genkeypair -keystore $keystore -storepass android -keypass android -alias personalmemory -keyalg RSA -keysize 2048 -validity 10000 -dname 'CN=Personal Memory Local,O=Personal,C=CN'
    if ($LASTEXITCODE -ne 0) { throw 'Debug keystore creation failed.' }
}
$outputDirectory = Split-Path -Parent ([System.IO.Path]::GetFullPath($OutputApk))
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
& $apksigner sign --ks $keystore --ks-key-alias personalmemory --ks-pass pass:android --key-pass pass:android --out $OutputApk $aligned
if ($LASTEXITCODE -ne 0) { throw 'APK signing failed.' }
& $apksigner verify --verbose --print-certs $OutputApk
if ($LASTEXITCODE -ne 0) { throw 'APK verification failed.' }

[pscustomobject]@{
    Built = $true
    Apk = [System.IO.Path]::GetFullPath($OutputApk)
    Bytes = (Get-Item -LiteralPath $OutputApk).Length
    MinSdk = 26
    TargetSdk = 35
}
