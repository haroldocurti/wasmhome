#include "native_services.h"

#ifndef WASMHOME_NATIVE_SERVICES_HOST
#include "sdkconfig.h"
#endif

#if defined(CONFIG_WASMHOME_NATIVE_OTA) && !defined(WASMHOME_NATIVE_SERVICES_HOST)
#include "native_ota.h"
#include "signed_update.h"
#endif
#if defined(CONFIG_WASMHOME_RESILIENCE_TEST_MODE) && !defined(WASMHOME_NATIVE_SERVICES_HOST)
#include "resilience_test.h"
#endif

#include <limits.h>
#include <string.h>

static bool bytes_nonzero(const uint8_t *bytes, size_t size)
{
    uint8_t any = 0;
    for (size_t i = 0; i < size; ++i) any |= bytes[i];
    return any != 0;
}

bool wh_native_services_state_init(wh_native_services_state_t *state,
                                   uint8_t module_version)
{
    if (!state || (module_version != 1 && module_version != 2)) return false;
    memset(state, 0, sizeof(*state));
    state->module_version = module_version;
    /* GPIO is configured low by wh_platform_start before the service starts. */
    state->requested_gpio = 0;
    return true;
}

static void copy_control_result(const wh_native_control_record_t *record,
                                uint8_t module_version,
                                wh_native_control_result_t *result)
{
    result->module_version = module_version;
    result->requested_gpio = record->requested_gpio;
    result->observed_gpio = record->observed_gpio;
    result->status = record->status;
}

wh_native_control_disposition_t wh_native_control_request(
    wh_native_services_state_t *state, uint64_t sample_id,
    int32_t input_temperature_mC, wh_native_gpio_apply_fn apply_gpio,
    void *gpio_context, wh_native_control_result_t *result)
{
    /* Match CONTROL_REQUEST's documented DS18B20 stimulus domain. */
    if (!state || !result || !apply_gpio ||
        (state->module_version != 1 && state->module_version != 2) ||
        state->requested_gpio > 1 || !sample_id ||
        input_temperature_mC < -55000 || input_temperature_mC > 125000)
        return WH_NATIVE_CONTROL_INVALID;

    for (size_t i = 0; i < WH_NATIVE_CONTROL_REPLAY_CAPACITY; ++i) {
        const wh_native_control_record_t *record = &state->control_records[i];
        if (!record->valid || record->sample_id != sample_id) continue;
        if (record->input_temperature_mC != input_temperature_mC)
            return WH_NATIVE_CONTROL_CONFLICT;
        copy_control_result(record, state->module_version, result);
        return WH_NATIVE_CONTROL_REPLAY;
    }

    /* Never re-execute an old ID after its cached result ages out. */
    if (sample_id <= state->highest_sample_id) return WH_NATIVE_CONTROL_STALE;

    const int32_t threshold_mC = state->module_version == 1 ? 25000 : 30000;
    if (input_temperature_mC >= threshold_mC + 500) state->requested_gpio = 1;
    else if (input_temperature_mC <= threshold_mC - 500) state->requested_gpio = 0;

    uint8_t observed = 255;
    bool applied = apply_gpio(gpio_context, state->requested_gpio, &observed);
    uint8_t status = WH_NATIVE_CONTROL_EXECUTION_ERROR;
    if (applied && observed <= 1)
        status = observed == state->requested_gpio ? WH_NATIVE_CONTROL_APPLIED : WH_NATIVE_CONTROL_MISMATCH;
    else
        observed = 255;

    wh_native_control_record_t *record =
        &state->control_records[state->next_control_record];
    *record = (wh_native_control_record_t){
        .sample_id = sample_id,
        .input_temperature_mC = input_temperature_mC,
        .requested_gpio = state->requested_gpio,
        .observed_gpio = observed,
        .status = status,
        .valid = true,
    };
    state->next_control_record =
        (state->next_control_record + 1) % WH_NATIVE_CONTROL_REPLAY_CAPACITY;
    state->highest_sample_id = sample_id;
    *result = (wh_native_control_result_t){
        .module_version = state->module_version,
        .requested_gpio = state->requested_gpio,
        .observed_gpio = observed,
        .status = status,
    };
    return WH_NATIVE_CONTROL_NEW;
}

