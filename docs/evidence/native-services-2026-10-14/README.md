# WasmHome services-run03 final review

**Disposition: PASS.** The closed run result is `passed`, has no recorded errors, and the runner returns exit status 0 for a passed result. Physical execution covered control-v1 and control-v2 only.

The frozen services runner was rechecked offline against the closed issue-11 records. Both firmware versions passed all 45 checks, reported five distinct valid DS18B20 samples, and reported zero foreign-boot packets. Source, all five candidate input pins, and their configs and build receipts matched the approved input manifest.

## Review criteria

| Criterion | Evidence and result |
|---|---|
| Source and gate | Clean source commit `a91ff5efabbdd57a5bd742f5682bab1c3124e018`, tree `c0a09c694cc8e751b9d5f8122d07004c5da420ef`; approved gate, manifest, and runner hashes are recorded in `final-review.json`. |
| Control-v1 | 45/45 checks; threshold 25.0 °C; five distinct sensor samples; zero foreign-boot packets. |
| Control-v2 | 45/45 checks; threshold 30.0 °C; five distinct sensor samples; zero foreign-boot packets. |
| Hysteresis | Both versions exercised initial low, upper edge high, high retention inside the band, lower edge low, and low retention inside the band at ±0.5 °C. |
| GPIO loopback | Seven checks per version reported requested and input levels equal with status 0. GPIO4 is the sensor bus, GPIO5 the output, and GPIO6 the loopback input; the physical wiring was user-confirmed. |
| Flash boundary | Full-flash comparisons after v1 and v2 found zero changed bytes outside `ota_0`. The harness itself requested no flash or OTA write; the physical runner staged the test images in `ota_0`. |
| Restoration | Original `ota_0` readback matches its pinned hash. The 16 MiB pre-boot restoration image and the later post-boot observation are byte-equal to the baseline. The restored app and final startup loaded from offset `0x20000`. |

The GPIO evidence is the firmware-reported GPIO6 loopback result. No oscilloscope or independent voltage measurement was captured. Replay evidence covers cached response behavior; rejection checks do not prove rejected callbacks were never invoked. The exact full-image post-boot observation precedes the final startup capture, so this review makes no full-flash equality claim after that final startup reset.

## Pinned firmware and build evidence

The JSON packet records SHA-256 references for BIN, ELF, sdkconfig, and build receipt for each of the five preflight candidates. Only control-v1 and control-v2 were physically executed. Hashes identify artifacts; no firmware binaries, configs, flash dumps, or UART text are included.

## Evidence index

`final-review.json` contains the sanitized source, gate, input, runner, result, artifact, config, receipt, and closed raw-evidence hash references. Its raw-evidence index covers run events and result, all command captures, flash captures, UART captures, and each issue-11 result, manifest, and event record. `SHA256SUMS` verifies this packet.

No private filesystem paths, credentials, device addresses, raw firmware/config files, flash contents, or UART text are included.
