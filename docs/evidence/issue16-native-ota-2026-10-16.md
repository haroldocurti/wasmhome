# Native signed OTA result — 2026-10-16

The ESP32-S3 completed a real HTTPS update from native firmware v1 to v2. The signed v2 image was downloaded, verified, selected for boot, and reconciled after reboot. The first valid post-update control response matched v2's 27 °C GPIO behavior, and the accepted anti-rollback floor was read back as sequence 2. The run passed all 15 checks in the native offline analyzer.

## Reproducible build and artifact identity

The firmware was built from source commit `269afe2ee4291c5877009356201b94d1cb354465` (tree `d4fc93caa712ca2f00f01d7f22618bf6bb939855`) with the pinned ESP-IDF v5.2.6 environment and Python 3.11 tool environment. The v1 and v2 profiles set `CONFIG_APP_PROJECT_VER_FROM_CONFIG=y` and their own `app_desc` versions; their configuration delta was limited to `CONFIG_APP_PROJECT_VER`, `CONFIG_WASMHOME_CONTROL_VERSION`, and `CONFIG_WASMHOME_UPDATE_RELEASE_VERSION`. Checks confirmed the signed-update, rollback, trusted-time, observer, Wi-Fi, and HTTPS endpoint settings without publishing configuration values.

Build both variants with local, access-controlled configuration profiles. The profiles contain network settings and must stay outside the repository:

```sh
. "$IDF_PATH/export.sh"  # pinned ESP-IDF v5.2.6
idf.py -C . -B "$BUILD_DIR/v1" -D SDKCONFIG="$PRIVATE_CONFIG_DIR/sdkconfig.v1" build
idf.py -C . -B "$BUILD_DIR/v2" -D SDKCONFIG="$PRIVATE_CONFIG_DIR/sdkconfig.v2" build
```

Before signing, verify `app_desc` reports `v1` and `v2` respectively, and check each image checksum and validation hash. The captured build receipt is identified by SHA-256 `965cf19a8b9297385ab20ca987573d056021dda74d5ee0197255d5eac26ea681`.

| Firmware | Size | SHA-256 | `app_desc` |
| --- | ---: | --- | --- |
| v1 | 898,832 bytes | `cbab0994735000ac368b6004e3d32fde1faf611ce8aa0f4ccf35d6ca0c1f23b6` | `v1` |
| v2 | 898,832 bytes | `8df016fe253d58eb0b9073037e38cf6568162a0964c03aa2aa384d9185f44137` | `v2` |

The immutable HTTPS campaign contained six signed artifacts: native v1/v2, interpreter control v1/v2, and AOT control v1/v2. A pinned-key verification passed all six. Host validation made 13 HTTPS GETs (the index plus the six envelopes and six payloads); every response was HTTP 200 with immutable cache metadata, and both CA-chain and hostname checks passed. The public signing-key pin is identified by SHA-256 `5a068ceb66e76b886b676982ea94ac9fa75a6f8b26a47fe54657459e8570fcd2`. The host-verification receipt SHA-256 is `0f46b173695e3a51310d0e27df471f6007b972e3e3c0be37be6e2e414363bdc6`; the six-artifact signature receipt SHA-256 is `a2d27bc25cfd12694a7b393579f2602b3adc26269fafdefab4a8d66de3cb87d6`.

No board is needed for the host tests or firmware build. A real ESP32-S3, its serial connection, the restricted HTTPS service, and a separately running external observer are required to measure boot, radio service, and first post-update control behavior. The board was used only after build and package review.

## Physical update measurements

The successful run downloaded and ran the exact signed v2 payload: 898,832 bytes and SHA-256 `8df016fe253d58eb0b9073037e38cf6568162a0964c03aa2aa384d9185f44137`. The analyzer matched that hash to the running image, correlated the UDP acceptance to the same attempt across two boot IDs, verified the floor readback, and checked the v1-to-v2 GPIO distinction at 27 °C. It saw 188 valid v1 27 °C responses before acceptance and 746 valid v2 responses after activation, with no v1 response after the first v2 response.

Host-monotonic measurements across the reboot were:

