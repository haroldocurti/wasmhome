# Issue #17 interpreted module-update HIL

The interpreter-profile HIL demonstrated an in-place signed control-module update from v1 to v2 while the telemetry instance remained active in the same boot. The strict analyzer and an independent root replay both passed all 24 checks. A separate no-trigger smoke confirmed functional v2 behavior after reset, and the full baseline image was restored and read back byte for byte.

The shared updater, event schema, observer, and analyzer implementation are part of the stacked [PR #35](https://github.com/haroldocurti/wasmhome/pull/35). This evidence records the interpreter scenario for issue #17. The firmware used here is from source commit `1e54b1128cf083fb2c000dfd6d79303a5467fef7`; production sources were unchanged by this evidence-only PR.

## Main interpreted update

The approved interpreter image was 984,496 bytes, SHA-256 `7412f02b1e2c9766602fe63bac0535da268575b44efe2158d794337e9a983c49`. The signed v2 control module was 328 bytes with SHA-256 `3a972948112397acdaad03c81714a3daf147ecce926f0854341e58a350f3d5c5`.

The observer ran for 90 seconds and sent one scenario 2, component 1, sequence 2 update trigger after 20 seconds. The complete UART record contains two POWERON boots from setup before the observer started; the observer and update events belong to the second, stable boot. There was no reset or serial reopen after observation began. The updater recorded the signed manifest and payload stages, candidate load and instantiation, health check, floor commit/readback at sequence 2, control activation, and release of the previous control.

At 27 °C, direct control results distinguished the versions: 66 valid v1 responses applied GPIO high before activation, and 209 valid v2 responses applied GPIO low after activation. Across all temperatures the capture contained 199 valid v1 results before acceptance and 629 valid v2 results after activation, with no v1 result after activation. The active control generation changed from 2 to 3, and its SHA-256 matched the fully received and verified payload. The manifest digest at download completion matched the digest at signature verification.

The telemetry instance stayed at generation 1 with SHA-256 `03dabd22bb69276546bade37bccc318fe7a84e3cb63c9420c6ca7e758b04eb11` in every reported update phase. The analyzer's acceptance-to-first-v2 interval was 7.117 seconds (7.123 seconds from trigger to first v2) and contained 71 valid telemetry samples against a minimum of 70. Baseline median cadence was 100.036 ms; the largest gap during the update was 162.573 ms against a 200.073 ms limit. The telemetry runtime busy-drop counter delta was zero. Across the full observation, maximum telemetry gap was 222.297 ms. There were zero unresolved sample-sequence gaps; this does not mean there were zero temporal gaps.

The full observation recorded 903 valid telemetry samples, 885 valid control results, 14 control timeouts, and 8 late control responses. The last-valid-v1 to first-v2 interval was 1.510 seconds; estimated control unavailability after subtracting the 100 ms request cadence was 1.410 seconds. This service gap is reported separately from the device's 7.123-second update duration.

Candidate load, instantiate, overlap, and previous-control release appeared in the required order. Minimum internal free heap was 151,383 bytes, with a 53,248-byte largest free block; minimum PSRAM free heap was 8,097,276 bytes. WAMR allocator failures were zero. The active-module size was not independently exposed by the runtime snapshot; the payload size is established by the signed manifest and complete 328/328-byte transfer, while the active module hash matched the verified payload.

The unchanged strict analyzer passed 24/24 checks. Root's independent replay produced the same analysis digest. No rollback or recovery path was exercised; the successful run does not claim recovery coverage.

## Separate persistence smoke and restoration

After the main capture was closed, a separate 20-second no-trigger smoke ran in a new boot after a PC observer restart and USB reconnection. It recorded 199 valid v2 control results and 203 valid telemetry samples: 67 at 23 °C with GPIO low, 66 at 27 °C with GPIO low, and 66 at 32 °C with GPIO high. There were no control timeouts, late results, or unresolved sample-sequence gaps. This is functional evidence that v2 remained selected after reset. No direct post-reset ledger or payload-hash readback was performed.

The authoritative 16 MiB baseline was restored and read back in full. The readback SHA-256 was `b66c24a3514be56702c1cf7f8d2116e44d5fe0475e3c3c67b65850258eb18ae3`, equal to the baseline byte for byte. Root independently verified the full image and all eight tracked regions. The device was left in the ROM bootloader and the hardware lease was released.

## Evidence integrity and privacy

Raw UART, UDP observations, serial-control records, full flash images, and service configuration remain in private storage. `summary.json` contains sanitized measurements and SHA-256 digests of the private capture/review artifacts; it does not contain raw records, credentials, device addresses, or flash contents. Root's review digests are included so the independent replays and restoration review can be matched to the private records.
