#include "module_resilience_test.h"

#include "esp_log.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "module_manager.h"
#include "module_update.h"
#include "observation_wire.h"
#include "platform.h"
#include "resilience_test_protocol.h"
#include "sdkconfig.h"
#include "nvs.h"

#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#define MODULE_RESILIENCE_NVS_NAMESPACE "wh_mod_resil"
#define MODULE_RESILIENCE_NVS_KEY "pending"
#define MODULE_RESILIENCE_PENDING_MAGIC 0x574d5232u
#define MODULE_RESILIENCE_PENDING_VERSION 1u
#define MODULE_RESILIENCE_LINE_CAPACITY 257u
#define MODULE_RESILIENCE_TELEMETRY_TX 0x01u
#define MODULE_RESILIENCE_CONTROL_TX 0x02u
#define MODULE_RESILIENCE_ENV_SIG 4u
#define MODULE_RESILIENCE_ENV_HASH 5u
#define MODULE_RESILIENCE_ENV_OLD 6u

typedef struct {
    uint32_t magic;
    uint16_t version;
    uint8_t case_code;
    uint8_t recovery_mask;
    uint8_t accepted;
    uint8_t trap_seen;
    uint8_t slot_recovered;
    uint8_t recovery_reported;
    uint64_t accepted_boot_id;
    uint64_t recovery_boot_id;
    uint64_t trap_monotonic_us;
    uint64_t slot_restored_monotonic_us;
    uint64_t telemetry_tx_monotonic_us;
    uint64_t control_tx_monotonic_us;
    uint8_t attempt_id[16];
    uint8_t reserved[8];
} module_resilience_pending_t;

typedef struct {
    wh_resilience_fault_request_t request;
    uint8_t case_code;
    wh_module_resilience_case_t wasm_case;
    bool accepted;
} module_resilience_trial_t;

static const char *TAG = "module_resilience";
static SemaphoreHandle_t state_lock;
static module_resilience_pending_t pending;
static bool pending_valid;

static uint8_t
case_code_from_name(const char *case_id)
{
    if (strcmp(case_id, "WASM-ZERO") == 0) return WH_MODULE_RESILIENCE_ZERO;
    if (strcmp(case_id, "WASM-OOB") == 0) return WH_MODULE_RESILIENCE_OOB;
    if (strcmp(case_id, "WASM-DIV0") == 0) return WH_MODULE_RESILIENCE_DIV0;
    if (strcmp(case_id, "ENV-SIG") == 0) return MODULE_RESILIENCE_ENV_SIG;
    if (strcmp(case_id, "ENV-HASH") == 0) return MODULE_RESILIENCE_ENV_HASH;
    if (strcmp(case_id, "ENV-OLD") == 0) return MODULE_RESILIENCE_ENV_OLD;
    return 0;
}

static const char *
case_name(uint8_t code)
{
    switch (code) {
    case WH_MODULE_RESILIENCE_ZERO: return "WASM-ZERO";
    case WH_MODULE_RESILIENCE_OOB: return "WASM-OOB";
    case WH_MODULE_RESILIENCE_DIV0: return "WASM-DIV0";
    case MODULE_RESILIENCE_ENV_SIG: return "ENV-SIG";
    case MODULE_RESILIENCE_ENV_HASH: return "ENV-HASH";
    case MODULE_RESILIENCE_ENV_OLD: return "ENV-OLD";
    default: return "UNKNOWN";
    }
}

static bool
bytes_nonzero(const uint8_t *bytes, size_t size)
{
    uint8_t any = 0;
    for (size_t i = 0; i < size; ++i) any |= bytes[i];
    return any != 0;
}

static void
format_uuid(const uint8_t bytes[16], char text[37])
{
    (void)snprintf(text, 37,
        "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x",
        bytes[0], bytes[1], bytes[2], bytes[3], bytes[4], bytes[5], bytes[6],
        bytes[7], bytes[8], bytes[9], bytes[10], bytes[11], bytes[12],
        bytes[13], bytes[14], bytes[15]);
}

