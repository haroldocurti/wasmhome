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

The protocol, firmware harness, workload, artifact builder, log collector and
automated local tests are present. No ESP32 result has been collected yet.
OTA, dynamic replacement and external monitoring remain to be implemented.
Thirty current calls are not thirty updates; loading occurs once per boot.
The last environment inspection found Docker and Git, but no ESP-IDF environment,
CMake, Ninja, WASI-SDK or `wamrc` in `PATH`. Recheck in stage E0.

## Prerequisites

- ESP32-S3 development board
- ESP-IDF 5.2.x environment
- Python 3.10 or newer
- WASI-SDK with `clang`
- `wamrc` built from WAMR 2.4.0, matching the pinned ESP-IDF component

## Build

These commands are provisional. Stage E1 must validate component/compiler
versions and isolate SDKCONFIG files per scenario before experimental use.

First generate the two modular artifacts:

```powershell
$env:WASI_SDK_PATH = 'C:\tools\wasi-sdk'
$env:WAMRC = 'C:\tools\wamrc.exe'
.\tools\build-artifacts.ps1
```

Then build each firmware from an ESP-IDF PowerShell prompt:

```powershell
idf.py -B build-native -DEXPERIMENT_SCENARIO=native set-target esp32s3 build
idf.py -B build-interp -DEXPERIMENT_SCENARIO=interp `
  '-DSDKCONFIG_DEFAULTS=sdkconfig.defaults;sdkconfig.interp' set-target esp32s3 build
idf.py -B build-aot -DEXPERIMENT_SCENARIO=aot `
  '-DSDKCONFIG_DEFAULTS=sdkconfig.defaults;sdkconfig.aot' set-target esp32s3 build
```

Flash one scenario at a time and save the serial output. The firmware emits one
CSV row per measured repetition after five warm-up runs. See
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
