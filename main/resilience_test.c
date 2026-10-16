#include "resilience_test.h"
#include "resilience_test_protocol.h"

#include "esp_log.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "native_ota.h"
#include "native_services.h"
#include "nvs.h"
#include "platform.h"
#include "signed_update.h"
#include "sdkconfig.h"

#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#define RESILIENCE_NVS_NAMESPACE "wh_resilience"
#define RESILIENCE_NVS_KEY "pending"
#define RESILIENCE_PENDING_MAGIC 0x57524832u
#define RESILIENCE_PENDING_VERSION 1u
#define RESILIENCE_LINE_CAPACITY 257u
#define RESILIENCE_RECOVERY_TELEMETRY 0x01u
#define RESILIENCE_RECOVERY_CONTROL 0x02u

typedef struct {
    uint32_t magic;
    uint16_t version;
    uint8_t case_code;
    uint8_t recovery_mask;
    uint64_t accepted_boot_id;
    uint64_t recovery_boot_id;
    uint8_t attempt_id[16];
    uint8_t reserved[8];
} resilience_pending_t;

static const char *TAG = "resilience_test";
static SemaphoreHandle_t state_lock;
static resilience_pending_t pending;
static bool pending_valid;

enum {
    RESILIENCE_CASE_NAT_ABORT = 1,
    RESILIENCE_CASE_ENV_SIG = 2,
    RESILIENCE_CASE_ENV_HASH = 3,
    RESILIENCE_CASE_ENV_OLD = 4,
};

static uint8_t case_code_from_name(const char *case_id)
{
    if (strcmp(case_id, "NAT-ABORT") == 0) return RESILIENCE_CASE_NAT_ABORT;
    if (strcmp(case_id, "ENV-SIG") == 0) return RESILIENCE_CASE_ENV_SIG;
    if (strcmp(case_id, "ENV-HASH") == 0) return RESILIENCE_CASE_ENV_HASH;
    if (strcmp(case_id, "ENV-OLD") == 0) return RESILIENCE_CASE_ENV_OLD;
    return 0;
}

static const char *case_name(uint8_t case_code)
{
    switch (case_code) {
    case RESILIENCE_CASE_NAT_ABORT: return "NAT-ABORT";
    case RESILIENCE_CASE_ENV_SIG: return "ENV-SIG";
    case RESILIENCE_CASE_ENV_HASH: return "ENV-HASH";
    case RESILIENCE_CASE_ENV_OLD: return "ENV-OLD";
    default: return "UNKNOWN";
    }
}

static bool bytes_nonzero(const uint8_t *bytes, size_t size)
{
    uint8_t any = 0;
    for (size_t i = 0; i < size; ++i) any |= bytes[i];
    return any != 0;
}

static void format_uuid(const uint8_t bytes[16], char text[37])
{
    (void)snprintf(text, 37,
        "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",
        bytes[0], bytes[1], bytes[2], bytes[3], bytes[4], bytes[5], bytes[6],
        bytes[7], bytes[8], bytes[9], bytes[10], bytes[11], bytes[12],
        bytes[13], bytes[14], bytes[15]);
}

static esp_err_t persist_pending(const resilience_pending_t *value)
{
    nvs_handle_t handle;
    esp_err_t err = nvs_open(RESILIENCE_NVS_NAMESPACE, NVS_READWRITE, &handle);
    if (err != ESP_OK) return err;
    err = nvs_set_blob(handle, RESILIENCE_NVS_KEY, value, sizeof(*value));
    if (err == ESP_OK) err = nvs_commit(handle);
    nvs_close(handle);
    if (err != ESP_OK) return err;

    resilience_pending_t readback = {0};
    size_t size = sizeof(readback);
    err = nvs_open(RESILIENCE_NVS_NAMESPACE, NVS_READONLY, &handle);
    if (err != ESP_OK) return err;
    err = nvs_get_blob(handle, RESILIENCE_NVS_KEY, &readback, &size);
    nvs_close(handle);
    if (err != ESP_OK) return err;
    if (size != sizeof(readback) || memcmp(&readback, value, sizeof(readback)) != 0)
        return ESP_ERR_INVALID_STATE;
    return ESP_OK;
}