static bool
emit_event(const char *event, const uint8_t attempt_id[16], const char *case_id,
           uint64_t boot_id, uint64_t monotonic_us, const char *fields)
{
    char attempt[37];
    format_uuid(attempt_id, attempt);
#if defined(WASMHOME_SCENARIO_MODULE_INTERP)
    const char *scenario = "module-interp";
#else
    const char *scenario = "module-aot";
#endif
    const int written = printf(
        "WASMHOME_RESILIENCE,{\"schema\":\"wasmhome-resilience-v1\","
        "\"event\":\"%s\",\"attempt_id\":\"%s\",\"case_id\":\"%s\","
        "\"scenario\":\"%s\",\"boot_id\":\"%016" PRIx64 "\","
        "\"monotonic_us\":%" PRIu64 ","
        "\"functional_timeout_ms_candidate\":%d,"
        "\"functional_timeout_frozen\":false%s}\n",
        event, attempt, case_id, scenario, boot_id, monotonic_us,
        CONFIG_WASMHOME_MODULE_RESILIENCE_FUNCTIONAL_TIMEOUT_MS,
        fields ? fields : "");
    return written > 0 && fflush(stdout) == 0;
}

static bool
emit_event_for_pending(const char *event, const module_resilience_pending_t *value,
                       const char *fields)
{
    return emit_event(event, value->attempt_id, case_name(value->case_code),
                      wh_platform_boot_id(), (uint64_t)esp_timer_get_time(), fields);
}

static void
emit_request_rejected(const wh_resilience_fault_request_t *request,
                      const char *reason)
{
    char fields[128];
    (void)snprintf(fields, sizeof(fields), ",\"reason\":\"%s\"", reason);
    (void)emit_event("fault_request_rejected", request->attempt_id,
                     request->case_id, wh_platform_boot_id(),
                     (uint64_t)esp_timer_get_time(), fields);
}

static esp_err_t
persist_pending(const module_resilience_pending_t *value)
{
    nvs_handle_t handle;
    esp_err_t err = nvs_open(MODULE_RESILIENCE_NVS_NAMESPACE, NVS_READWRITE,
                             &handle);
    if (err != ESP_OK) return err;
    err = nvs_set_blob(handle, MODULE_RESILIENCE_NVS_KEY, value, sizeof(*value));
    if (err == ESP_OK) err = nvs_commit(handle);
    nvs_close(handle);
    if (err != ESP_OK) return err;

    module_resilience_pending_t readback = {0};
    size_t size = sizeof(readback);
    err = nvs_open(MODULE_RESILIENCE_NVS_NAMESPACE, NVS_READONLY, &handle);
    if (err != ESP_OK) return err;
    err = nvs_get_blob(handle, MODULE_RESILIENCE_NVS_KEY, &readback, &size);
    nvs_close(handle);
    if (err != ESP_OK) return err;
    if (size != sizeof(readback) || memcmp(&readback, value, sizeof(readback)) != 0)
        return ESP_ERR_INVALID_STATE;
    return ESP_OK;
}

static bool
persist_fault_accepted(module_resilience_trial_t *trial)
{
    if (!trial || !state_lock || xSemaphoreTake(state_lock, portMAX_DELAY) != pdTRUE)
        return false;
    const uint8_t complete = MODULE_RESILIENCE_TELEMETRY_TX |
                             MODULE_RESILIENCE_CONTROL_TX;
    if (pending_valid && (pending.recovery_mask != complete ||
        !pending.slot_recovered ||
        memcmp(pending.attempt_id, trial->request.attempt_id, 16) == 0)) {
        xSemaphoreGive(state_lock);
        return false;
    }
    module_resilience_pending_t next = {0};
    next.magic = MODULE_RESILIENCE_PENDING_MAGIC;
    next.version = MODULE_RESILIENCE_PENDING_VERSION;
    next.case_code = trial->case_code;
    next.accepted = 1;
    next.accepted_boot_id = wh_platform_boot_id();
    memcpy(next.attempt_id, trial->request.attempt_id, sizeof(next.attempt_id));
    esp_err_t err = persist_pending(&next);
    if (err == ESP_OK) {
        pending = next;
        pending_valid = true;
        trial->accepted = true;
    }
    xSemaphoreGive(state_lock);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "acceptance persistence/readback failed: %s",
                 esp_err_to_name(err));
        return false;
    }
    return emit_event_for_pending("fault_accepted", &next, NULL);
}