static bool same_update_request(const wh_native_update_record_t *record,
                                const uint8_t attempt_id[16],
                                uint64_t request_boot_id,
                                uint64_t request_sequence,
                                uint64_t target_sequence, uint8_t scenario,
                                uint8_t component, uint16_t target_version)
{
    return record->request_boot_id == request_boot_id &&
        record->request_sequence == request_sequence &&
        record->target_sequence == target_sequence &&
        record->scenario == scenario && record->component == component &&
        record->target_version == target_version &&
        memcmp(record->attempt_id, attempt_id, 16) == 0;
}

wh_native_update_disposition_t wh_native_update_reject(
    wh_native_services_state_t *state, const uint8_t attempt_id[16],
    uint64_t request_boot_id, uint64_t request_sequence,
    uint64_t target_sequence, uint8_t scenario, uint8_t component,
    uint16_t target_version, uint64_t current_boot_id, uint8_t *reason)
{
    if (!state || !attempt_id || !reason || !bytes_nonzero(attempt_id, 16) ||
        !request_boot_id || !request_sequence || !target_sequence || !current_boot_id)
        return WH_NATIVE_UPDATE_INVALID;

    for (size_t i = 0; i < WH_NATIVE_UPDATE_REPLAY_CAPACITY; ++i) {
        const wh_native_update_record_t *record = &state->update_records[i];
        if (!record->valid || memcmp(record->attempt_id, attempt_id, 16) != 0) continue;
        if (!same_update_request(record, attempt_id, request_boot_id, request_sequence,
                                 target_sequence, scenario, component, target_version)) {
            *reason = WH_NATIVE_REASON_ATTEMPT_CONFLICT;
            return WH_NATIVE_UPDATE_ATTEMPT_CONFLICT;
        }
        *reason = record->reason;
        return WH_NATIVE_UPDATE_REPLAY_REJECTION;
    }

    /* Keep every admitted UUID addressable for the complete device boot. */
    if (state->update_record_count >= WH_NATIVE_UPDATE_REPLAY_CAPACITY) {
        *reason = WH_NATIVE_REASON_NONE;
        return WH_NATIVE_UPDATE_CAPACITY_FULL;
    }
    uint8_t rejection = WH_NATIVE_REASON_BUSY; /* This build has no update/download path. */
    if (request_boot_id != current_boot_id) rejection = WH_NATIVE_REASON_WRONG_BOOT;
    else if (scenario != WH_NATIVE_SCENARIO_NATIVE) rejection = WH_NATIVE_REASON_SCENARIO_MISMATCH;
    else if (component != WH_NATIVE_COMPONENT_CONTROL) rejection = WH_NATIVE_REASON_UNSUPPORTED_COMPONENT;
    else if (target_version != 1 && target_version != 2) rejection = WH_NATIVE_REASON_UNSUPPORTED_VERSION;

    wh_native_update_record_t *record = &state->update_records[state->update_record_count];
    *record = (wh_native_update_record_t){
        .valid = true,
        .request_boot_id = request_boot_id,
        .request_sequence = request_sequence,
        .target_sequence = target_sequence,
        .scenario = scenario,
        .component = component,
        .target_version = target_version,
        .reason = rejection,
    };
    memcpy(record->attempt_id, attempt_id, 16);
    state->update_record_count++;
    *reason = rejection;
    return WH_NATIVE_UPDATE_NEW_REJECTION;
}

