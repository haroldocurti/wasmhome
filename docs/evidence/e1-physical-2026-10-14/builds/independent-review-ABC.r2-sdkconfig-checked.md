# Independent port3 review: CT107 A/B/C

## Result

The three closed builds share the reviewed source revision/tree, ESP-IDF pin, runtime package `.3`, lockfile and generated Wasm/AOT inputs. The actual C/AOT Xtensa relocation helper object uses aligned 32-bit stores in both the in-word and crossing-word cases. The exact object is byte-identical to the member in the runtime archive; the link map and ELF symbol table confirm it is linked. All build records remain `build_checked_not_device_validated`; this is compile/link evidence, not hardware execution.

## Common pins

Source commit/tree: `6317fa9a39e5d99e8ba1293dae80da801d2b6d55` / `e0eeacede32eb98d0bc4444aea044f531b875f8b` (clean). ESP-IDF 5.2.6, revision `9ef24e3e2a2c96e720d83c574a3f8699177573da`, tree `e74c4c35593551e612df306248b403a92ec8a403`. Runtime `2.4.5-wasmhome.3`, component SHA `7928f0852db4c43b1e2d7ae19ac957d0cddae77e458046612c6100e9e9dbee39`, lock SHA `7aea69ad144467e077d156732939e2d254d8b910f670a62fda5a5c9194d34c8e`, WAMR revision `25bd7eb63e828e4bd242cc9b38d260b4b31c6605`, app framework revision `e97860611e371d91d2ef25101b02b29719476a48`, AOT format 5.
Final build-index SHA-256 `e1400fe82ac5e62d0c215f45fb51b0bb25df714bb0a56cf08b7a7711a068b870`; evidence-index SHA-256 `3332c06c1d0dcca5e1e6be1caf445fd59d6a4b63cbaa3830364cc2057fd2090f`. Runner input manifest SHA-256 `3e271f03b924d865d34bc770f7517520b8569973e4b7e43d4c529416c23db983` (pending root gate). Snapshot SHA-256s: A `ec3e3ad7ca7612b837a0a9e12ecceba455e8a7752df2d8b555296068c30fb754`, A supplement `cc39c30e1230e47629d2014a11021016e077f41c9ae63331524a594c56abc486`, B `e3c50ffe3691c5f43e3c7315825e55827029ef3e657af78beee75810407d3ada`, C `093a25458d1715e4076faa02a90e549b35d715a7d01b812a4aaf24a22ab04665`.

The table lists 15 primary closed pins (five per build). The JSON report also carries every per-build artifact hash and the profile/input pins from the final index.

| Build | Artifact | Bytes | SHA-256 |
|---|---|---:|---|
| aot | `e1_validator` | 4783 | `2f7b6bbcbd2702d191d3ab00cfe0a0c69df8119bb66340185558376ea8225236` |
| aot | `elf` | 10569824 | `696b2525f5f19ee543a39ba12819e564e2aa4a8a478e7eafb9e1551d998be323` |
| aot | `bin` | 824144 | `616543f91f0dab956709b2f512625d0e567187ee7b410f10b8ff96bfb54fe9ee` |
| aot | `phyoff_receipt` | 2362 | `6c1c5516c153a6cd0f3d9ed8e3a51bc28b2cc489ca50fad451cab4cc9890fb54` |
| aot | `compile_commands` | 7869965 | `6d7b2ce5c6f9156ba6f7687ae642782847b092dd44cfc867e16e8ea7d8fb83d8` |
| interp | `e1_validator` | 4786 | `609b78563f896166abb5f64fddf203c353d365b352bca6844501aa5e1f9f1021` |
| interp | `elf` | 10552088 | `510cdb74bd0b0e25cc61ccc706359a8e7d326bd76cb0f9a5c2eedc72e25c1154` |
| interp | `bin` | 839440 | `81b407edfafbeab7021499dc6db4b5f8ead3cfd5c22e6f904ef10d2de84d3d5f` |
| interp | `phyoff_receipt` | 2435 | `e0e52f26410ec9d4d5f1e9d6197a8300dc9f4f3374bd2d03b180cc58099c90cc` |
| interp | `compile_commands` | 7858422 | `68607c25b12dc56d780cb0c27938f4b7eaabbaf089d9429ae655dd9501f463f7` |
| native | `e1_validator` | 4182 | `8ba7881e65c0e99eb7169e355d25ae8fa4fe55c9307d62192b3f12bb683fb9c3` |
| native | `elf` | 9218960 | `bbb89321e0e1a223e74ed4ba8196da163e909e50955552c64b87c62c4aed8fee` |
| native | `bin` | 747920 | `b3cfc8710c8cdd853fe088e3936ea91c3a7ce708bee6260c5e73f96c83a18795` |
| native | `phyoff_receipt` | 2217 | `e9a1c9205fbcb38120ec6391d9dddeeec20a2feb3f984f3009464b7348b4c87b` |
| native | `compile_commands` | 7319211 | `936b41e14e9cbb18a6f452427a7c2cb28599f27b43d1a1c70eec2d7fd28c8207` |

