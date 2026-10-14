# E1 ABC physical trial — source 8b3bddd — run 01

**State: failed; issue #9 remains open.** This is a private sanitized staging record, not a publication. The trial used source commit `8b3bddd729996853d212b9f678c2b6c6fb4d949f` and tree `db38a2b261c2ebcc259a362eb7c8b3d589e56009`. No hardware access was performed while preparing this record.

The native and interpreted stages each completed one boot with 5 warmups, 30 measured calls, 30 CSV rows plus a header, and a valid checksum. Each emitted 67 memory events. The full-flash comparisons after those two stages showed zero changed bytes outside `ota_0`. Their firmware build receipts remain `build_checked_not_device_validated`; the successful stage runs do not approve the complete ABC trial.

The AOT stage failed before the workload marker and produced zero benchmark CSV rows. The closed UART capture contains 192 repeated `LoadProhibited` panics at PC `0x420b0fae` with exception address `0x81006136`. The runner recorded the AOT stage as failed and the overall result as `failed`.

## Restoration chronology

The original `ota_0` backup, extracted pre-write image, and restore readback share SHA-256 `67860a5f2a36c67fa36a19ca34badaf795dd003bd1240ebd2fc0dce375a2a48b`. After restoration and before booting the original app, the complete 16 MiB flash matched the pre-write checkpoint exactly at SHA-256 `c3b5b9c7bf15817fcb208769a01985692740939a29ca5506bc02f4e755884214`.

After the original app booted, a separate diagnostic full read had SHA-256 `4fcc8eb606747bd4cbb016de24e06a12e08d02fbe9628143b9a933a118c74ed3`: 5,979 bytes differed from the preboot image, all within NVS; no bytes outside NVS or in `ota_0` differed. The final original-app startup capture reports version `6d7382b-dirty` loaded from `0x20000`. A fresh read at `2026-10-14T11:28:11.782969Z` matched the `4fcc…` postboot image and that image was adopted as the new baseline with the `c3…` lineage preserved. This does **not** mean the full flash remained equal to `c3…` after the original app boot.

## AOT interpretation limits

The current AOT failure is distinct from the earlier cache-disabled panic. The offline relocator review found the embedded module’s first Xtensa `L32R` relocation is expected to change its placeholder immediate from `0xffff` to `0xfff6`. The observed exception address `0x81006136` matches the first instruction word at the derived code entry if the placeholder remains visible; the following instruction is an `L32I`. This is consistent with either relocation contents not being visible at runtime or stale instruction fetch. It does not distinguish those mechanisms and is not a final root-cause finding. The post-load loader guard is expected to run for this module, so a bypass of that guard is not supported by the evidence.

The previous cache-disabled failure remains a separate historical event. The IDF 5.2.6 source review confirms the intended address invalidation path exists for the selected M2C/INST call, but it does not prove the dynamic mapping, relocation result, or runtime fetch contents. Do not describe the earlier cache issue as the proven cause of this new panic.

Raw UART, command logs, full-flash images, firmware, sdkconfig, NVS values, and device/network identifiers remain private and are not included. The exact runner, test, input manifest, approved gate, result, and evidence hashes are in `trial-summary.json`; SHA256SUMS covers this staging tree.
