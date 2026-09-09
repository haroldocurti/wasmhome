# Experimental protocol

> Superseded by [Plano de execução](plano-de-execucao.md). This document records
> the initial microbenchmark proposal only. Its 30 function calls are not 30
> independent updates, and its collection instructions are not the final campaign.

## Research comparison

The independent variable is the execution and update strategy. The workload,
board, CPU frequency, power supply, compiler optimization, input data and number
of repetitions stay fixed.

The three scenarios are:

1. Native C. The workload is linked into the ESP-IDF application. Its update
   payload is the complete OTA application image and activation requires reboot.
2. WAMR interpreter. The update payload is `benchmark.wasm`; WAMR validates,
   instantiates and interprets it.
3. WAMR AOT. The update payload is `benchmark.aot`; WAMR loads code previously
   compiled for Xtensa by `wamrc`.

This is a comparison of three execution strategies, not three different test
modules. I/O, numerical processing and fault injection are separate workloads
that may be added after this baseline is stable.

## Controlled configuration

- One physical ESP32-S3 board for all runs
- Fixed 240 MHz CPU clock
- Same USB power source and cable
- ESP-IDF 5.2.x for every firmware
- WAMR ESP-IDF component `2.4.0~1` and `wamrc` built from WAMR 2.4.0
- Classic interpreter enabled only in scenario B; AOT enabled only in scenario C
- `-O3` for the shared workload in all scenarios
- Five warm-up executions, followed by 30 measured executions
- Serial monitor logging disabled except for result rows during measurement
- Board reset and a 60 second thermal settling interval before each scenario
- Scenario order randomized for each complete block when collecting more than
  one 30-run block

Record the board model, silicon revision, flash and PSRAM sizes, ESP-IDF commit,
WAMR version, WASI-SDK version, `wamrc --version`, supply voltage, date and room
temperature with every dataset.

## Primary measurements

The firmware emits these direct measurements:

- module load time in microseconds
- module instantiation time in microseconds
- workload execution time in microseconds
- internal free heap before load, after load and after instantiation
- minimum internal free heap observed since boot
- deterministic checksum and validity status

Collect these additional values outside the firmware:

- byte size and SHA-256 of `benchmark.wasm` and `benchmark.aot`
- byte size and SHA-256 of each `build-*/wasmhome_bench.bin`
- OTA transfer time from first payload byte to completed write
- service unavailability from last successful heartbeat to first successful
  heartbeat after activation
- lost sequence numbers from a fixed-rate producer during update

The current harness covers execution and memory. OTA transfer, hot-swap,
heartbeat and loss instrumentation remain separate implementation work. Do not
report zero for an unimplemented metric.

## Collection sequence

1. Build all three artifacts from a clean source revision.
2. Save tool versions, artifact sizes and SHA-256 hashes.
3. Flash the selected scenario and reset the board.
4. Wait 60 seconds with the same physical setup.
5. Capture the complete serial log until `scenario ... complete` appears.
6. Repeat for the remaining scenarios in the randomized order.
7. Extract rows only after all logs exist:

```powershell
python tools/collect_results.py `
  results/raw/native.log results/raw/interp.log results/raw/aot.log `
  --output results/processed/execution.csv
```

The collector rejects missing repetitions, unknown scenarios and any row whose
checksum failed.

## Acceptance gates

A scenario is ready for analysis only when:

- its build succeeds from the recorded clean revision
- it runs on the ESP32-S3 without reset or watchdog event
- all 30 measured rows have status `ok`
- the checksum is identical across all three scenarios
- the raw serial log and artifact hashes are preserved
- no metric is inferred from logs that do not contain its measurement boundary

Local unit tests and successful compilation are preliminary evidence. Only the
board run satisfies these gates.

## Known protocol corrections

The TCC draft mixes the three execution scenarios with three proposed workload
modules. The first experiment should cross one workload with the three execution
modes. Adding all three workloads immediately would create nine cells and make
fault behavior incomparable with normal performance.

The draft also says `wamrc --target=xtensa --cpu=esp32s3`. The WAMR documentation
lists `xtensa` as the architecture. Keep `--cpu=esp32s3` out of the reproducible
command unless the pinned `wamrc --help` explicitly accepts it and the generated
AOT module loads on the board.
