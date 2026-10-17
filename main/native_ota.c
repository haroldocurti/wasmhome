#include "native_ota.h"

#include "esp_system.h"
#include "esp_ota_ops.h"
#include "esp_partition.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "mbedtls/constant_time.h"
#include "mbedtls/md.h"
#include "native_services.h"
#include "platform.h"
#include "sdkconfig.h"
#if defined(CONFIG_WASMHOME_RESILIENCE_TEST_MODE)
#include "resilience_test.h"
#endif
#include "signed_update.h"

#include <stdio.h>
#include <string.h>

#define NATIVE_OTA_WORKER_STACK_BYTES 32768
#define NATIVE_OTA_BOOT_RECONCILE_STACK_BYTES 8192
#define NATIVE_OTA_NOTIFY_START 1u
#define NATIVE_OTA_NOTIFY_CANCEL 2u
#define NATIVE_OTA_NOTIFY_HEALTH_CHECK 3u
#if defined(CONFIG_WASMHOME_RESILIENCE_TEST_MODE)
#define NATIVE_OTA_NOTIFY_RESILIENCE 4u
#endif
#define NATIVE_OTA_HASH_CHUNK_BYTES 1024u
#define NATIVE_OTA_ERASE_SIZE 4096u

typedef struct {
    const esp_partition_t *partition;
    esp_ota_handle_t handle;
    uint32_t expected_size;
    uint32_t written;
    bool begun;
    bool invalidated;
} native_ota_sink_context_t;

static SemaphoreHandle_t ota_state_lock;
static TaskHandle_t ota_worker_task;
static bool ota_reserved;
static bool ota_busy;
static bool ota_health_complete;
static wh_native_ota_request_t reserved_request;
#if defined(CONFIG_WASMHOME_RESILIENCE_TEST_MODE)
static char resilience_case_id[9];
static uint8_t resilience_attempt_id[16];
#endif

typedef struct {
    SemaphoreHandle_t completion;
    esp_err_t result;
    uint32_t stack_high_water_bytes;
} native_ota_boot_reconcile_t;

static void native_ota_boot_reconcile_task(void *argument)
{
    native_ota_boot_reconcile_t *request = argument;
    request->result = wh_signed_update_reconcile_native_boot();
    request->stack_high_water_bytes =
        (uint32_t)(uxTaskGetStackHighWaterMark(NULL) * sizeof(StackType_t));
    xSemaphoreGive(request->completion);
    vTaskDelete(NULL);
}

esp_err_t wh_native_ota_reconcile_boot(uint32_t *stack_high_water_bytes)
{
    if (!stack_high_water_bytes) return ESP_ERR_INVALID_ARG;
    *stack_high_water_bytes = 0;
    native_ota_boot_reconcile_t request = {0};
    request.completion = xSemaphoreCreateBinary();
    if (!request.completion) return ESP_ERR_NO_MEM;
    BaseType_t created = xTaskCreate(native_ota_boot_reconcile_task,
        "ota_boot_reconcile", NATIVE_OTA_BOOT_RECONCILE_STACK_BYTES,
        &request, 5, NULL);
    if (created != pdPASS) {
        vSemaphoreDelete(request.completion);
        return ESP_ERR_NO_MEM;
    }
    if (xSemaphoreTake(request.completion, portMAX_DELAY) != pdTRUE) {
        vSemaphoreDelete(request.completion);
        return ESP_FAIL;
    }
    *stack_high_water_bytes = request.stack_high_water_bytes;
    vSemaphoreDelete(request.completion);
    return request.result;
}

static bool bytes_equal(const uint8_t *a, const uint8_t *b, size_t size)
{
    return a && b && mbedtls_ct_memcmp(a, b, size) == 0;
}

