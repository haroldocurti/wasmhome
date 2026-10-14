# Rebuild plan after the WAMR pthread integration fix

This is a preparation note only. Do not run a build until the root publishes the reviewed fix commit and tree. No new build was started while the fix is pending.

Keep the a91ff5 five-build package, all build receipts, and run02 evidence immutable. After the new source pin exists, use a new wasmhome-owned CT source/output root whose name includes that exact commit. Verify HEAD, tree and clean status before configuration; retain the shallow-history boundary as previously recorded. Reuse pinned ESP-IDF 5.2.6 and the existing toolchain, and use the reviewed R10 source-main and PHY-off helpers.

Create fresh build directories for native, interp, AOT, control-v1 and control-v2 from that single source pin. Preserve the PHY-storage-off profile and each scenario’s reviewed profiles; never print or place the private network config in argv, logs or shared receipts. Materialize the runtime package from pinned clean sources using the repository recipe and reuse already verified toolchain components where possible. Do not attribute old a91ff5 firmware or receipts to the fix revision.

For each build, record exact argv before execution, use one Ninja job, preserve failures, emit a new receipt bound to BIN/ELF/sdkconfig/source tree, and run the reviewed static PHY-off check. Recompute the five ELF section-memory rows and verify all new transfer hashes. Keep services receipts labeled build-checked, not device-validated. A physical retry remains outside this plan and requires its own current gate and root coordination.
