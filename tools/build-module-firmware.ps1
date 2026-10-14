[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidateSet('module-interp', 'module-aot')][string]$Scenario,
    [Parameter(Mandatory=$true)][string]$WasiSdkPackage,
    [Parameter(Mandatory=$true)][string]$WamrcBuildReceipt,
    [string]$BuildDirectory
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if (-not $BuildDirectory) { $BuildDirectory = Join-Path $root "build/$Scenario" }
$BuildDirectory = [System.IO.Path]::GetFullPath($BuildDirectory)
if (-not $env:WASI_SDK_PATH -or -not $env:WAMRC -or -not $env:IDF_PATH) {
    throw 'Set WASI_SDK_PATH, WAMRC and IDF_PATH to the verified toolchain paths.'
}

$variant = if ($Scenario -eq 'module-interp') { 'interp' } else { 'aot' }
$defaults = "sdkconfig.defaults;sdkconfig.$variant;sdkconfig.module-services"
$sdkconfig = Join-Path $BuildDirectory 'sdkconfig'
New-Item -ItemType Directory -Force -Path $BuildDirectory | Out-Null
$env:WASMHOME_EXPERIMENT_SCENARIO = $Scenario

Push-Location $root
try {
    & python tools/generate_service_modules.py `
        --wasi-sdk $env:WASI_SDK_PATH `
        --wasi-sdk-package $WasiSdkPackage `
        --wamrc $env:WAMRC `
        --wamrc-build-receipt $WamrcBuildReceipt `
        --idf $env:IDF_PATH
    if ($LASTEXITCODE -ne 0) { throw 'Service module generation failed.' }

    & idf.py -B $BuildDirectory `
        -D "IDF_TARGET=esp32s3" `
        -D "EXPERIMENT_SCENARIO=$Scenario" `
        -D "SDKCONFIG=$sdkconfig" `
        -D "SDKCONFIG_DEFAULTS=$defaults" `
        -D "WAMR_BUILD_ALLOC_WITH_USAGE=1" build
    if ($LASTEXITCODE -ne 0) { throw "ESP-IDF build failed for $Scenario." }
} finally {
    Pop-Location
}