Of 27 per-build file pins in the final index, 26 matched directly to the A/B/C immutable subset snapshots. The remaining raw C AOT `sdkconfig` is absent from the subset tar, so I read the file at the path named by the runner input manifest (SHA-256 `3e271f03b924d865d34bc770f7517520b8569973e4b7e43d4c529416c23db983`). Its 65,758-byte size and SHA-256 `4d3540000c35301fd99740a3b4bc3c2678495c78b7502af94cba56b9d9a71ead` match both the manifest pin and final build-index. All 27 pins are resolved; the raw file content was not printed or copied into this report.

## Wasm/AOT generation

The shared build-manifest SHA is `ab177a56451991f1349de548193c0c35f44a24c03a2950c026079bbcec245fb2`. Wasm is 174 bytes, SHA `d4cc0e280d02a697bb52c391e3b225a0691bc24d14cefb2883e51e085e466847`; AOT is 812 bytes, SHA `c4c94777e5f9460b8e60065d5cb89c7e79907cac61720a61f5ebd5467b512d88`. The B receipt and C build-index reference this same manifest/payload pair. `wamrc` is wamrc 2.4.5, binary SHA `05689efa0236defc1a61b2931a251b1e7c7f6c184d0d82a21d384f9313fa7760`, source revision `25bd7eb63e828e4bd242cc9b38d260b4b31c6605`. Canonical build-receipt SHA `1a690cb7d20e8d2563664d4eddbb9758fc65cbda152c8f9cbc1c9b885dbe8f76` and receipt-input SHA `413225a53a96ef68933fe3e75bb054d368365d671e42989a11b714f79c50922f` match the independently validated receipt. WASI SDK 27.0 package SHA `b7d4d944c88503e4f21d84af07ac293e3440b1b6210bfd7fe78e0afd92c23bc2`; the manifest records `wamrc` AOT flags `--target=xtensa --bounds-checks=1 --disable-llvm-lto --opt-level=3`.

## Actual C/AOT relocation helper

Patched source SHA: `3f123dfa62dfb00d27147a9f9d2e89e670bedcd3557a266f4e2a3646e3680c58`. The actual compile database contains one Xtensa GCC Xtensa GCC 13.2.0_20250707 entry with flags `-O3 -O2` (effective `-O2`) and no `-fno-strict-aliasing`. The loose object SHA `d9236668009cbb0b30f2f53ee1b2ed16579f846f33a08d183a27cd58e8d2a97b` matches byte-for-byte the sole `aot_reloc_xtensa.c.obj` member in archive SHA `289b3630cabf4db8e6901a09fee70286567fc8578520ae32bb77236eaf102443`.

In the exact object disassembly (SHA `efe145374fdf5dc2208cc027fe694330b658204dd3129a4cbdec4bcd35346800`), the base pointer is aligned with `address & ~3`. Lanes 0–2 read and update the aligned word with one `s32i.n` at offset 0. Lane 3 aligns `(address + 3) & ~3` and emits two `s32i.n` stores at offset 0, one to each aligned word. The helper contains no byte or halfword store. The final link map references this archive member, and the linked ELF symbol table contains local `put_imm16_to_addr` at `0x42011544` (85 bytes), `apply_relocation`, `os_get_dbus_mirror`, `aot_module_validate`, and `benchmark_aot` (812 bytes).

## Native memory and remaining limits

The read-only `xtensa-esp32s3-elf-size -A` report (SHA `5ff4c78cdb47687f75e7ea0c9769d083738668aa64885570ac00a7f1a815b1e6`) reports IRAM text 91,835 B, DRAM data 19,520 B, DRAM BSS 15,288 B, flash text 524,667 B, flash rodata 110,512 B, and flash rodata-noload 11,280 B. Its total includes debug sections and is not a flash-size estimate.

A native Ninja, E1 validator and final-verify exit-code files are zero. Its first wrapper status later recorded a postcheck failure limited to the approved IDF managed-components/onewire dependency-lock drift. The resume index preserves this history, records native as previously passed, and marks the full A/B/C sequence complete; B and C final gates all exit 0. No firmware build, Ninja, `idf.py size`, LLVM rebuild, flash, reset, serial, UDP or hardware operation was performed during this independent review.
