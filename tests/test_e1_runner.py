import pathlib
import json
import hashlib
import os
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

    @patch("validate_build_config.validate_manifest")
    def test_artifact_manifest_rejects_tampering_and_stale_source(self, provenance_gate):
        with tempfile.TemporaryDirectory() as temporary:
            folder = pathlib.Path(temporary)
            identity = dict(component="wasmhome/wasm-micro-runtime", version="2.4.5-wasmhome.3", component_hash="c" * 64, repository_revision="25bd7eb63e828e4bd242cc9b38d260b4b31c6605", aot_version=5)
            for kind, data in (("wasm", b"\0asm\x01\0\0\0"), ("aot", b"\0aot\x05\0\0\0")):
                (folder / f"benchmark.{kind}").write_bytes(data)
                (folder / f"benchmark_{kind}_embedded.c").write_text(render(data, f"benchmark_{kind}"), encoding="ascii")
            (folder / "benchmark.wasm.s").write_text("loop\n")
            manifest = dict(schema="wasmhome-e1-artifacts-v3", aot_format_version=5, source_sha256=sha256(ROOT / "workload/benchmark.c"),
                            header_sha256=sha256(ROOT / "workload/benchmark.h"), runtime_identity=dict(identity),
                            wamrc_source_revision="25bd7eb63e828e4bd242cc9b38d260b4b31c6605",
                            wamrc_version="wamrc 2.4.5", wamrc_path="fixture-wamrc", wamrc_sha256="a" * 64,
                            clang_version="clang fixture", clang_path="fixture-clang", clang_sha256="b" * 64,
                            expected_checksum="0xDBB2F0D4", clang_flags=["--target=wasm32-wasi", "-O3"], aot_flags=["--target=xtensa", "--bounds-checks=1", "--disable-llvm-lto", "--opt-level=3"],
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
                expected = "-O3" if key == "clang_flags" else "--opt-level=3"
                wrong = "-O2" if key == "clang_flags" else "--opt-level=2"
                for flags in ([flag for flag in original if flag != expected], [flag.replace(expected, wrong) for flag in original], original + [wrong]):
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
            manifest = dict(wamrc_source_revision="25bd7eb63e828e4bd242cc9b38d260b4b31c6605",
                            wamrc_version="wamrc 2.4.5", wamrc_path=str(tool), wamrc_sha256=sha256(tool),
                            clang_version="clang fixture", clang_path=str(tool), clang_sha256=sha256(tool))
            with patch("validate_build_config.subprocess.run", side_effect=[SimpleNamespace(stdout="wamrc 2.4.5\n", stderr=""), SimpleNamespace(stdout="clang fixture\n", stderr="")]):
                validate_toolchain(manifest)
            for key, value in (("wamrc_source_revision", "abc"), ("wamrc_source_revision", "47c7c85ac21f240a878026af603e032065d183fb"), ("wamrc_version", "wamrc 2.4.1"), ("wamrc_sha256", "0" * 64)):
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
            available_cmake = shutil.which("cmake")
            if available_cmake is None:
                self.skipTest("CMake unavailable; integration gate needs verification separately")
            cmake = pathlib.Path(available_cmake)
        with tempfile.TemporaryDirectory(prefix="wasmhome-cmake-") as temporary:
            root = pathlib.Path(temporary)
            (root / "tools").mkdir()
            for name in ("validate_build_config.py", "embed_artifact.py", "evidence_json.py", "toolchain_provenance.py", "runtime_package.py"):
                shutil.copyfile(ROOT / "tools" / name, root / "tools" / name)
            (root / "artifacts").mkdir()
            (root / "artifacts/benchmark.wasm").write_bytes(b"old payload")
            (root / "artifacts/benchmark_wasm_embedded.c").write_text("old embedded payload")
            (root / "check.cmake").write_text(f'set(CMAKE_SOURCE_DIR "{root.as_posix()}")\nset(PYTHON "{pathlib.Path(sys.executable).as_posix()}")\nset(EXPERIMENT_SCENARIO interp)\ninclude("{(ROOT / "main/CMakeLists.txt").as_posix()}")\n')
            result = subprocess.run([str(cmake), "-P", str(root / "check.cmake")], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("E1 artifacts are missing/stale/invalid", result.stderr)
            self.assertIn("build-manifest.json", result.stderr)

    def test_cmake_requires_posix_pthread_only_for_benchmark_builds(self):
        cmake = shutil.which("cmake")
        if cmake is None:
            self.skipTest("CMake unavailable; benchmark component dependency needs verification separately")
        with tempfile.TemporaryDirectory(prefix="wasmhome-thread-dependency-") as temporary:
            folder = pathlib.Path(temporary)
            for benchmark_enabled in (True, False):
                with self.subTest(benchmark_enabled=benchmark_enabled):
                    enabled = "ON" if benchmark_enabled else "OFF"
                    script = f'''\
set(CMAKE_SOURCE_DIR "{ROOT.as_posix()}")
set(CMAKE_BUILD_EARLY_EXPANSION TRUE)
set(EXPERIMENT_SCENARIO native)
set(CONFIG_WASMHOME_RUN_BENCHMARK {enabled})
function(idf_component_register)
    cmake_parse_arguments(ARG "" "" "SRCS;INCLUDE_DIRS;REQUIRES" ${{ARGN}})
    set(CAPTURED_REQUIRES "${{ARG_REQUIRES}}" PARENT_SCOPE)
endfunction()
function(target_compile_definitions)
endfunction()
function(set_source_files_properties)
endfunction()
include("{(ROOT / 'main/CMakeLists.txt').as_posix()}")
list(FIND CAPTURED_REQUIRES pthread PTHREAD_INDEX)
if(CONFIG_WASMHOME_RUN_BENCHMARK AND PTHREAD_INDEX EQUAL -1)
    message(FATAL_ERROR "benchmark component is missing pthread dependency")
endif()
if(NOT CONFIG_WASMHOME_RUN_BENCHMARK AND NOT PTHREAD_INDEX EQUAL -1)
    message(FATAL_ERROR "non-benchmark component unexpectedly requires pthread")
endif()
'''
                    path = folder / f"benchmark-{enabled.lower()}.cmake"
                    path.write_text(script)
                    result = subprocess.run([cmake, "-P", str(path)], cwd=folder,
                                            capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


STUB = r'''
#pragma once
#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#define CONFIG_WASMHOME_RUN_BENCHMARK 1
#define CONFIG_WAMR_ENABLE_INTERP 1
#define CONFIG_WAMR_INTERP_CLASSIC 1
#ifdef WASMHOME_TEST_SPIRAM
#define CONFIG_SPIRAM 1
#endif
#define MALLOC_CAP_INTERNAL 1
#define MALLOC_CAP_SPIRAM 2
#define ESP_ERROR_CHECK(x) assert((x) == 0)
#define ESP_LOGE(tag, fmt, ...) ((void)tag)
#define ESP_LOGI(tag, fmt, ...) ((void)tag)
static int stage, calls, frees, unloads, deinsts, exec_frees, destroys;
typedef struct { size_t stack_size; int detach_state; bool initialized; } pthread_attr_t;
typedef unsigned pthread_t;
#define PTHREAD_CREATE_DETACHED 1
static bool benchmark_pthread_active;
static int pthread_create_calls, pthread_attr_init_calls, pthread_attr_destroy_calls;
static int pthread_attr_stack_calls, pthread_attr_detach_calls;
static size_t benchmark_thread_stack_size;
static int benchmark_thread_detach_state;
static int pthread_attr_init(pthread_attr_t *attr) {
    pthread_attr_init_calls++;
    memset(attr, 0, sizeof(*attr));
    attr->initialized = true;
    return 0;
}
static int pthread_attr_setstacksize(pthread_attr_t *attr, size_t stack_size) {
    assert(attr->initialized);
    pthread_attr_stack_calls++;
    attr->stack_size = stack_size;
    benchmark_thread_stack_size = stack_size;
    return 0;
}
static int pthread_attr_setdetachstate(pthread_attr_t *attr, int detach_state) {
    assert(attr->initialized);
    pthread_attr_detach_calls++;
    attr->detach_state = detach_state;
    benchmark_thread_detach_state = detach_state;
    return 0;
}
static int pthread_create(pthread_t *thread, const pthread_attr_t *attr,
                          void *(*start_routine)(void *), void *arg) {
    assert(!benchmark_pthread_active);
    assert(attr->initialized);
    assert(attr->stack_size == 8192);
    assert(attr->detach_state == PTHREAD_CREATE_DETACHED);
    assert(arg == NULL);
    pthread_create_calls++;
    if (stage == 10) return 11;
    *thread = 1;
    benchmark_pthread_active = true;
    assert(start_routine(arg) == NULL);
    benchmark_pthread_active = false;
    return 0;
}
static int pthread_attr_destroy(pthread_attr_t *attr) {
    assert(attr->initialized);
    attr->initialized = false;
    pthread_attr_destroy_calls++;
    return 0;
}
static void *allocated;
static bool memory_output_active;
static int memory_output_printf_calls, memory_output_events;
static size_t heap_total_internal_calls, heap_total_psram_calls;
static size_t heap_info_internal_calls, heap_info_psram_calls;
static size_t heap_info_internal_sum_changes, heap_info_psram_sum_changes;
static size_t heap_info_internal_previous_sum, heap_info_psram_previous_sum;
static size_t legacy_largest_calls;
typedef struct {
    size_t total_free_bytes;
    size_t total_allocated_bytes;
    size_t largest_free_block;
    size_t minimum_free_bytes;
    size_t allocated_blocks;
    size_t free_blocks;
    size_t total_blocks;
} multi_heap_info_t;
static int test_printf(const char *format, ...) {
    bool starts_memory = strncmp(format, "WASMHOME_MEMORY,", 15) == 0;
    if (strcmp(format, "%s\n") == 0) {
        va_list peek;
        va_start(peek, format);
        const char *value = va_arg(peek, const char *);
        starts_memory = value && strncmp(value, "WASMHOME_MEMORY,", 15) == 0;
        va_end(peek);
    }
    if (starts_memory) {
        assert(!memory_output_active);
        memory_output_active = true;
        memory_output_printf_calls = 0;
        memory_output_events++;
    }
    if (memory_output_active) memory_output_printf_calls++;
    va_list arguments;
    va_start(arguments, format);
    int result = vprintf(format, arguments);
    va_end(arguments);
    if (memory_output_active) {
        fputs("\nWASMHOME_EVENT,{\"event\":\"concurrent_log_probe\"}\n", stdout);
        if (strchr(format, '\n')) {
            assert(memory_output_printf_calls == 1);
            memory_output_active = false;
        }
    }
    return result;
}
static int test_puts(const char *value) {
    int result = fputs(value, stdout);
    if (memory_output_active) {
        memory_output_printf_calls++;
        assert(memory_output_printf_calls == 1);
        memory_output_active = false;
        fputs("\nWASMHOME_EVENT,{\"event\":\"concurrent_log_probe\"}\n", stdout);
    }
    return result;
}
#define printf test_printf
#define puts test_puts
typedef void *wasm_module_t;
typedef void *wasm_module_inst_t;
typedef void *wasm_exec_env_t;
typedef void *wasm_function_inst_t;
typedef struct { int mem_alloc_type; struct { struct { void *malloc_func, *realloc_func, *free_func; } allocator; } mem_alloc_option; } RuntimeInitArgs;
enum { Alloc_With_Allocator };
static size_t heap_caps_get_total_size(uint32_t x) {
    if (x == MALLOC_CAP_INTERNAL) {
        heap_total_internal_calls++;
        return 2048;
    }
    assert(x == MALLOC_CAP_SPIRAM);
    heap_total_psram_calls++;
    return 4096;
}
static size_t heap_caps_get_free_size(uint32_t x) {
    return x == MALLOC_CAP_INTERNAL ? 800 : 2800;
}
static size_t heap_caps_get_largest_free_block(uint32_t x) {
    legacy_largest_calls++;
    return x == MALLOC_CAP_INTERNAL ? 900 : 2900;
}
static void heap_caps_get_info(multi_heap_info_t *info, uint32_t caps) {
    size_t snapshot;
    if (caps == MALLOC_CAP_INTERNAL) {
        snapshot = heap_info_internal_calls++;
        assert(snapshot < 100);
        info->total_free_bytes = 1000 + snapshot;
        info->total_allocated_bytes = 500 + (snapshot % 7) * 13;
        info->largest_free_block = 800 + snapshot;
        if (snapshot > 0 &&
            info->total_free_bytes + info->total_allocated_bytes != heap_info_internal_previous_sum) {
            heap_info_internal_sum_changes++;
        }
        heap_info_internal_previous_sum = info->total_free_bytes + info->total_allocated_bytes;
    } else {
        assert(caps == MALLOC_CAP_SPIRAM);
        snapshot = heap_info_psram_calls++;
        assert(snapshot < 100);
        info->total_free_bytes = 3000 + snapshot;
        info->total_allocated_bytes = 700 + (snapshot % 7) * 17;
        info->largest_free_block = 2400 + snapshot;
        if (snapshot > 0 &&
            info->total_free_bytes + info->total_allocated_bytes != heap_info_psram_previous_sum) {
            heap_info_psram_sum_changes++;
        }
        heap_info_psram_previous_sum = info->total_free_bytes + info->total_allocated_bytes;
    }
    info->minimum_free_bytes = info->total_free_bytes;
    info->allocated_blocks = 1;
    info->free_blocks = 1;
    info->total_blocks = 2;
}
static size_t heap_caps_get_minimum_free_size(int x) { return 900; }
static int64_t esp_timer_get_time(void) { static int64_t t; return ++t; }
static int wh_platform_start(void) { assert(!benchmark_pthread_active); return 0; }
static uint64_t wh_platform_boot_id(void) {
    assert(benchmark_pthread_active);
    return UINT64_C(0x0102030405060708);
}
static void *os_malloc(unsigned size) { if(stage == 1) return NULL; return allocated = malloc(size); }
static void *os_realloc(void *p, unsigned s) { return realloc(p,s); }
static void os_free(void *p) { assert(p == allocated); assert(!unloads || unloads == 1); free(p); frees++; }
static bool wasm_runtime_full_init(RuntimeInitArgs *a) {
    assert(benchmark_pthread_active);
    return stage != 8;
}
static void wasm_runtime_destroy(void) { assert(benchmark_pthread_active); destroys++; }
extern const uint8_t benchmark_wasm[];
static wasm_module_t wasm_runtime_load(uint8_t *p, uint32_t n, char *err, uint32_t z) {
    assert(benchmark_pthread_active);
    assert(p != benchmark_wasm && p == allocated && ((uintptr_t)p % 8) == 0);
    assert(n == 8 && memcmp(p, benchmark_wasm, n) == 0); p[0] = 99;
    return stage == 2 ? NULL : (void *)1;
}
static wasm_module_inst_t wasm_runtime_instantiate(wasm_module_t m, unsigned s, unsigned h, char *err, unsigned z) { return stage == 3 ? NULL : (void *)2; }
static wasm_exec_env_t wasm_runtime_create_exec_env(wasm_module_inst_t m, unsigned s) { return stage == 4 ? NULL : (void *)3; }
static wasm_function_inst_t wasm_runtime_lookup_function(wasm_module_inst_t m, const char *n) { return stage == 5 ? NULL : (void *)4; }
static bool wasm_runtime_call_wasm(wasm_exec_env_t e, wasm_function_inst_t f, unsigned argc, uint32_t *argv) {
    assert(benchmark_pthread_active);
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
    assert(pthread_attr_init_calls == 1);
    assert(pthread_attr_stack_calls == 1);
    assert(pthread_attr_detach_calls == 1);
    assert(pthread_attr_destroy_calls == 1);
    assert(pthread_create_calls == 1);
    assert(benchmark_thread_stack_size == 8192);
    assert(benchmark_thread_detach_state == PTHREAD_CREATE_DETACHED);
    assert(!benchmark_pthread_active);
    if (stage == 10) {
        assert(calls == 0 && memory_output_events == 0);
        assert(frees == 0 && unloads == 0 && deinsts == 0 && exec_frees == 0 && destroys == 0);
        return 0;
    }
    assert(heap_total_internal_calls == (size_t)memory_output_events);
    assert(heap_info_internal_calls == (size_t)memory_output_events);
    assert(heap_info_internal_sum_changes > 0);
    assert(legacy_largest_calls == 0);
#ifdef CONFIG_SPIRAM
    assert(heap_total_psram_calls == (size_t)memory_output_events);
    assert(heap_info_psram_calls == (size_t)memory_output_events);
    assert(heap_info_psram_sum_changes > 0);
#else
    assert(heap_total_psram_calls == 0);
    assert(heap_info_psram_calls == 0);
#endif
    assert(frees == ((stage == 1 || stage == 8) ? 0 : 1));
    assert(unloads == ((stage == 1 || stage == 2 || stage == 8) ? 0 : 1));
    assert(deinsts == ((stage >= 1 && stage <= 3) || stage == 8 ? 0 : 1));
    assert(exec_frees == ((stage >= 1 && stage <= 4) || stage == 8 ? 0 : 1));
    assert(destroys == (stage == 8 ? 0 : 1));
    return 0;
}
'''

NATIVE_HARNESS = r'''
#include "main.c"
const uint8_t benchmark_wasm[] = {0,97,115,109,1,0,0,0};
const uint32_t benchmark_wasm_len = 8;
uint32_t benchmark_run(void) {
    assert(benchmark_pthread_active);
    calls++;
    return WASMHOME_EXPECTED_CHECKSUM;
}
int main(void) {
    app_main();
    assert(pthread_attr_init_calls == 1 && pthread_attr_destroy_calls == 1);
    assert(pthread_attr_stack_calls == 1 && pthread_attr_detach_calls == 1);
    assert(pthread_create_calls == 1 && !benchmark_pthread_active);
    assert(benchmark_thread_stack_size == BENCHMARK_HOST_THREAD_STACK_SIZE);
    assert(benchmark_thread_detach_state == PTHREAD_CREATE_DETACHED);
    assert(calls == 35);
    assert(memory_output_events == 67);
    assert(heap_total_internal_calls == 67 && heap_info_internal_calls == 67);
    assert(heap_info_internal_sum_changes > 0 && legacy_largest_calls == 0);
    return 0;
}
'''


class RunnerCompiledTests(unittest.TestCase):
    def test_load_failures_traps_and_checksums_release_resources(self):
        vcvars = pathlib.Path(r"C:\Program Files\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvars64.bat")
        with tempfile.TemporaryDirectory(prefix="wasmhome-e1-") as temporary:
            folder = pathlib.Path(temporary)
            shutil.copyfile(ROOT / "main/main.c", folder / "main.c")
            (folder / "stub.h").write_text(STUB)
            for header in ("esp_heap_caps.h", "esp_log.h", "esp_timer.h", "platform.h", "sdkconfig.h", "bh_platform.h", "wasm_export.h", "pthread.h"):
                (folder / header).write_text('#include "stub.h"\n')
            (folder / "runner.c").write_text(HARNESS)
            if vcvars.is_file():
                command = f'""{vcvars}" >nul && cl /nologo /std:c11 /DWASMHOME_SCENARIO_INTERP=1 /I"{folder}" /I"{ROOT / "workload"}" runner.c /Fe:runner.exe"'
                build = subprocess.run(f'cmd /d /s /c {command}', cwd=folder, capture_output=True, text=True)
                executable = folder / "runner.exe"
            else:
                if os.name == "nt":
                    self.skipTest("MSVC setup unavailable; compiled runner check required separately")
                compiler = shutil.which("cc") or shutil.which("gcc")
                if compiler is None:
                    self.skipTest("C compiler unavailable; compiled runner check required separately")
                executable = folder / "runner"
                build = subprocess.run(
                    [compiler, "-std=c11", "-DWASMHOME_SCENARIO_INTERP=1", f"-I{folder}", f"-I{ROOT / 'workload'}", "runner.c", "-o", str(executable)],
                    cwd=folder,
                    capture_output=True,
                    text=True,
                )
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            for stage in range(10):
                with self.subTest(stage=stage):
                    run = subprocess.run([str(executable), str(stage)], capture_output=True, text=True)
                    self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                    memory = [json.loads(line.split("WASMHOME_MEMORY,", 1)[1])
                              for line in run.stdout.splitlines() if "WASMHOME_MEMORY," in line]
                    self.assertGreaterEqual(len(memory), 3)
                    self.assertEqual(
                        run.stdout.count('WASMHOME_EVENT,{"event":"concurrent_log_probe"}'),
                        len(memory),
                    )
                    for index, event in enumerate(memory):
                        self.assertEqual(event["scenario"], "interp")
                        self.assertEqual(event["boot_id"], "0102030405060708")
                        self.assertEqual(event["units"], "bytes")
                        self.assertEqual(event["internal"], {
                            "capability": "MALLOC_CAP_INTERNAL", "status": "measured", "total_bytes": 2048,
                            "free_bytes": 1000 + index, "largest_free_block_bytes": 800 + index,
                            "phase_peak_free_bytes": None, "phase_peak_status": "not_instrumented",
                        })
                        self.assertLessEqual(event["internal"]["largest_free_block_bytes"],
                                             event["internal"]["free_bytes"])
                        self.assertLessEqual(event["internal"]["free_bytes"],
                                             event["internal"]["total_bytes"])
                        self.assertEqual(event["psram"], {
                            "capability": "MALLOC_CAP_SPIRAM", "status": "not_applicable",
                            "total_bytes": None, "free_bytes": None,
                            "largest_free_block_bytes": None, "phase_peak_free_bytes": None,
                            "phase_peak_status": "not_applicable",
                        })
                        self.assertEqual(event["static_memory"], {
                            "status": "external_elf", "source": "ELF", "bytes": None,
                        })
                    phases = [event["phase"] for event in memory]
                    self.assertEqual(phases.count("before_runtime"), 1)
                    self.assertEqual(phases.count("after_init"), 1)
                    self.assertEqual(phases.count("after_release"), 1)
                    if stage in (6, 7):
                        self.assertNotIn("WASMHOME_CSV", run.stdout)
                        self.assertEqual(phases.count("after_warmup"), 1)
                        self.assertEqual(next(event for event in memory
                                              if event["phase"] == "after_warmup")["phase_status"], "failed")
                    if stage == 9:
                        self.assertIn("0x00000000,invalid", run.stdout)
                        self.assertEqual(run.stdout.count("WASMHOME_CSV,interp,"), 30)
                    if stage == 0:
                        for phase in ("after_load", "after_instantiate", "after_exec_env_lookup", "after_warmup"):
                            self.assertEqual(phases.count(phase), 1)
                        before_execution = [event for event in memory if event["phase"] == "before_execution"]
                        execution = [event for event in memory if event["phase"] == "execution"]
                        self.assertEqual([event["repetition"] for event in before_execution], list(range(1, 31)))
                        self.assertEqual([event["repetition"] for event in execution], list(range(1, 31)))
                        self.assertTrue(all(event["phase_status"] == "ok" for event in execution))
                        rows = [line.split("WASMHOME_CSV,", 1)[1].split(",")
                                for line in run.stdout.splitlines() if "WASMHOME_CSV,interp," in line]
                        self.assertEqual([row[4] for row in rows], ["1"] * 30)
                    if stage == 8:
                        self.assertEqual(next(event for event in memory
                                              if event["phase"] == "after_init")["phase_status"], "failed")
                        self.assertEqual(next(event for event in memory
                                              if event["phase"] == "after_release")["phase_status"], "not_applicable")

            failed_thread = subprocess.run([str(executable), "10"], capture_output=True, text=True)
            self.assertEqual(failed_thread.returncode, 0, failed_thread.stdout + failed_thread.stderr)

            if vcvars.is_file():
                command = f'""{vcvars}" >nul && cl /nologo /std:c11 /DWASMHOME_SCENARIO_INTERP=1 /DWASMHOME_TEST_SPIRAM=1 /I"{folder}" /I"{ROOT / "workload"}" runner.c /Fe:runner-psram.exe"'
                build_psram = subprocess.run(f"cmd /d /s /c {command}", cwd=folder, capture_output=True, text=True)
                psram_executable = folder / "runner-psram.exe"
            else:
                build_psram = subprocess.run(
                    [compiler, "-std=c11", "-DWASMHOME_SCENARIO_INTERP=1", "-DWASMHOME_TEST_SPIRAM=1",
                     f"-I{folder}", f"-I{ROOT / 'workload'}", "runner.c", "-o", "runner-psram"],
                    cwd=folder,
                    capture_output=True,
                    text=True,
                )
                psram_executable = folder / "runner-psram"
            self.assertEqual(build_psram.returncode, 0, build_psram.stdout + build_psram.stderr)
            psram_run = subprocess.run([str(psram_executable), "0"], capture_output=True, text=True)
            self.assertEqual(psram_run.returncode, 0, psram_run.stdout + psram_run.stderr)
            psram_events = [json.loads(line.split("WASMHOME_MEMORY,", 1)[1])
                            for line in psram_run.stdout.splitlines() if "WASMHOME_MEMORY," in line]
            self.assertEqual(len(psram_events), 67)
            for index, event in enumerate(psram_events):
                self.assertEqual(event["psram"], {
                    "capability": "MALLOC_CAP_SPIRAM", "status": "measured", "total_bytes": 4096,
                    "free_bytes": 3000 + index, "largest_free_block_bytes": 2400 + index,
                    "phase_peak_free_bytes": None, "phase_peak_status": "not_instrumented",
                })
                self.assertLessEqual(event["psram"]["largest_free_block_bytes"],
                                     event["psram"]["free_bytes"])
                self.assertLessEqual(event["psram"]["free_bytes"], event["psram"]["total_bytes"])

    def test_native_benchmark_uses_same_posix_worker_and_preserves_sample_count(self):
        vcvars = pathlib.Path(r"C:\Program Files\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvars64.bat")
        with tempfile.TemporaryDirectory(prefix="wasmhome-e1-native-thread-") as temporary:
            folder = pathlib.Path(temporary)
            shutil.copyfile(ROOT / "main/main.c", folder / "main.c")
            (folder / "stub.h").write_text(STUB)
            for header in ("esp_heap_caps.h", "esp_log.h", "esp_timer.h", "platform.h", "sdkconfig.h", "bh_platform.h", "wasm_export.h", "pthread.h"):
                (folder / header).write_text('#include "stub.h"\n')
            (folder / "runner.c").write_text(NATIVE_HARNESS)
            if vcvars.is_file():
                command = f'""{vcvars}" >nul && cl /nologo /std:c11 /DWASMHOME_SCENARIO_NATIVE=1 /I"{folder}" /I"{ROOT / "workload"}" runner.c /Fe:runner-native.exe"'
                build = subprocess.run(f"cmd /d /s /c {command}", cwd=folder, capture_output=True, text=True)
                executable = folder / "runner-native.exe"
            else:
                if os.name == "nt":
                    self.skipTest("MSVC setup unavailable; compiled native worker check required separately")
                compiler = shutil.which("cc") or shutil.which("gcc")
                if compiler is None:
                    self.skipTest("C compiler unavailable; native worker check required separately")
                executable = folder / "runner-native"
                build = subprocess.run(
                    [compiler, "-std=c11", "-DWASMHOME_SCENARIO_NATIVE=1", f"-I{folder}", f"-I{ROOT / 'workload'}", "runner.c", "-o", str(executable)],
                    cwd=folder,
                    capture_output=True,
                    text=True,
                )
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            run = subprocess.run([str(executable)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertEqual(run.stdout.count("WASMHOME_CSV,native,"), 30)


if __name__ == "__main__":
    unittest.main()