static esp_err_t load_pending(resilience_pending_t *value, bool *found)
{
    *found = false;
    nvs_handle_t handle;
    esp_err_t err = nvs_open(RESILIENCE_NVS_NAMESPACE, NVS_READONLY, &handle);
    if (err == ESP_ERR_NVS_NOT_FOUND) return ESP_OK;
    if (err != ESP_OK) return err;
    size_t size = sizeof(*value);
    err = nvs_get_blob(handle, RESILIENCE_NVS_KEY, value, &size);
    nvs_close(handle);
    if (err == ESP_ERR_NVS_NOT_FOUND) return ESP_OK;
    if (err != ESP_OK) return err;
    if (size != sizeof(*value) || value->magic != RESILIENCE_PENDING_MAGIC ||
        value->version != RESILIENCE_PENDING_VERSION ||
        value->case_code < RESILIENCE_CASE_NAT_ABORT ||
        value->case_code > RESILIENCE_CASE_ENV_OLD ||
        value->accepted_boot_id == 0 || !bytes_nonzero(value->attempt_id,
                                                        sizeof(value->attempt_id)))
        return ESP_ERR_INVALID_STATE;
    *found = true;
    return ESP_OK;
}

static const char *reset_reason_name(esp_reset_reason_t reason)
{
    switch (reason) {
    case ESP_RST_POWERON: return "ESP_RST_POWERON";
    case ESP_RST_EXT: return "ESP_RST_EXT";
    case ESP_RST_SW: return "ESP_RST_SW";
    case ESP_RST_PANIC: return "ESP_RST_PANIC";
    case ESP_RST_INT_WDT: return "ESP_RST_INT_WDT";
    case ESP_RST_TASK_WDT: return "ESP_RST_TASK_WDT";
    case ESP_RST_WDT: return "ESP_RST_WDT";
    case ESP_RST_DEEPSLEEP: return "ESP_RST_DEEPSLEEP";
    case ESP_RST_BROWNOUT: return "ESP_RST_BROWNOUT";
    case ESP_RST_SDIO: return "ESP_RST_SDIO";
    default: return "ESP_RST_OTHER";
    }
}

static void emit_event(const char *event, const resilience_pending_t *value,
                       const char *fields)
{
    char attempt[37];
    format_uuid(value->attempt_id, attempt);
    printf("WASMHOME_RESILIENCE,{\"schema\":\"wasmhome-resilience-v1\","
           "\"event\":\"%s\",\"attempt_id\":\"%s\",\"case_id\":\"%s\","
           "\"scenario\":\"native\",\"boot_id\":\"%016" PRIx64 "\","
           "\"monotonic_us\":%" PRId64 ","
           "\"functional_timeout_ms_candidate\":%d,"
           "\"functional_timeout_frozen\":false%s}\n",
           event, attempt, case_name(value->case_code), wh_platform_boot_id(), esp_timer_get_time(),
           CONFIG_WASMHOME_RESILIENCE_FUNCTIONAL_TIMEOUT_MS,
           fields ? fields : "");
    fflush(stdout);
}

static void emit_rejected(const wh_resilience_fault_request_t *request, const char *reason)
{
    char fields[96];
    (void)snprintf(fields, sizeof(fields), ",\"reason\":\"%s\"", reason);
    char attempt[37];
    format_uuid(request->attempt_id, attempt);
    printf("WASMHOME_RESILIENCE,{\"schema\":\"wasmhome-resilience-v1\","
           "\"event\":\"fault_request_rejected\",\"attempt_id\":\"%s\","
           "\"case_id\":\"%s\",\"scenario\":\"native\","
           "\"boot_id\":\"%016" PRIx64 "\",\"monotonic_us\":%" PRId64 ","
           "\"functional_timeout_ms_candidate\":%d,"
           "\"functional_timeout_frozen\":false%s}\n",
           attempt, request->case_id, wh_platform_boot_id(), esp_timer_get_time(),
           CONFIG_WASMHOME_RESILIENCE_FUNCTIONAL_TIMEOUT_MS, fields);
    fflush(stdout);
}

