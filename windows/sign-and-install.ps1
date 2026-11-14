[CmdletBinding()]
param([switch]$UseExistingSignature, [string]$StudioPath, [string]$HdcServer = '127.0.0.1:8710')

$ErrorActionPreference = 'Stop'
$project = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$runtime = Join-Path $project '.runtime'
$studio = Join-Path $runtime 'DevEcoStudio'
$cli = Join-Path $runtime 'deveco-cli/node_modules/.bin/devecocli.cmd'

Push-Location (Join-Path $project 'harmony')
try {
    if (-not $UseExistingSignature) {
        $env:DEVECO_CLI_STUDIO_PATH = $studio
        $env:DEVECO_SDK_HOME = Join-Path $studio 'sdk'
        & $cli signature generate --product default
        if ($LASTEXITCODE -ne 0) { throw 'Could not generate the Huawei debug signature.' }
    }
    & (Join-Path $PSScriptRoot 'build-harmony.ps1') -StudioPath $StudioPath
    $hdc = Join-Path $env:DEVECO_SDK_HOME 'default/openharmony/toolchains/hdc.exe'
    $hap = Get-Item -LiteralPath 'entry/build/default/outputs/default/entry-default-signed.hap'
    $result = & $hdc -s $HdcServer install -r $hap.FullName 2>&1 | Out-String
    Write-Output $result
    if ($LASTEXITCODE -ne 0 -or $result -notmatch '(?i)success') {
        throw 'The device did not confirm installation.'
    }
    $start = & $hdc -s $HdcServer shell aa start -a EntryAbility -b cn.researchwiki.collector 2>&1 | Out-String
    Write-Output $start
    if ($LASTEXITCODE -ne 0 -or $start -notmatch '(?i)success') {
        throw 'The device did not confirm application startup.'
    }
} finally {
    Pop-Location
}
