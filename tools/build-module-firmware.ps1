[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidateSet('module-interp', 'module-aot')][string]$Scenario,
    [Parameter(Mandatory=$true)][string]$WasiSdkPackage,
    [Parameter(Mandatory=$true)][string]$WamrcBuildReceipt,
    [string]$BuildDirectory,
    [switch]$ModuleResilienceTestMode,
    [string]$Wat2Wasm,
    [string]$UpdateCaCertificatePath,
    [string]$UpdateSigningPublicKeyPath,
    [string]$FixtureServerIPv4,
    [string]$FixtureServerName
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

if ($ModuleResilienceTestMode) {
    if (-not $Wat2Wasm -or -not (Test-Path -LiteralPath $Wat2Wasm -PathType Leaf)) {
        throw 'Module resilience builds require the pinned WABT wat2wasm executable.'
    }
    if (-not $UpdateCaCertificatePath -or
        -not (Test-Path -LiteralPath $UpdateCaCertificatePath -PathType Leaf) -or
        -not $UpdateSigningPublicKeyPath -or
        -not (Test-Path -LiteralPath $UpdateSigningPublicKeyPath -PathType Leaf)) {
        throw 'Module resilience builds require the trusted CA PEM and signing public DER inputs.'
    }
    if ($FixtureServerIPv4 -notmatch '^\d{1,3}(\.\d{1,3}){3}$') {
        throw 'FixtureServerIPv4 must be a valid dotted IPv4 address.'
    }
    $invalidOctets = @($FixtureServerIPv4.Split('.') | Where-Object { [int]$_ -gt 255 })
    if ($invalidOctets.Count -gt 0) {
        throw 'FixtureServerIPv4 must be a valid dotted IPv4 address.'
    }
    if ($FixtureServerName -notmatch '^[A-Za-z0-9.-]+$') {
        throw 'FixtureServerName must be a DNS name without whitespace or quotes.'
    }
    $resilienceDefaults = Join-Path $BuildDirectory 'sdkconfig.module-resilience.defaults'
    $configLines = @(
        'CONFIG_WASMHOME_SIGNED_UPDATE=y',
        'CONFIG_WASMHOME_MODULE_RESILIENCE_TEST_MODE=y',
        "CONFIG_WASMHOME_UPDATE_CA_CERT_PATH=`"$($UpdateCaCertificatePath.Replace('\','/'))`"",
        "CONFIG_WASMHOME_UPDATE_SIGNING_PUBLIC_KEY_PATH=`"$($UpdateSigningPublicKeyPath.Replace('\','/'))`"",
        "CONFIG_WASMHOME_RESILIENCE_FIXTURE_SERVER_IPV4=`"$FixtureServerIPv4`"",
        "CONFIG_WASMHOME_RESILIENCE_FIXTURE_SERVER_NAME=`"$FixtureServerName`""
    )
    Set-Content -LiteralPath $resilienceDefaults -Value $configLines -Encoding ascii
    $defaults += ";$resilienceDefaults"
}

Push-Location $root
try {
    & python tools/generate_service_modules.py `
        --wasi-sdk $env:WASI_SDK_PATH `
        --wasi-sdk-package $WasiSdkPackage `
        --wamrc $env:WAMRC `
        --wamrc-build-receipt $WamrcBuildReceipt `
        --idf $env:IDF_PATH
    if ($LASTEXITCODE -ne 0) { throw 'Service module generation failed.' }

    if ($ModuleResilienceTestMode) {
        & python tools/generate_resilience_wasm.py `
            --wat2wasm $Wat2Wasm `
            --wamrc $env:WAMRC `
            --wamrc-build-receipt $WamrcBuildReceipt
        if ($LASTEXITCODE -ne 0) { throw 'Pinned resilience Wasm/AOT fixture generation failed.' }
    }

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