static bool
mark_recovered_after_health_check(const uint8_t attempt_id[16], uint64_t boot_id)
{
    if (!state_lock || xSemaphoreTake(state_lock, portMAX_DELAY) != pdTRUE)
        return false;
    const bool matches = pending_valid && pending.accepted &&
        memcmp(pending.attempt_id, attempt_id, 16) == 0;
    module_resilience_pending_t updated = pending;
    if (matches) {
        updated.recovery_boot_id = boot_id;
        updated.slot_recovered = 1;
        updated.slot_restored_monotonic_us = (uint64_t)esp_timer_get_time();
    }
    esp_err_t err = matches ? persist_pending(&updated) : ESP_ERR_INVALID_STATE;
    if (err == ESP_OK) pending = updated;
    xSemaphoreGive(state_lock);
    if (err != ESP_OK) return false;
    return emit_event_for_pending("control_slot_restored", &updated,
        ",\"control_slot_recovered\":true,\"control_health_check_passed\":true");
}

static bool
snapshot_pending_for_attempt(const uint8_t attempt_id[16],
                             module_resilience_pending_t *snapshot)
{
    if (!attempt_id || !snapshot || !state_lock ||
        xSemaphoreTake(state_lock, portMAX_DELAY) != pdTRUE)
        return false;
    const bool matches = pending_valid && pending.accepted &&
        memcmp(pending.attempt_id, attempt_id, sizeof(pending.attempt_id)) == 0;
    if (matches) *snapshot = pending;
    xSemaphoreGive(state_lock);
    return matches;
}

static esp_err_t
load_pending(module_resilience_pending_t *value, bool *found)
{
    *found = false;
    nvs_handle_t handle;
    esp_err_t err = nvs_open(MODULE_RESILIENCE_NVS_NAMESPACE, NVS_READONLY,
                             &handle);
    if (err == ESP_ERR_NVS_NOT_FOUND) return ESP_OK;
    if (err != ESP_OK) return err;
    size_t size = sizeof(*value);
    err = nvs_get_blob(handle, MODULE_RESILIENCE_NVS_KEY, value, &size);
    nvs_close(handle);
    if (err == ESP_ERR_NVS_NOT_FOUND) return ESP_OK;
    if (err != ESP_OK) return err;
    if (size != sizeof(*value) || value->magic != MODULE_RESILIENCE_PENDING_MAGIC ||
        value->version != MODULE_RESILIENCE_PENDING_VERSION ||
        value->case_code < WH_MODULE_RESILIENCE_ZERO ||
        value->case_code > MODULE_RESILIENCE_ENV_OLD || !value->accepted ||
        value->accepted_boot_id == 0 ||
        !bytes_nonzero(value->attempt_id, sizeof(value->attempt_id)))
        return ESP_ERR_INVALID_STATE;
    *found = true;
    return ESP_OK;
}

static void
emit_boot_observed(module_resilience_pending_t *value)
{
    char fields[96];
    (void)snprintf(fields, sizeof(fields), ",\"reset_reason\":%d",
                   (int)esp_reset_reason());
    (void)emit_event_for_pending("boot_observed", value, fields);
    (void)emit_event_for_pending("control_recovery_started", value, NULL);
}