static void emit_boot_observed(void)
{
    wh_signed_update_state_t update_state = {0};
    esp_err_t state_err = wh_signed_update_get_state(WH_NATIVE_SCENARIO_NATIVE,
        "firmware", CONFIG_WASMHOME_UPDATE_CAMPAIGN_ID, &update_state);
    char active_version[16];
    char accepted_sequence[32];
    (void)snprintf(active_version, sizeof(active_version), "%s",
                   state_err == ESP_OK ? "0" : "null");
    (void)snprintf(accepted_sequence, sizeof(accepted_sequence), "%s",
                   state_err == ESP_OK ? "0" : "null");
    if (state_err == ESP_OK) {
        (void)snprintf(active_version, sizeof(active_version), "%u",
                       update_state.active_target_version);
        (void)snprintf(accepted_sequence, sizeof(accepted_sequence), "%" PRIu64,
                       update_state.accepted_floor);
    }
    char fields[192];
    (void)snprintf(fields, sizeof(fields),
        ",\"reset_reason\":\"%s\",\"active_version\":%s,"
        "\"accepted_sequence\":%s,\"update_state_status\":\"%s\"",
        reset_reason_name(esp_reset_reason()), active_version, accepted_sequence,
        state_err == ESP_OK ? "available" : "unavailable");
    emit_event("boot_observed", &pending, fields);
}

static void emit_boot_and_recovery(void)
{
    emit_boot_observed();
    wh_signed_update_state_t update_state = {0};
    esp_err_t state_err = wh_signed_update_get_state(WH_NATIVE_SCENARIO_NATIVE,
        "firmware", CONFIG_WASMHOME_UPDATE_CAMPAIGN_ID, &update_state);
    char active_version[16];
    char accepted_sequence[32];
    (void)snprintf(active_version, sizeof(active_version), "%s",
                   state_err == ESP_OK ? "0" : "null");
    (void)snprintf(accepted_sequence, sizeof(accepted_sequence), "%s",
                   state_err == ESP_OK ? "0" : "null");
    if (state_err == ESP_OK) {
        (void)snprintf(active_version, sizeof(active_version), "%u",
                       update_state.active_target_version);
        (void)snprintf(accepted_sequence, sizeof(accepted_sequence), "%" PRIu64,
                       update_state.accepted_floor);
    }
    char fields[128];
    (void)snprintf(fields, sizeof(fields),
        ",\"active_version\":%s,\"accepted_sequence\":%s,"
        "\"update_state_status\":\"%s\"",
        active_version, accepted_sequence,
        state_err == ESP_OK ? "available" : "unavailable");
    emit_event("control_recovery_started", &pending, fields);
}

static void handle_fault_request(const wh_resilience_fault_request_t *request)
{
    const uint8_t request_case_code = case_code_from_name(request->case_id);
    if (!request_case_code) {
        emit_rejected(request, "case_not_supported_by_this_firmware");
        return;
    }

    if (xSemaphoreTake(state_lock, portMAX_DELAY) != pdTRUE) return;
    resilience_pending_t previous = {0};
    bool found = false;
    esp_err_t err = load_pending(&previous, &found);
    if (err != ESP_OK) {
        xSemaphoreGive(state_lock);
        ESP_LOGE(TAG, "cannot read pending resilience record: %s", esp_err_to_name(err));
        emit_rejected(request, "persistence_unavailable");
        return;
    }
    if (found && previous.recovery_mask != (RESILIENCE_RECOVERY_TELEMETRY |
                                             RESILIENCE_RECOVERY_CONTROL)) {
        xSemaphoreGive(state_lock);
        emit_rejected(request, "previous_attempt_not_recovered");
        return;
    }
    if (found && memcmp(previous.attempt_id, request->attempt_id,
                        sizeof(previous.attempt_id)) == 0) {
        xSemaphoreGive(state_lock);
        emit_rejected(request, "attempt_id_reused");
        return;
    }

    resilience_pending_t next = {0};
    next.magic = RESILIENCE_PENDING_MAGIC;
    next.version = RESILIENCE_PENDING_VERSION;
    next.case_code = request_case_code;
    next.accepted_boot_id = wh_platform_boot_id();
    memcpy(next.attempt_id, request->attempt_id, sizeof(next.attempt_id));
    err = persist_pending(&next);
    if (err == ESP_OK) {
        pending = next;
        pending_valid = true;
    }
    xSemaphoreGive(state_lock);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "cannot persist and read back accepted attempt: %s", esp_err_to_name(err));
        emit_rejected(request, "persistence_unavailable");
        return;
    }

    emit_event("fault_accepted", &pending, NULL);
    if (request_case_code != RESILIENCE_CASE_NAT_ABORT) {
        err = wh_native_ota_run_resilience_fixture(request->case_id,
                                                   request->attempt_id);
        if (err != ESP_OK) {
            char attempt[37];
            format_uuid(request->attempt_id, attempt);
            printf("WASMHOME_RESILIENCE,{\"schema\":\"wasmhome-resilience-v1\","
                   "\"event\":\"fault_execution_failed\",\"attempt_id\":\"%s\","
                   "\"case_id\":\"%s\",\"scenario\":\"native\","
                   "\"boot_id\":\"%016" PRIx64 "\",\"monotonic_us\":%" PRId64 ","
                   "\"reason\":\"fixture_worker_unavailable\",\"esp_err\":%d}\n",
                   attempt, request->case_id, wh_platform_boot_id(),
                   esp_timer_get_time(), err);
            fflush(stdout);
            wh_resilience_test_fixture_finished(request->attempt_id);
        }
    }
}

