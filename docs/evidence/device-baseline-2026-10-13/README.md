# Sanitized device-map evidence r5

The offline map identifies the partition table at 0x8000, `otadata` sequence 1 as valid, and `ota_0` at 0x20000 as the selected candidate. A separate saved UART capture after reset records the bootloader load offset 0x20000; its raw log remains private.

The planned bench process manually writes only the active `ota_0` partition, then restores the exact 0x700000-byte baseline payload and verifies its partition hash. The decoder makes no hardware or serial calls.

The full flash dump, extracted partitions (including NVS), and UART log remain outside this directory under the private baseline path. Only the hashes and fixed, sanitized metadata needed to review the map are included here. See `evidence-index.json` for source and artifact hashes.
