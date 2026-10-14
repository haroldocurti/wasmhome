# Ensaio físico E1 — fonte 8b3bddd — 14/10/2026

This published package contains sanitized metadata and summaries only; the original records are preserved privately. The latest ABC physical trial (`abc-8b3bddd-run01`) is **failed** because the AOT stage produced no accepted workload output and the runner did not capture the required marker. Issue #9 remains open.

The exact source pin is `8b3bddd729996853d212b9f678c2b6c6fb4d949f` / tree `db38a2b261c2ebcc259a362eb7c8b3d589e56009`. The three E1 builds and their artifact hashes are summarized in `builds/build-summary.json`; each is explicitly marked build-checked, not device-validated. Native and interp passed their individual benchmark gates, but that does not change the overall failed result.

The runner restored original `ota_0` and obtained a full-flash match to the pre-write `c3…` checkpoint before booting the original app. Booting the original app was followed by a 5,979-byte NVS-only drift; the later `4fcc…` read was separately confirmed and adopted as a new baseline with the `c3…` lineage retained. This package makes no claim that the full flash remained identical to `c3…` after boot.

The AOT `LoadProhibited` and the previous cache-disabled panic are separate failures. The current L32R evidence is consistent with a relocation or instruction-fetch visibility problem, but the final cause is unresolved. See `diagnostics/aot-failure.md`.

No firmware images, sdkconfig files, raw UART/command logs, flash/NVS dumps, credentials, MAC/IP addresses, or private paths are included. The issue #11 services run is outside this package. The evidence assembly performed no hardware I/O.