static esp_err_t hash_partition(const esp_partition_t *partition, uint32_t size,
                                uint8_t digest[32])
{
    if (!partition || !size || size > partition->size || !digest)
        return ESP_ERR_INVALID_SIZE;
    const mbedtls_md_info_t *sha = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
    if (!sha) return ESP_FAIL;
    mbedtls_md_context_t context;
    mbedtls_md_init(&context);
    esp_err_t err = mbedtls_md_setup(&context, sha, 0) == 0 &&
        mbedtls_md_starts(&context) == 0 ? ESP_OK : ESP_FAIL;
    uint8_t chunk[NATIVE_OTA_HASH_CHUNK_BYTES];
    for (uint32_t offset = 0; err == ESP_OK && offset < size;) {
        size_t amount = size - offset;
        if (amount > sizeof(chunk)) amount = sizeof(chunk);
        err = esp_partition_read(partition, offset, chunk, amount);
        if (err == ESP_OK && mbedtls_md_update(&context, chunk, amount) != 0)
            err = ESP_FAIL;
        offset += (uint32_t)amount;
    }
    if (err == ESP_OK && mbedtls_md_finish(&context, digest) != 0) err = ESP_FAIL;
    mbedtls_md_free(&context);
    return err;
}

static esp_err_t sink_begin(void *opaque,
                            const wh_signed_update_manifest_t *manifest,
                            uint32_t *slot_id)
{
    native_ota_sink_context_t *context = opaque;
    if (!context || !manifest || !slot_id || context->begun || !manifest->payload_size)
        return ESP_ERR_INVALID_ARG;
    const esp_partition_t *running = esp_ota_get_running_partition();
    const esp_partition_t *next = esp_ota_get_next_update_partition(NULL);
    if (!running || !next || next == running || manifest->payload_size > next->size)
        return ESP_ERR_INVALID_SIZE;
    context->partition = next;
    context->expected_size = manifest->payload_size;
    context->written = 0;
    context->begun = false;
    context->handle = 0;
    *slot_id = next->address;
    /* Erase is deliberately deferred to write(): the shared ledger records
       this inactive partition before the first destructive operation. */
    return ESP_OK;
}

static esp_err_t sink_write(void *opaque, const uint8_t *data, size_t size)
{
    native_ota_sink_context_t *context = opaque;
    if (!context || !context->partition || !data || !size ||
        context->written > context->expected_size ||
        size > context->expected_size - context->written) return ESP_ERR_INVALID_ARG;
    if (!context->begun) {
        esp_err_t err = esp_ota_begin(context->partition, context->expected_size,
                                      &context->handle);
        if (err != ESP_OK) return err;
        context->begun = true;
    }
    esp_err_t err = esp_ota_write(context->handle, data, size);
    if (err == ESP_OK) context->written += (uint32_t)size;
    return err;
}

static esp_err_t sink_finish(void *opaque,
                             const wh_signed_update_manifest_t *manifest,
                             uint32_t *slot_id)
{
    native_ota_sink_context_t *context = opaque;
    if (!context || !manifest || !slot_id || !context->partition ||
        !context->begun || context->written != manifest->payload_size ||
        context->expected_size != manifest->payload_size) return ESP_ERR_INVALID_STATE;
    esp_err_t err = esp_ota_end(context->handle);
    context->begun = false;
    if (err != ESP_OK) return err;
    uint8_t readback_sha256[32];
    err = hash_partition(context->partition, manifest->payload_size, readback_sha256);
    if (err != ESP_OK) return err;
    if (!bytes_equal(readback_sha256, manifest->payload_sha256, 32))
        return ESP_ERR_INVALID_CRC;
    *slot_id = context->partition->address;
    return ESP_OK;
}

