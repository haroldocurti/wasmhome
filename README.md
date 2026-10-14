# WasmHome experimental bench

This repository contains a preliminary harness for the three scenarios defined
in the TCC. Follow the [staged execution plan](docs/plano-de-execucao.md) for
scope, dependencies and acceptance criteria.

| ID | Scenario | Executed artifact | Update payload |
|---|---|---|---|
| A | Native C | ESP-IDF firmware | Complete OTA application image |
| B | WAMR interpreter | `benchmark.wasm` | Wasm module |
| C | WAMR AOT | `benchmark.aot` | WAMR AOT module for Xtensa |

The deterministic integer workload in `workload/benchmark.c` is a preliminary
execution harness. It is not the final campaign application or Embench. The
campaign compares the common telemetry and GPIO-control services defined in
`CONTEXT.md` and the [staged execution plan](docs/plano-de-execucao.md).

## Current state

Status synchronized on 2026-10-13 from the 2026-10-12 evidence. The
[Linux handoff record](docs/retomada-linux.md) records the integrated observer/LXC
deliveries, SDK preparation, compatible LLVM/wamrc R5 and verified A/B/C firmware
builds on CT107. Wasm/AOT artifacts were reproduced with identical hashes; see
the [E1 build evidence](docs/evidence/retomada-linux-2026-10-12/e1-scenarios/README.md).
Local tests for the latest changes, independent compiler attestation and #7
review/acceptance remain pending. A/B/C execution on the ESP32-S3 (#9), campaign
services, updates and experimental campaigns are still pending. No firmware was
flashed during the 2026-10-12 handoff.

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
the tool versions and image hashes. The 2026-10-12 evidence additionally records
verified SDKs, `wamrc` host execution and A/B/C builds on CT107. Interpreted/AOT
execution on the device remains to be validated; host compilation and artifact
reproduction do not establish physical viability.

## Prerequisites

- ESP32-S3 development board
- ESP-IDF 5.2.6 at Git SHA `9ef24e3e2a2c96e720d83c574a3f8699177573da`
- Python 3.11 or newer
- Official WASI-SDK 27.0 Linux x86_64 package and its verified extraction
- Candidate `wamrc` built from upstream WAMR 2.4.5 SHA `25bd7eb63e828e4bd242cc9b38d260b4b31c6605` with Xtensa LLVM
- Independent candidate runtime `wasmhome/wasm-micro-runtime` 2.4.5-wasmhome.3; see [candidate port and reproduction](docs/runtime-port-candidato.md)

## Build

These commands are provisional. Stage E1 must validate component/compiler
versions and isolate SDKCONFIG files per scenario before experimental use.

First acquire and check the candidate source, then generate both modular artifacts.
Generation runs in PowerShell Linux and requires a clean WasmHome revision plus
the real donor build receipt described in the candidate document. Missing proof
blocks generation before writes. Independent review and physical B/C validation
remain pending:

```powershell
python tools/runtime_package.py --acquire
$env:WASI_SDK_PATH = '/tools/wasi-sdk-27.0-x86_64-linux'
$env:WAMRC = '/tools/wamrc'
$env:IDF_PATH = '/tools/esp-idf'
./tools/build-artifacts.ps1 -WasiSdkPackage /receipts/wasi-sdk-27.0-x86_64-linux.tar.gz `
  -WamrcBuildReceipt /receipts/wamrc-build-receipt.json
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

## Module manager firmware (#12)

The ABI-v1 guest sources are `workload/service_telemetry.c` and
`workload/service_control.c`. `tools/generate_service_modules.py` builds their
Wasm and Xtensa AOT images and generates the embedded C files under the ignored
`artifacts/` directory. The build wrapper runs that generator, verifies the
toolchain receipts, and gives each scenario its own `sdkconfig` and build
directory. The generated services prove loading and the manager ABI; physical
acceptance and workload behavior remain pending.

Set `WASI_SDK_PATH`, `WAMRC`, and `IDF_PATH`, then build either dedicated
scenario from the ESP-IDF PowerShell environment:

```powershell
./tools/build-module-firmware.ps1 -Scenario module-interp `
  -WasiSdkPackage /receipts/wasi-sdk-27.0-x86_64-linux.tar.gz `
  -WamrcBuildReceipt /receipts/wamrc-build-receipt.json
./tools/build-module-firmware.ps1 -Scenario module-aot `
  -WasiSdkPackage /receipts/wasi-sdk-27.0-x86_64-linux.tar.gz `
  -WamrcBuildReceipt /receipts/wamrc-build-receipt.json
```

These builds use the module-service sdkconfig profile, set the target to
ESP32-S3, and enable WAMR's usage-aware allocator without changing the pinned
runtime package. Keep Wi-Fi and observer credentials in a local sdkconfig, not
in the checked-in profile. The firmware emits `WASMHOME_MODULE_MEMORY` snapshots
at runtime, slot load/instantiate, candidate overlap, and release boundaries;
they separate ESP-IDF heap readings from WAMR allocator counters and label queue
payload capacity, requested worker stacks, and retained watchdog slots as
manager-side categories. Watchdog timer handles and gates stay with the static
slots across generations because ESP-IDF 5.2.6 dispatches timer deletion
asynchronously; late callbacks check the active call's deadline.

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
