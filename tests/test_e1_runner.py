import pathlib
import json
import hashlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from embed_artifact import render
from validate_build_config import sha256, resolved_runtime, validate_artifacts, validate_config, validate_symbols, validate_toolchain, validate_runtime_commands, validate_native_workload


class EvidenceTests(unittest.TestCase):
    def test_embedding_requires_nonempty_ascii_identifier(self):
        for data, symbol in ((b"", "valid"), (b"a", "ação"), (b"a", "a-b"), (b"a", "while"), (b"a", "_Atomic")):
            with self.assertRaises(ValueError):
                render(data, symbol)
        self.assertIn("_Alignas(8) const", render(b"abc", "benchmark_wasm"))

    def test_scenario_mismatch_rejected(self):
        config = dict(IDF_TARGET="esp32s3", WASMHOME_RUN_BENCHMARK=True,
                      ESP_DEFAULT_CPU_FREQ_MHZ=240, COMPILER_OPTIMIZATION_PERF=True,
                      WAMR_ENABLE_INTERP=True, WAMR_INTERP_CLASSIC=True)
        validate_config(config, "interp")
        for flag in ("WAMR_INTERP_FAST", "WAMR_ENABLE_AOT", "WASMHOME_OBSERVER_SELFTEST"):
            with self.subTest(flag=flag), self.assertRaises(ValueError):
                validate_config(dict(config, **{flag: True}), "interp")
        with self.assertRaises(ValueError):
            validate_config(dict(config, WASMHOME_RUN_BENCHMARK=False), "interp")

    def test_native_runtime_symbols_rejected(self):
        validate_symbols({"benchmark_run", "app_main"}, "native")
        for symbol in ("wasm_runtime_load", "wasm_interp_call_wasm", "aot_call_function"):
            with self.subTest(symbol=symbol), self.assertRaises(ValueError):
                validate_symbols({"benchmark_run", symbol}, "native")

    def test_native_final_optimization_rejects_missing_o2_and_later_override(self):
        source = str(ROOT / "workload/benchmark.c")
        validate_native_workload([dict(file=source, command=f'cc -O2 -O3 -c "{source}"')], ROOT)
        for flags in ("", "-O2", "-O3 -O2", "-O3 -Os", "-O3 --opt-level=2", "-O3 @hidden-options.rsp"):
            with self.subTest(flags=flags), self.assertRaises(ValueError):
                validate_native_workload([dict(file=source, command=f'cc {flags} -c "{source}"')], ROOT)

    def test_artifact_manifest_rejects_tampering_and_stale_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = pathlib.Path(temporary)
            identity = dict(component="wasmhome/wasm-micro-runtime", version="2.4.5-wasmhome.1", component_hash="c" * 64, repository_revision="25bd7eb63e828e4bd242cc9b38d260b4b31c6605", aot_version=5)
            for kind, data in (("wasm", b"\0asm\x01\0\0\0"), ("aot", b"\0aot\x05\0\0\0")):
                (folder / f"benchmark.{kind}").write_bytes(data)
                (folder / f"benchmark_{kind}_embedded.c").write_text(render(data, f"benchmark_{kind}"), encoding="ascii")
            (folder / "benchmark.wasm.s").write_text("loop\n")
            manifest = dict(schema="wasmhome-e1-artifacts-v2", aot_format_version=5, source_sha256=sha256(ROOT / "workload/benchmark.c"),
                            header_sha256=sha256(ROOT / "workload/benchmark.h"), runtime_identity=dict(identity),
                            wamrc_source_revision_declared="25bd7eb63e828e4bd242cc9b38d260b4b31c6605", wamrc_source_provenance="operator_declaration_not_binary_origin_proof",
                            wamrc_version="wamrc 2.4.5", wamrc_path="fixture-wamrc", wamrc_sha256="a" * 64,
                            clang_version="clang fixture", clang_path="fixture-clang", clang_sha256="b" * 64,
                            expected_checksum="0xDBB2F0D4", clang_flags=["--target=wasm32-wasi", "-O3"], aot_flags=["--target=xtensa", "--bounds-checks=1", "--disable-llvm-lto", "-O3"],
                            files={p.name: dict(sha256=sha256(p), bytes=p.stat().st_size) for p in folder.iterdir()})
            path = folder / "build-manifest.json"
            path.write_text(json.dumps(manifest))
            validate_artifacts(folder, ROOT, check_tools=False, runtime_identity=identity)
            for key, value in (("schema", "wasmhome-e1-artifacts-v1"), ("aot_format_version", 4)):
                original_value = manifest[key]
                manifest[key] = value
                path.write_text(json.dumps(manifest))
                with self.subTest(key=key), self.assertRaises(ValueError):
                    validate_artifacts(folder, ROOT, check_tools=False, runtime_identity=identity)
                manifest[key] = original_value
            aot = folder / "benchmark.aot"
            aot.write_bytes(b"\0aot\x04\0\0\0")
            manifest["files"][aot.name] = dict(sha256=sha256(aot), bytes=aot.stat().st_size)
            path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "AOT format must be version 5"):
                validate_artifacts(folder, ROOT, check_tools=False, runtime_identity=identity)
            aot.write_bytes(b"\0aot\x05\0\0\0")
            manifest["files"][aot.name] = dict(sha256=sha256(aot), bytes=aot.stat().st_size)
            for key in ("clang_flags", "aot_flags"):
                original = manifest[key][:]
                for flags in ([flag for flag in original if flag != "-O3"], [flag.replace("-O3", "-O2") for flag in original], original + ["-O2"]):
                    manifest[key] = flags
                    path.write_text(json.dumps(manifest))
                    with self.subTest(key=key, flags=flags), self.assertRaisesRegex(ValueError, "final optimization"):
                        validate_artifacts(folder, ROOT, check_tools=False, runtime_identity=identity)
                manifest[key] = original
            manifest["aot_flags"].append("--bounds-checks=0")
            path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "disable bounds"):
                validate_artifacts(folder, ROOT, check_tools=False, runtime_identity=identity)
            manifest["aot_flags"].pop()
            manifest["aot_flags"].remove("--disable-llvm-lto")
            path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "explicitly disable LLVM LTO"):
                validate_artifacts(folder, ROOT, check_tools=False, runtime_identity=identity)
            manifest["aot_flags"].append("--disable-llvm-lto")
            manifest["runtime_identity"]["version"] = "2.4.1"
            path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "runtime identity"):
                validate_artifacts(folder, ROOT, check_tools=False, runtime_identity=identity)
            manifest["runtime_identity"] = dict(identity)
            path.write_text(json.dumps(manifest))
            (folder / "benchmark_wasm_embedded.c").write_text("tampered")
            with self.assertRaisesRegex(ValueError, "hash/size"):
                validate_artifacts(folder, ROOT, check_tools=False, runtime_identity=identity)
            manifest["source_sha256"] = "0" * 64
            path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "source changed"):
                validate_artifacts(folder, ROOT, check_tools=False, runtime_identity=identity)

    def test_toolchain_identity_rejects_missing_sha_changed_binary_or_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            tool = pathlib.Path(temporary) / "fixture-tool"
            tool.write_bytes(b"tool")
            manifest = dict(wamrc_source_revision_declared="25bd7eb63e828e4bd242cc9b38d260b4b31c6605", wamrc_source_provenance="operator_declaration_not_binary_origin_proof",
                            wamrc_version="wamrc 2.4.5", wamrc_path=str(tool), wamrc_sha256=sha256(tool),
                            clang_version="clang fixture", clang_path=str(tool), clang_sha256=sha256(tool))
            with patch("validate_build_config.subprocess.run", side_effect=[SimpleNamespace(stdout="wamrc 2.4.5\n", stderr=""), SimpleNamespace(stdout="clang fixture\n", stderr="")]):
                validate_toolchain(manifest)
            for key, value in (("wamrc_source_revision_declared", "abc"), ("wamrc_source_revision_declared", "47c7c85ac21f240a878026af603e032065d183fb"), ("wamrc_version", "wamrc 2.4.1"), ("wamrc_sha256", "0" * 64)):
                with self.subTest(key=key), self.assertRaises(ValueError):
                    validate_toolchain(dict(manifest, **{key: value}))
            with patch("validate_build_config.subprocess.run", return_value=SimpleNamespace(stdout="wamrc 2.4.1\n", stderr="")), self.assertRaisesRegex(ValueError, "version differs"):
                validate_toolchain(manifest)

    def test_runtime_validator_bounds_and_compiled_source_paths(self):
        directory = pathlib.Path(tempfile.gettempdir()) / "espressif__wasm-micro-runtime"
        source = directory / "wasm_runtime_common.c"
        validator = directory / "aot_validator.c"
        flags = "-DWASM_ENABLE_INTERP=0 -DWASM_ENABLE_AOT=1 -DWASM_ENABLE_FAST_INTERP=0 -DWASM_ENABLE_AOT_VALIDATOR=1 -DWASM_CONFIGURABLE_BOUNDS_CHECKS=0"
        commands = [dict(file=str(file), command=flags) for file in (source, validator)]
        validate_runtime_commands(commands, "aot", directory, {source, validator})
        for before, after in (("WASM_ENABLE_AOT_VALIDATOR=1", "WASM_ENABLE_AOT_VALIDATOR=0"), ("WASM_CONFIGURABLE_BOUNDS_CHECKS=0", "WASM_CONFIGURABLE_BOUNDS_CHECKS=1")):
            with self.subTest(macro=before), self.assertRaises(ValueError):
                validate_runtime_commands([dict(x, command=x["command"].replace(before, after)) for x in commands], "aot", directory, {source, validator})
        with self.assertRaisesRegex(ValueError, "validator source"):
            validate_runtime_commands(commands[:1], "aot", directory, {source})
        with self.assertRaisesRegex(ValueError, "resolved component"):
            validate_runtime_commands(commands, "aot", directory / "another", {source, validator})

    def test_cmake_rejects_old_payloads_without_manifest(self):
        cmake = pathlib.Path(r"C:\esp\tools-task8\tools\cmake\3.30.2\bin\cmake.exe")
        if not cmake.is_file():
            self.skipTest("CMake unavailable; integration gate needs verification separately")
        with tempfile.TemporaryDirectory(prefix="wasmhome-cmake-") as temporary:
            root = pathlib.Path(temporary)
            (root / "tools").mkdir()
            for name in ("validate_build_config.py", "embed_artifact.py"):
                shutil.copyfile(ROOT / "tools" / name, root / "tools" / name)
            (root / "artifacts").mkdir()
            (root / "artifacts/benchmark.wasm").write_bytes(b"old payload")
            (root / "artifacts/benchmark_wasm_embedded.c").write_text("old embedded payload")
            (root / "check.cmake").write_text(f'set(CMAKE_SOURCE_DIR "{root.as_posix()}")\nset(PYTHON "{pathlib.Path(sys.executable).as_posix()}")\nset(EXPERIMENT_SCENARIO interp)\ninclude("{(ROOT / "main/CMakeLists.txt").as_posix()}")\n')
            result = subprocess.run([str(cmake), "-P", str(root / "check.cmake")], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("E1 artifacts are missing/stale/invalid", result.stderr)
            self.assertIn("build-manifest.json", result.stderr)


STUB = r'''
#pragma once
#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#define CONFIG_WASMHOME_RUN_BENCHMARK 1
#define CONFIG_WAMR_ENABLE_INTERP 1
#define CONFIG_WAMR_INTERP_CLASSIC 1
#define MALLOC_CAP_INTERNAL 1
#define ESP_ERROR_CHECK(x) assert((x) == 0)
#define ESP_LOGE(tag, fmt, ...) ((void)tag)
#define ESP_LOGI(tag, fmt, ...) ((void)tag)
static int stage, calls, frees, unloads, deinsts, exec_frees, destroys;
static void *allocated;
typedef void *wasm_module_t;
typedef void *wasm_module_inst_t;
typedef void *wasm_exec_env_t;
typedef void *wasm_function_inst_t;
typedef struct { int mem_alloc_type; struct { struct { void *malloc_func, *realloc_func, *free_func; } allocator; } mem_alloc_option; } RuntimeInitArgs;
enum { Alloc_With_Allocator };
static size_t heap_caps_get_free_size(int x) { return 1000; }
static size_t heap_caps_get_minimum_free_size(int x) { return 900; }
static int64_t esp_timer_get_time(void) { static int64_t t; return ++t; }
static int wh_platform_start(void) { return 0; }
static void *os_malloc(unsigned size) { if(stage == 1) return NULL; return allocated = malloc(size); }
static void *os_realloc(void *p, unsigned s) { return realloc(p,s); }
static void os_free(void *p) { assert(p == allocated); assert(!unloads || unloads == 1); free(p); frees++; }
static bool wasm_runtime_full_init(RuntimeInitArgs *a) { return stage != 8; }
static void wasm_runtime_destroy(void) { destroys++; }
extern const uint8_t benchmark_wasm[];
static wasm_module_t wasm_runtime_load(uint8_t *p, uint32_t n, char *err, uint32_t z) {
    assert(p != benchmark_wasm && p == allocated && ((uintptr_t)p % 8) == 0);
    assert(n == 8 && memcmp(p, benchmark_wasm, n) == 0); p[0] = 99;
    return stage == 2 ? NULL : (void *)1;
}
static wasm_module_inst_t wasm_runtime_instantiate(wasm_module_t m, unsigned s, unsigned h, char *err, unsigned z) { return stage == 3 ? NULL : (void *)2; }
static wasm_exec_env_t wasm_runtime_create_exec_env(wasm_module_inst_t m, unsigned s) { return stage == 4 ? NULL : (void *)3; }
static wasm_function_inst_t wasm_runtime_lookup_function(wasm_module_inst_t m, const char *n) { return stage == 5 ? NULL : (void *)4; }
static bool wasm_runtime_call_wasm(wasm_exec_env_t e, wasm_function_inst_t f, unsigned argc, uint32_t *argv) {
    calls++; if(stage == 6 && calls == 2) return false;
    if(stage == 9 && calls == 6) return false;
    argv[0] = stage == 7 ? 0 : 0xDBB2F0D4u; return true;
}
static const char *wasm_runtime_get_exception(wasm_module_inst_t m) { return "fake trap"; }
static void wasm_runtime_destroy_exec_env(wasm_exec_env_t e) { assert(!deinsts && !unloads && !frees); exec_frees++; }
static void wasm_runtime_deinstantiate(wasm_module_inst_t m) { assert(!unloads && !frees); deinsts++; }
static void wasm_runtime_unload(wasm_module_t m) { assert(!frees); unloads++; }
'''

HARNESS = r'''
#include "main.c"
const uint8_t benchmark_wasm[] = {0,97,115,109,1,0,0,0};
const uint32_t benchmark_wasm_len = 8;
int main(int argc, char **argv) {
    stage = argc > 1 ? atoi(argv[1]) : 0;
    app_main();
    assert(benchmark_wasm[0] == 0);
    assert(frees == ((stage == 1 || stage == 8) ? 0 : 1));
    assert(unloads == ((stage == 1 || stage == 2 || stage == 8) ? 0 : 1));
    assert(deinsts == ((stage >= 1 && stage <= 3) || stage == 8 ? 0 : 1));
    assert(exec_frees == ((stage >= 1 && stage <= 4) || stage == 8 ? 0 : 1));
    assert(destroys == (stage == 8 ? 0 : 1));
    return 0;
}
'''


class RunnerCompiledTests(unittest.TestCase):
    def test_load_failures_traps_and_checksums_release_resources(self):
        vcvars = pathlib.Path(r"C:\Program Files\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvars64.bat")
        if not vcvars.is_file():
            self.skipTest("MSVC setup unavailable; compiled runner check required separately")
        with tempfile.TemporaryDirectory(prefix="wasmhome-e1-") as temporary:
            folder = pathlib.Path(temporary)
            shutil.copyfile(ROOT / "main/main.c", folder / "main.c")
            (folder / "stub.h").write_text(STUB)
            for header in ("esp_heap_caps.h", "esp_log.h", "esp_timer.h", "platform.h", "sdkconfig.h", "bh_platform.h", "wasm_export.h"):
                (folder / header).write_text('#include "stub.h"\n')
            (folder / "runner.c").write_text(HARNESS)
            command = f'""{vcvars}" >nul && cl /nologo /std:c11 /DWASMHOME_SCENARIO_INTERP=1 /I"{ROOT / "workload"}" runner.c /Fe:runner.exe"'
            build = subprocess.run(f'cmd /d /s /c {command}', cwd=folder, capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            for stage in range(10):
                with self.subTest(stage=stage):
                    run = subprocess.run([str(folder / "runner.exe"), str(stage)], capture_output=True, text=True)
                    self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                    if stage in (6, 7):
                        self.assertNotIn("WASMHOME_CSV", run.stdout)
                    if stage == 9:
                        self.assertIn("0x00000000,invalid", run.stdout)
                        self.assertEqual(run.stdout.count("WASMHOME_CSV,interp,"), 30)


if __name__ == "__main__":
    unittest.main()
