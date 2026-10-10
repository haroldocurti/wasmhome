# WasmHome experimental bench

This repository contains a preliminary harness for the three scenarios defined
in the TCC. Follow the [staged execution plan](docs/plano-de-execucao.md) for
scope, dependencies and acceptance criteria.

| ID | Scenario | Executed artifact | Update payload |
|---|---|---|---|
| A | Native C | ESP-IDF firmware | Complete OTA application image |
| B | WAMR interpreter | `benchmark.wasm` | Wasm module |
| C | WAMR AOT | `benchmark.aot` | WAMR AOT module for Xtensa |

All scenarios execute the same deterministic integer workload from
`workload/benchmark.c`. Keeping one implementation avoids comparing three
different algorithms by accident.

## Current state

The UDP observation contract, host-side reference codec, firmware harness,
workload, artifact builder, log collector and automated local tests are present.
The common platform source now includes sensor acquisition, GPIO loopback,
Wi-Fi and embedded UDP transport. See [platform configuration and validation](docs/plataforma-comum.md).
Initial ESP32-S3 platform diagnostics are recorded in the
[hardware verification](docs/evidence/esp32s3-hardware-2026-10-02.md).
The [external observer](docs/observador-externo.md) now sends functional control
probes and update triggers, preserves JSONL events and has local UDP calibration
with simulated reboot. Its [ESP32-S3 calibration](docs/evidence/observador-hardware-2026-10-02.md)
also covers known interruptions, physical GPIO loopback and actual reboot.
OTA, dynamic replacement and integration with the campaign modules remain to
be validated in their respective stages.
Thirty current calls are not thirty updates; loading occurs once per boot.
The common platform built locally with ESP-IDF 5.2.6 on 2026-10-02; the
[verification record](docs/evidence/plataforma-comum-2026-10-02.md) identifies
the tool versions and image hashes. WASI-SDK, `wamrc`, interpreted/AOT execution
and the LXC toolchain remain to be validated in their respective stages.

## Prerequisites

- ESP32-S3 development board
- ESP-IDF 5.2.x environment
- Python 3.10 or newer
- WASI-SDK with `clang`
- Candidate `wamrc` built from upstream WAMR 2.4.5 SHA `25bd7eb63e828e4bd242cc9b38d260b4b31c6605` with Xtensa LLVM
- Independent candidate runtime `wasmhome/wasm-micro-runtime` 2.4.5-wasmhome.1; see [candidate port and reproduction](docs/runtime-port-candidato.md)

## Build

These commands are provisional. Stage E1 must validate component/compiler
versions and isolate SDKCONFIG files per scenario before experimental use.

First acquire and check the candidate source, then generate both modular artifacts.
This candidate still requires independent review and physical B/C validation:

```powershell
python tools/runtime_package.py --acquire
$env:WASI_SDK_PATH = 'C:\tools\wasi-sdk'
$env:WAMRC = 'C:\tools\wamrc.exe'
.\tools\build-artifacts.ps1 -WamrcSourceRevision 25bd7eb63e828e4bd242cc9b38d260b4b31c6605
```

Then build each firmware from an ESP-IDF PowerShell prompt:

```powershell
idf.py -B build-native -DSDKCONFIG=build-native/sdkconfig -DEXPERIMENT_SCENARIO=native set-target esp32s3 build
idf.py -B build-interp -DSDKCONFIG=build-interp/sdkconfig -DEXPERIMENT_SCENARIO=interp `
  '-DSDKCONFIG_DEFAULTS=sdkconfig.defaults;sdkconfig.interp' set-target esp32s3 build
idf.py -B build-aot -DSDKCONFIG=build-aot/sdkconfig -DEXPERIMENT_SCENARIO=aot `
  '-DSDKCONFIG_DEFAULTS=sdkconfig.defaults;sdkconfig.aot' set-target esp32s3 build
```

Configure the Wi-Fi and observer settings locally before using the platform.
Enable `WASMHOME_RUN_BENCHMARK` for the preliminary execution check. In that mode,
the firmware emits one CSV row per measured repetition after five warm-up runs. See
[`docs/experiment-protocol.md`](docs/experiment-protocol.md) before collecting
data.

## Local checks

```powershell
python -m unittest discover -s tests -v
python tools/collect_results.py --help
```

Passing these checks proves the workload and parser contracts locally. It does
not prove that ESP-IDF builds, that the modules load on an ESP32-S3 or that AOT
works on the selected board.

The interpreted build deliberately selects WAMR's classic interpreter because
that is the mode named in the research proposal. Changing it to fast interpreter
creates a different experimental scenario and must be recorded as such.