wh_native_update_disposition_t wh_native_update_admit(
    wh_native_services_state_t *state, const uint8_t attempt_id[16],
    uint64_t request_boot_id, uint64_t request_sequence,
    uint64_t target_sequence, uint8_t scenario, uint8_t component,
    uint16_t target_version, uint64_t current_boot_id,
    uint64_t accepted_floor, bool updater_available, uint8_t *reason)
{
    if (!state || !attempt_id || !reason || !bytes_nonzero(attempt_id, 16) ||
        !request_boot_id || !request_sequence || !target_sequence || !current_boot_id)
        return WH_NATIVE_UPDATE_INVALID;

    for (size_t i = 0; i < state->update_record_count; ++i) {
        wh_native_update_record_t *record = &state->update_records[i];
        if (memcmp(record->attempt_id, attempt_id, 16) != 0) continue;
        if (!same_update_request(record, attempt_id, request_boot_id, request_sequence,
                                 target_sequence, scenario, component, target_version)) {
            *reason = WH_NATIVE_REASON_ATTEMPT_CONFLICT;
            return WH_NATIVE_UPDATE_ATTEMPT_CONFLICT;
        }
        *reason = record->reason;
        return record->accepted ? WH_NATIVE_UPDATE_REPLAY_ACCEPTANCE
                                : WH_NATIVE_UPDATE_REPLAY_REJECTION;
    }

    if (state->update_record_count >= WH_NATIVE_UPDATE_REPLAY_CAPACITY) {
        *reason = WH_NATIVE_REASON_BUSY;
        return WH_NATIVE_UPDATE_CAPACITY_FULL;
    }

    uint8_t rejection = WH_NATIVE_REASON_NONE;
    if (request_boot_id != current_boot_id) rejection = WH_NATIVE_REASON_WRONG_BOOT;
    else if (scenario != WH_NATIVE_SCENARIO_NATIVE) rejection = WH_NATIVE_REASON_SCENARIO_MISMATCH;
    else if (component != WH_NATIVE_COMPONENT_CONTROL) rejection = WH_NATIVE_REASON_UNSUPPORTED_COMPONENT;
    else if (target_version != 2) rejection = WH_NATIVE_REASON_UNSUPPORTED_VERSION;
    else if (target_sequence <= accepted_floor) rejection = WH_NATIVE_REASON_SEQUENCE_NOT_NEWER;
    else if (!updater_available) rejection = WH_NATIVE_REASON_BUSY;

    wh_native_update_record_t *record = &state->update_records[state->update_record_count++];
    *record = (wh_native_update_record_t){
        .valid = true,
        .accepted = rejection == WH_NATIVE_REASON_NONE,
        .request_boot_id = request_boot_id,
        .request_sequence = request_sequence,
        .target_sequence = target_sequence,
        .scenario = scenario,
        .component = component,
        .target_version = target_version,
        .reason = rejection,
    };
    memcpy(record->attempt_id, attempt_id, 16);
    *reason = rejection;
    return record->accepted ? WH_NATIVE_UPDATE_NEW_ACCEPTANCE
                            : WH_NATIVE_UPDATE_NEW_REJECTION;
}

bool wh_native_update_mark_start_failed(wh_native_services_state_t *state,
                                        const uint8_t attempt_id[16])
{
    if (!state || !attempt_id) return false;
    for (size_t i = 0; i < state->update_record_count; ++i) {
        wh_native_update_record_t *record = &state->update_records[i];
        if (record->valid && record->accepted &&
            memcmp(record->attempt_id, attempt_id, 16) == 0) {
            record->accepted = false;
            record->reason = WH_NATIVE_REASON_BUSY;
            return true;
        }
    }
    return false;
}

bool wh_native_telemetry_body(uint8_t body[16], uint64_t sample_id,
                              int32_t temperature_mC, uint64_t acquired_us,
                              uint8_t sensor_status, uint64_t now_us)
{
    if (!body || sensor_status > WH_NATIVE_SENSOR_NO_VALID_SAMPLE) return false;

    uint16_t age_ms;
    if (!sample_id) {
        if (sensor_status == WH_NATIVE_SENSOR_VALID) return false;
        temperature_mC = INT32_MIN;
        age_ms = UINT16_MAX;
    } else {
        if (temperature_mC < -55000 || temperature_mC > 125000 ||
            sensor_status == WH_NATIVE_SENSOR_NO_VALID_SAMPLE || now_us < acquired_us)
            return false;
        uint64_t age = (now_us - acquired_us) / 1000;
        age_ms = age > 65534 ? 65534 : (uint16_t)age;
    }

    for (size_t i = 0; i < 8; ++i) body[7 - i] = (uint8_t)(sample_id >> (i * 8));
    uint32_t wire_temperature = (uint32_t)temperature_mC;
    for (size_t i = 0; i < 4; ++i) body[11 - i] = (uint8_t)(wire_temperature >> (i * 8));
    body[12] = (uint8_t)(age_ms >> 8);
    body[13] = (uint8_t)age_ms;
    body[14] = sensor_status;
    body[15] = 0;
    return true;
}