static bool
json_escape(const char *input, char *output, size_t capacity)
{
    if (!input || !output || capacity == 0) return false;
    size_t used = 0;
    for (const unsigned char *p = (const unsigned char *)input; *p; ++p) {
        char encoded[7];
        size_t count = 0;
        switch (*p) {
        case '"': encoded[0] = '\\'; encoded[1] = '"'; count = 2; break;
        case '\\': encoded[0] = '\\'; encoded[1] = '\\'; count = 2; break;
        case '\b': encoded[0] = '\\'; encoded[1] = 'b'; count = 2; break;
        case '\f': encoded[0] = '\\'; encoded[1] = 'f'; count = 2; break;
        case '\n': encoded[0] = '\\'; encoded[1] = 'n'; count = 2; break;
        case '\r': encoded[0] = '\\'; encoded[1] = 'r'; count = 2; break;
        case '\t': encoded[0] = '\\'; encoded[1] = 't'; count = 2; break;
        default:
            if (*p < 0x20) {
                (void)snprintf(encoded, sizeof(encoded), "\\u%04x", *p);
                count = 6;
            } else {
                encoded[0] = (char)*p;
                count = 1;
            }
        }
        if (used + count + 1 > capacity) return false;
        memcpy(output + used, encoded, count);
        used += count;
    }
    output[used] = '\0';
    return true;
}

static bool
module_resilience_event(void *context, const char *event, const char *case_id,
                        bool call_ok, bool timed_out, int32_t result,
                        const char *exception_raw,
                        const wh_module_slot_info_t *telemetry,
                        const wh_module_slot_info_t *control,
                        uint64_t telemetry_runtime_busy_drops)
{
    module_resilience_trial_t *trial = context;
    if (!trial || strcmp(case_id, trial->request.case_id) != 0) return false;
    if (strcmp(event, "fault_accepted") == 0)
        return persist_fault_accepted(trial);

    if (!state_lock || xSemaphoreTake(state_lock, portMAX_DELAY) != pdTRUE)
        return false;
    const bool found = pending_valid &&
        memcmp(pending.attempt_id, trial->request.attempt_id, 16) == 0 &&
        pending.accepted_boot_id == wh_platform_boot_id();
    module_resilience_pending_t updated = pending;
    if (found && strcmp(event, "wasm_trap") == 0) {
        updated.trap_seen = 1;
        updated.trap_monotonic_us = (uint64_t)esp_timer_get_time();
    } else if (found && strcmp(event, "control_recovery_completed") == 0) {
        updated.slot_recovered = 1;
        updated.slot_restored_monotonic_us = (uint64_t)esp_timer_get_time();
    }
    esp_err_t persist_err = ESP_OK;
    if (found && memcmp(&updated, &pending, sizeof(updated)) != 0) {
        persist_err = persist_pending(&updated);
        if (persist_err == ESP_OK) pending = updated;
    }
    if (found) updated = pending;
    xSemaphoreGive(state_lock);
    if (!found || persist_err != ESP_OK) return false;

    char fields[512];
    fields[0] = '\0';
    if (strcmp(event, "wasm_trap") == 0 || strcmp(event, "wasm_control") == 0) {
        char escaped_exception[256];
        const bool has_exception = exception_raw && exception_raw[0];
        if (has_exception && !json_escape(exception_raw, escaped_exception,
                                            sizeof(escaped_exception)))
            return false;
        if (has_exception) {
            (void)snprintf(fields, sizeof(fields),
                ",\"call_ok\":%s,\"timed_out\":%s,\"return_value\":%" PRId32
                ",\"exception_raw\":\"%s\",\"telemetry_generation\":%" PRIu64
                ",\"control_generation\":%" PRIu64
                ",\"telemetry_runtime_busy_drops\":%" PRIu64,
                call_ok ? "true" : "false", timed_out ? "true" : "false", result,
                escaped_exception, telemetry ? telemetry->generation : 0,
                control ? control->generation : 0, telemetry_runtime_busy_drops);
        } else {
            (void)snprintf(fields, sizeof(fields),
                ",\"call_ok\":%s,\"timed_out\":%s,\"return_value\":%" PRId32
                ",\"exception_raw\":null,\"telemetry_generation\":%" PRIu64
                ",\"control_generation\":%" PRIu64
                ",\"telemetry_runtime_busy_drops\":%" PRIu64,
                call_ok ? "true" : "false", timed_out ? "true" : "false", result,
                telemetry ? telemetry->generation : 0,
                control ? control->generation : 0, telemetry_runtime_busy_drops);
        }
    } else if (strcmp(event, "control_recovery_completed") == 0) {
        (void)snprintf(fields, sizeof(fields),
            ",\"control_slot_recovered\":true,\"control_health_check_passed\":true");
    } else if (strcmp(event, "control_recovery_failed") == 0) {
        char escaped_exception[256];
        const bool escaped = exception_raw &&
            json_escape(exception_raw, escaped_exception, sizeof(escaped_exception));
        (void)snprintf(fields, sizeof(fields),
            ",\"call_ok\":%s,\"timed_out\":%s,\"control_slot_recovered\":false"
            ",\"exception_raw\":%s",
            call_ok ? "true" : "false", timed_out ? "true" : "false",
            escaped ? escaped_exception : "null");
    } else {
        (void)snprintf(fields, sizeof(fields),
            ",\"call_ok\":%s,\"timed_out\":%s,\"result\":%" PRId32,
            call_ok ? "true" : "false", timed_out ? "true" : "false", result);
    }
    const char *serial_event = strcmp(event, "control_recovery_completed") == 0
        ? "control_slot_restored" : event;
    return emit_event_for_pending(serial_event, &updated, fields);
}

