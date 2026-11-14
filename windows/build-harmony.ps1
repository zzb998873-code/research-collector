[CmdletBinding()]
param([string]$StudioPath)

$ErrorActionPreference = 'Stop'
$project = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$runtime = Join-Path $project '.runtime'
$portable = Join-Path $runtime 'DevEcoStudio'
if (-not $StudioPath) {
    $registry = @(
        'HKLM:/SOFTWARE/Microsoft/Windows/CurrentVersion/Uninstall/*',
        'HKLM:/SOFTWARE/WOW6432Node/Microsoft/Windows/CurrentVersion/Uninstall/*',
        'HKCU:/SOFTWARE/Microsoft/Windows/CurrentVersion/Uninstall/*'
    )
    $installed = Get-ItemProperty -Path $registry -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayName -eq 'DevEco Studio' -and $_.InstallLocation } |
        Select-Object -First 1 -ExpandProperty InstallLocation
    $StudioPath = if ($installed) { $installed } else { $portable }
}
$studio = (Resolve-Path -LiteralPath $StudioPath).Path
$wrapper = Join-Path $studio 'tools/hvigor/bin/hvigorw.js'
$node = Join-Path $studio 'tools/node/node.exe'
if (-not (Test-Path -LiteralPath $wrapper) -or -not (Test-Path -LiteralPath $node)) {
    throw "The DevEco Studio toolchain is missing from $studio. Pass -StudioPath to select an installation."
}

$env:DEVECO_SDK_HOME = Join-Path $studio 'sdk'
$env:JAVA_HOME = Join-Path $studio 'jbr'
$env:NODE_HOME = Join-Path $studio 'tools/node'
$env:Path = $env:NODE_HOME + ';' + (Join-Path $env:JAVA_HOME 'bin') + ';' + $env:Path
$buildOptions = @('--analyze=normal', '--parallel', '--incremental', '--daemon')
if ($studio -eq $portable) {
    $env:HVIGOR_USER_HOME = Join-Path $runtime 'hvigor-home'
    $env:npm_config_cache = Join-Path $runtime 'npm-cache'
    $buildOptions = @('--no-daemon')
}

Push-Location (Join-Path $project 'harmony')
try {
    & $node $wrapper --mode module -p module=entry@default -p product=default -p requiredDeviceType=phone assembleHap @buildOptions
    if ($LASTEXITCODE -ne 0) { throw "HarmonyOS build failed with exit code $LASTEXITCODE." }
    Get-ChildItem -LiteralPath 'entry/build/default/outputs/default' -Filter '*.hap'
} finally {
    Pop-Location
}