#if defined(CONFIG_WASMHOME_NATIVE_SERVICES) && !defined(WASMHOME_NATIVE_SERVICES_HOST)
#if !defined(WASMHOME_SCENARIO_NATIVE)
#error "Native functional services are only supported in the native scenario"
#endif
#if defined(CONFIG_WASMHOME_PLATFORM_SELFTEST) || defined(CONFIG_WASMHOME_OBSERVER_SELFTEST) || defined(CONFIG_WASMHOME_RUN_BENCHMARK)
#error "Native functional services cannot be combined with diagnostics or benchmark"
#endif

#include "platform.h"
#include "observation_wire.h"
#include "esp_task_wdt.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include <limits.h>

_Static_assert((int)WH_SENSOR_VALID == (int)WH_NATIVE_SENSOR_VALID, "sensor wire state drift");
_Static_assert((int)WH_SENSOR_CRC_ERROR == (int)WH_NATIVE_SENSOR_CRC_ERROR, "sensor wire state drift");
_Static_assert((int)WH_SENSOR_DISCONNECTED == (int)WH_NATIVE_SENSOR_DISCONNECTED, "sensor wire state drift");
_Static_assert((int)WH_SENSOR_NOT_READY == (int)WH_NATIVE_SENSOR_NOT_READY, "sensor wire state drift");
_Static_assert((int)WH_SENSOR_NO_VALID_SAMPLE == (int)WH_NATIVE_SENSOR_NO_VALID_SAMPLE, "sensor wire state drift");

static wh_native_services_state_t service_state;
static SemaphoreHandle_t service_lock;

static int32_t signed_be32(const uint8_t *bytes)
{
    uint32_t value = (uint32_t)wh_read_be(bytes, 4);
    return value <= INT32_MAX ? (int32_t)value : (int32_t)((int64_t)value - 0x100000000LL);
}

static bool apply_gpio(void *context, uint8_t requested, uint8_t *observed)
{
    (void)context;
    *observed = 255;
    return wh_gpio_apply(requested, observed) == ESP_OK;
}

static esp_err_t send_control_result(const uint8_t *request, uint64_t sample_id,
                                     const wh_native_control_result_t *result)
{
    uint8_t body[12] = {0};
    wh_write_be(body, sample_id, 8);
    body[8] = result->module_version;
    body[9] = result->requested_gpio;
    body[10] = result->observed_gpio;
    body[11] = result->status;
    esp_err_t err = wh_observation_send(WH_CONTROL_RESULT, request + 8, body, sizeof(body));
#if defined(CONFIG_WASMHOME_RESILIENCE_TEST_MODE) && !defined(WASMHOME_NATIVE_SERVICES_HOST)
    if (err == ESP_OK) {
        const bool correct = result->status == WH_NATIVE_CONTROL_APPLIED &&
            result->requested_gpio <= 1 &&
            result->requested_gpio == result->observed_gpio;
        wh_resilience_test_note_control_request(request + 8, correct);
    }
#endif
    if (err != ESP_OK) wh_platform_event("native_control_result_send_failed", err);
    return err;
}

static esp_err_t send_update_response(const uint8_t *request,
                                      uint64_t request_sequence,
                                      uint64_t target_sequence, bool accepted,
                                      uint8_t reason)
{
    uint8_t body[20] = {0};
    wh_write_be(body, request_sequence, 8);
    wh_write_be(body + 8, target_sequence, 8);
    body[16] = accepted ? WH_NATIVE_ACCEPTANCE_ACCEPTED : WH_NATIVE_ACCEPTANCE_REJECTED;
    body[17] = accepted ? WH_NATIVE_REASON_NONE : reason;
    esp_err_t err = wh_observation_send(WH_UPDATE_ACCEPTANCE, request + 8,
                                        body, sizeof(body));
    if (err != ESP_OK)
        wh_platform_event(accepted ? "native_update_acceptance_send_failed"
                                  : "native_update_rejection_send_failed", err);
    return err;
}