static esp_err_t sink_abort(void *opaque)
{
    native_ota_sink_context_t *context = opaque;
    if (!context) return ESP_ERR_INVALID_ARG;
    esp_err_t err = ESP_OK;
    if (context && context->begun) {
        err = esp_ota_abort(context->handle);
        context->begun = false;
    }
    context->invalidated = context->written == 0;
    const esp_partition_t *running = esp_ota_get_running_partition();
    if (context->written != 0) {
        if (!context->partition || context->partition == running ||
            context->partition->size % NATIVE_OTA_ERASE_SIZE != 0)
            return err == ESP_OK ? ESP_ERR_INVALID_STATE : err;
        esp_err_t erase_err = esp_partition_erase_range(
            context->partition, 0, context->partition->size);
        uint8_t verify[NATIVE_OTA_HASH_CHUNK_BYTES];
        esp_err_t readback_err = ESP_OK;
        context->invalidated = true;
        for (uint32_t offset = 0; offset < context->partition->size;) {
            size_t amount = context->partition->size - offset;
            if (amount > sizeof(verify)) amount = sizeof(verify);
            const esp_err_t read_err = esp_partition_read(
                context->partition, offset, verify, amount);
            if (read_err != ESP_OK) {
                context->invalidated = false;
                if (readback_err == ESP_OK) readback_err = read_err;
            } else {
                for (size_t i = 0; i < amount; ++i) {
                    if (verify[i] != 0xff) context->invalidated = false;
                }
            }
            offset += (uint32_t)amount;
        }
        memset(verify, 0, sizeof(verify));
        if (err == ESP_OK && erase_err != ESP_OK) err = erase_err;
        if (err == ESP_OK && readback_err != ESP_OK) err = readback_err;
        if (err == ESP_OK && !context->invalidated) err = ESP_ERR_INVALID_CRC;
    }
    return err;
}

static void emit_manifest_event(const wh_native_ota_request_t *request,
                                const wh_signed_update_manifest_t *manifest,
                                wh_signed_update_phase_t phase,
                                wh_signed_update_failure_t failure)
{
    wh_signed_update_state_t state = {0};
    (void)wh_signed_update_get_state(WH_NATIVE_SCENARIO_NATIVE, "firmware",
        CONFIG_WASMHOME_UPDATE_CAMPAIGN_ID, &state);
    wh_signed_update_event_t event = {
        .phase = phase,
        .failure = failure,
        .attempt_id = request->attempt_id,
        .origin_boot_id = request->request_boot_id,
        .accepted_floor = state.accepted_floor,
        .ledger_readback_sequence = state.active_sequence,
        .scenario = request->scenario,
        .component_id = request->component,
        .target_version = request->target_version,
        .sequence = request->target_sequence,
        .release_version = manifest ? manifest->release_version : "v2",
        .payload_bytes_expected = manifest ? manifest->payload_size : 0,
        .payload_bytes_received = manifest ? manifest->payload_size : 0,
        .current_task_stack_high_water_bytes =
            (uint32_t)(uxTaskGetStackHighWaterMark(NULL) * sizeof(StackType_t)),
        .elapsed_us = (uint64_t)esp_timer_get_time(),
        .elapsed_scope = "boot",
        .payload_sha256 = manifest ? manifest->payload_sha256 : NULL,
        .manifest_sha256 = manifest ? manifest->manifest_sha256 : NULL,
    };
    wh_signed_update_emit_event(&event);
}

