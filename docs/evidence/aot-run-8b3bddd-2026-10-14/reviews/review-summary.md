# Review and evidence index

This published index refers to the preserved private reviews and raw evidence. It records the failed ABC physical attempt and does not close issue #9. The frozen runner, runner tests, input manifest, approved root gate, run result, event stream, and closed captures are bound by hashes in `physical/trial-summary.json`; none of those raw materials are copied here.

| Scope | Evidence | Outcome | Limits |
|---|---|---|---|
| Three E1 builds | `builds/build-summary.json` | All three build-checked | Not device validation; no ELF/BIN/config included |
| Native/interp physical stages | Closed ABC run 01 | Both benchmark stages passed their local gates | Overall ABC failed because AOT failed |
| AOT relocator | Offline report SHA `e3175a5bec34d6fef17b5fb7ec00d155f619a5c5af67926480d9e47786f95c96` | L32R-placeholder consistency found | Cannot distinguish relocation visibility from stale fetch; no final cause |
| Restoration | Read-only review SHA `cbcbe952863a4ea36e3a05b32e4c6dd388fab656838a5f0da2ec1ef2e0c65c5a` | OTA0 restored; full 16 MiB matched preboot c3; postboot NVS-only drift recorded | Do not claim full equality after boot; adopted later 4fcc baseline has c3 lineage |
| IDF cache API | Offline source review SHA `44d61795fbf610a4f28e0fcfb2866512a01d3ac5f67b4ae13cff8aba15aeb8ce` | M2C/INST path exists for IDF 5.2.6 target | Does not establish dynamic mapping or runtime instruction visibility |

The static-memory table previously named for source `03677ebb` is intentionally not reused as an `8b3bddd` audit. Issue #11 results are excluded from this package.