#if defined(CONFIG_WASMHOME_NATIVE_OTA) && !defined(WASMHOME_NATIVE_SERVICES_HOST)
static void emit_update_acceptance(const uint8_t *request, uint64_t request_boot_id,
                                   uint8_t scenario, uint8_t component,
                                   uint16_t target_version, uint64_t target_sequence)
{
    wh_signed_update_state_t state = {0};
    (void)wh_signed_update_get_state(scenario, "firmware",
        CONFIG_WASMHOME_UPDATE_CAMPAIGN_ID, &state);
    wh_signed_update_event_t event = {
        .phase = WH_SIGNED_UPDATE_PHASE_ACCEPTED,
        .failure = WH_SIGNED_UPDATE_FAILURE_NONE,
        .attempt_id = request + 8,
        .origin_boot_id = request_boot_id,
        .accepted_floor = state.accepted_floor,
        .ledger_readback_sequence = state.active_sequence,
        .scenario = scenario,
        .component_id = component,
        .target_version = target_version,
        .sequence = target_sequence,
        .release_version = "v2",
        .elapsed_us = (uint64_t)esp_timer_get_time(),
        .elapsed_scope = "boot",
    };
    wh_signed_update_emit_event(&event);
}

static void emit_update_start_failure(const uint8_t *request,
                                      uint64_t request_boot_id,
                                      uint64_t target_sequence)
{
    wh_signed_update_event_t event = {
        .phase = WH_SIGNED_UPDATE_PHASE_UPDATE_FAILED,
        .failure = WH_SIGNED_UPDATE_FAILURE_PERSISTENCE,
        .attempt_id = request + 8,
        .origin_boot_id = request_boot_id,
        .scenario = WH_NATIVE_SCENARIO_NATIVE,
        .component_id = WH_NATIVE_COMPONENT_CONTROL,
        .target_version = 2,
        .sequence = target_sequence,
        .release_version = "v2",
        .elapsed_us = (uint64_t)esp_timer_get_time(),
        .elapsed_scope = "boot",
    };
    wh_signed_update_emit_event(&event);
}
#endif

