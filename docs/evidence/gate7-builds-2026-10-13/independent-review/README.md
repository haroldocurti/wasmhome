# E1 build evidence, sanitized

Three ESP-IDF 5.2.6 builds (native, interpreter, AOT) completed from source commit `91f11f715ea7dc2c3a670e55d7f31c7356fdc0f4` / tree `86afd5816215e07f71d04eee0781631eb9dc6cd1`. Each receipt remains `build_checked_not_device_validated`; no flash or device run is asserted here. Source tracked status was clean. The IDF checkout was clean, and `git clean -ndx` returned empty. The source copy had tracked status clean but `git clean -ndx` listed expected ignored/generated runtime artifacts, managed components and dependency lock; they were preserved.

The private artifacts remain under CT107 `/home/wasmhome/.local/toolchains/wasmhome-e1-20261013-reviewed-91f11f7/`. This public projection contains hashes, sizes, modes and an allowlisted config projection only. It contains no sdkconfig, credentials, raw compile database, ELF or BIN. The manifest is 3,636,126 bytes and remains private; its SHA-256 is recorded.

The three build invocations used `idf.py -B BUILD -DSDKCONFIG=BUILD/sdkconfig -DEXPERIMENT_SCENARIO=SCENARIO -DSDKCONFIG_DEFAULTS=DEFAULTS -DIDF_TARGET=esp32s3 -DCCACHE_ENABLE=0 reconfigure`, then `ninja -C BUILD -j 1`, followed by `python -B tools/validate_build_config.py --build-dir BUILD --scenario SCENARIO --nm XTENSA_NM --output BUILD/e1-build.json`. `build-e1.sh` SHA-256: `67f9608097cf72d898eb738b9ad0dca3a7f56c42d49555421b9c3ecab343a297`. The retained generation log documents Xtensa, `xtensa-pc-linux-gnu`, optimization level 3 and successful AoT generation; it does not include the original generator command line.

See `receipts.json` and `normalized-config.json` for per-scenario details. The log and manifest hashes are in `receipts.json`; complete raw build logs and artifact manifest remain on CT107.
