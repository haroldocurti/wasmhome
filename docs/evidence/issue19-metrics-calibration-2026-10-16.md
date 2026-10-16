# Issue #19 — comparative metrics calibration

The offline adapter `tools/compare_update_metrics.py` normalizes the reviewed
physical results from issues #16, #17 and #18 and the common E1 execution
benchmark. It replays the offline analyzers over matching private UART/UDP files
and publishes derived values and input hashes only. It does not contact the
ESP32, observer, or artifact server, and it does not copy raw logs into Git.
Recreate the machine-readable comparison with the local private run directory:

```sh
PRIVATE_ROOT=/path/to/wasmhome-private/issues16-18-integration-20261015
python3 tools/compare_update_metrics.py \
  --native-uart "$PRIVATE_ROOT/hil-native-20261016-02-r3/attempt-001/uart.log" \
  --native-observations "$PRIVATE_ROOT/hil-native-20261016-02-r3/attempt-001/observations.jsonl" \
  --native-summary "$PRIVATE_ROOT/hil-native-20261016-02-r3/attempt-001/summary.json" \
  --interp-uart "$PRIVATE_ROOT/hil-interp-20261016-02/main/uart.log" \
  --interp-observations "$PRIVATE_ROOT/hil-interp-20261016-02/main/observer/observations.jsonl" \
  --interp-summary "$PRIVATE_ROOT/hil-interp-20261016-02/main/observer/summary.json" \
  --interp-smoke-verification "$PRIVATE_ROOT/hil-interp-20261016-02/smoke/20261016T1529Z/smoke-verification.json" \
  --interp-restoration-verification "$PRIVATE_ROOT/hil-interp-20261016-02/restoration/restoration-verification.json" \
  --aot-uart "$PRIVATE_ROOT/hil-aot-20261016-05/main/uart-aot.log" \
  --aot-observations "$PRIVATE_ROOT/hil-aot-20261016-05/main/observer/observations.jsonl" \
  --aot-summary "$PRIVATE_ROOT/hil-aot-20261016-05/main/observer/summary.json" \
  --aot-smoke-verification "$PRIVATE_ROOT/hil-aot-20261016-05/smoke/smoke-verification.json" \
  --aot-restoration-verification "$PRIVATE_ROOT/hil-aot-20261016-05/restoration/restore-verification.json" \
  --output /tmp/wasmhome-metrics-calibration.json
```

The generated report records SHA-256 for the public evidence, each private main
capture, and the smoke/restoration verification reports. It checks private
capture hashes against the receipts published by #16–#18 and fails closed if a
result, payload transfer, functional oracle, event ID, clock boundary, memory
snapshot, or workload checksum is absent or inconsistent. It writes
exclusively and will not replace an earlier report.

## Calibrated physical update metrics

| Scenario | v2 artifact / received bytes | Trigger → first correct v2 | Acceptance → first correct v2 | Minimum free internal heap | Minimum free PSRAM | Control requests | Timeouts (rate) | Late results | Maximum control gap |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Native OTA | 898,832 / 898,832 B | 16.303452369 s | 16.296308228 s | 225,379 B | 8,384,416 B | 2,965 | 34 (1.147%) | 0 | 3.495783592 s |
| WAMR interpreter | 328 / 328 B | 7.123206406 s | 7.117390684 s | 151,383 B | 8,097,276 B | 899 | 14 (1.557%) | 8 | 1.510080355 s |
| WAMR AOT | 1,256 / 1,256 B | 6.624129703 s | 6.618637635 s | 139,719 B | 8,082,796 B | 899 | 14 (1.557%) | 8 | 1.505387202 s |

The raw analyzers confirm complete transfers and matching active-image hashes
for all three scenarios. Timeout sample IDs and the subset that later returned
late are in the JSON report. Late results remain separate from timely valid
controls. These bytes are artifact and transfer costs; they are not evidence
that the artifacts should have equal size.