static void perform_update(const wh_native_ota_request_t *request)
{
    native_ota_sink_context_t sink_context = {0};
    const wh_signed_update_sink_t sink = {
        .context = &sink_context,
        .begin = sink_begin,
        .write = sink_write,
        .finish = sink_finish,
        .abort = sink_abort,
    };
    wh_signed_update_manifest_t manifest = {0};
    wh_signed_update_failure_t failure = WH_SIGNED_UPDATE_FAILURE_NONE;
    esp_err_t err = wh_signed_update_download(request->scenario, "v2",
        request->target_sequence, request->attempt_id, &sink, NULL, NULL,
        &manifest, &failure);
    if (err != ESP_OK) {
        wh_platform_event("native_ota_download_failed", err);
        return;
    }

    wh_signed_update_state_t state;
    err = wh_signed_update_get_state(request->scenario, "firmware",
        CONFIG_WASMHOME_UPDATE_CAMPAIGN_ID, &state);
    if (err == ESP_OK && (!state.pending || state.pending_slot != sink_context.partition->address))
        err = ESP_ERR_INVALID_STATE;
    if (err == ESP_OK)
        emit_manifest_event(request, &manifest, WH_SIGNED_UPDATE_PHASE_ACTIVATING,
                            WH_SIGNED_UPDATE_FAILURE_NONE);
    if (err == ESP_OK) err = wh_signed_update_mark_activating(&manifest,
                                                              sink_context.partition->address);
    bool boot_selection_attempted = false;
    if (err == ESP_OK) {
        /* set_boot_partition() may return an error after OTADATA has been
           erased or partially written. From this point, the pending ledger
           must remain intact until boot-time reconciliation determines which
           image the bootloader selected. */
        boot_selection_attempted = true;
        err = esp_ota_set_boot_partition(sink_context.partition);
    }
    if (err != ESP_OK) {
        if (!boot_selection_attempted) {
            const esp_err_t abort_err = wh_signed_update_abort(&manifest);
            if (abort_err != ESP_OK)
                wh_platform_event("native_ota_ledger_abort_failed", abort_err);
        }
        emit_manifest_event(request, &manifest, WH_SIGNED_UPDATE_PHASE_UPDATE_FAILED,
                            err == ESP_ERR_INVALID_CRC
                                ? WH_SIGNED_UPDATE_FAILURE_PAYLOAD_HASH
                                : WH_SIGNED_UPDATE_FAILURE_PERSISTENCE);
        wh_platform_event("native_ota_activation_failed", err);
        if (boot_selection_attempted) {
            fflush(stdout);
            /* Force the reconciler to inspect the actual boot selection. A
               selected new slot enters trial verification; an old slot clears
               pending only when metadata proves it was not selected or rolled
               back, and otherwise keeps the update state for later recovery. */
            esp_restart();
        }
        return;
    }
    emit_manifest_event(request, &manifest, WH_SIGNED_UPDATE_PHASE_REBOOT_REQUESTED,
                        WH_SIGNED_UPDATE_FAILURE_NONE);
    fflush(stdout);
    esp_restart();
}

#if defined(CONFIG_WASMHOME_RESILIENCE_TEST_MODE)
static bool resilience_bytes_nonzero(const uint8_t *bytes, size_t size)
{
    uint8_t value = 0;
    for (size_t i = 0; i < size; ++i) value |= bytes[i];
    return value != 0;
}

static void format_resilience_uuid(const uint8_t attempt_id[16], char text[37])
{
    (void)snprintf(text, 37,
        "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",
        attempt_id[0], attempt_id[1], attempt_id[2], attempt_id[3],
        attempt_id[4], attempt_id[5], attempt_id[6], attempt_id[7],
        attempt_id[8], attempt_id[9], attempt_id[10], attempt_id[11],
        attempt_id[12], attempt_id[13], attempt_id[14], attempt_id[15]);
}

static bool active_state_unchanged(const wh_signed_update_state_t *before,
                                   const wh_signed_update_state_t *after)
{
    return before && after && !after->pending &&
        before->scenario == after->scenario &&
        strcmp(before->component, after->component) == 0 &&
        before->accepted_floor == after->accepted_floor &&
        before->active_sequence == after->active_sequence &&
        before->active_slot == after->active_slot &&
        before->active_payload_size == after->active_payload_size &&
        before->active_target_version == after->active_target_version &&
        before->active_origin_boot_id == after->active_origin_boot_id &&
        strcmp(before->active_release_version, after->active_release_version) == 0 &&
        memcmp(before->active_payload_sha256, after->active_payload_sha256, 32) == 0 &&
        memcmp(before->active_manifest_sha256, after->active_manifest_sha256, 32) == 0 &&
        memcmp(before->active_attempt_id, after->active_attempt_id, 16) == 0;
}

static bool resilience_fixture_approved(bool expected_failure,
                                        bool state_preserved,
                                        bool payload_verified,
                                        esp_err_t cleanup_status)
{
    return expected_failure && state_preserved && payload_verified &&
        cleanup_status == ESP_OK;
}