| Measurement | Result |
| --- | ---: |
| Trigger to first valid v2 response | 16.303452369 s |
| Acceptance to first valid v2 response | 16.296308228 s |
| Last valid v1 to first valid v2 response | 3.495783592 s |
| Estimated control unavailability (gap minus 100 ms request period) | 3.395783592 s |

Within-boot device event durations were measured on the MCU's monotonic clock. They do not span a reboot:

| Stage | Duration |
| --- | ---: |
| Acceptance to manifest download start | 66.900 ms |
| Manifest request to manifest downloaded | 4.838580 s |
| Manifest downloaded to signature verified | 261.552 ms |
| Signature verified to payload download start | 156.676 ms |
| Payload download start to complete payload verification | 6.762880 s |
| Payload verified to staged | 419.045 ms |
| Staged to activation | 84.805 ms |
| Activation to reboot requested | 212.372 ms |
| New boot pending to health check passed | 2.193509 s |
| Health check passed to floor commit/readback | 195.764 ms |

The external observer recorded 2,931 valid control responses and 34 control timeouts from 2,965 requests at a 100 ms period. It recorded 2,922 valid telemetry samples and 15 unavailable samples. The largest measured control gap was 3.495783592 s; the largest telemetry gap was 3.341380146 s. No unresolved sequence gaps remained. The result reports these losses alongside the successful update.

Recorded phase memory snapshots were:

| Phase | Internal free | Largest internal block | PSRAM free | Largest PSRAM block |
| --- | ---: | ---: | ---: | ---: |
| Accepted, old boot | 226,339 B | 124,928 B | 8,384,416 B | 8,257,536 B |
| Payload verified, old boot | 225,379 B | 100,352 B | 8,384,416 B | 8,257,536 B |
| Boot pending, new boot | 252,383 B | 155,648 B | 8,384,416 B | 8,257,536 B |
| Health check passed, new boot | 226,335 B | 124,928 B | 8,384,416 B | 8,257,536 B |
| Floor committed, new boot | 226,335 B | 124,928 B | 8,384,416 B | 8,257,536 B |

The minimum free internal heap, 225,379 B, and PSRAM, 8,384,416 B, are minima among these phase snapshots; they are not claimed as global allocation peaks. The bounded boot-reconciliation worker retained 5,740 B on the initial boot and 3,964 B on the post-update boot.

## Setup-only attempts and limits

Earlier setup attempts did not send an update trigger and are not included in the measurements above. In attempt 001, the native observer target was empty and the device repeatedly reset during initialization because the main task stack was too small. The next preflight booted stably but had no Wi-Fi credential in its private profile, so it did not establish the network needed for an update. The final 60-second v1 baseline preflight passed with 570 telemetry samples, 566 control responses, and no loss or sequence gaps; it also sent no update trigger.

This run proves the normal v1-to-v2 HTTPS path and post-reboot health confirmation. Physical rollback, power-loss recovery, and injected update failures were not exercised; the host-side activation/ledger tests are separate evidence and do not substitute for those physical fault runs.

After the run, the entire 16 MiB flash was restored and read back byte-for-byte to the authorized pre-run snapshot: SHA-256 `b66c24a3514be56702c1cf7f8d2116e44d5fe0475e3c3c67b65850258eb18ae3`. All eight region comparisons (bootloader, partition table, NVS, OTA metadata, PHY init, both app slots, and module-slot area) matched. The board was left in ROM. The private full-readback receipt SHA-256 is `7511e3ff8324ea544205d12089914a7282024d8f98e49a8973d567f4922f1a17`; the region-comparison receipt SHA-256 is `0ae9871c1437b5942c18373dd4982d553471634318e700567ff8b692ebe27c9a`.

The sanitized analyzer report passed 15/15 checks and records 26 update events. Raw UART, observer captures, configuration files, firmware binaries, and flash snapshots remain in the private evidence store. The analyzer report SHA-256 is `7ef8354c972245571156c5b8c015f7117a5c7b51390c83d2a80f93ef9251dc38`.

The host suite passed: 156 tests run, 5 skipped. It includes the real C ledger path under host shims, parser negative cases, and native update analysis tests. A pinned PowerShell runtime is needed for one publication test.
