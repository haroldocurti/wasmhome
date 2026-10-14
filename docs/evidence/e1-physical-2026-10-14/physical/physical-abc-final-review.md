# Final independent review — E1 ABC physical session

**Verdict: PASS within the recorded scope, with a postboot NVS limitation.** The closed session result is `passed` with no errors; the frozen offline validator reports `session_logs_and_manifest_consistent`. Approved input, gate, runner, baseline and artifact identities are pinned in the accompanying JSON.

All three modes completed five checked warmups and 30 measured calls with checksum `0xDBB2F0D4`. Each mode-specific OTA0 write/readback matched its pinned firmware BIN, and each 16 MiB full-flash checkpoint recorded zero changed bytes outside OTA0. The restored OTA0 readback matched the original OTA0 hash, and the full image taken before the restored-app boot matched the fresh baseline exactly. Both restored-app startup captures identify version `6d7382b-dirty` loaded at `0x20000`.

After that app boot, the full-flash observation differed from the exact preboot baseline by 1,961 bytes, from `0xb038` through `0xdc3d`. All differing bytes are within mapped NVS `[0x9000, 0xf000)`; OTA0 retains its original hash, and other mapped partitions are equal. This locates the observed delta but does not establish its cause. No full-flash equality check followed the final startup capture.

Timing and memory metrics are included separately. This is one boot/session per mode, not a repeatability campaign; 30 calls within a session are not independent updates. Heap data are point samples rather than peaks. Internal heap and PSRAM must remain separate, and capacities differ by firmware. Native mode does not load a WAMR module, so raw zero load/instantiate fields are not cost measurements. The result supports only the stated E1 feasibility/acceptance scope, not production reliability.

The packet contains sanitized metadata and hashes only; it excludes raw UART and flash data, firmware/config contents, credentials, device addresses and private paths.
