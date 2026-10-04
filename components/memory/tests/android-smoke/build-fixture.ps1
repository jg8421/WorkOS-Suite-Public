param(
    [string]$ProjectRoot = (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)),
    [string]$ToolsRoot = '',
    [string]$OutputApk = (Join-Path $PSScriptRoot 'dist\MemoryCaptureSmoke.apk')
)
$ErrorActionPreference = 'Stop'
$ProjectRoot = [IO.Path]::GetFullPath($ProjectRoot)
if (-not $ToolsRoot) { $ToolsRoot = Join-Path (Split-Path -Parent $ProjectRoot) 'tools' }
$ToolsRoot = [IO.Path]::GetFullPath($ToolsRoot)
$fixtureRoot = [IO.Path]::GetFullPath($PSScriptRoot)
$buildRoot = Join-Path $fixtureRoot ('.build\' + [Guid]::NewGuid().ToString('N'))
$classes = Join-Path $buildRoot 'classes'
$dex = Join-Path $buildRoot 'dex'
New-Item -ItemType Directory -Path $classes,$dex -Force | Out-Null
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
foreach ($required in @($androidJar,$aapt2,$d8,$zipalign,$apksigner,$javac,$jar,$keytool)) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Missing build tool: $required" }
}
$env:JAVA_HOME = $jdkRoot
$unsigned = Join-Path $buildRoot 'unsigned.apk'
$aligned = Join-Path $buildRoot 'aligned.apk'
$classJar = Join-Path $buildRoot 'classes.jar'
& $aapt2 link -o $unsigned -I $androidJar --manifest (Join-Path $fixtureRoot 'AndroidManifest.xml') --min-sdk-version 26 --target-sdk-version 35 --version-code 1 --version-name '1.0'
if ($LASTEXITCODE -ne 0) { throw 'Fixture manifest link failed.' }
& $javac -encoding UTF-8 -source 17 -target 17 -classpath $androidJar -d $classes (Join-Path $fixtureRoot 'SmokeActivity.java')
if ($LASTEXITCODE -ne 0) { throw 'Fixture javac failed.' }
& $jar --create --file $classJar -C $classes .
if ($LASTEXITCODE -ne 0) { throw 'Fixture class jar failed.' }
& $d8 --lib $androidJar --min-api 26 --output $dex $classJar
if ($LASTEXITCODE -ne 0) { throw 'Fixture dex failed.' }
& $jar --update --file $unsigned -C $dex classes.dex
if ($LASTEXITCODE -ne 0) { throw 'Fixture dex packaging failed.' }
& $zipalign -p -f 4 $unsigned $aligned
if ($LASTEXITCODE -ne 0) { throw 'Fixture zipalign failed.' }
$keystore = Join-Path $buildRoot 'fixture-debug.keystore'
& $keytool -genkeypair -keystore $keystore -storepass android -keypass android -alias fixture -keyalg RSA -keysize 2048 -validity 10000 -dname 'CN=Local Test Fixture'
if ($LASTEXITCODE -ne 0) { throw 'Fixture signing key failed.' }
$OutputApk = [IO.Path]::GetFullPath($OutputApk)
New-Item -ItemType Directory -Path (Split-Path -Parent $OutputApk) -Force | Out-Null
& $apksigner sign --ks $keystore --ks-key-alias fixture --ks-pass pass:android --key-pass pass:android --out $OutputApk $aligned
if ($LASTEXITCODE -ne 0) { throw 'Fixture signing failed.' }
& $apksigner verify --verbose $OutputApk
if ($LASTEXITCODE -ne 0) { throw 'Fixture signature verification failed.' }
& $aapt2 dump permissions $OutputApk
if ($LASTEXITCODE -ne 0) { throw 'Fixture permission inspection failed.' }
[pscustomobject]@{ Apk=$OutputApk; Package='com.personalmemory.smokefixture'; Permissions='None'; BuildRoot=$buildRoot }
