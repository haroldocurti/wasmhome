"""Exercise the real native OTA activation function with focused SDK shims."""

import pathlib
import shutil
import subprocess
import tempfile
import textwrap
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


PRELUDE = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef int esp_err_t;
#define ESP_OK 0
#define ESP_FAIL 1
#define ESP_ERR_INVALID_STATE 2
#define ESP_ERR_INVALID_CRC 3
#define ESP_ERR_IO 4
#define CONFIG_WASMHOME_UPDATE_CAMPAIGN_ID "test-campaign"
#define WH_NATIVE_SCENARIO_NATIVE 1
#define WH_SIGNED_UPDATE_FAILURE_NONE 0
#define WH_SIGNED_UPDATE_FAILURE_PERSISTENCE 7
#define WH_SIGNED_UPDATE_FAILURE_PAYLOAD_HASH 9
#define WH_SIGNED_UPDATE_PHASE_ACTIVATING 8
#define WH_SIGNED_UPDATE_PHASE_UPDATE_FAILED 21
#define WH_SIGNED_UPDATE_PHASE_REBOOT_REQUESTED 15

typedef struct { uint32_t address; } esp_partition_t;
typedef uint32_t esp_ota_handle_t;
typedef struct {
    uint8_t attempt_id[16];
    uint64_t request_boot_id;
    uint64_t request_sequence;
    uint64_t target_sequence;
    uint8_t scenario;
    uint8_t component;
    uint16_t target_version;
} wh_native_ota_request_t;
typedef struct {
    uint8_t scenario;
    char release_version[32];
    uint64_t sequence;
    uint32_t payload_size;
    uint8_t payload_sha256[32];
    uint8_t manifest_sha256[32];
} wh_signed_update_manifest_t;
typedef int wh_signed_update_failure_t;
typedef int wh_signed_update_phase_t;
typedef struct {
    bool pending;
    uint32_t pending_slot;
} wh_signed_update_state_t;
typedef struct {
    const esp_partition_t *partition;
    esp_ota_handle_t handle;
    uint32_t expected_size;
    uint32_t written;
    bool begun;
} native_ota_sink_context_t;
typedef struct wh_signed_update_sink {
    void *context;
    esp_err_t (*begin)(void *, const wh_signed_update_manifest_t *, uint32_t *);
    esp_err_t (*write)(void *, const uint8_t *, size_t);
    esp_err_t (*finish)(void *, const wh_signed_update_manifest_t *, uint32_t *);
    void (*abort)(void *);
} wh_signed_update_sink_t;

static esp_partition_t candidate_partition = { .address = 0x720000 };
static bool pending;
static uint32_t pending_slot;
static esp_err_t mark_activating_result;
static esp_err_t set_boot_result;
static unsigned set_boot_calls;
static unsigned abort_calls;
static unsigned restart_calls;
static unsigned platform_event_calls;
static unsigned emitted_events;
static wh_signed_update_phase_t last_phase;
static wh_signed_update_failure_t last_failure;

static esp_err_t sink_begin(void *, const wh_signed_update_manifest_t *, uint32_t *);
static esp_err_t sink_write(void *, const uint8_t *, size_t);
static esp_err_t sink_finish(void *, const wh_signed_update_manifest_t *, uint32_t *);
static void sink_abort(void *);
static void emit_manifest_event(const wh_native_ota_request_t *,
                                const wh_signed_update_manifest_t *,
                                wh_signed_update_phase_t,
                                wh_signed_update_failure_t);
static esp_err_t wh_signed_update_download(uint8_t, const char *, uint64_t,
                                            const uint8_t[16],
                                            const wh_signed_update_sink_t *,
                                            void *, void *,
                                            wh_signed_update_manifest_t *,
                                            wh_signed_update_failure_t *);
static esp_err_t wh_signed_update_get_state(uint8_t, const char *, const char *,
                                             wh_signed_update_state_t *);
static esp_err_t wh_signed_update_mark_activating(
    const wh_signed_update_manifest_t *, uint32_t);
static esp_err_t wh_signed_update_abort(const wh_signed_update_manifest_t *);
static esp_err_t esp_ota_set_boot_partition(const esp_partition_t *);
static void wh_platform_event(const char *, esp_err_t);
static void esp_restart(void);

static esp_err_t sink_begin(void *opaque,
                            const wh_signed_update_manifest_t *manifest,
                            uint32_t *slot_id)
{
    native_ota_sink_context_t *context = opaque;
    assert(manifest && slot_id);
    context->partition = &candidate_partition;
    context->expected_size = manifest->payload_size;
    *slot_id = candidate_partition.address;
    return ESP_OK;
}
static esp_err_t sink_write(void *opaque, const uint8_t *data, size_t size)
{
    (void)opaque; assert(data && size); return ESP_OK;
}
static esp_err_t sink_finish(void *opaque,
                             const wh_signed_update_manifest_t *manifest,
                             uint32_t *slot_id)
{
    (void)opaque; assert(manifest && slot_id); *slot_id = candidate_partition.address;
    return ESP_OK;
}
static void sink_abort(void *opaque) { (void)opaque; }