Per-phase device durations and free-heap/PSRAM snapshots for every scenario are
in the JSON report. Native phases remain separated by boot; interpreter and
AOT stages use the MCU clock within their single boot. The report keeps the
largest control gap, v1-to-v2 transition gap, update-window telemetry gap, full
observation window, timeout IDs and unresolved sequence gaps separate.

## Smoke and physical restoration

Post-reboot smoke passed for both modular scenarios. Interpreter recorded 203
valid telemetry events and 199 correct v2 controls on a boot distinct from the
update capture, with zero smoke errors. AOT recorded 203 valid telemetry events,
199/199 correct controls, zero timeouts and zero unresolved sequence gaps.
Neither smoke run sent an update trigger.

Both restoration reports verified the complete 16 MiB baseline image by SHA-256
and byte comparison (`b66c24a3…58eb18ae3`). The interpreter report records
byte-for-byte equality; the AOT report records a full-image match and leaves
the device in the ROM bootloader. Verification-result hashes are in the
machine-readable report.

The same functional contract was observed in each update: v1 drives GPIO high
and v2 drives it low at 27 °C. The independent E1 run used the same benchmark
source and inputs in all modes: five checked warmups, 30 measured calls and the
same checksum `0xDBB2F0D4` for native, interpreted and AOT execution.

## Processing and runtime costs

E1 execution time includes the workload and, for WAMR, the guest invocation and
runtime path. It does not isolate pure host-call overhead. WAMR module load and
instantiation are separately measured; these operations are not applicable to
the native scenario.

| Mode | Execution median / mean (µs per call) | WAMR module load (µs) | WAMR instantiation (µs) |
|---|---:|---:|---:|
| Native | 125.5 / 124.33 | n/a | n/a |
| Interpreter | 9,111 / 9,135.7 | 1,075 | 2,161 |
| AOT | 254.5 / 250.87 | 1,291 | 2,167 |

These are descriptive values from one boot/session per mode. Repeated calls
within that session are not independent update attempts. The report encodes
non-applicable or unpublished fields as JSON `null`; consumers must not treat
them as zero.

## Timing, memory and loss boundaries

End-to-end and reboot-spanning durations use only the observer's
`host_monotonic_ns`. Device stage durations use MCU `elapsed_us` only within a
single `boot_id`; the native OTA report keeps pre- and post-reboot stages
separate. No value is subtracted across clock domains. Raw UART/observer
captures were replayed through the versioned analyzer, so per-stage MCU values
and phase snapshots are included for the interpreter as well as AOT and native.

The memory comparison reports minimum observed free internal heap and PSRAM as
separate domains. These are minima of phase snapshots, not transient global
peaks. Native firmware has no WAMR allocator; WAMR-only costs are not
applicable there. Heap capacity, stack high-water marks, allocator counters,
and phase-specific measurements remain available in each source evidence
record; a missing value is preserved as `null`.

Control timeouts, late results, temporal service gaps, telemetry gaps and
unresolved sequence gaps are separate metrics. Private observer events provide
the request denominators and timeout sample IDs for all scenarios. Zero
unresolved sequence gaps does not imply zero temporal gaps. The native
telemetry maximum spans its full capture, while interpreter/AOT telemetry gaps
cover the update interval; each value carries its observation window and
should not be compared as if windows matched.

This calibration has one accepted physical update per scenario and one E1
session per execution mode. It establishes the metric definitions, value
mapping and workload checks for the pilot; it does not estimate campaign
variance, choose the final timeout/sample target, or replace issue #20's pilot
and protocol freeze. Physical rollback and injected-failure recovery are outside
this calibration and remain in the resilience track.

## Source evidence

- [Issue #16 native OTA evidence](issue16-native-ota-2026-10-16.md)
- [Issue #17 interpreted update evidence](issue17-interp-2026-10-16/README.md)
- [Issue #18 AOT update evidence](issue18-aot-https-2026-10-16.md)
- [E1 execution and memory benchmark](e1-physical-2026-10-14/physical/physical-abc-benchmark-metrics.md)
- [Machine-readable normalized report](issue19-metrics-calibration-2026-10-16.json)
