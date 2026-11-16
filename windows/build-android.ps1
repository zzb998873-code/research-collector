[CmdletBinding()]
param(
    [string]$SdkPath,
    [string]$JavaHome = 'D:/DevEco Studio/jbr'
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression.FileSystem
$project = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$source = Join-Path $project 'android'
$runtime = Join-Path $project '.runtime/android'
if (-not $SdkPath) { $SdkPath = $runtime }
$buildTools = Join-Path $SdkPath 'build-tools/35.0.0'
if (-not (Test-Path -LiteralPath $buildTools)) { $buildTools = Join-Path $SdkPath 'android-15' }
$platform = Join-Path $SdkPath 'platforms/android-35/android.jar'
if (-not (Test-Path -LiteralPath $platform)) { $platform = Join-Path $SdkPath 'android-35/android.jar' }
$env:JAVA_HOME = $JavaHome
$env:Path = (Join-Path $JavaHome 'bin') + ';' + $env:Path
$env:TEMP = (New-Item -ItemType Directory -Path (Join-Path $runtime 'temp') -Force).FullName
$env:TMP = $env:TEMP
$build = (New-Item -ItemType Directory -Path (Join-Path $runtime ('build-' + [Guid]::NewGuid().ToString('N'))) -Force).FullName
$key = Join-Path $runtime 'collector-debug.jks'
$release = (New-Item -ItemType Directory -Path (Join-Path $project 'release') -Force).FullName
[xml]$manifest = Get-Content -LiteralPath (Join-Path $source 'AndroidManifest.xml') -Raw -Encoding UTF8
$version = $manifest.DocumentElement.GetAttribute('versionName', 'http://schemas.android.com/apk/res/android')
$apk = Join-Path $release "research-collector-nova7-$version.apk"

function Run-Tool($tool, [string[]]$command) {
    & $tool @command
    if ($LASTEXITCODE -ne 0) { throw "Android tool failed: $tool ($LASTEXITCODE)" }
}

$compiled = Join-Path $build 'resources.zip'
$unsigned = Join-Path $build 'unsigned.apk'
$aligned = Join-Path $build 'aligned.apk'
$classes = (New-Item -ItemType Directory -Path (Join-Path $build 'classes')).FullName
$dex = (New-Item -ItemType Directory -Path (Join-Path $build 'dex')).FullName

Run-Tool (Join-Path $buildTools 'aapt2.exe') @('compile','--dir',(Join-Path $source 'res'),'-o',$compiled)
Run-Tool (Join-Path $buildTools 'aapt2.exe') @('link','-I',$platform,'--manifest',(Join-Path $source 'AndroidManifest.xml'),'-o',$unsigned,$compiled)
$sources = Get-ChildItem -LiteralPath (Join-Path $source 'src') -File -Recurse -Filter '*.java' | Select-Object -ExpandProperty FullName
Run-Tool (Join-Path $JavaHome 'bin/javac.exe') (@('-encoding','UTF-8','--release','11','-classpath',$platform,'-d',$classes) + $sources)
$bytecode = Join-Path $build 'classes.jar'
[System.IO.Compression.ZipFile]::CreateFromDirectory($classes, $bytecode)
Run-Tool (Join-Path $buildTools 'd8.bat') @('--min-api','29','--lib',$platform,'--output',$dex,$bytecode)
$archive = [System.IO.Compression.ZipFile]::Open($unsigned, [System.IO.Compression.ZipArchiveMode]::Update)
try {
    [void][System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($archive, (Join-Path $dex 'classes.dex'), 'classes.dex')
} finally {
    $archive.Dispose()
}
Run-Tool (Join-Path $buildTools 'zipalign.exe') @('-f','4',$unsigned,$aligned)
if (-not (Test-Path -LiteralPath $key)) {
    Run-Tool (Join-Path $JavaHome 'bin/keytool.exe') @('-genkeypair','-keystore',$key,'-storepass','android',
        '-keypass','android','-alias','collector-debug','-keyalg','RSA','-keysize','2048','-validity','3650',
        '-dname','CN=Research Collector Debug')
}
Run-Tool (Join-Path $buildTools 'apksigner.bat') @('sign','--ks',$key,'--ks-key-alias','collector-debug',
    '--ks-pass','pass:android','--key-pass','pass:android','--out',$apk,$aligned)
Run-Tool (Join-Path $buildTools 'apksigner.bat') @('verify','--verbose',$apk)
Get-Item -LiteralPath $apk
