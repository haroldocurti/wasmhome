"""Exercise the real native ENV fixture preflight with a fake NVS ledger."""

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
#include <string.h>

typedef int esp_err_t;
#define ESP_OK 0
#define ESP_FAIL 1
#define ESP_ERR_INVALID_ARG 2
#define ESP_ERR_INVALID_STATE 3
#define ESP_ERR_INVALID_VERSION 4
#define CONFIG_WASMHOME_RESILIENCE_TEST_MODE 1
#define CONFIG_WASMHOME_UPDATE_CAMPAIGN_ID "preflight-test"
#define WH_NATIVE_SCENARIO_NATIVE 1
#define portMAX_DELAY 0xffffffffu
#define pdTRUE 1

typedef int SemaphoreHandle_t;
typedef int TaskHandle_t;
typedef struct {
    bool pending;
    uint64_t accepted_floor;
} wh_signed_update_state_t;

static SemaphoreHandle_t ota_state_lock = 1;
static TaskHandle_t ota_worker_task = 1;
static bool ota_busy;
static bool ota_reserved;
static esp_err_t ledger_read_result = ESP_OK;
static wh_signed_update_state_t ledger_state;

static int xSemaphoreTake(SemaphoreHandle_t semaphore, unsigned wait)
{
    (void)semaphore; (void)wait; return pdTRUE;
}
static int xSemaphoreGive(SemaphoreHandle_t semaphore)
{
    (void)semaphore; return pdTRUE;
}
static esp_err_t wh_signed_update_get_state(uint8_t scenario,
    const char *component, const char *campaign,
    wh_signed_update_state_t *state)
{
    assert(scenario == WH_NATIVE_SCENARIO_NATIVE);
    assert(strcmp(component, "firmware") == 0);
    assert(strcmp(campaign, "preflight-test") == 0);
    *state = ledger_state;
    return ledger_read_result;
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


MAIN = r'''
int main(void)
{
    ledger_state = (wh_signed_update_state_t){.accepted_floor = 0};
    assert(wh_native_ota_resilience_preflight("ENV-OLD") ==
           ESP_ERR_INVALID_VERSION);
    ledger_state.accepted_floor = 3;
    ledger_state.pending = true;
    assert(wh_native_ota_resilience_preflight("ENV-OLD") ==
           ESP_ERR_INVALID_STATE);
    ledger_state.pending = false;
    assert(wh_native_ota_resilience_preflight("ENV-OLD") == ESP_OK);
    assert(wh_native_ota_resilience_preflight("ENV-HASH") == ESP_OK);
    assert(wh_native_ota_resilience_preflight("NAT-ABORT") == ESP_ERR_INVALID_ARG);
    ota_busy = true;
    assert(wh_native_ota_resilience_preflight("ENV-SIG") == ESP_ERR_INVALID_STATE);
    return 0;
}
'''


class NativeResiliencePreflightTests(unittest.TestCase):
    def test_real_preflight_rejects_zero_floor_and_pending_ledger(self):
        compiler = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
        if compiler is None:
            self.skipTest("A host C compiler is required for the preflight shim test")
        source = (ROOT / "main/native_ota.c").read_text(encoding="utf-8")
        function = extract_function(source,
                                    "esp_err_t wh_native_ota_resilience_preflight(")
        harness = "\n".join((PRELUDE, function, MAIN))
        with tempfile.TemporaryDirectory(prefix="wasmhome-native-preflight-") as temp_name:
            temp = pathlib.Path(temp_name)
            source_path = temp / "preflight.c"
            executable = temp / "preflight"
            source_path.write_text(textwrap.dedent(harness), encoding="utf-8")
            build = subprocess.run(
                [compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-pedantic",
                 str(source_path), "-o", str(executable)],
                capture_output=True, text=True,
            )
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_completed_native_attempt_id_is_not_reused_for_later_telemetry(self):
        compiler = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
        if compiler is None:
            self.skipTest("A host C compiler is required for the retirement shim test")
        source = (ROOT / "main/resilience_test.c").read_text(encoding="utf-8")
        function = extract_function(source, "void wh_resilience_test_telemetry_attempt(")
        prelude = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#define pdTRUE 1
#define portMAX_DELAY 0xffffffffu
#define RESILIENCE_RECOVERY_TELEMETRY 0x01u
#define RESILIENCE_RECOVERY_CONTROL 0x02u
typedef int SemaphoreHandle_t;
typedef struct { uint8_t recovery_mask; uint8_t attempt_id[16]; }
    resilience_pending_t;
static SemaphoreHandle_t state_lock = 1;
static bool pending_valid = true;
static resilience_pending_t pending;
static int xSemaphoreTake(SemaphoreHandle_t lock, unsigned wait)
{ (void)lock; (void)wait; return pdTRUE; }
static int xSemaphoreGive(SemaphoreHandle_t lock)
{ (void)lock; return pdTRUE; }
'''
        main = r'''
int main(void)
{
    uint8_t output[16];
    for (size_t i = 0; i < sizeof(pending.attempt_id); ++i)
        pending.attempt_id[i] = (uint8_t)(i + 1);
    pending.recovery_mask = RESILIENCE_RECOVERY_TELEMETRY;
    wh_resilience_test_telemetry_attempt(output);
    assert(memcmp(output, pending.attempt_id, sizeof(output)) == 0);
    pending.recovery_mask = RESILIENCE_RECOVERY_TELEMETRY |
                            RESILIENCE_RECOVERY_CONTROL;
    wh_resilience_test_telemetry_attempt(output);
    const uint8_t zero[16] = {0};
    assert(memcmp(output, zero, sizeof(output)) == 0);
    return 0;
}
'''
        harness = "\n".join((prelude, function, main))
        with tempfile.TemporaryDirectory(prefix="wasmhome-native-attempt-retire-") as temp_name:
            temp = pathlib.Path(temp_name)
            source_path = temp / "retire.c"
            executable = temp / "retire"
            source_path.write_text(textwrap.dedent(harness), encoding="utf-8")
            build = subprocess.run(
                [compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-pedantic",
                 str(source_path), "-o", str(executable)],
                capture_output=True, text=True,
            )
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_preflight_runs_before_acceptance_is_persisted_or_reported(self):
        source = (ROOT / "main/resilience_test.c").read_text(encoding="utf-8")
        function = extract_function(source, "static void handle_fault_request(")
        preflight = function.index("wh_native_ota_resilience_preflight")
        persist_acceptance = function.index("err = persist_pending(&next)")
        emit_acceptance = function.index('emit_event("fault_accepted"')
        self.assertLess(preflight, persist_acceptance)
        self.assertLess(persist_acceptance, emit_acceptance)

    def test_native_fixture_and_panic_are_gated_on_flushed_acceptance_ack(self):
        source = (ROOT / "main/resilience_test.c").read_text(encoding="utf-8")
        handler = extract_function(source, "static void handle_fault_request(")
        emitted_ack = handler.index('if (!emit_event("fault_accepted"')
        persisted_ack_state = handler.index("acknowledge_pending_attempt")
        fixture_dispatch = handler.index("wh_native_ota_run_resilience_fixture")
        self.assertLess(emitted_ack, persisted_ack_state)
        self.assertLess(persisted_ack_state, fixture_dispatch)
        self.assertIn("disarm_unacknowledged_attempt", handler[emitted_ack:persisted_ack_state])

        abort_handler = extract_function(
            source, "void wh_resilience_test_note_control_request(")
        abort_guard = abort_handler.index("pending.accepted_acknowledged")
        abort_call = abort_handler.index("esp_system_abort")
        self.assertLess(abort_guard, abort_call)
        self.assertIn("return written > 0 && fflush(stdout) == 0", source)


if __name__ == "__main__":
    unittest.main()