static void receive_request(const uint8_t *data, size_t length)
{
    if (!data || !wh_wire_valid(data, length)) {
        wh_platform_event("native_request_invalid", ESP_ERR_INVALID_ARG);
        return;
    }

    if (data[5] == WH_CONTROL_REQUEST) {
        uint64_t sample_id = wh_read_be(data + WH_HEADER_SIZE, 8);
        int32_t input_temperature_mC = signed_be32(data + WH_HEADER_SIZE + 8);
#if defined(CONFIG_WASMHOME_NATIVE_OTA)
        const wh_sensor_sample_t sensor = wh_sensor_latest();
#endif
        wh_native_control_result_t result;
        xSemaphoreTake(service_lock, portMAX_DELAY);
        wh_native_control_disposition_t disposition = wh_native_control_request(
            &service_state, sample_id, input_temperature_mC,
            apply_gpio, NULL, &result);
        xSemaphoreGive(service_lock);

        if (disposition == WH_NATIVE_CONTROL_NEW || disposition == WH_NATIVE_CONTROL_REPLAY) {
            esp_err_t send_err = send_control_result(data, sample_id, &result);
#if defined(CONFIG_WASMHOME_NATIVE_OTA)
            if (send_err == ESP_OK && disposition == WH_NATIVE_CONTROL_NEW &&
                sensor.status == WH_SENSOR_VALID && sensor.sample_id != 0 &&
                sensor.acquired_us >= 0 &&
                (uint64_t)esp_timer_get_time() >= (uint64_t)sensor.acquired_us &&
                (uint64_t)esp_timer_get_time() - (uint64_t)sensor.acquired_us <= 2000000 &&
                /* 27000 mC separates the policies: v1 (25000 mC) drives GPIO 1,
                   v2 (30000 mC) drives GPIO 0. Commit only after this build's
                   own policy answers correctly; #22 keeps policy 1 in its
                   native v2 release, while #20's native v2 uses policy 2. */
                input_temperature_mC == 27000 &&
                result.module_version == CONFIG_WASMHOME_CONTROL_VERSION &&
                result.requested_gpio == (CONFIG_WASMHOME_CONTROL_VERSION == 1 ? 1 : 0) &&
                result.observed_gpio == result.requested_gpio &&
                result.status == WH_NATIVE_CONTROL_APPLIED) {
                esp_err_t health_err = wh_native_ota_queue_health_check();
                if (health_err != ESP_OK && health_err != ESP_ERR_INVALID_STATE)
                    wh_platform_event("native_ota_health_queue_failed", health_err);
            }
#else
            (void)send_err;
#endif
        } else if (disposition == WH_NATIVE_CONTROL_CONFLICT) {
            wh_platform_event("control_id_conflict", ESP_ERR_INVALID_ARG);
        } else if (disposition == WH_NATIVE_CONTROL_STALE) {
            wh_platform_event("control_id_stale", ESP_ERR_INVALID_STATE);
        } else {
            wh_platform_event("native_control_invalid", ESP_ERR_INVALID_ARG);
        }
        return;
    }

    if (data[5] == WH_UPDATE_TRIGGER) {
        uint64_t request_sequence = wh_read_be(data + 24, 8);
        uint64_t request_boot_id = wh_read_be(data + 32, 8);
        uint64_t target_sequence = wh_read_be(data + WH_HEADER_SIZE, 8);
        uint8_t scenario = data[WH_HEADER_SIZE + 8];
        uint8_t component = data[WH_HEADER_SIZE + 9];
        uint16_t target_version = (uint16_t)wh_read_be(data + WH_HEADER_SIZE + 10, 2);
        uint8_t reason = WH_NATIVE_REASON_BUSY;
#if defined(CONFIG_WASMHOME_NATIVE_OTA)
        wh_native_ota_request_t ota_request = {0};
#endif
        xSemaphoreTake(service_lock, portMAX_DELAY);
#if defined(CONFIG_WASMHOME_NATIVE_OTA)
        wh_signed_update_state_t update_state = {0};
        esp_err_t state_err = wh_signed_update_get_state(WH_NATIVE_SCENARIO_NATIVE,
            "firmware", CONFIG_WASMHOME_UPDATE_CAMPAIGN_ID, &update_state);
        bool updater_available = state_err == ESP_OK && !update_state.pending;
        wh_native_update_disposition_t disposition = wh_native_update_admit(
            &service_state, data + 8, request_boot_id, request_sequence,
            target_sequence, scenario, component, target_version,
            wh_platform_boot_id(), update_state.accepted_floor,
            updater_available, &reason);
        if (disposition == WH_NATIVE_UPDATE_NEW_ACCEPTANCE) {
            memcpy(ota_request.attempt_id, data + 8, 16);
            ota_request.request_boot_id = request_boot_id;
            ota_request.request_sequence = request_sequence;
            ota_request.target_sequence = target_sequence;
            ota_request.scenario = scenario;
            ota_request.component = component;
            ota_request.target_version = target_version;
            esp_err_t reserve_err = wh_native_ota_reserve(&ota_request);
            if (reserve_err != ESP_OK) {
                wh_native_update_record_t *record =
                    &service_state.update_records[service_state.update_record_count - 1];
                record->accepted = false;
                record->reason = WH_NATIVE_REASON_BUSY;
                reason = WH_NATIVE_REASON_BUSY;
                disposition = WH_NATIVE_UPDATE_NEW_REJECTION;
            }
        }
#else
        wh_native_update_disposition_t disposition = wh_native_update_reject(
            &service_state, data + 8, request_boot_id, request_sequence,
            target_sequence, scenario, component, target_version,
            wh_platform_boot_id(), &reason);
#endif
        xSemaphoreGive(service_lock);
        if (disposition == WH_NATIVE_UPDATE_INVALID) {
            wh_platform_event("native_update_invalid", ESP_ERR_INVALID_ARG);
            return;
        }
        if (disposition == WH_NATIVE_UPDATE_CAPACITY_FULL)
            wh_platform_event("native_update_replay_capacity_full", ESP_ERR_NO_MEM);
        if (disposition == WH_NATIVE_UPDATE_ATTEMPT_CONFLICT)
            wh_platform_event("native_update_attempt_conflict", ESP_ERR_INVALID_ARG);
        else if (disposition == WH_NATIVE_UPDATE_REPLAY_REJECTION)
            wh_platform_event("native_update_rejection_replayed", ESP_OK);
#if defined(CONFIG_WASMHOME_NATIVE_OTA)
        const bool accepted = disposition == WH_NATIVE_UPDATE_NEW_ACCEPTANCE ||
                              disposition == WH_NATIVE_UPDATE_REPLAY_ACCEPTANCE;
        esp_err_t response_err = send_update_response(data, request_sequence,
            target_sequence, accepted, reason);
        if (disposition == WH_NATIVE_UPDATE_NEW_ACCEPTANCE) {
            if (response_err != ESP_OK) {
                wh_native_ota_cancel_reserved();
                xSemaphoreTake(service_lock, portMAX_DELAY);
                if (service_state.update_record_count) {
                    wh_native_update_record_t *record =
                        &service_state.update_records[service_state.update_record_count - 1];
                    if (memcmp(record->attempt_id, data + 8, 16) == 0) {
                        record->accepted = false;
                        record->reason = WH_NATIVE_REASON_BUSY;
                    }
                }
                xSemaphoreGive(service_lock);
                return;
            }
            emit_update_acceptance(data, request_boot_id, scenario, component,
                                   target_version, target_sequence);
            esp_err_t start_err = wh_native_ota_start_reserved();
            if (start_err != ESP_OK) {
                wh_native_ota_cancel_reserved();
                xSemaphoreTake(service_lock, portMAX_DELAY);
                (void)wh_native_update_mark_start_failed(&service_state, data + 8);
                xSemaphoreGive(service_lock);
                emit_update_start_failure(data, request_boot_id, target_sequence);
                wh_platform_event("native_ota_worker_start_failed", start_err);
            }
        }
#else
        (void)send_update_response(data, request_sequence, target_sequence,
                                   false, reason);
#endif
        return;
    }

    wh_platform_event("native_request_unsupported", ESP_ERR_NOT_SUPPORTED);
}