static esp_err_t wh_signed_update_download(uint8_t scenario, const char *release,
                                            uint64_t sequence,
                                            const uint8_t attempt_id[16],
                                            const wh_signed_update_sink_t *sink,
                                            void *event_fn, void *event_context,
                                            wh_signed_update_manifest_t *manifest,
                                            wh_signed_update_failure_t *failure)
{
    (void)event_fn; (void)event_context; assert(scenario == WH_NATIVE_SCENARIO_NATIVE);
    assert(strcmp(release, "v2") == 0 && sequence == 2 && attempt_id && sink);
    memset(manifest, 0, sizeof(*manifest));
    manifest->scenario = scenario; strcpy(manifest->release_version, "v2");
    manifest->sequence = 2; manifest->payload_size = 64;
    uint32_t slot = 0;
    assert(sink->begin(sink->context, manifest, &slot) == ESP_OK);
    assert(sink->write(sink->context, (const uint8_t *)"x", 1) == ESP_OK);
    assert(sink->finish(sink->context, manifest, &slot) == ESP_OK);
    pending = true; pending_slot = slot; *failure = WH_SIGNED_UPDATE_FAILURE_NONE;
    return ESP_OK;
}
static esp_err_t wh_signed_update_get_state(uint8_t scenario, const char *component,
                                             const char *campaign,
                                             wh_signed_update_state_t *state)
{
    assert(scenario == WH_NATIVE_SCENARIO_NATIVE);
    assert(strcmp(component, "firmware") == 0 && strcmp(campaign, "test-campaign") == 0);
    state->pending = pending; state->pending_slot = pending_slot; return ESP_OK;
}
static esp_err_t wh_signed_update_mark_activating(
    const wh_signed_update_manifest_t *manifest, uint32_t slot)
{
    assert(manifest && slot == candidate_partition.address);
    return mark_activating_result;
}
static esp_err_t wh_signed_update_abort(const wh_signed_update_manifest_t *manifest)
{
    assert(manifest); ++abort_calls; pending = false; return ESP_OK;
}
static esp_err_t esp_ota_set_boot_partition(const esp_partition_t *partition)
{
    assert(partition == &candidate_partition); ++set_boot_calls; return set_boot_result;
}
static void emit_manifest_event(const wh_native_ota_request_t *request,
                                const wh_signed_update_manifest_t *manifest,
                                wh_signed_update_phase_t phase,
                                wh_signed_update_failure_t failure)
{
    assert(request && manifest); ++emitted_events; last_phase = phase; last_failure = failure;
}
static void wh_platform_event(const char *name, esp_err_t error)
{
    assert(name && error != ESP_OK); ++platform_event_calls;
}
static void esp_restart(void) { ++restart_calls; }

static void reset_state(void)
{
    pending = false; pending_slot = 0; mark_activating_result = ESP_OK;
    set_boot_result = ESP_OK; set_boot_calls = abort_calls = restart_calls = 0;
    platform_event_calls = emitted_events = 0; last_phase = -1; last_failure = -1;
}
'''


MAIN = r'''
int main(void)
{
    wh_native_ota_request_t request = { .target_sequence = 2,
        .scenario = WH_NATIVE_SCENARIO_NATIVE, .target_version = 2 };
    memset(request.attempt_id, 0x5a, sizeof(request.attempt_id));

    /* A set_boot_partition error is ambiguous: pending remains for boot-time
       reconciliation and the function must never clear it here. */
    reset_state(); set_boot_result = ESP_ERR_IO;
    perform_update(&request);
    assert(set_boot_calls == 1 && abort_calls == 0 && pending);
    assert(restart_calls == 1 && platform_event_calls == 1);
    assert(last_phase == WH_SIGNED_UPDATE_PHASE_UPDATE_FAILED);
    assert(last_failure == WH_SIGNED_UPDATE_FAILURE_PERSISTENCE);

    /* Failures before the selection call are safe to abort and must not reboot. */
    reset_state(); mark_activating_result = ESP_ERR_IO;
    perform_update(&request);
    assert(set_boot_calls == 0 && abort_calls == 1 && !pending);
    assert(restart_calls == 0 && last_phase == WH_SIGNED_UPDATE_PHASE_UPDATE_FAILED);

    /* Successful selection preserves pending through the requested reboot. */
    reset_state();
    perform_update(&request);
    assert(set_boot_calls == 1 && abort_calls == 0 && pending);
    assert(restart_calls == 1 && last_phase == WH_SIGNED_UPDATE_PHASE_REBOOT_REQUESTED);
    return 0;
}
'''


def extract_function(source: str, signature: str) -> str:
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"unterminated function: {signature}")


class NativeOtaActivationHostTests(unittest.TestCase):
    def test_real_update_flow_keeps_pending_after_ambiguous_boot_selection_error(self):
        compiler = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
        if compiler is None:
            self.skipTest("A host C compiler is required for the native OTA shim test")

        source = (ROOT / "main/native_ota.c").read_text(encoding="utf-8")
        function = extract_function(source, "static void perform_update(")
        with tempfile.TemporaryDirectory(prefix="wasmhome-native-ota-activation-") as temp_name:
            temp = pathlib.Path(temp_name)
            harness = temp / "activation_harness.c"
            executable = temp / "activation_harness"
            harness.write_text(textwrap.dedent("\n".join((PRELUDE, function, MAIN))), encoding="utf-8")
            build = subprocess.run(
                [compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-pedantic",
                 str(harness), "-o", str(executable)],
                capture_output=True, text=True,
            )
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
