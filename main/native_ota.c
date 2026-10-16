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
#include "signed_update.h"

#include <stdio.h>
#include <string.h>

#define NATIVE_OTA_WORKER_STACK_BYTES 32768
#define NATIVE_OTA_BOOT_RECONCILE_STACK_BYTES 8192
#define NATIVE_OTA_NOTIFY_START 1u
#define NATIVE_OTA_NOTIFY_CANCEL 2u
#define NATIVE_OTA_NOTIFY_HEALTH_CHECK 3u
#define NATIVE_OTA_HASH_CHUNK_BYTES 1024u

typedef struct {
    const esp_partition_t *partition;
    esp_ota_handle_t handle;
    uint32_t expected_size;
    uint32_t written;
    bool begun;
} native_ota_sink_context_t;

static SemaphoreHandle_t ota_state_lock;
static TaskHandle_t ota_worker_task;
static bool ota_reserved;
static bool ota_busy;
static bool ota_health_complete;
static wh_native_ota_request_t reserved_request;

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

static void sink_abort(void *opaque)
{
    native_ota_sink_context_t *context = opaque;
    if (context && context->begun) {
        (void)esp_ota_abort(context->handle);
        context->begun = false;
    }
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
    if (err == ESP_OK) err = esp_ota_set_boot_partition(sink_context.partition);
    if (err != ESP_OK) {
        (void)wh_signed_update_abort(&manifest);
        emit_manifest_event(request, &manifest, WH_SIGNED_UPDATE_PHASE_UPDATE_FAILED,
                            err == ESP_ERR_INVALID_CRC
                                ? WH_SIGNED_UPDATE_FAILURE_PAYLOAD_HASH
                                : WH_SIGNED_UPDATE_FAILURE_PERSISTENCE);
        wh_platform_event("native_ota_activation_failed", err);
        return;
    }
    emit_manifest_event(request, &manifest, WH_SIGNED_UPDATE_PHASE_REBOOT_REQUESTED,
                        WH_SIGNED_UPDATE_FAILURE_NONE);
    fflush(stdout);
    esp_restart();
}

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