static void telemetry_task(void *argument)
{
    (void)argument;
    ESP_ERROR_CHECK(esp_task_wdt_add(NULL));
    TickType_t cycle = xTaskGetTickCount();
    for (;;) {
        wh_sensor_sample_t sample = wh_sensor_latest();
        uint8_t body[16];
        uint64_t now_us = (uint64_t)esp_timer_get_time();
#if defined(CONFIG_WASMHOME_RESILIENCE_TEST_MODE) && !defined(WASMHOME_NATIVE_SERVICES_HOST)
        uint8_t attempt_id[16];
        wh_resilience_test_telemetry_attempt(attempt_id);
#else
        const uint8_t attempt_id[16] = {0};
#endif
        if (wh_native_telemetry_body(body, sample.sample_id, sample.temperature_mC,
                                     (uint64_t)sample.acquired_us,
                                     (uint8_t)sample.status, now_us)) {
            esp_err_t err = wh_observation_send(WH_TELEMETRY, attempt_id,
                                                body, sizeof(body));
#if defined(CONFIG_WASMHOME_RESILIENCE_TEST_MODE) && !defined(WASMHOME_NATIVE_SERVICES_HOST)
            const bool sample_correct = err == ESP_OK &&
                sample.status == WH_SENSOR_VALID && sample.sample_id != 0 &&
                sample.acquired_us >= 0 && now_us >= (uint64_t)sample.acquired_us &&
                now_us - (uint64_t)sample.acquired_us <= 2000000;
            wh_resilience_test_note_service_result(true, sample_correct);
#endif
            if (err != ESP_OK) wh_platform_event("native_telemetry_send_failed", err);
        } else {
            wh_platform_event("native_telemetry_state_invalid", ESP_ERR_INVALID_STATE);
        }
        ESP_ERROR_CHECK(esp_task_wdt_reset());
        vTaskDelayUntil(&cycle, pdMS_TO_TICKS(100));
    }
}

esp_err_t wh_native_services_start(void)
{
    if (service_lock) return ESP_ERR_INVALID_STATE;
    if (!wh_native_services_state_init(&service_state,
                                       (uint8_t)CONFIG_WASMHOME_CONTROL_VERSION))
        return ESP_ERR_INVALID_ARG;
    service_lock = xSemaphoreCreateMutex();
    if (!service_lock) return ESP_ERR_NO_MEM;

    esp_err_t err;
#if defined(CONFIG_WASMHOME_NATIVE_OTA)
    err = wh_native_ota_start();
    if (err != ESP_OK) return err;
#endif

    err = wh_observation_set_handler(receive_request);
    if (err != ESP_OK) return err;
    if (xTaskCreate(telemetry_task, "wh_telemetry", 4096, NULL, 5, NULL) != pdPASS) {
        wh_observation_set_handler(NULL);
        return ESP_ERR_NO_MEM;
    }
    wh_platform_event("native_services_ready", ESP_OK);
    return ESP_OK;
}
#endif
