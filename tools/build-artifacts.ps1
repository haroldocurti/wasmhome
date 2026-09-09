[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$source = Join-Path $root 'workload\benchmark.c'
$artifacts = Join-Path $root 'artifacts'
$wasm = Join-Path $artifacts 'benchmark.wasm'
$aot = Join-Path $artifacts 'benchmark.aot'

if (-not $env:WASI_SDK_PATH) {
    throw 'Set WASI_SDK_PATH to the extracted WASI-SDK directory.'
}
if (-not $env:WAMRC) {
    throw 'Set WAMRC to the wamrc executable built from the WAMR version used by ESP-IDF.'
}

$clang = Join-Path $env:WASI_SDK_PATH 'bin\clang.exe'
if (-not (Test-Path -LiteralPath $clang)) {
    $clang = Join-Path $env:WASI_SDK_PATH 'bin\clang'
}
if (-not (Test-Path -LiteralPath $clang)) {
    throw "WASI-SDK clang not found under $env:WASI_SDK_PATH\bin."
}
if (-not (Test-Path -LiteralPath $env:WAMRC)) {
    throw "wamrc not found at $env:WAMRC."
}

New-Item -ItemType Directory -Force -Path $artifacts | Out-Null

& $clang --target=wasm32-wasi -O3 -flto -nostartfiles `
    '-Wl,--no-entry' '-Wl,--export=benchmark_run' '-Wl,--strip-all' `
    -I (Join-Path $root 'workload') -o $wasm $source
if ($LASTEXITCODE -ne 0) { throw 'Wasm compilation failed.' }

& $env:WAMRC --target=xtensa -O3 -o $aot $wasm
if ($LASTEXITCODE -ne 0) { throw 'AOT compilation failed.' }

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { throw 'Python is required to embed the generated artifacts.' }
& $python.Source (Join-Path $PSScriptRoot 'embed_artifact.py') $wasm `
    (Join-Path $artifacts 'benchmark_wasm_embedded.c') benchmark_wasm
if ($LASTEXITCODE -ne 0) { throw 'Embedding the Wasm artifact failed.' }
& $python.Source (Join-Path $PSScriptRoot 'embed_artifact.py') $aot `
    (Join-Path $artifacts 'benchmark_aot_embedded.c') benchmark_aot
if ($LASTEXITCODE -ne 0) { throw 'Embedding the AOT artifact failed.' }

Get-FileHash -Algorithm SHA256 $wasm, $aot |
    Select-Object Path, Hash |
    Format-Table -AutoSize
