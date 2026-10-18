"""Compile and exercise the complete ESP adapter branch with a tiny SDK shim."""

import pathlib
import shutil
import subprocess
import tempfile
import textwrap
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


STUBS = {
    "sdkconfig.h": r'''
#pragma once
#define CONFIG_WASMHOME_NATIVE_SERVICES 1
#define CONFIG_WASMHOME_CONTROL_VERSION 2
''',
    "esp_err.h": r'''
#pragma once
#include <stdlib.h>
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_ERR_INVALID_ARG 0x101
#define ESP_ERR_INVALID_STATE 0x102
#define ESP_ERR_NOT_SUPPORTED 0x103
#define ESP_ERR_NO_MEM 0x104
#define ESP_ERROR_CHECK(expr) do { if ((expr) != ESP_OK) abort(); } while (0)
''',
    "esp_task_wdt.h": r'''
#pragma once
#include "esp_err.h"
esp_err_t esp_task_wdt_add(void *task);
esp_err_t esp_task_wdt_reset(void);
''',
    "esp_timer.h": r'''
#pragma once
#include <stdint.h>
int64_t esp_timer_get_time(void);
''',
    "freertos/FreeRTOS.h": r'''
#pragma once
#include <stdint.h>
typedef int BaseType_t;
typedef unsigned int UBaseType_t;
typedef uint32_t TickType_t;
#define pdPASS 1
#define portMAX_DELAY UINT32_MAX
#define pdMS_TO_TICKS(ms) ((TickType_t)(ms))
''',
    "freertos/semphr.h": r'''
#pragma once
#include "freertos/FreeRTOS.h"
typedef void *SemaphoreHandle_t;
SemaphoreHandle_t xSemaphoreCreateMutex(void);
BaseType_t xSemaphoreTake(SemaphoreHandle_t semaphore, TickType_t wait);
BaseType_t xSemaphoreGive(SemaphoreHandle_t semaphore);
''',
    "freertos/task.h": r'''
#pragma once
#include "freertos/FreeRTOS.h"
typedef void (*TaskFunction_t)(void *);
typedef void *TaskHandle_t;
TickType_t xTaskGetTickCount(void);
BaseType_t xTaskCreate(TaskFunction_t function, const char *name,
                       uint32_t stack_size, void *argument,
                       UBaseType_t priority, TaskHandle_t *handle);
void vTaskDelayUntil(TickType_t *previous_wake, TickType_t increment);
''',
}


HARNESS = r'''
#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "native_services.h"
#include "observation_wire.h"
#include "platform.h"

#include <assert.h>
#include <string.h>

static int mutex_token;
static wh_request_handler_t installed_handler;
static uint8_t sent_type;
static uint8_t sent_attempt[16];
static uint8_t sent_body[20];
static size_t sent_body_size;

SemaphoreHandle_t xSemaphoreCreateMutex(void) { return &mutex_token; }
BaseType_t xSemaphoreTake(SemaphoreHandle_t semaphore, TickType_t wait)
{
    assert(semaphore == &mutex_token && wait == portMAX_DELAY);
    return 1;
}
BaseType_t xSemaphoreGive(SemaphoreHandle_t semaphore)
{
    assert(semaphore == &mutex_token);
    return 1;
}
TickType_t xTaskGetTickCount(void) { return 0; }
BaseType_t xTaskCreate(TaskFunction_t function, const char *name,
                       uint32_t stack_size, void *argument,
                       UBaseType_t priority, TaskHandle_t *handle)
{
    assert(function && name && stack_size && !argument && priority && !handle);
    return pdPASS; /* Do not run the perpetual telemetry task in this test. */
}
void vTaskDelayUntil(TickType_t *previous_wake, TickType_t increment)
{
    *previous_wake += increment;
}
esp_err_t esp_task_wdt_add(void *task) { (void)task; return ESP_OK; }
esp_err_t esp_task_wdt_reset(void) { return ESP_OK; }
int64_t esp_timer_get_time(void) { return 1000000; }

uint64_t wh_platform_boot_id(void) { return UINT64_C(0x1122334455667788); }
void wh_platform_event(const char *event, esp_err_t status)
{
    (void)event;
    (void)status;
}
wh_sensor_sample_t wh_sensor_latest(void)
{
    return (wh_sensor_sample_t){0};
}
esp_err_t wh_gpio_apply(uint8_t requested, uint8_t *observed)
{
    *observed = requested;
    return ESP_OK;
}
esp_err_t wh_observation_set_handler(wh_request_handler_t handler)
{
    installed_handler = handler;
    return ESP_OK;
}
esp_err_t wh_observation_send(uint8_t type, const uint8_t attempt[16],
                              const uint8_t *body, size_t body_size)
{
    assert(body_size <= sizeof(sent_body));
    sent_type = type;
    memcpy(sent_attempt, attempt, sizeof(sent_attempt));
    memcpy(sent_body, body, body_size);
    sent_body_size = body_size;
    return ESP_OK;
}

static void check_target_version(uint64_t sequence, uint16_t target_version,
                                 uint8_t expected_reason)
{
    uint8_t attempt[16] = {0};
    attempt[0] = (uint8_t)sequence;
    uint8_t body[12] = {0};
    uint8_t request[WH_HEADER_SIZE + sizeof(body)];
    wh_write_be(body, UINT64_C(0x0102030405060708), 8);
    body[8] = WH_NATIVE_SCENARIO_NATIVE;
    body[9] = WH_NATIVE_COMPONENT_CONTROL;
    wh_write_be(body + 10, target_version, 2);
    size_t length = wh_wire_encode(request, sizeof(request), WH_UPDATE_TRIGGER,
                                   attempt, sequence,
                                   UINT64_C(0x1122334455667788), 42,
                                   body, sizeof(body));
    assert(length == sizeof(request));
    sent_type = 0;
    sent_body_size = 0;
    installed_handler(request, length);
    assert(sent_type == WH_UPDATE_ACCEPTANCE);
    assert(sent_body_size == 20);
    assert(memcmp(sent_attempt, attempt, sizeof(attempt)) == 0);
    assert(wh_read_be(sent_body, 8) == sequence);
    assert(wh_read_be(sent_body + 8, 8) == UINT64_C(0x0102030405060708));
    assert(sent_body[16] == WH_NATIVE_ACCEPTANCE_REJECTED);
    assert(sent_body[17] == expected_reason);
}

int main(void)
{
    assert(wh_native_services_start() == ESP_OK);
    assert(installed_handler != NULL);
    /* 0x0102 proves the adapter consumes the complete uint16 field. */
    check_target_version(10, 2, WH_NATIVE_REASON_BUSY);
    check_target_version(11, 0x0102, WH_NATIVE_REASON_UNSUPPORTED_VERSION);
    return 0;
}
'''


