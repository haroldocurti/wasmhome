#!/usr/bin/env bash
set -euo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1
ROOT=/home/wasmhome/.local/toolchains/wasmhome-e1-20261013-reviewed-91f11f7
OUT=$ROOT/workload-inspection-r1
REPRO=$ROOT/generation-repro-r1
OBJDUMP=/home/wasmhome/.local/toolchains/wasmhome-e1-20261010-187ff962/idf-tools/tools/xtensa-esp-elf/esp-13.2.0_20250707/xtensa-esp-elf/bin/xtensa-esp32s3-elf-objdump
WAMRC=/home/wasmhome/.local/toolchains/wasmhome-e1-20261012-linux-r5/wamrc
NATIVE_ELF=$ROOT/build-e1-native/wasmhome_bench.elf
AOT_WASM=$REPRO/source/artifacts/benchmark.wasm
mkdir -m 700 "$OUT"
native_argv=("$OBJDUMP" -d --disassemble=benchmark_run "$NATIVE_ELF")
python3 -B -c "import json,os,sys; json.dump(dict(cwd=os.getcwd(),argv=sys.argv[2:]),open(sys.argv[1],chr(119)),indent=2); open(sys.argv[1],chr(97)).write(chr(10))" "$OUT/native-disassembly-argv.json" "${native_argv[@]}"
set +e
"${native_argv[@]}" > "$OUT/benchmark_run-native-disassembly.txt" 2> "$OUT/native-objdump.stderr.log"
native_rc=$?
set -e
printf '%s\n' "$native_rc" > "$OUT/native-objdump-exit-code.txt"
if [ "$native_rc" -ne 0 ]; then exit "$native_rc"; fi
aot_argv=("$WAMRC" --target=xtensa --bounds-checks=1 --disable-llvm-lto --opt-level=3 --format=object -o "$OUT/benchmark_xtensa.o" "$AOT_WASM")
python3 -B -c "import json,os,sys; json.dump(dict(cwd=os.getcwd(),argv=sys.argv[2:]),open(sys.argv[1],chr(119)),indent=2); open(sys.argv[1],chr(97)).write(chr(10))" "$OUT/aot-object-argv.json" "${aot_argv[@]}"
set +e
"${aot_argv[@]}" > "$OUT/aot-object.stdout.log" 2> "$OUT/aot-object.stderr.log"
aot_rc=$?
set -e
printf '%s\n' "$aot_rc" > "$OUT/aot-object-exit-code.txt"
if [ "$aot_rc" -ne 0 ]; then exit "$aot_rc"; fi
object_argv=("$OBJDUMP" -d "$OUT/benchmark_xtensa.o")
python3 -B -c "import json,os,sys; json.dump(dict(cwd=os.getcwd(),argv=sys.argv[2:]),open(sys.argv[1],chr(119)),indent=2); open(sys.argv[1],chr(97)).write(chr(10))" "$OUT/aot-disassembly-argv.json" "${object_argv[@]}"
"${object_argv[@]}" > "$OUT/benchmark_aot-workload-disassembly.txt" 2> "$OUT/aot-objdump.stderr.log"
"$OBJDUMP" -t -h "$OUT/benchmark_xtensa.o" > "$OUT/aot-object-symbols-sections.txt"
sha256sum "$0" "$NATIVE_ELF" "$AOT_WASM" "$OUT/benchmark_xtensa.o" "$OUT/benchmark_run-native-disassembly.txt" "$OUT/benchmark_aot-workload-disassembly.txt" "$OUT/aot-object-symbols-sections.txt" > "$OUT/SHA256SUMS"
