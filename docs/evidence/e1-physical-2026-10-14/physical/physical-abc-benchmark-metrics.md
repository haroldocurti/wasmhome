# E1 ABC physical-run metrics

One physical boot/session per mode; five checked warmups and 30 measured calls per mode, checksum `0xDBB2F0D4`. This is a descriptive single-run record, not an update campaign or independent repeated-call study.

| Mode | Execution min / median / mean / max (µs) | Load / instantiate (µs) | Internal dynamic heap capacity (B) | Internal free heap during execution min–max (B) | PSRAM free during execution min–max (B) |
|---|---:|---:|---:|---:|---:|
| native | 118 / 125.5 / 124.33 / 131 | n/a | 383763 | 269699–269735 | 8384700–8384700 |
| interp | 9001 / 9111.0 / 9135.7 / 10268 | 1075 / 2161 | 382963 | 257359–257383 | 8310960–8310960 |
| aot | 220 / 254.5 / 250.87 / 376 | 1291 / 2167 | 379699 | 254411–254607 | 8306624–8306624 |

Native load/instantiate is not applicable because native mode does not load a WAMR module; raw zeros do not mean zero-cost module operations. Heap measurements are point samples, not phase peaks. Internal heap and PSRAM are separate (not summed), and allocator capacities differ by firmware image. Full phase and CSV summaries are in the JSON file.