static void serial_request_task(void *argument)
{
    (void)argument;
    char line[RESILIENCE_LINE_CAPACITY];
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

esp_err_t wh_resilience_test_start(void)
{
    if (state_lock) return ESP_ERR_INVALID_STATE;
    state_lock = xSemaphoreCreateMutex();
    if (!state_lock) return ESP_ERR_NO_MEM;

    bool found = false;
    esp_err_t err = load_pending(&pending, &found);
    if (err != ESP_OK) {
        vSemaphoreDelete(state_lock);
        state_lock = NULL;
        return err;
    }
    pending_valid = found;
    if (found && pending.accepted_boot_id != wh_platform_boot_id()) {
        if (pending.recovery_mask != (RESILIENCE_RECOVERY_TELEMETRY |
                                      RESILIENCE_RECOVERY_CONTROL)) {
            pending.recovery_boot_id = wh_platform_boot_id();
            pending.recovery_mask = 0;
            err = persist_pending(&pending);
            if (err != ESP_OK) {
                vSemaphoreDelete(state_lock);
                state_lock = NULL;
                pending_valid = false;
                return err;
            }
            emit_boot_and_recovery();
        } else {
            emit_boot_observed();
        }
    }
    if (xTaskCreate(serial_request_task, "wh_resilience_serial", 4096,
                    NULL, 4, NULL) != pdPASS) {
        vSemaphoreDelete(state_lock);
        state_lock = NULL;
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

void wh_resilience_test_note_service_result(bool telemetry, bool correct)
{
    if (!state_lock || !correct ||
        xSemaphoreTake(state_lock, portMAX_DELAY) != pdTRUE) return;
    if (!pending_valid || !pending.recovery_boot_id ||
        pending.recovery_boot_id != wh_platform_boot_id()) {
        xSemaphoreGive(state_lock);
        return;
    }
    const uint8_t bit = telemetry ? RESILIENCE_RECOVERY_TELEMETRY
                                  : RESILIENCE_RECOVERY_CONTROL;
    if (pending.recovery_mask & bit) {
        xSemaphoreGive(state_lock);
        return;
    }
    resilience_pending_t next = pending;
    next.recovery_mask |= bit;
    esp_err_t err = persist_pending(&next);
    if (err == ESP_OK) pending = next;
    const bool completed = err == ESP_OK && next.recovery_mask ==
        (RESILIENCE_RECOVERY_TELEMETRY | RESILIENCE_RECOVERY_CONTROL);
    xSemaphoreGive(state_lock);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "cannot persist recovery service evidence: %s", esp_err_to_name(err));
        return;
    }
    if (completed) {
        wh_signed_update_state_t update_state = {0};
        esp_err_t state_err = wh_signed_update_get_state(WH_NATIVE_SCENARIO_NATIVE,
            "firmware", CONFIG_WASMHOME_UPDATE_CAMPAIGN_ID, &update_state);
        char fields[192];
        char active_version[16];
        char accepted_sequence[32];
        (void)snprintf(active_version, sizeof(active_version), "%s",
                       state_err == ESP_OK ? "0" : "null");
        (void)snprintf(accepted_sequence, sizeof(accepted_sequence), "%s",
                       state_err == ESP_OK ? "0" : "null");
        if (state_err == ESP_OK) {
            (void)snprintf(active_version, sizeof(active_version), "%u",
                           update_state.active_target_version);
            (void)snprintf(accepted_sequence, sizeof(accepted_sequence), "%" PRIu64,
                           update_state.accepted_floor);
        }
        (void)snprintf(fields, sizeof(fields),
            ",\"active_version\":%s,\"accepted_sequence\":%s,"
            "\"update_state_status\":\"%s\","
            "\"firmware_udp_tx\":true,\"control_udp_tx\":true",
            active_version, accepted_sequence,
            state_err == ESP_OK ? "available" : "unavailable");
        emit_event("control_recovery_completed", &next, fields);
    }
}

void wh_resilience_test_note_control_request(const uint8_t attempt_id[16],
                                            bool correct)
{
    if (!attempt_id || !state_lock ||
        xSemaphoreTake(state_lock, portMAX_DELAY) != pdTRUE) return;
    if (pending_valid && pending.case_code == RESILIENCE_CASE_NAT_ABORT &&
        pending.accepted_boot_id == wh_platform_boot_id() &&
        pending.recovery_boot_id == 0 &&
        memcmp(attempt_id, pending.attempt_id, sizeof(pending.attempt_id)) == 0) {
        resilience_pending_t accepted = pending;
        xSemaphoreGive(state_lock);
        char attempt[37];
        format_uuid(accepted.attempt_id, attempt);
        printf("resilience:controlled_abort attempt_id=%s case_id=NAT-ABORT "
               "boot_id=%016" PRIx64 " monotonic_us=%" PRId64 "\n",
               attempt, wh_platform_boot_id(), esp_timer_get_time());
        fflush(stdout);
        esp_system_abort("resilience:controlled_abort");
        return;
    }
    if (pending_valid && pending.recovery_boot_id == wh_platform_boot_id() &&
        memcmp(attempt_id, pending.attempt_id, sizeof(pending.attempt_id)) != 0) {
        xSemaphoreGive(state_lock);
        return;
    }
    xSemaphoreGive(state_lock);
    wh_resilience_test_note_service_result(false, correct);
}

void wh_resilience_test_telemetry_attempt(uint8_t attempt_id[16])
{
    memset(attempt_id, 0, 16);
    if (!state_lock || xSemaphoreTake(state_lock, portMAX_DELAY) != pdTRUE) return;
    if (pending_valid) memcpy(attempt_id, pending.attempt_id, 16);
    xSemaphoreGive(state_lock);
}

void wh_resilience_test_fixture_finished(const uint8_t attempt_id[16])
{
    if (!attempt_id || !state_lock ||
        xSemaphoreTake(state_lock, portMAX_DELAY) != pdTRUE) return;
    if (!pending_valid || pending.case_code == RESILIENCE_CASE_NAT_ABORT ||
        memcmp(pending.attempt_id, attempt_id, 16) != 0 ||
        pending.accepted_boot_id != wh_platform_boot_id()) {
        xSemaphoreGive(state_lock);
        return;
    }
    resilience_pending_t next = pending;
    next.recovery_boot_id = wh_platform_boot_id();
    next.recovery_mask = 0;
    esp_err_t err = persist_pending(&next);
    if (err == ESP_OK) pending = next;
    xSemaphoreGive(state_lock);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "cannot persist post-fixture recovery state: %s",
                 esp_err_to_name(err));
        return;
    }
    wh_signed_update_state_t update_state = {0};
    err = wh_signed_update_get_state(WH_NATIVE_SCENARIO_NATIVE, "firmware",
        CONFIG_WASMHOME_UPDATE_CAMPAIGN_ID, &update_state);
    char fields[192];
    char active_version[16];
    char accepted_sequence[32];
    (void)snprintf(active_version, sizeof(active_version), "%s",
                   err == ESP_OK ? "0" : "null");
    (void)snprintf(accepted_sequence, sizeof(accepted_sequence), "%s",
                   err == ESP_OK ? "0" : "null");
    if (err == ESP_OK) {
        (void)snprintf(active_version, sizeof(active_version), "%u",
                       update_state.active_target_version);
        (void)snprintf(accepted_sequence, sizeof(accepted_sequence), "%" PRIu64,
                       update_state.accepted_floor);
    }
    (void)snprintf(fields, sizeof(fields),
        ",\"active_version\":%s,\"accepted_sequence\":%s,"
        "\"update_state_status\":\"%s\"",
        active_version, accepted_sequence,
        err == ESP_OK ? "available" : "unavailable");
    emit_event("control_recovery_started", &next, fields);
}
