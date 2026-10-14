# Port `.3` diagnosis and validation

## Diagnosis

The AOT failure mechanism is consistent with a strict-aliasing defect in the Xtensa immediate-relocation helper. The earlier production object was built with Xtensa GCC 13.2.0, effective `-O2`, and strict aliasing enabled. Its aligned relocation branch returned without writing the updated L32R immediate. A target-compiler comparison with strict aliasing disabled emitted the missing store. This is strong evidence for the cause of the observed `LoadProhibited`, while physical resolution remains unconfirmed.

The `.3` patch replaces the typed scratch-buffer accesses with aligned 32-bit read-modify-write operations. It preserves adjacent bytes and avoids byte or halfword writes to the Xtensa instruction mirror. Review of the package metadata, lock, patch digest, patched-source digest, and materialized package identity found them mutually consistent; no blocking discrepancy was found.

## Validation

- Runtime package verification passed for component `wasmhome/wasm-micro-runtime`, version `2.4.5-wasmhome.3`, AOT format 5, upstream revision `25bd7eb63e828e4bd242cc9b38d260b4b31c6605`, app-framework revision `e97860611e371d91d2ef25101b02b29719476a48`, and package SHA-256 `7928f0852db4c43b1e2d7ae19ac957d0cddae77e458046612c6100e9e9dbee39`.
- The lock SHA-256 is `7aea69ad144467e077d156732939e2d254d8b910f670a62fda5a5c9194d34c8e`; the new alias patch SHA-256 is `9684378a29175f43775750d9aa3c1d6c01bca65d6ade6b79ca3493617c83c382`; the patched Xtensa source SHA-256 is `3f123dfa62dfb00d27147a9f9d2e89e670bedcd3557a266f4e2a3646e3680c58`.
- The isolated Xtensa compile probe succeeded with the production compiler and optimization flags. Its disassembly uses aligned 32-bit stores for both the in-word and crossing-word paths, with no byte or halfword stores. The host relocation regressions passed; the dedicated review record is [candidate target review](candidate-target-review.md).
- The saved full suite completed 96 cases: 95 passed and one Windows-only junction test was skipped. The C wire codec was enabled for this run. The separately saved run without that codec skipped its four codec cases as well.
- A fresh `wamrc 2.4.5` configure and build completed with exit status 0. The new executable SHA-256 is `05689efa0236defc1a61b2931a251b1e7c7f6c184d0d82a21d384f9313fa7760`. Receipt SHA-256: `413225a53a96ef68933fe3e75bb054d368365d671e42989a11b714f79c50922f`.
- LLVM was reused from its existing pinned source and build. The prior receipt digest matches the recorded input; LLVM cache and compile-command snapshots match the earlier receipt, and all 98 linked LLVM library hashes match. No LLVM configure or build command was run for this candidate.

## Limits

This validates package identity, host regressions, target code generation, and a fresh host `wamrc` build. It does not validate a new firmware link or execution on the ESP32-S3. A/B/C rebuilds and physical repetition remain outstanding, so the AOT issue remains open. No hardware, serial, or UDP action was part of this review.

[Production object diagnosis](production-object-review.md) · [Validation metadata](validation.json) · [Host suite](host-suite.log).
