# E1 and native-service evidence package, sanitized

Package root: `/tmp/wasmhome-e1-build-package-20261013-r2` (directory mode 0700; files mode 0600).

Three E1 builds (native, interpreter, AOT) were checked from source commit `91f11f715ea7dc2c3a670e55d7f31c7356fdc0f4`, tree `86afd5816215e07f71d04eee0781631eb9dc6cd1`. Their receipts report `build_checked_not_device_validated`. E1 build command template and allowlisted configs are in `commands.txt` and `normalized-config.json`; details and hashes are in `receipts.json`.

A subsequent fresh artifact-generation run used the same 91f11 source commit/tree. Its wrapper records the exact argv and working directory before invoking the generator. Exit code was 0. All five payload hashes matched the earlier recorded outputs; the earlier and repeat manifest hashes differ. The earlier event's argv remains unavailable, so this is a new reproducible event and does not retroactively identify the earlier command. See `generation-reproduction.json`, `generation-repro-r1/`, and the generator wrapper.

Static disassembly is included for `benchmark_run` from the native ELF and for a WAMRC-produced Xtensa object from the repeated WASM. Both show a 2000-iteration loop (`0x7d0`). The AOT object itself, firmware ELF/BIN, private manifests, raw compile database, and sdkconfig files are excluded. The AOT disassembly is a derivative object from the recorded WAMRC invocation, not a claim that the container `.aot` file was directly disassembled. See `workload-disassembly.json` and `workload-inspection-r1/`.

The issue #11 harness source, tests and docs are indexed in `issue11-harness.json`; its seven fake tests pass. No UDP/serial physical run was made. Boot-evidence limitations remain as documented in the harness docs.

Two native-services firmware builds on source commit `7b00b1585e1b27819979a6d82dd4144dc6f19682` are summarized in `services-builds.json`. Their private CT107 paths are included for authorized transfer. Both statuses are `services_build_checked_not_device_validated`; no device run is asserted.

Secret-bearing sdkconfig values are not present in this package. The manifest files and binary firmware outputs remain on CT107.
