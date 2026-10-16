"""Exercise the real bounded module-log ring implementation on the host."""

import pathlib
import shutil
import subprocess
import tempfile
import textwrap
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


PRELUDE = r'''
#include <assert.h>
#include <inttypes.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define WH_MODULE_DEFERRED_LOG_CAPACITY 32u
#define WH_MODULE_DEFERRED_LOG_LINE_BYTES 2048u
#define WH_MODULE_DEFERRED_LOG_POST_COMMIT_RESERVE 20u
#define MALLOC_CAP_SPIRAM 1
#define MALLOC_CAP_INTERNAL 2
#define MALLOC_CAP_8BIT 4
#define ESP_OK 0
#define ESP_ERR_INVALID_ARG 1
#define ESP_ERR_INVALID_SIZE 2
#define ESP_ERR_NO_MEM 3
#define ESP_ERR_INVALID_STATE 4
typedef int esp_err_t;
typedef unsigned portMUX_TYPE;
typedef void *TaskHandle_t;
static TaskHandle_t current_task = (void *)1;
#define portMUX_INITIALIZER_UNLOCKED 0
#define portENTER_CRITICAL(mux) ((void)(mux))
#define portEXIT_CRITICAL(mux) ((void)(mux))
static TaskHandle_t xTaskGetCurrentTaskHandle(void) { return current_task; }
static void *heap_caps_malloc(size_t bytes, int caps)
{ (void)caps; return malloc(bytes); }
static void heap_caps_free(void *pointer) { free(pointer); }

static portMUX_TYPE deferred_log_mux = portMUX_INITIALIZER_UNLOCKED;
static char *deferred_log_storage;
static size_t deferred_log_lengths[WH_MODULE_DEFERRED_LOG_CAPACITY];
static size_t deferred_log_read;
static size_t deferred_log_write;
static size_t deferred_log_count;
static uint32_t deferred_log_dropped;
static bool deferred_log_active;
static size_t deferred_log_reserved;
static TaskHandle_t deferred_log_owner;
'''


HARNESS = r'''
static void enqueue_text(const char *text)
{
    assert(wh_module_manager_emit_or_defer_log_line(text, strlen(text)) == ESP_OK);
}

int main(void)
{
    assert(deferred_logs_allocate() == ESP_OK);

    /* The ring owns a copy, and draining preserves FIFO order. */
    assert(deferred_logs_begin() == ESP_OK);
    char mutable_line[32] = "fifo-first\n";
    enqueue_text(mutable_line);
    strcpy(mutable_line, "overwritten\n");
    enqueue_text("fifo-second\n");
    assert(deferred_logs_flush() == ESP_OK);

    /* A full ring records sticky overflow and emits an explicit marker. */
    assert(deferred_logs_begin() == ESP_OK);
    char item[32];
    for (size_t i = 0; i < WH_MODULE_DEFERRED_LOG_CAPACITY; ++i) {
        int written = snprintf(item, sizeof(item), "full-%02zu\n", i);
        assert(written > 0 && (size_t)written < sizeof(item));
        enqueue_text(item);
    }
    assert(wh_module_manager_emit_or_defer_log_line("overflow\n", 9) == ESP_ERR_NO_MEM);
    assert(deferred_logs_failed());
    assert(deferred_logs_flush() == ESP_ERR_NO_MEM);

    /* Formatting truncation sets the sticky failure and emits a loss marker. */
    assert(deferred_logs_begin() == ESP_OK);
    char oversized[WH_MODULE_DEFERRED_LOG_LINE_BYTES + 64];
    memset(oversized, 'x', sizeof(oversized) - 1);
    oversized[sizeof(oversized) - 1] = '\0';
    assert(module_log_printf("%s", oversized) == ESP_ERR_INVALID_SIZE);
    assert(deferred_logs_failed());
    assert(deferred_logs_flush() == ESP_ERR_NO_MEM);

    /* A formatter failure path can mark loss even when no line was formatted. */
    assert(deferred_logs_begin() == ESP_OK);
    wh_module_manager_mark_deferred_log_failure();
    assert(deferred_logs_failed());
    enqueue_text("WASMHOME_MODULE_LOG,{\"schema\":\"wasmhome-module-log-v1\","
                 "\"event\":\"update_event_format_failed\"}\n");
    assert(deferred_logs_flush() == ESP_ERR_NO_MEM);

    /* A failed pre-commit reservation prevents commit and is not silent. */
    assert(deferred_logs_begin() == ESP_OK);
    for (size_t i = 0; i < WH_MODULE_DEFERRED_LOG_CAPACITY - 18; ++i)
        enqueue_text("reserve-fill\n");
    bool commit_called = false;
    if (deferred_logs_reserve_remaining(WH_MODULE_DEFERRED_LOG_POST_COMMIT_RESERVE))
        commit_called = true;
    assert(!commit_called);
    assert(!deferred_logs_failed());
    assert(deferred_logs_flush() == ESP_OK);

    /* A reservation protects the owner’s final record from other producers. */
    assert(deferred_logs_begin() == ESP_OK);
    assert(deferred_logs_reserve_remaining(1));
    current_task = (void *)2;
    for (size_t i = 0; i < WH_MODULE_DEFERRED_LOG_CAPACITY - 1; ++i)
        enqueue_text("other-producer\n");
    assert(wh_module_manager_emit_or_defer_log_line("nonowner-overflow\n", 18) ==
           ESP_ERR_NO_MEM);
    current_task = (void *)1;
    enqueue_text("reserved-owner\n");
    assert(deferred_logs_failed());
    assert(deferred_logs_flush() == ESP_ERR_NO_MEM);

    heap_caps_free(deferred_log_storage);
    deferred_log_storage = NULL;
    return 0;
}
'''