OTA_HEALTH_HARNESS = r'''
static unsigned health_checks;
esp_err_t wh_native_ota_start(void) { return ESP_OK; }
esp_err_t wh_native_ota_queue_health_check(void) { ++health_checks; return ESP_OK; }
esp_err_t wh_native_ota_reserve(const wh_native_ota_request_t *request)
{ (void)request; return ESP_ERR_INVALID_STATE; }
esp_err_t wh_native_ota_start_reserved(void) { return ESP_ERR_INVALID_STATE; }
void wh_native_ota_cancel_reserved(void) {}
esp_err_t wh_signed_update_get_state(uint8_t scenario, const char *component,
    const char *campaign, wh_signed_update_state_t *state)
{ (void)scenario; (void)component; (void)campaign; memset(state, 0, sizeof(*state)); return ESP_OK; }
void wh_signed_update_emit_event(const wh_signed_update_event_t *event) { (void)event; }

static void control(uint64_t sample_id, int32_t temperature)
{
    uint8_t attempt[16] = {1}, body[12] = {0};
    uint8_t request[WH_HEADER_SIZE + sizeof(body)];
    wh_write_be(body, sample_id, 8);
    wh_write_be(body + 8, (uint32_t)temperature, 4);
    size_t length = wh_wire_encode(request, sizeof(request), WH_CONTROL_REQUEST,
        attempt, sample_id, wh_platform_boot_id(), 42, body, sizeof(body));
    assert(length == sizeof(request));
    installed_handler(request, length);
    assert(sent_type == WH_CONTROL_RESULT);
}

int main(void)
{
    assert(wh_native_services_start() == ESP_OK);
    control(1, 26000); /* Correct control alone does not qualify as health stimulus. */
    assert(health_checks == 0);
    control(2, 27000);
    assert(health_checks == 1);
    control(2, 27000); /* A replay cannot queue a second health check. */
    assert(health_checks == 1);
    return 0;
}
'''


class NativeServicesAdapterCompileTests(unittest.TestCase):
    def test_complete_esp_adapter_compiles_and_decodes_target_version(self):
        self._compile_adapter(STUBS, HARNESS)

    def test_native_health_commit_follows_each_build_policy(self):
        for version in (1, 2):
            with self.subTest(control_version=version):
                stubs = dict(STUBS)
                stubs["sdkconfig.h"] = (STUBS["sdkconfig.h"].replace(
                    "CONTROL_VERSION 2", f"CONTROL_VERSION {version}") +
                    '\n#define CONFIG_WASMHOME_NATIVE_OTA 1\n'
                    '#define CONFIG_WASMHOME_UPDATE_CAMPAIGN_ID "health-test"\n')
                harness = HARNESS.replace('#include "platform.h"',
                    '#include "platform.h"\n#include "native_ota.h"\n#include "signed_update.h"')
                harness = harness.replace('return (wh_sensor_sample_t){0};',
                    'return (wh_sensor_sample_t){.sample_id=1, .acquired_us=900000, '
                    '.status=WH_SENSOR_VALID};')
                start = harness.index('static void check_target_version(')
                harness = harness[:start] + OTA_HEALTH_HARNESS
                self._compile_adapter(stubs, harness)

    def _compile_adapter(self, stubs, harness_source):
        compiler = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
        if compiler is None:
            self.skipTest("A host C compiler is required for the ESP adapter shim test")

        with tempfile.TemporaryDirectory(prefix="wasmhome-native-adapter-") as temp_name:
            temp = pathlib.Path(temp_name)
            stub_root = temp / "stubs"
            stub_root.mkdir()
            for relative, contents in stubs.items():
                target = stub_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(textwrap.dedent(contents).lstrip(), encoding="utf-8")
            harness = temp / "adapter_harness.c"
            executable = temp / "adapter_harness"
            harness.write_text(harness_source, encoding="utf-8")
            command = [
                compiler, "-std=c11", "-D_POSIX_C_SOURCE=200809L",
                "-DESP_PLATFORM=1", "-DWASMHOME_SCENARIO_NATIVE=1",
                "-Wall", "-Wextra", "-Werror", "-pedantic",
                f"-I{stub_root}", f"-I{ROOT / 'main'}", str(harness),
                str(ROOT / "main/native_services.c"),
                str(ROOT / "main/observation_wire.c"), "-o", str(executable),
            ]
            build = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