static void
emit_recovery_if_complete(void)
{
    module_resilience_pending_t current;
    if (!state_lock || xSemaphoreTake(state_lock, portMAX_DELAY) != pdTRUE) return;
    const uint8_t complete = MODULE_RESILIENCE_TELEMETRY_TX |
                             MODULE_RESILIENCE_CONTROL_TX;
    const bool ready = pending_valid && pending.slot_recovered &&
        !pending.recovery_reported &&
        pending.recovery_boot_id == wh_platform_boot_id() &&
        pending.recovery_mask == complete;
    current = pending;
    xSemaphoreGive(state_lock);
    if (!ready) return;

    char fields[192];
    const bool same_boot = current.accepted_boot_id == current.recovery_boot_id;
    const bool telemetry_within_candidate = same_boot &&
        current.trap_monotonic_us != 0 &&
        current.telemetry_tx_monotonic_us >= current.trap_monotonic_us &&
        current.telemetry_tx_monotonic_us - current.trap_monotonic_us <=
            (uint64_t)CONFIG_WASMHOME_MODULE_RESILIENCE_FUNCTIONAL_TIMEOUT_MS * 1000;
    const bool within_30s = same_boot &&
        current.control_tx_monotonic_us >= current.slot_restored_monotonic_us &&
        current.control_tx_monotonic_us - current.slot_restored_monotonic_us <=
            UINT64_C(30000000);
    (void)snprintf(fields, sizeof(fields),
        ",\"control_slot_recovered\":true,\"control_health_check_passed\":true"
        ",\"telemetry_udp_tx\":true,\"control_udp_tx\":true"
        ",\"telemetry_within_timeout\":%s,\"control_recovery_within_30s\":%s",
        telemetry_within_candidate ? "true" : "false",
        within_30s ? "true" : "false");
    if (emit_event_for_pending("control_recovery_completed", &current, fields)) {
        if (xSemaphoreTake(state_lock, portMAX_DELAY) == pdTRUE) {
            if (pending_valid &&
                memcmp(pending.attempt_id, current.attempt_id, 16) == 0) {
                module_resilience_pending_t next = pending;
                next.recovery_reported = 1;
                if (persist_pending(&next) == ESP_OK) pending = next;
            }
            xSemaphoreGive(state_lock);
        }
    }
}

static void
emit_environment_execution_failed(const module_resilience_trial_t *trial,
                                 esp_err_t error,
                                 const wh_module_update_resilience_result_t *result)
{
    char fields[192];
    (void)snprintf(fields, sizeof(fields),
        ",\"esp_err\":%d,\"cleanup_err\":%d,\"stage\":\"%s\"",
        error, result ? result->cleanup_status : ESP_FAIL,
        result ? result->stage : "fixture_setup");
    (void)emit_event("fault_execution_failed", trial->request.attempt_id,
        trial->request.case_id, wh_platform_boot_id(),
        (uint64_t)esp_timer_get_time(), fields);
}