static void perform_resilience_fixture(const char *case_id,
                                       const uint8_t attempt_id[16])
{
    native_ota_sink_context_t sink_context = {0};
    const wh_signed_update_sink_t sink = {
        .context = &sink_context,
        .begin = sink_begin,
        .write = sink_write,
        .finish = sink_finish,
        .abort = sink_abort,
    };
    wh_signed_update_state_t before = {0}, after = {0};
    const esp_err_t before_err = wh_signed_update_get_state(
        WH_NATIVE_SCENARIO_NATIVE, "firmware", CONFIG_WASMHOME_UPDATE_CAMPAIGN_ID,
        &before);
    wh_signed_update_manifest_t manifest = {0};
    wh_signed_update_failure_t failure = WH_SIGNED_UPDATE_FAILURE_NONE;
    const char *stage = "fixture_setup";
    esp_err_t cleanup_status = ESP_FAIL;
    const esp_err_t err = wh_signed_update_resilience_download(
        WH_NATIVE_SCENARIO_NATIVE, case_id, attempt_id, &sink, NULL, NULL,
        &manifest, &failure, &stage, &cleanup_status);

    const bool is_sig = strcmp(case_id, "ENV-SIG") == 0;
    const bool is_hash = strcmp(case_id, "ENV-HASH") == 0;
    const bool is_old = strcmp(case_id, "ENV-OLD") == 0;
    const char *expected_reason = is_sig ? "SIGNATURE_INVALID" :
                                  is_hash ? "HASH_MISMATCH" :
                                  is_old ? "SEQUENCE_NOT_NEWER" : "UNSUPPORTED_CASE";
    const char *expected_stage = is_sig ? "envelope_signature" :
                                 is_hash ? "payload_hash" :
                                 is_old ? "envelope_sequence" : "fixture_setup";
    const bool nvs_floor_checked = is_old && before_err == ESP_OK &&
        before.accepted_floor >= 1 && manifest.sequence != 0 &&
        manifest.sequence <= before.accepted_floor &&
        err == ESP_ERR_INVALID_VERSION;
    const bool expected_failure = is_sig
        ? (err != ESP_OK && failure == WH_SIGNED_UPDATE_FAILURE_SIGNATURE &&
           strcmp(stage, expected_stage) == 0)
        : is_hash
            ? (err == ESP_ERR_INVALID_CRC &&
               failure == WH_SIGNED_UPDATE_FAILURE_PAYLOAD_HASH &&
               strcmp(stage, expected_stage) == 0 && sink_context.written > 0 &&
               sink_context.invalidated)
            : is_old
                ? (err == ESP_ERR_INVALID_VERSION &&
                   failure == WH_SIGNED_UPDATE_FAILURE_POLICY &&
                   strcmp(stage, expected_stage) == 0 && nvs_floor_checked)
                : false;

    /* A mistakenly accepted fixture is never activated by this path. */
    if (err == ESP_OK) {
        esp_err_t abort_err = sink_abort(&sink_context);
        if (abort_err != ESP_OK) {
            wh_platform_event("resilience_fixture_sink_abort_failed", abort_err);
            cleanup_status = abort_err;
        }
        esp_err_t ledger_err = wh_signed_update_abort(&manifest);
        if (ledger_err != ESP_OK) {
            wh_platform_event("resilience_fixture_ledger_abort_failed", ledger_err);
            if (cleanup_status == ESP_OK) cleanup_status = ledger_err;
        }
    }
    const esp_err_t after_err = wh_signed_update_get_state(
        WH_NATIVE_SCENARIO_NATIVE, "firmware", CONFIG_WASMHOME_UPDATE_CAMPAIGN_ID,
        &after);
    const bool state_preserved = before_err == ESP_OK && after_err == ESP_OK &&
        active_state_unchanged(&before, &after);
    const bool no_payload_for_reject = (is_sig || is_old) && sink_context.written == 0;
    const bool inactive_slot_only = is_hash && sink_context.partition &&
        sink_context.partition != esp_ota_get_running_partition();
    const bool payload_verified = is_hash
        ? sink_context.written > 0 && inactive_slot_only && sink_context.invalidated
        : no_payload_for_reject;
    const bool approved = resilience_fixture_approved(expected_failure,
        state_preserved, payload_verified, cleanup_status);

    char attempt[37];
    format_resilience_uuid(attempt_id, attempt);
    if (approved) {
        printf("WASMHOME_RESILIENCE,{\"schema\":\"wasmhome-resilience-v1\","
               "\"event\":\"update_rejected\",\"attempt_id\":\"%s\","
               "\"case_id\":\"%s\",\"scenario\":\"native\","
               "\"boot_id\":\"%016" PRIx64 "\",\"monotonic_us\":%" PRId64 ","
               "\"reason\":\"%s\",\"stage\":\"%s\","
               "\"sequence\":%" PRIu64 ",\"accepted_floor_before\":%" PRIu64 ","
               "\"accepted_floor_after\":%" PRIu64 ",\"state_preserved\":true,"
               "\"real_envelope_validator\":true,"
               "\"active_slot_unchanged\":true,\"active_version_unchanged\":true,"
               "\"accepted_sequence_unchanged\":true,\"nvs_floor_checked\":%s,"
               "\"payload_written\":%s,\"payload_bytes_written\":%" PRIu32 ","
               "\"inactive_slot_only\":%s,\"payload_slot\":\"%s\","
               "\"payload_invalidated\":%s,\"cleanup_err\":%d}\n",
               attempt, case_id, wh_platform_boot_id(), esp_timer_get_time(),
               expected_reason, expected_stage, manifest.sequence,
               before.accepted_floor, after.accepted_floor,
               nvs_floor_checked ? "true" : "false",
               sink_context.written > 0 ? "true" : "false", sink_context.written,
               inactive_slot_only ? "true" : "false",
               is_hash ? "inactive" : "none",
               sink_context.invalidated ? "true" : "false", cleanup_status);
    } else {
        printf("WASMHOME_RESILIENCE,{\"schema\":\"wasmhome-resilience-v1\","
               "\"event\":\"fault_execution_failed\",\"attempt_id\":\"%s\","
               "\"case_id\":\"%s\",\"scenario\":\"native\","
               "\"boot_id\":\"%016" PRIx64 "\",\"monotonic_us\":%" PRId64 ","
               "\"reason\":\"%s\",\"stage\":\"%s\","
               "\"failure\":\"%s\",\"esp_err\":%d,"
               "\"state_preserved\":%s,\"payload_bytes_written\":%" PRIu32 ","
               "\"payload_invalidated\":%s,\"cleanup_err\":%d}\n",
               attempt, case_id, wh_platform_boot_id(), esp_timer_get_time(),
               expected_reason, stage, wh_signed_update_failure_name(failure), err,
               state_preserved ? "true" : "false", sink_context.written,
               sink_context.invalidated ? "true" : "false", cleanup_status);
    }
    fflush(stdout);
#if defined(CONFIG_WASMHOME_RESILIENCE_TEST_MODE)
    wh_resilience_test_fixture_finished(attempt_id);
#endif
}
#endif

