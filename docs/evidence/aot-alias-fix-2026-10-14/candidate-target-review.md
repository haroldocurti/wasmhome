# Xtensa compile review of alias-safe relocation helper, candidate r3

## Finding

The candidate r3 helper compiles as a complete translation unit with the exact Xtensa compiler and effective flags from the real AOT build at source pin `8b3bddd729996853d212b9f678c2b6c6fb4d949f`, while retaining strict aliasing. In target disassembly, the non-crossing case writes the replacement through one aligned `s32i.n`; the crossing case writes the two aligned words through two `s32i.n` instructions. Neither case uses byte/halfword stores to the instruction mirror. The BE lane-3 mask issue identified in candidate r2 is corrected in r3, and the candidate r3 host tests cover all four lane positions for simulated BE and LE.

This is a compile/codegen review only. The candidate object was not linked into firmware, loaded, or tested on hardware. It does not establish that the fix resolves the physical AOT failure.

## Build identity and exact-command comparison

The real compile database is `builds/e1-aot/compile_commands.json`, SHA-256 `011e75e38167785ac2b1f15aaed1450392f24cdcf18307ecd69164ac8774839b`. I selected its sole `aot_reloc_xtensa.c` command (Xtensa GCC 13.2.0, flags include `-O3 -O2`, effective `-O2`, strict aliasing enabled). The command was rerun against a private copy of candidate r3. Only the source-file argument and the object/dependency output destinations were changed; all macros, include paths, target options, and optimization flags remained those in the real compile command.

Candidate r3 patch SHA-256: `9684378a29175f43775750d9aa3c1d6c01bca65d6ade6b79ca3493617c83c382`.
Patched translation unit SHA-256: `3f123dfa62dfb00d27147a9f9d2e89e670bedcd3557a266f4e2a3646e3680c58`.
Compile exit status: `0`.
Private probe object SHA-256: `07aa63449034c6f55701facc9b0268396eb563d01107c44dbbe8b04ef6651180`.

## Target disassembly

`put_imm16_to_addr` starts by loading the first aligned word (`l32i.n`). For lanes 0–2 it computes the shifted 16-bit replacement and mask, preserves neighboring bytes, then stores the resulting aligned word once (`s32i.n` at helper offset `0x2b`). Lane 3 loads the second aligned word, constructs both final words, then stores each with `s32i.n` (offsets `0x4c` and `0x51`). The generated helper contains no `s8i` or `s16i` stores.

`apply_relocation` computes the DBUS mirror address, forms the immediate pointer at the expected byte offset, and calls `put_imm16_to_addr`; the helper address load is at offset `0xef`, followed by `callx8` at `0xf2` in this candidate object. The exact target compiler therefore emits the intended alias-safe helper and reaches it from the relocation path.

Saved disassemblies:
- `helper-disassembly.txt`, SHA-256 `3ca125ac34643755594aa244fcefbf18566b6d6545af3b63f4fdb933055b8757`.
- `apply-relocation-disassembly.txt`, SHA-256 `9d25295efe5a86cc5343082e6a3d0f569beb8afba27f4ffb79d464b4447e25e7`.

Candidate r3's independent host regression suite includes the original O2 strict-alias failure, optimized/UBSan little-endian cases, and simulated big-endian all four lanes including the crossing case. I reran the suite; it reports 3 tests passed. Captured log: `candidate-r3-host-tests.log`, SHA-256 `0a82a69783e11c5221ee2e0d6682ce4723b4d4c7db40c0144ec86abc48a18e04`. Host tests support arithmetic coverage but do not replace target code generation or a firmware/runtime test.

## Scope and limits

This was one isolated component-object compile in a new private provenance directory, using the existing pinned toolchain. No repository files, existing build outputs, runtime archive, firmware ELF/BIN, or device state were changed. No firmware link, flash, reset, serial, UDP, or other hardware I/O occurred. The prior finding on the actual linked object remains: its aligned relocation branch skipped the mirror write, consistent with the unmodified L32R placeholder. Candidate r3 changes only the helper at compile-probe time; the physical AOT issue remains open until a reviewed source pin is built and exercised.