def extract_function(source, signature):
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 0
    in_string = False
    in_char = False
    escaped = False
    in_line_comment = False
    in_block_comment = False
    for index in range(opening, len(source)):
        char = source[index]
        next_char = source[index + 1] if index + 1 < len(source) else ""
        if in_line_comment:
            if char == "\n":
                in_line_comment = False
            continue
        if in_block_comment:
            if char == "*" and next_char == "/":
                in_block_comment = False
            continue
        if escaped:
            escaped = False
            continue
        if (in_string or in_char) and char == "\\":
            escaped = True
            continue
        if in_string:
            if char == '"':
                in_string = False
            continue
        if in_char:
            if char == "'":
                in_char = False
            continue
        if char == "/" and next_char == "/":
            in_line_comment = True
            continue
        if char == "/" and next_char == "*":
            in_block_comment = True
            continue
        if char == '"':
            in_string = True
            continue
        if char == "'":
            in_char = True
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"unterminated function: {signature}")


class ModuleDeferredLogsHostTests(unittest.TestCase):
    def test_ring_copy_fifo_capacity_reservation_and_sticky_failures(self):
        compiler = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
        if compiler is None:
            self.skipTest("A host C compiler is required for the deferred log test")

        source = (ROOT / "main/module_manager.c").read_text(encoding="utf-8")
        functions = [
            "static esp_err_t\ndeferred_logs_allocate(",
            "static esp_err_t\ndeferred_logs_begin(",
            "static bool\ndeferred_logs_failed(",
            "static void\ndeferred_logs_mark_failed(",
            "static bool\ndeferred_logs_reserve_remaining(",
            "esp_err_t\nwh_module_manager_emit_or_defer_log_line(",
            "void\nwh_module_manager_mark_deferred_log_failure(",
            "static esp_err_t\ndeferred_logs_flush(",
            "static esp_err_t\nmodule_log_printf(",
        ]
        harness = "\n".join(
            [PRELUDE]
            + [extract_function(source, signature) for signature in functions]
            + [HARNESS]
        )
        with tempfile.TemporaryDirectory(prefix="wasmhome-module-deferred-logs-") as temp_name:
            temp = pathlib.Path(temp_name)
            source_path = temp / "deferred_logs_harness.c"
            executable = temp / "deferred_logs_harness"
            source_path.write_text(textwrap.dedent(harness), encoding="utf-8")
            build = subprocess.run(
                [compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-pedantic",
                 str(source_path), "-o", str(executable)],
                capture_output=True, text=True,
            )
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            lines = result.stdout.splitlines()
            self.assertEqual(lines[:2], ["fifo-first", "fifo-second"])
            self.assertEqual(lines[2:34], [f"full-{i:02}" for i in range(32)])
            self.assertIn('"event":"deferred_log_overflow"', lines[34])
            self.assertEqual(lines[35], 'WASMHOME_MODULE_LOG,{"schema":"wasmhome-module-log-v1","event":"record_truncated"}')
            self.assertIn('"event":"deferred_log_overflow"', lines[36])
            self.assertEqual(lines[37], 'WASMHOME_MODULE_LOG,{"schema":"wasmhome-module-log-v1","event":"update_event_format_failed"}')
            self.assertIn('"event":"deferred_log_overflow"', lines[38])
            self.assertEqual(lines[39:53], ["reserve-fill"] * 14)
            self.assertEqual(lines[53:84], ["other-producer"] * 31)
            self.assertEqual(lines[84], "reserved-owner")
            self.assertIn('"event":"deferred_log_overflow"', lines[85])


if __name__ == "__main__":
    unittest.main()
