# Port 3 build and provenance metadata

**Status:** A/B/C build and physical checks passed; restoration is proven with the scope and postboot NVS delta described below.

This supplemental public metadata record summarizes the closed native, interpreter and AOT builds and the static review of the Xtensa AOT relocation helper. It does not replace the separately pinned original review. The SDK configuration check is recorded by digest only; its contents are not disclosed.

## Source and runtime identity

- Source commit/tree: `6317fa9a39e5d99e8ba1293dae80da801d2b6d55` / `e0eeacede32eb98d0bc4444aea044f531b875f8b` (clean worktree).
- ESP-IDF 5.2.6; revision `9ef24e3e2a2c96e720d83c574a3f8699177573da`, tree `e74c4c35593551e612df306248b403a92ec8a403`.
- Runtime package 2.4.5-wasmhome.3; component digest `7928f0852db4c43b1e2d7ae19ac957d0cddae77e458046612c6100e9e9dbee39`, lock digest `7aea69ad144467e077d156732939e2d254d8b910f670a62fda5a5c9194d34c8e`.
- Build index digest `e1400fe82ac5e62d0c215f45fb51b0bb25df714bb0a56cf08b7a7711a068b870`; all 27 indexed pins resolved, with no size or digest mismatch.

## Closed build runs

All three runs passed Ninja, E1 validation and final verification (exit 0). These are build checks; the separately closed physical session is summarized below.

| Scenario | ELF SHA-256 | BIN SHA-256 | Build receipt SHA-256 |
|---|---|---|---|
| native | `bbb89321e0e1a223e74ed4ba8196da163e909e50955552c64b87c62c4aed8fee` | `b3cfc8710c8cdd853fe088e3936ea91c3a7ce708bee6260c5e73f96c83a18795` | `8ba7881e65c0e99eb7169e355d25ae8fa4fe55c9307d62192b3f12bb683fb9c3` |
| interp | `510cdb74bd0b0e25cc61ccc706359a8e7d326bd76cb0f9a5c2eedc72e25c1154` | `81b407edfafbeab7021499dc6db4b5f8ead3cfd5c22e6f904ef10d2de84d3d5f` | `609b78563f896166abb5f64fddf203c353d365b352bca6844501aa5e1f9f1021` |
| aot | `696b2525f5f19ee543a39ba12819e564e2aa4a8a478e7eafb9e1551d998be323` | `616543f91f0dab956709b2f512625d0e567187ee7b410f10b8ff96bfb54fe9ee` | `2f7b6bbcbd2702d191d3ab00cfe0a0c69df8119bb66340185558376ea8225236` |

The complete set of 15 canonical primary artifact pins (five per run) is in the companion JSON. The AOT C `sdkconfig` was separately checked by byte count and SHA-256 against both the runner input manifest and final build index; no configuration contents are included.

## Wasm/AOT generation

- Workload Wasm: 174 bytes, SHA-256 `d4cc0e280d02a697bb52c391e3b225a0691bc24d14cefb2883e51e085e466847`.
- Generated AOT: 812 bytes, SHA-256 `c4c94777e5f9460b8e60065d5cb89c7e79907cac61720a61f5ebd5467b512d88`.
- Artifact manifest SHA-256 `ab177a56451991f1349de548193c0c35f44a24c03a2950c026079bbcec245fb2`; WAMR wamrc 2.4.5. Provenance is an unsigned build receipt plus checked local inputs, not an independent rebuild.

## AOT relocation helper

The reviewed source digest is `3f123dfa62dfb00d27147a9f9d2e89e670bedcd3557a266f4e2a3646e3680c58`. The actual compiled archive member `aot_reloc_xtensa.c.obj` has digest `d9236668009cbb0b30f2f53ee1b2ed16579f846f33a08d183a27cd58e8d2a97b` and matches the loose object. The AOT link map references that member. Disassembly inspection found aligned 32-bit stores for lanes 0–2 and two aligned 32-bit stores across the lane-3 boundary, with no byte or halfword store opcodes. The helper is compiled with Xtensa GCC 13.2.0_20250707, effective `-O2`; `-fno-strict-aliasing` is absent. This establishes static object/link evidence, not device behavior.

## SDK digest check

The AOT C `sdkconfig` was checked as 65758 bytes, SHA-256 `4d3540000c35301fd99740a3b4bc3c2678495c78b7502af94cba56b9d9a71ead`, matching the runner input manifest and final build index. Its contents were not disclosed.

## Physical status

The closed A/B/C session passed all three stages with zero errors. Each scenario recorded five warmups, 30 measured workload calls, checksum `DBB2F0D4`, and 67 memory events. The AOT boot and write/readback checks passed. No checksum algorithm is asserted. Independent reviewer C separately confirmed offline E1 pass for native, interpreter and AOT.

Restoration evidence is scoped to OTA0. The full 16 MiB preboot checkpoint exactly matched the saved baseline (SHA-256 `4fcc8eb606747bd4cbb016de24e06a12e08d02fbe9628143b9a933a118c74ed3`). The later full postboot read (SHA-256 `bcbd8b0f77e5d34d6d6d08a19fc9b40558da264067f81a39ed61877cb6636a5c`) differs by 1,961 bytes, all within NVS offsets `0x9000`–`0xEFFF` (first `0xB038`, last `0xDC3D`); there were zero changed bytes outside that range. The OTA0 digest remained `67860a5f2a36c67fa36a19ca34badaf795dd003bd1240ebd2fc0dce375a2a48b`. The original application startup was confirmed after the final full read, so no full-flash equality claim is made after that startup. NVS was not erased and the delta is not attributed to an exclusive cause.

The closed result record has SHA-256 `3a865350633e761ed97959d1062881293e49095344dff5502429fc017382585c`. This metadata packet includes no raw flash, firmware, configuration or logs and no device identifiers or host-specific paths.

## Supplemental review records

The supplemental review files are `independent-review-ABC.r2-sdkconfig-checked.md` (SHA-256 `6779fcc49a99791c4ed25fe117661fb751551df57925a243ddc5fac282924eaa`) and `.json` (SHA-256 `2eed904014d6fdba77198590b6e99998e5e94d82a39ccc8b142582a9dda3eca8`). They add direct verification of the C `sdkconfig` digest; the original root-pinned review remains a separate immutable record.

This packet contains metadata and hashes only. It excludes firmware/config contents, logs, flash images, device identifiers, network addresses and host-specific paths.

Publication copy: the coordinator removed a stale pending-device sentence from the r3 Markdown; original r3 digest `6fbe288d5dec1a1460d8bb3bb2df1106114780063d862b2adda607b87c878647` is preserved here. The coordinator also removed stale pending-closure wording from the JSON (original r3 digest `02806dacb2b39c152abe12f759b454f8e3cf9da42ef8784f6fb354438cb924dc`); all measurements and digests are preserved. Supplemental reviews are byte-identical.