static void
handle_environment_fixture(module_resilience_trial_t *trial)
{
    const esp_err_t err = wh_module_manager_set_resilience_attempt_id(
        trial->request.attempt_id);
    if (err != ESP_OK || !persist_fault_accepted(trial)) {
        if (!trial->accepted)
            emit_request_rejected(&trial->request,
                                  "acceptance_not_durable_or_attempt_unavailable");
        else
            emit_environment_execution_failed(trial, err, NULL);
        return;
    }

    wh_module_update_resilience_result_t result = {0};
    const esp_err_t queue_err = wh_module_update_run_resilience_fixture(
        trial->request.case_id, trial->request.attempt_id, &result);
    if (queue_err != ESP_OK) {
        emit_environment_execution_failed(trial, queue_err, &result);
        return;
    }
    if (!result.expected_rejection) {
        emit_environment_execution_failed(trial, result.status, &result);
        return;
    }

    const char *reason = trial->case_code == MODULE_RESILIENCE_ENV_SIG
        ? "SIGNATURE_INVALID" :
        trial->case_code == MODULE_RESILIENCE_ENV_HASH
            ? "HASH_MISMATCH" : "SEQUENCE_NOT_NEWER";
    char fields[512];
    (void)snprintf(fields, sizeof(fields),
        ",\"reason\":\"%s\",\"stage\":\"%s\","
        "\"real_envelope_validator\":true,\"active_slot_unchanged\":%s,"
        "\"active_version_unchanged\":%s,\"accepted_sequence_unchanged\":%s,"
        "\"before_floor\":%" PRIu64 ",\"after_floor\":%" PRIu64 ","
        "\"payload_written\":%s,\"payload_bytes_written\":%" PRIu32 ","
        "\"inactive_slot_only\":%s,\"payload_invalidated\":%s,"
        "\"nvs_floor_checked\":%s,\"cleanup_err\":%d",
        reason, result.stage,
        result.active_slot_unchanged ? "true" : "false",
        result.active_version_unchanged ? "true" : "false",
        result.accepted_sequence_unchanged ? "true" : "false",
        result.previous_floor, result.observed_floor,
        result.payload_bytes_written ? "true" : "false",
        result.payload_bytes_written,
        result.inactive_slot_only ? "true" : "false",
        result.payload_invalidated ? "true" : "false",
        result.nvs_floor_checked ? "true" : "false", result.cleanup_status);
    module_resilience_pending_t current = {0};
    if (!snapshot_pending_for_attempt(trial->request.attempt_id, &current) ||
        !emit_event_for_pending("update_rejected", &current, fields))
        return;

    const uint64_t boot_id = wh_platform_boot_id();
    bool recovery_state_persisted = false;
    if (state_lock && xSemaphoreTake(state_lock, portMAX_DELAY) == pdTRUE) {
        if (pending_valid && memcmp(pending.attempt_id,
                                    trial->request.attempt_id, 16) == 0) {
            module_resilience_pending_t next = pending;
            next.recovery_boot_id = boot_id;
            next.recovery_mask = 0;
            next.slot_recovered = 0;
            next.recovery_reported = 0;
            next.telemetry_tx_monotonic_us = 0;
            next.control_tx_monotonic_us = 0;
            if (persist_pending(&next) == ESP_OK) {
                pending = next;
                current = next;
                recovery_state_persisted = true;
            }
        }
        xSemaphoreGive(state_lock);
    }
    if (!recovery_state_persisted) {
        ESP_LOGE(TAG, "recovery state persistence failed after expected update rejection");
        return;
    }
    (void)emit_event_for_pending("control_recovery_started", &current, NULL);
    const esp_err_t health_err = wh_module_manager_health_check_active_control();
    if (health_err != ESP_OK) {
        char health_fields[128];
        (void)snprintf(health_fields, sizeof(health_fields),
            ",\"control_slot_recovered\":false,\"control_health_check_passed\":false,"
            "\"esp_err\":%d", health_err);
        (void)emit_event_for_pending("control_recovery_failed", &current,
                                     health_fields);
        return;
    }
    if (!mark_recovered_after_health_check(trial->request.attempt_id, boot_id))
        ESP_LOGE(TAG, "control health passed but recovery readback failed");
}

