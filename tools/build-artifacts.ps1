[CmdletBinding()]
param([string]$WamrcSourceRevision)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$artifacts = [IO.Path]::GetFullPath((Join-Path $root 'artifacts'))
if (-not $env:WASI_SDK_PATH) { throw 'Set WASI_SDK_PATH to the extracted WASI-SDK directory.' }
if (-not $env:WAMRC) { throw 'Set WAMRC to a WAMR 2.4.5 compiler built with the Xtensa LLVM backend.' }
if ($WamrcSourceRevision -cne '25bd7eb63e828e4bd242cc9b38d260b4b31c6605') { throw 'Compiler source must match upstream WAMR 2.4.5 SHA 25bd7eb63e828e4bd242cc9b38d260b4b31c6605.' }
$clang = Join-Path $env:WASI_SDK_PATH 'bin\clang.exe'
if (-not (Test-Path -LiteralPath $clang)) { $clang = Join-Path $env:WASI_SDK_PATH 'bin\clang' }
foreach ($tool in @($clang, $env:WAMRC)) {
    if (-not (Test-Path -LiteralPath $tool -PathType Leaf)) { throw "Compiler not found: $tool" }
}
$python = (Get-Command python -ErrorAction Stop).Source
$runtimeJson = (& $python (Join-Path $PSScriptRoot 'validate_build_config.py') --runtime-identity | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Resolved runtime identity/checksums could not be verified.' }
$runtimeIdentity = $runtimeJson | ConvertFrom-Json -AsHashtable
if ($runtimeIdentity.component -ne 'wasmhome/wasm-micro-runtime' -or
    $runtimeIdentity.version -ne '2.4.5-wasmhome.1' -or
    $runtimeIdentity.repository_revision -cne '25bd7eb63e828e4bd242cc9b38d260b4b31c6605' -or
    $runtimeIdentity.aot_version -ne 5) { throw 'Resolved runtime differs from the independent WAMR 2.4.5 candidate.' }
$clangVersion = (& $clang --version 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { throw 'clang version query failed.' }
$wamrcVersion = (& $env:WAMRC --version 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $wamrcVersion -notmatch '^wamrc\s+2\.4\.5$') {
    throw 'wamrc version must be 2.4.5 to match the candidate runtime pin.'
}
New-Item -ItemType Directory -Force -Path $artifacts | Out-Null
$manifestPath = Join-Path $artifacts 'build-manifest.json'
# A manifest is the commit marker. Invalidate it before touching any payload.
if (Test-Path -LiteralPath $manifestPath) { Remove-Item -LiteralPath $manifestPath }
$stage = Join-Path $artifacts ('.staging-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $stage | Out-Null
try {
    $source = Join-Path $root 'workload\benchmark.c'
    $wasm = Join-Path $stage 'benchmark.wasm'
    $aot = Join-Path $stage 'benchmark.aot'
    $assembly = Join-Path $stage 'benchmark.wasm.s'
    $clangFlags = @('--target=wasm32-wasi', '-O3', '-nostdlib', '-I', (Join-Path $root 'workload'))
    $linkFlags = @('-Wl,--no-entry', '-Wl,--export=benchmark_run', '-Wl,--strip-all', '-Wl,-z,stack-size=8192', '-Wl,--initial-memory=65536')
    & $clang @clangFlags @linkFlags -o $wasm $source
    if ($LASTEXITCODE -ne 0) { throw 'Wasm compilation failed.' }
    & $clang @clangFlags -S -o $assembly $source
    if ($LASTEXITCODE -ne 0) { throw 'Wasm assembly generation failed.' }
    if ((Get-Content -LiteralPath $assembly -Raw) -notmatch '(?m)^\s*loop\b') {
        throw 'Generated Wasm assembly has no loop; investigate workload elimination before E1.'
    }
    # Explicit memory bounds checks; no unverified esp32s3 CPU flag.
    $aotFlags = @('--target=xtensa', '--bounds-checks=1', '--disable-llvm-lto', '-O3')
    & $env:WAMRC @aotFlags -o $aot $wasm
    if ($LASTEXITCODE -ne 0) { throw 'AOT compilation failed. Confirm the Xtensa LLVM backend.' }
    foreach ($kind in @('wasm', 'aot')) {
        $binary = Join-Path $stage "benchmark.$kind"
        & $python (Join-Path $PSScriptRoot 'embed_artifact.py') $binary (Join-Path $stage "benchmark_${kind}_embedded.c") "benchmark_$kind"
        if ($LASTEXITCODE -ne 0) { throw "Embedding $kind failed." }
    }
    $files = @{}
    foreach ($file in Get-ChildItem -LiteralPath $stage -File) {
        $files[$file.Name] = @{ sha256 = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant(); bytes = $file.Length }
    }
    $manifest = @{
        aot_format_version = 5
        schema = 'wasmhome-e1-artifacts-v2'; status = 'generated_not_device_validated'
        runtime_identity = $runtimeIdentity
        wamrc_source_revision_declared = $WamrcSourceRevision
        wamrc_source_provenance = 'operator_declaration_not_binary_origin_proof'
        clang_version = $clangVersion; wamrc_version = $wamrcVersion
        clang_path = [IO.Path]::GetFullPath($clang); wamrc_path = [IO.Path]::GetFullPath($env:WAMRC)
        clang_sha256 = (Get-FileHash -LiteralPath $clang -Algorithm SHA256).Hash.ToLowerInvariant()
        wamrc_sha256 = (Get-FileHash -LiteralPath $env:WAMRC -Algorithm SHA256).Hash.ToLowerInvariant()
        source_sha256 = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
        header_sha256 = (Get-FileHash -LiteralPath (Join-Path $root 'workload\benchmark.h') -Algorithm SHA256).Hash.ToLowerInvariant()
        clang_flags = @($clangFlags + $linkFlags); aot_flags = $aotFlags; files = $files
        expected_checksum = '0xDBB2F0D4'; pending = @('inspect native and AOT disassembly', 'execute checksum on ESP32-S3', 'accept runtime pin after security review')
    }
    foreach ($file in Get-ChildItem -LiteralPath $stage -File) {
        Move-Item -LiteralPath $file.FullName -Destination (Join-Path $artifacts $file.Name) -Force
    }
    $manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $manifestPath -Encoding utf8
    & $python (Join-Path $PSScriptRoot 'validate_build_config.py') --artifact-integrity
    if ($LASTEXITCODE -ne 0) {
        Remove-Item -LiteralPath $manifestPath
        throw 'Published artifact set failed independent integrity validation.'
    }
    Write-Output "Generated artifacts and manifest at $artifacts. Device validation remains pending."
} finally {
    if ((Split-Path -Parent ([IO.Path]::GetFullPath($stage))) -ne $artifacts) { throw 'Unexpected staging path.' }
    Remove-Item -LiteralPath $stage -Recurse -Force
}