static void ota_worker(void *argument)
{
    (void)argument;
    for (;;) {
        uint32_t command = 0;
        if (xTaskNotifyWait(0, UINT32_MAX, &command, portMAX_DELAY) != pdTRUE) continue;
        if (command == NATIVE_OTA_NOTIFY_CANCEL) {
            xSemaphoreTake(ota_state_lock, portMAX_DELAY);
            ota_reserved = false;
            ota_busy = false;
            memset(&reserved_request, 0, sizeof(reserved_request));
            xSemaphoreGive(ota_state_lock);
            continue;
        }
        if (command == NATIVE_OTA_NOTIFY_HEALTH_CHECK) {
            esp_err_t health_err = wh_signed_update_mark_native_healthy();
            if (health_err != ESP_OK)
                wh_platform_event("native_ota_health_commit_failed", health_err);
            xSemaphoreTake(ota_state_lock, portMAX_DELAY);
            ota_health_complete = health_err == ESP_OK;
            ota_busy = false;
            xSemaphoreGive(ota_state_lock);
            continue;
        }
#if defined(CONFIG_WASMHOME_RESILIENCE_TEST_MODE)
        if (command == NATIVE_OTA_NOTIFY_RESILIENCE) {
            char case_id[sizeof(resilience_case_id)];
            uint8_t attempt_id[16];
            xSemaphoreTake(ota_state_lock, portMAX_DELAY);
            memcpy(case_id, resilience_case_id, sizeof(case_id));
            memcpy(attempt_id, resilience_attempt_id, sizeof(attempt_id));
            memset(resilience_case_id, 0, sizeof(resilience_case_id));
            memset(resilience_attempt_id, 0, sizeof(resilience_attempt_id));
            xSemaphoreGive(ota_state_lock);
            perform_resilience_fixture(case_id, attempt_id);
            xSemaphoreTake(ota_state_lock, portMAX_DELAY);
            ota_busy = false;
            xSemaphoreGive(ota_state_lock);
            continue;
        }
#endif
        if (command != NATIVE_OTA_NOTIFY_START) continue;
        wh_native_ota_request_t request;
        xSemaphoreTake(ota_state_lock, portMAX_DELAY);
        request = reserved_request;
        ota_reserved = false;
        xSemaphoreGive(ota_state_lock);
        perform_update(&request);
        xSemaphoreTake(ota_state_lock, portMAX_DELAY);
        ota_busy = false;
        memset(&reserved_request, 0, sizeof(reserved_request));
        xSemaphoreGive(ota_state_lock);
    }
}