static void
handle_fault_request(const wh_resilience_fault_request_t *request)
{
    const uint8_t case_code = case_code_from_name(request->case_id);
    if (!case_code) {
        emit_request_rejected(request, "case_not_supported_by_module_firmware");
        return;
    }
    if (!state_lock || xSemaphoreTake(state_lock, portMAX_DELAY) != pdTRUE) {
        emit_request_rejected(request, "persistence_unavailable");
        return;
    }
    module_resilience_pending_t previous = {0};
    bool found = false;
    esp_err_t err = load_pending(&previous, &found);
    const uint8_t complete = MODULE_RESILIENCE_TELEMETRY_TX |
                             MODULE_RESILIENCE_CONTROL_TX;
    if (err != ESP_OK || (found &&
        (previous.recovery_mask != complete || !previous.slot_recovered))) {
        xSemaphoreGive(state_lock);
        emit_request_rejected(request, err == ESP_OK ?
                              "previous_attempt_not_recovered" :
                              "persistence_unavailable");
        return;
    }
    if (found && memcmp(previous.attempt_id, request->attempt_id, 16) == 0) {
        xSemaphoreGive(state_lock);
        emit_request_rejected(request, "attempt_id_reused");
        return;
    }
    pending = previous;
    pending_valid = found;
    xSemaphoreGive(state_lock);

    module_resilience_trial_t trial = {
        .request = *request,
        .case_code = case_code,
        .wasm_case = (wh_module_resilience_case_t)case_code,
    };
    if (case_code >= MODULE_RESILIENCE_ENV_SIG) {
        err = wh_module_update_resilience_preflight(request->case_id);
        if (err != ESP_OK) {
            emit_request_rejected(request, "module_update_baseline_or_fixture_endpoint_unavailable");
            return;
        }
        handle_environment_fixture(&trial);
        return;
    }
    err = wh_module_manager_run_resilience_case(
        request->attempt_id, trial.wasm_case, module_resilience_event, &trial);
    if (err != ESP_OK && !trial.accepted) {
        emit_request_rejected(request, "fixture_not_ready_or_acceptance_not_durable");
    } else if (err != ESP_OK) {
        char fields[96];
        (void)snprintf(fields, sizeof(fields), ",\"esp_err\":%d", err);
        (void)emit_event("fault_execution_failed", request->attempt_id,
                         request->case_id, wh_platform_boot_id(),
                         (uint64_t)esp_timer_get_time(), fields);
    }
}

static void
serial_request_task(void *argument)
{
    (void)argument;
    char line[MODULE_RESILIENCE_LINE_CAPACITY];
    for (;;) {
        if (!fgets(line, sizeof(line), stdin)) {
            vTaskDelay(pdMS_TO_TICKS(100));
            continue;
        }
        size_t length = strlen(line);
        if (length == sizeof(line) - 1 && line[length - 1] != '\n') {
            int ch;
            while ((ch = fgetc(stdin)) != '\n' && ch != EOF) { }
            ESP_LOGW(TAG, "discarded overlength serial line");
            continue;
        }
        while (length && (line[length - 1] == '\n' || line[length - 1] == '\r'))
            line[--length] = '\0';
        wh_resilience_fault_request_t request;
        if (!wh_resilience_parse_fault_request(line, &request)) {
            ESP_LOGW(TAG, "discarded malformed or out-of-contract serial request");
            continue;
        }
        handle_fault_request(&request);
    }
}

