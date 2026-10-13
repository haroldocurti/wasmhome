#!/usr/bin/env bash
set -euo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1
ROOT=/home/wasmhome/.local/toolchains/wasmhome-e1-20261013-reviewed-91f11f7/generation-repro-r1
SRC=$ROOT/source
SDK=/home/wasmhome/.local/toolchains/wasmhome-e1-20261010-187ff962/wasi-sdk/wasi-sdk-27.0-x86_64-linux
SDK_TAR=/home/wasmhome/.local/toolchains/wasmhome-e1-20261010-187ff962/downloads/wasi-sdk-27.0-x86_64-linux.tar.gz
WAMRC=/home/wasmhome/.local/toolchains/wasmhome-e1-20261012-linux-r5/wamrc
RECEIPT=$SRC/docs/evidence/retomada-linux-2026-10-12/wamrc-build-receipt-r5.json
IDF=/home/wasmhome/.local/toolchains/wasmhome-e1-20261010-187ff962/esp-idf
cd "$SRC"
argv=(python3 -B tools/generate_artifacts.py --wasi-sdk "$SDK" --wasi-sdk-package "$SDK_TAR" --wamrc "$WAMRC" --wamrc-build-receipt "$RECEIPT" --idf "$IDF")
python3 -B -c "import json,os,sys; json.dump(dict(cwd=os.getcwd(),argv=sys.argv[2:]),open(sys.argv[1],chr(119)),indent=2); open(sys.argv[1],chr(97)).write(chr(10))" "$ROOT/invocation.json" "${argv[@]}"
sha256sum tools/generate_artifacts.py > "$ROOT/generator-script.sha256"
set +e
"${argv[@]}" > "$ROOT/generation.stdout.log" 2>&1
rc=$?
set -e
printf '%s\n' "$rc" > "$ROOT/exit-code.txt"
exit "$rc"
