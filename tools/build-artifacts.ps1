[CmdletBinding()]
param([string]$WasiSdkPackage, [string]$WamrcBuildReceipt, [switch]$RecoverPublication)
$ErrorActionPreference = 'Stop'
$python = (Get-Command python -ErrorAction Stop).Source
$generator = Join-Path $PSScriptRoot 'generate_artifacts.py'
if ($RecoverPublication) {
    & $python $generator --recover-publication
} else {
    if (-not $env:WASI_SDK_PATH -or -not $env:WAMRC -or -not $env:IDF_PATH -or
        -not $WasiSdkPackage -or -not $WamrcBuildReceipt) {
        throw 'Supply WASI_SDK_PATH, WAMRC, IDF_PATH, -WasiSdkPackage and -WamrcBuildReceipt. Missing origin proof blocks generation before writes.'
    }
    & $python $generator --wasi-sdk $env:WASI_SDK_PATH --wamrc $env:WAMRC `
        --idf $env:IDF_PATH --wasi-sdk-package $WasiSdkPackage --wamrc-build-receipt $WamrcBuildReceipt
}
if ($LASTEXITCODE -ne 0) { throw 'Artifact generation/recovery failed; inspect the preserved previous set and transaction journal.' }