esp_err_t wh_native_ota_start(void)
{
    if (ota_worker_task || ota_state_lock) return ESP_ERR_INVALID_STATE;
    ota_state_lock = xSemaphoreCreateMutex();
    if (!ota_state_lock) return ESP_ERR_NO_MEM;
    if (xTaskCreate(ota_worker, "wh_native_ota", NATIVE_OTA_WORKER_STACK_BYTES,
                    NULL, 5, &ota_worker_task) != pdPASS) {
        vSemaphoreDelete(ota_state_lock);
        ota_state_lock = NULL;
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

esp_err_t wh_native_ota_reserve(const wh_native_ota_request_t *request)
{
    if (!request || !ota_state_lock || !ota_worker_task ||
        request->scenario != WH_NATIVE_SCENARIO_NATIVE ||
        request->component != WH_NATIVE_COMPONENT_CONTROL ||
        request->target_version != 2 || !request->target_sequence)
        return ESP_ERR_INVALID_ARG;
    xSemaphoreTake(ota_state_lock, portMAX_DELAY);
    esp_err_t err = ESP_OK;
    if (ota_busy) err = ESP_ERR_INVALID_STATE;
    else {
        reserved_request = *request;
        ota_reserved = true;
        ota_busy = true;
        ota_health_complete = false;
    }
    xSemaphoreGive(ota_state_lock);
    return err;
}

esp_err_t wh_native_ota_start_reserved(void)
{
    if (!ota_state_lock || !ota_worker_task) return ESP_ERR_INVALID_STATE;
    xSemaphoreTake(ota_state_lock, portMAX_DELAY);
    const bool reserved = ota_reserved;
    BaseType_t notified = pdPASS;
    if (reserved)
        notified = xTaskNotify(ota_worker_task, NATIVE_OTA_NOTIFY_START,
                               eSetValueWithOverwrite);
    xSemaphoreGive(ota_state_lock);
    return reserved && notified == pdPASS ? ESP_OK : ESP_ERR_INVALID_STATE;
}

void wh_native_ota_cancel_reserved(void)
{
    if (!ota_state_lock || !ota_worker_task) return;
    xSemaphoreTake(ota_state_lock, portMAX_DELAY);
    if (ota_reserved) {
        ota_reserved = false;
        ota_busy = false;
        memset(&reserved_request, 0, sizeof(reserved_request));
    }
    xSemaphoreGive(ota_state_lock);
}

esp_err_t wh_native_ota_queue_health_check(void)
{
    if (!ota_state_lock || !ota_worker_task) return ESP_ERR_INVALID_STATE;
    xSemaphoreTake(ota_state_lock, portMAX_DELAY);
    if (ota_busy || ota_reserved || ota_health_complete) {
        xSemaphoreGive(ota_state_lock);
        return ESP_ERR_INVALID_STATE;
    }
    ota_busy = true;
    BaseType_t notified = xTaskNotify(ota_worker_task,
        NATIVE_OTA_NOTIFY_HEALTH_CHECK, eSetValueWithOverwrite);
    if (notified != pdPASS) ota_busy = false;
    xSemaphoreGive(ota_state_lock);
    return notified == pdPASS ? ESP_OK : ESP_FAIL;
}

esp_err_t wh_native_ota_resilience_preflight(const char *case_id)
{
#if !defined(CONFIG_WASMHOME_RESILIENCE_TEST_MODE)
    (void)case_id;
    return ESP_ERR_INVALID_STATE;
#else
    const bool supported = case_id &&
        (strcmp(case_id, "ENV-SIG") == 0 ||
         strcmp(case_id, "ENV-HASH") == 0 ||
         strcmp(case_id, "ENV-OLD") == 0);
    if (!supported) return ESP_ERR_INVALID_ARG;
    if (!ota_state_lock || !ota_worker_task) return ESP_ERR_INVALID_STATE;

    xSemaphoreTake(ota_state_lock, portMAX_DELAY);
    const bool busy = ota_busy || ota_reserved;
    xSemaphoreGive(ota_state_lock);
    if (busy) return ESP_ERR_INVALID_STATE;

    wh_signed_update_state_t state = {0};
    const esp_err_t err = wh_signed_update_get_state(WH_NATIVE_SCENARIO_NATIVE,
        "firmware", CONFIG_WASMHOME_UPDATE_CAMPAIGN_ID, &state);
    if (err != ESP_OK) return err;
    if (state.pending) return ESP_ERR_INVALID_STATE;
    if (strcmp(case_id, "ENV-OLD") == 0 && state.accepted_floor < 1)
        return ESP_ERR_INVALID_VERSION;
    return ESP_OK;
#endif
}

esp_err_t wh_native_ota_run_resilience_fixture(
    const char *case_id, const uint8_t attempt_id[16])
{
#if !defined(CONFIG_WASMHOME_RESILIENCE_TEST_MODE)
    (void)case_id;
    (void)attempt_id;
    return ESP_ERR_INVALID_STATE;
#else
    if (!case_id || !attempt_id || !resilience_bytes_nonzero(attempt_id, 16) ||
        (strcmp(case_id, "ENV-SIG") != 0 &&
         strcmp(case_id, "ENV-HASH") != 0 &&
         strcmp(case_id, "ENV-OLD") != 0)) return ESP_ERR_INVALID_ARG;
    if (!ota_state_lock || !ota_worker_task) return ESP_ERR_INVALID_STATE;
    xSemaphoreTake(ota_state_lock, portMAX_DELAY);
    if (ota_busy || ota_reserved) {
        xSemaphoreGive(ota_state_lock);
        return ESP_ERR_INVALID_STATE;
    }
    (void)snprintf(resilience_case_id, sizeof(resilience_case_id), "%s", case_id);
    memcpy(resilience_attempt_id, attempt_id, sizeof(resilience_attempt_id));
    ota_busy = true;
    BaseType_t notified = xTaskNotify(ota_worker_task,
        NATIVE_OTA_NOTIFY_RESILIENCE, eSetValueWithOverwrite);
    if (notified != pdPASS) {
        ota_busy = false;
        memset(resilience_case_id, 0, sizeof(resilience_case_id));
        memset(resilience_attempt_id, 0, sizeof(resilience_attempt_id));
    }
    xSemaphoreGive(ota_state_lock);
    return notified == pdPASS ? ESP_OK : ESP_FAIL;
#endif
}