esp_err_t
wh_module_resilience_test_start(void)
{
    if (state_lock) return ESP_ERR_INVALID_STATE;
    state_lock = xSemaphoreCreateMutex();
    if (!state_lock) return ESP_ERR_NO_MEM;
    esp_err_t err = wh_module_update_start();
    if (err != ESP_OK) {
        vSemaphoreDelete(state_lock);
        state_lock = NULL;
        return err;
    }
    bool found = false;
    err = load_pending(&pending, &found);
    if (err != ESP_OK) {
        vSemaphoreDelete(state_lock);
        state_lock = NULL;
        return err;
    }
    pending_valid = found;
    const uint8_t complete = MODULE_RESILIENCE_TELEMETRY_TX |
                             MODULE_RESILIENCE_CONTROL_TX;
    if (found && pending.recovery_mask != complete) {
        if (pending.accepted_boot_id != wh_platform_boot_id()) {
            pending.recovery_boot_id = wh_platform_boot_id();
            pending.recovery_mask = 0;
            pending.slot_recovered = 0;
            pending.recovery_reported = 0;
            pending.slot_restored_monotonic_us = 0;
            pending.telemetry_tx_monotonic_us = 0;
            pending.control_tx_monotonic_us = 0;
            err = persist_pending(&pending);
            if (err != ESP_OK) {
                vSemaphoreDelete(state_lock);
                state_lock = NULL;
                pending_valid = false;
                return err;
            }
            emit_boot_observed(&pending);
        }
        err = wh_module_manager_set_resilience_attempt_id(pending.attempt_id);
        if (err != ESP_OK) {
            vSemaphoreDelete(state_lock);
            state_lock = NULL;
            pending_valid = false;
            return err;
        }
        if (pending.accepted_boot_id != wh_platform_boot_id() &&
            pending.recovery_boot_id == wh_platform_boot_id() &&
            !pending.slot_recovered) {
            const esp_err_t health_err =
                wh_module_manager_health_check_active_control();
            if (health_err == ESP_OK) {
                if (!mark_recovered_after_health_check(pending.attempt_id,
                                                       wh_platform_boot_id()))
                    ESP_LOGE(TAG, "boot control health passed but NVS readback failed");
            } else {
                char fields[128];
                (void)snprintf(fields, sizeof(fields),
                    ",\"control_slot_recovered\":false,"
                    "\"control_health_check_passed\":false,\"esp_err\":%d",
                    health_err);
                (void)emit_event_for_pending("control_recovery_failed", &pending,
                                             fields);
            }
        }
    }
    if (xTaskCreate(serial_request_task, "wh_mod_resilience_serial", 4096,
                    NULL, 4, NULL) != pdPASS) {
        vSemaphoreDelete(state_lock);
        state_lock = NULL;
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

void
wh_module_resilience_test_note_udp_tx(uint8_t type, const uint8_t attempt_id[16],
                                      bool sent)
{
    if (!attempt_id || !sent || !state_lock ||
        xSemaphoreTake(state_lock, portMAX_DELAY) != pdTRUE) return;
    const uint64_t boot = wh_platform_boot_id();
    if (!pending_valid || !pending.accepted ||
        memcmp(pending.attempt_id, attempt_id, sizeof(pending.attempt_id)) != 0 ||
        (pending.recovery_boot_id && pending.recovery_boot_id != boot)) {
        xSemaphoreGive(state_lock);
        return;
    }
    module_resilience_pending_t next = pending;
    if (type == WH_TELEMETRY && (next.trap_seen || next.slot_recovered)) {
        next.recovery_boot_id = boot;
        next.recovery_mask |= MODULE_RESILIENCE_TELEMETRY_TX;
        next.telemetry_tx_monotonic_us = (uint64_t)esp_timer_get_time();
    } else if (type == WH_CONTROL_RESULT && next.slot_recovered) {
        next.recovery_boot_id = boot;
        next.recovery_mask |= MODULE_RESILIENCE_CONTROL_TX;
        next.control_tx_monotonic_us = (uint64_t)esp_timer_get_time();
    }
    esp_err_t err = ESP_OK;
    if (memcmp(&next, &pending, sizeof(next)) != 0) {
        err = persist_pending(&next);
        if (err == ESP_OK) pending = next;
    }
    xSemaphoreGive(state_lock);
    if (err == ESP_OK) emit_recovery_if_complete();
}
