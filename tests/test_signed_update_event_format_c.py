"""Check that buffered and default signed-update events share identical JSON."""

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
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define CONFIG_SPIRAM 1
#define MALLOC_CAP_INTERNAL 1
#define MALLOC_CAP_SPIRAM 2
#define ESP_OK 0
#define ESP_ERR_INVALID_ARG 1
#define ESP_ERR_INVALID_SIZE 2
#define WH_SIGNED_UPDATE_MAX_RELEASE_BYTES 32
#define WH_SIGNED_UPDATE_EVENT_LINE_MAX_BYTES 1536
typedef int esp_err_t;
typedef enum { WH_SIGNED_UPDATE_PHASE_CONTROL_ACTIVATED = 1 } wh_signed_update_phase_t;
typedef enum { WH_SIGNED_UPDATE_FAILURE_NONE = 0 } wh_signed_update_failure_t;
typedef struct {
    wh_signed_update_phase_t phase;
    wh_signed_update_failure_t failure;
    const uint8_t *attempt_id;
    uint64_t origin_boot_id;
    uint64_t accepted_floor;
    uint64_t ledger_readback_sequence;
    uint8_t scenario;
    uint8_t component_id;
    uint16_t target_version;
    uint64_t sequence;
    const char *release_version;
    uint32_t payload_bytes_expected;
    uint32_t payload_bytes_received;
    uint32_t current_task_stack_high_water_bytes;
    uint64_t elapsed_us;
    const char *elapsed_scope;
    const uint8_t *payload_sha256;
    const uint8_t *running_sha256;
    const uint8_t *manifest_sha256;
    uint64_t telemetry_generation;
    const uint8_t *telemetry_sha256;
    uint64_t telemetry_runtime_busy_drops;
    bool health_reconciled;
    uint64_t control_generation;
    uint8_t control_module_version;
    const uint8_t *control_sha256;
} wh_signed_update_event_t;

static void hex_encode(const uint8_t *data, size_t size, char *out)
{
    static const char digits[] = "0123456789abcdef";
    for (size_t i = 0; i < size; ++i) {
        out[i * 2] = digits[data[i] >> 4];
        out[i * 2 + 1] = digits[data[i] & 15];
    }
    out[size * 2] = '\0';
}
static const char *wh_signed_update_phase_name(wh_signed_update_phase_t phase)
{ (void)phase; return "control_activated"; }
static const char *wh_signed_update_failure_name(wh_signed_update_failure_t failure)
{ (void)failure; return "none"; }
static const char *phase_status(wh_signed_update_phase_t phase,
                                wh_signed_update_failure_t failure)
{ (void)phase; (void)failure; return "ok"; }
static uint64_t wh_platform_boot_id(void) { return UINT64_C(0x0123456789abcdef); }
static size_t heap_caps_get_free_size(int caps) { return caps == MALLOC_CAP_INTERNAL ? 1234 : 5678; }
static size_t heap_caps_get_largest_free_block(int caps) { return caps == MALLOC_CAP_INTERNAL ? 1111 : 5555; }
'''


MAIN = r'''
int main(void)
{
    uint8_t attempt[16], payload[32], manifest[32], telemetry[32], control[32];
    for (size_t i = 0; i < sizeof(attempt); ++i) attempt[i] = (uint8_t)i;
    memset(payload, 0x11, sizeof(payload));
    memset(manifest, 0x22, sizeof(manifest));
    memset(telemetry, 0x33, sizeof(telemetry));
    memset(control, 0x44, sizeof(control));
    wh_signed_update_event_t event = {
        .phase = WH_SIGNED_UPDATE_PHASE_CONTROL_ACTIVATED,
        .failure = WH_SIGNED_UPDATE_FAILURE_NONE,
        .attempt_id = attempt,
        .origin_boot_id = UINT64_C(0x1122334455667788),
        .accepted_floor = 2,
        .ledger_readback_sequence = 2,
        .scenario = 3,
        .component_id = 1,
        .target_version = 2,
        .sequence = 2,
        .release_version = "v2",
        .payload_bytes_expected = 1256,
        .payload_bytes_received = 1256,
        .current_task_stack_high_water_bytes = 4096,
        .elapsed_us = 987654,
        .elapsed_scope = "boot",
        .payload_sha256 = payload,
        .manifest_sha256 = manifest,
        .telemetry_generation = 1,
        .telemetry_sha256 = telemetry,
        .telemetry_runtime_busy_drops = 12,
        .control_generation = 3,
        .control_module_version = 2,
        .control_sha256 = control,
    };
    char line[WH_SIGNED_UPDATE_EVENT_LINE_MAX_BYTES];
    size_t line_size = 0;
    assert(wh_signed_update_format_event(&event, line, sizeof(line), &line_size) == ESP_OK);
    assert(line_size > 0 && line[line_size - 1] == '\n');
    (void)fwrite(line, 1, line_size, stderr);
    char too_small[8];
    size_t small_size = 1;
    assert(wh_signed_update_format_event(&event, too_small, sizeof(too_small),
                                         &small_size) == ESP_ERR_INVALID_SIZE);
    assert(small_size == 0);
    small_size = 1;
    assert(wh_signed_update_format_event(&event, too_small, 0,
                                         &small_size) == ESP_ERR_INVALID_ARG);
    assert(small_size == 0);
    wh_signed_update_emit_event(&event);
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


class SignedUpdateEventFormatHostTests(unittest.TestCase):
    def test_deferred_formatter_matches_default_emitter_and_rejects_truncation(self):
        compiler = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
        if compiler is None:
            self.skipTest("A host C compiler is required for the event formatter test")

        source = (ROOT / "main/signed_update.c").read_text(encoding="utf-8")
        renderer = extract_function(source, "static esp_err_t render_signed_update_event(")
        formatter = extract_function(source, "esp_err_t wh_signed_update_format_event(")
        emitter = extract_function(source, "void wh_signed_update_emit_event(")
        harness = "\n".join((PRELUDE, renderer, formatter, emitter, MAIN))
        with tempfile.TemporaryDirectory(prefix="wasmhome-signed-event-format-") as temp_name:
            temp = pathlib.Path(temp_name)
            source_path = temp / "event_harness.c"
            executable = temp / "event_harness"
            source_path.write_text(textwrap.dedent(harness), encoding="utf-8")
            build = subprocess.run(
                [compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-pedantic",
                 str(source_path), "-o", str(executable)],
                capture_output=True, text=True,
            )
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout, result.stderr)


if __name__ == "__main__":
    unittest.main()
