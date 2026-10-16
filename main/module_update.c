#include "module_update.h"

#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "mbedtls/constant_time.h"
#include "mbedtls/sha256.h"
#include "module_slot_store.h"
#include "observation_wire.h"
#include "platform.h"
#include "sdkconfig.h"
#include "signed_update.h"

#include <inttypes.h>
#include <string.h>

#if defined(WASMHOME_SCENARIO_MODULE_AOT)
#define WH_MODULE_UPDATE_SCENARIO 3u
#elif defined(WASMHOME_SCENARIO_MODULE_INTERP)
#define WH_MODULE_UPDATE_SCENARIO 2u
#else
#error "module_update.c requires a module-interp or module-aot build"
#endif

#define WH_MODULE_UPDATE_COMPONENT_ID 1u
#define WH_MODULE_UPDATE_TARGET_VERSION 2u
#define WH_MODULE_UPDATE_SEQUENCE 2u
#define WH_MODULE_UPDATE_QUEUE_LENGTH 1u
#define WH_MODULE_UPDATE_TASK_STACK_BYTES (32u * 1024u)

enum {
    UPDATE_REASON_NONE = 0,
    UPDATE_REASON_WRONG_BOOT = 1,
    UPDATE_REASON_SCENARIO_MISMATCH = 2,
    UPDATE_REASON_UNSUPPORTED_COMPONENT = 3,
    UPDATE_REASON_UNSUPPORTED_VERSION = 4,
    UPDATE_REASON_SEQUENCE_NOT_NEWER = 5,
    UPDATE_REASON_BUSY = 6,
    UPDATE_REASON_ATTEMPT_CONFLICT = 7,
};

typedef struct {
    uint8_t packet[WH_HEADER_SIZE + 12];
    size_t packet_length;
    uint8_t attempt_id[16];
    uint64_t request_sequence;
    uint64_t origin_boot_id;
    uint64_t target_sequence;
} update_request_t;

typedef struct {
    update_request_t request;
    wh_signed_update_manifest_t manifest;
    wh_signed_update_failure_t failure;
    uint32_t slot_id;
    uint64_t started_us;
    uint64_t previous_floor;
    uint64_t previous_sequence;
    uint32_t previous_slot;
    wh_signed_update_state_t previous_ledger;
    uint64_t ledger_readback_sequence;
    uint64_t ledger_readback_floor;
    uint32_t payload_bytes_expected;
    uint32_t payload_bytes_received;
    wh_module_selection_outcome_t selection_outcome;
    bool manifest_ready;
    bool payload_verified;
    bool has_previous;
    wh_module_manager_info_t last_snapshot;
} update_attempt_t;

static QueueHandle_t update_queue;
static SemaphoreHandle_t update_lock;
static TaskHandle_t update_task;
static bool update_active;
static update_request_t active_request;

static esp_err_t send_acceptance(const uint8_t *packet, uint64_t request_sequence,
                                uint64_t target_sequence, uint8_t status,
                                uint8_t reason)
{
    uint8_t body[20] = {0};
    wh_write_be(body, request_sequence, 8);
    wh_write_be(body + 8, target_sequence, 8);
    body[16] = status;
    body[17] = reason;
    return wh_observation_send(WH_UPDATE_ACCEPTANCE, packet + 8,
                               body, sizeof(body));
}

#if CONFIG_WASMHOME_SIGNED_UPDATE
static const char *campaign_id(void)
{
    return CONFIG_WASMHOME_UPDATE_CAMPAIGN_ID;
}

static void fill_module_snapshot(wh_signed_update_event_t *event,
                                 const wh_module_slot_info_t *telemetry,
                                 const wh_module_slot_info_t *control,
                                 uint64_t busy_drops)
{
    if (!event) return;
    if (telemetry) {
        event->telemetry_generation = telemetry->generation;
        event->telemetry_sha256 = telemetry->sha256;
    }
    if (control) {
        event->control_generation = control->generation;
        event->control_module_version = control->module_version;
        event->control_sha256 = control->sha256;
    }
    event->telemetry_runtime_busy_drops = busy_drops;
}

static void emit_attempt_event(update_attempt_t *attempt,
                               wh_signed_update_phase_t phase,
                               wh_signed_update_failure_t failure,
                               const uint8_t *payload_hash,
                               const uint8_t *manifest_hash,
                               const wh_module_slot_info_t *telemetry,
                               const wh_module_slot_info_t *control,
                               uint64_t busy_drops,
                               uint64_t accepted_floor,
                               uint64_t ledger_readback_sequence,
                               bool defer_line)
{
    if (!attempt) return;
    const wh_signed_update_event_t event = {
        .phase = phase,
        .failure = failure,
        .attempt_id = attempt->request.attempt_id,
        .origin_boot_id = attempt->request.origin_boot_id,
        .accepted_floor = accepted_floor,
        .ledger_readback_sequence = ledger_readback_sequence,
        .scenario = WH_MODULE_UPDATE_SCENARIO,
        .component_id = WH_MODULE_UPDATE_COMPONENT_ID,
        .target_version = WH_MODULE_UPDATE_TARGET_VERSION,
        .sequence = WH_MODULE_UPDATE_SEQUENCE,
        .release_version = "v2",
        .current_task_stack_high_water_bytes =
            (uint32_t)(uxTaskGetStackHighWaterMark(NULL) * sizeof(StackType_t)),
        .payload_bytes_expected = attempt->payload_bytes_expected,
        .payload_bytes_received = attempt->payload_bytes_received,
        .elapsed_us = (uint64_t)esp_timer_get_time(),
        .elapsed_scope = "boot",
        .payload_sha256 = payload_hash,
        .manifest_sha256 = manifest_hash,
    };
    wh_signed_update_event_t enriched = event;
    fill_module_snapshot(&enriched, telemetry, control, busy_drops);
    if (defer_line) {
        char line[WH_SIGNED_UPDATE_EVENT_LINE_MAX_BYTES];
        size_t line_size = 0;
        if (wh_signed_update_format_event(&enriched, line, sizeof(line),
                                          &line_size) == ESP_OK) {
            (void)wh_module_manager_emit_or_defer_log_line(line, line_size);
        } else {
            wh_module_manager_mark_deferred_log_failure();
            (void)wh_module_manager_emit_or_defer_log_line(
                "WASMHOME_MODULE_LOG,{\"schema\":\"wasmhome-module-log-v1\","
                "\"event\":\"update_event_format_failed\"}\n",
                sizeof("WASMHOME_MODULE_LOG,{\"schema\":\"wasmhome-module-log-v1\","
                       "\"event\":\"update_event_format_failed\"}\n") - 1);
        }
    } else {
        wh_signed_update_emit_event(&enriched);
    }
}

static bool get_manager_snapshot(wh_module_manager_info_t *info)
{
    if (!info) return false;
    memset(info, 0, sizeof(*info));
    return wh_module_manager_get_info(info) == ESP_OK;
}

static void update_pipeline_event(void *context, wh_signed_update_event_t *event)
{
    update_attempt_t *attempt = context;
    if (!attempt || !event) return;
    event->origin_boot_id = attempt->request.origin_boot_id;
    attempt->payload_bytes_expected = event->payload_bytes_expected;
    attempt->payload_bytes_received = event->payload_bytes_received;
    if (event->phase == WH_SIGNED_UPDATE_PHASE_PAYLOAD_VERIFIED)
        attempt->payload_verified = true;
    if (event->manifest_sha256 && event->payload_bytes_expected != 0)
        attempt->manifest_ready = true;
    wh_module_manager_info_t info;
    if (get_manager_snapshot(&info)) {
        attempt->last_snapshot = info;
        event->telemetry_generation = attempt->last_snapshot.telemetry.generation;
        event->telemetry_sha256 = attempt->last_snapshot.telemetry.sha256;
        event->control_generation = attempt->last_snapshot.control.generation;
        event->control_module_version = attempt->last_snapshot.control.module_version;
        event->control_sha256 = attempt->last_snapshot.control.sha256;
        event->telemetry_runtime_busy_drops =
            attempt->last_snapshot.telemetry_runtime_busy_drops;
    }
}

static esp_err_t sink_begin(void *context,
                            const wh_signed_update_manifest_t *manifest,
                            uint32_t *slot_id)
{
    update_attempt_t *attempt = context;
    if (!attempt || !manifest || !slot_id || manifest->scenario != WH_MODULE_UPDATE_SCENARIO ||
        manifest->sequence != WH_MODULE_UPDATE_SEQUENCE || manifest->payload_size == 0 ||
        manifest->artifact_kind != WH_MODULE_UPDATE_SCENARIO ||
        strcmp(manifest->campaign_id, campaign_id()) != 0 ||
        strcmp(manifest->component, "gpio-control") != 0 ||
        strcmp(manifest->target, "esp32s3") != 0 ||
        strcmp(manifest->release_version, "v2") != 0 ||
        manifest->base_api_version != 1)
        return ESP_ERR_INVALID_ARG;
    wh_signed_update_state_t state;
    esp_err_t err = wh_signed_update_get_state(WH_MODULE_UPDATE_SCENARIO,
                                               "gpio-control", campaign_id(), &state);
    if (err != ESP_OK) return err;
    /* signed_update persists a pending record before invoking begin. Bind this
       sink to exactly that verified attempt before choosing an inactive slot. */
    if (!state.pending || state.pending_slot != WH_SIGNED_UPDATE_NO_ACTIVE_SLOT ||
        state.pending_sequence != manifest->sequence ||
        state.pending_target_version != WH_MODULE_UPDATE_TARGET_VERSION ||
        state.pending_payload_size != manifest->payload_size ||
        state.origin_boot_id != attempt->request.origin_boot_id ||
        memcmp(state.pending_attempt_id, attempt->request.attempt_id, 16) != 0 ||
        strcmp(state.pending_release_version, manifest->release_version) != 0 ||
        mbedtls_ct_memcmp(state.pending_payload_sha256,
                          manifest->payload_sha256, 32) != 0 ||
        mbedtls_ct_memcmp(state.pending_manifest_sha256,
                          manifest->manifest_sha256, 32) != 0)
        return ESP_ERR_INVALID_STATE;
    uint32_t target_slot = state.active_slot == WH_SIGNED_UPDATE_NO_ACTIVE_SLOT
        ? WH_MODULE_SLOT_ID_A
        : (state.active_slot == WH_MODULE_SLOT_ID_A
            ? WH_MODULE_SLOT_ID_B : WH_MODULE_SLOT_ID_A);
    if (state.active_slot != WH_SIGNED_UPDATE_NO_ACTIVE_SLOT &&
        state.active_slot != WH_MODULE_SLOT_ID_A &&
        state.active_slot != WH_MODULE_SLOT_ID_B)
        return ESP_ERR_INVALID_STATE;
    err = wh_module_slot_store_begin(WH_MODULE_UPDATE_SCENARIO, target_slot,
                                     manifest->payload_size);
    if (err == ESP_OK) {
        attempt->slot_id = target_slot;
        *slot_id = target_slot;
    }
    return err;
}

static esp_err_t sink_write(void *context, const uint8_t *data, size_t size)
{
    if (!context) return ESP_ERR_INVALID_ARG;
    return wh_module_slot_store_write(data, size);
}

static esp_err_t sink_finish(void *context,
                             const wh_signed_update_manifest_t *manifest,
                             uint32_t *slot_id)
{
    update_attempt_t *attempt = context;
    if (!attempt || !manifest || !slot_id || attempt->slot_id >= WH_MODULE_SLOT_COUNT)
        return ESP_ERR_INVALID_ARG;
    esp_err_t err = wh_module_slot_store_finish(
        WH_MODULE_UPDATE_SCENARIO, attempt->slot_id, manifest->payload_size,
        manifest->payload_sha256);
    if (err == ESP_OK) *slot_id = attempt->slot_id;
    return err;
}

static void sink_abort(void *context)
{
    (void)context;
    wh_module_slot_store_abort();
}

static bool state_has_target(const wh_signed_update_state_t *state,
                             const wh_signed_update_manifest_t *manifest,
                             const update_attempt_t *attempt,
                             uint32_t slot_id)
{
    return state && manifest && attempt && !state->pending && state->active_slot == slot_id &&
        state->accepted_floor == manifest->sequence &&
        state->active_sequence == manifest->sequence &&
        state->active_payload_size == manifest->payload_size &&
        state->active_target_version == 2 &&
        strcmp(state->active_release_version, "v2") == 0 &&
        memcmp(state->active_attempt_id, attempt->request.attempt_id, 16) == 0 &&
        mbedtls_ct_memcmp(state->active_payload_sha256,
                          manifest->payload_sha256, 32) == 0 &&
        mbedtls_ct_memcmp(state->active_manifest_sha256,
                          manifest->manifest_sha256, 32) == 0;
}

static uint8_t *allocate_module_buffer(size_t size)
{
    if (size == 0) return NULL;
#if defined(CONFIG_SPIRAM) && CONFIG_SPIRAM
    if (heap_caps_get_total_size(MALLOC_CAP_SPIRAM) >= size) {
        uint8_t *external = heap_caps_malloc(size, MALLOC_CAP_8BIT | MALLOC_CAP_SPIRAM);
        if (external) return external;
    }
#endif
    return heap_caps_malloc(size, MALLOC_CAP_8BIT | MALLOC_CAP_INTERNAL);
}

static bool state_active_matches_previous(const wh_signed_update_state_t *state,
                                          const update_attempt_t *attempt)
{
    const wh_signed_update_state_t *previous = attempt ? &attempt->previous_ledger : NULL;
    if (!state || !previous || state->scenario != previous->scenario ||
        strcmp(state->component, previous->component) != 0 ||
        state->accepted_floor != previous->accepted_floor ||
        state->active_sequence != previous->active_sequence ||
        state->active_slot != previous->active_slot ||
        state->active_payload_size != previous->active_payload_size ||
        state->active_target_version != previous->active_target_version ||
        state->active_origin_boot_id != previous->active_origin_boot_id ||
        strcmp(state->active_release_version, previous->active_release_version) != 0 ||
        memcmp(state->active_attempt_id, previous->active_attempt_id, 16) != 0 ||
        mbedtls_ct_memcmp(state->active_payload_sha256,
                          previous->active_payload_sha256, 32) != 0 ||
        mbedtls_ct_memcmp(state->active_manifest_sha256,
                          previous->active_manifest_sha256, 32) != 0)
        return false;
    return true;
}

static bool state_pending_matches_attempt(const wh_signed_update_state_t *state,
                                         const update_attempt_t *attempt)
{
    return state && attempt && state->pending &&
        state->pending_sequence == attempt->manifest.sequence &&
        state->pending_target_version == WH_MODULE_UPDATE_TARGET_VERSION &&
        state->pending_payload_size == attempt->manifest.payload_size &&
        state->pending_slot == attempt->slot_id &&
        state->origin_boot_id == attempt->request.origin_boot_id &&
        memcmp(state->pending_attempt_id, attempt->request.attempt_id, 16) == 0 &&
        strcmp(state->pending_release_version,
               attempt->manifest.release_version) == 0 &&
        mbedtls_ct_memcmp(state->pending_payload_sha256,
                          attempt->manifest.payload_sha256, 32) == 0 &&
        mbedtls_ct_memcmp(state->pending_manifest_sha256,
                          attempt->manifest.manifest_sha256, 32) == 0;
}

static esp_err_t commit_candidate(void *context,
                                  const wh_module_slot_info_t *telemetry,
                                  const wh_module_slot_info_t *candidate,
                                  wh_module_selection_outcome_t *outcome)
{
    update_attempt_t *attempt = context;
    if (!attempt || !telemetry || !candidate || !outcome ||
        candidate->module_version != 2 ||
        mbedtls_ct_memcmp(candidate->sha256,
                          attempt->manifest.payload_sha256, 32) != 0)
        return ESP_ERR_INVALID_ARG;
    *outcome = WH_MODULE_SELECTION_UNKNOWN;

    esp_err_t commit_status = wh_signed_update_commit(&attempt->manifest,
                                                       attempt->slot_id);
    wh_signed_update_state_t state;
    esp_err_t read_status = wh_signed_update_get_state(
        WH_MODULE_UPDATE_SCENARIO, "gpio-control", campaign_id(), &state);
    if (read_status == ESP_OK && state_has_target(&state, &attempt->manifest,
                                                   attempt, attempt->slot_id)) {
        attempt->ledger_readback_sequence = state.active_sequence;
        attempt->ledger_readback_floor = state.accepted_floor;
        *outcome = WH_MODULE_SELECTION_COMMITTED;
        attempt->selection_outcome = *outcome;
        return ESP_OK; /* Readback resolves even an ambiguous nvs_commit result. */
    }
    if (read_status == ESP_OK && state_active_matches_previous(&state, attempt)) {
        if (state.pending) {
            if (!state_pending_matches_attempt(&state, attempt)) {
                attempt->selection_outcome = WH_MODULE_SELECTION_UNKNOWN;
                return commit_status == ESP_OK ? ESP_ERR_INVALID_CRC : commit_status;
            }
            const esp_err_t abort_status = wh_signed_update_abort(&attempt->manifest);
            read_status = wh_signed_update_get_state(
                WH_MODULE_UPDATE_SCENARIO, "gpio-control", campaign_id(), &state);
            if (read_status != ESP_OK || state.pending ||
                !state_active_matches_previous(&state, attempt)) {
                attempt->selection_outcome = WH_MODULE_SELECTION_UNKNOWN;
                return commit_status == ESP_OK
                    ? (abort_status == ESP_OK ? ESP_ERR_INVALID_CRC : abort_status)
                    : commit_status;
            }
        }
        attempt->ledger_readback_sequence = state.active_sequence;
        attempt->ledger_readback_floor = state.accepted_floor;
        *outcome = WH_MODULE_SELECTION_NOT_COMMITTED;
        attempt->selection_outcome = *outcome;
        return commit_status == ESP_OK ? ESP_ERR_INVALID_CRC : commit_status;
    }
    attempt->selection_outcome = WH_MODULE_SELECTION_UNKNOWN;
    return read_status == ESP_OK ? ESP_ERR_INVALID_CRC : read_status;
}

static wh_signed_update_phase_t manager_phase(const char *phase)
{
    if (strcmp(phase, "candidate_loaded") == 0)
        return WH_SIGNED_UPDATE_PHASE_CANDIDATE_LOADED;
    if (strcmp(phase, "candidate_instantiated") == 0)
        return WH_SIGNED_UPDATE_PHASE_CANDIDATE_INSTANTIATED;
    if (strcmp(phase, "candidate_health_check") == 0)
        return WH_SIGNED_UPDATE_PHASE_CANDIDATE_HEALTH_CHECK;
    if (strcmp(phase, "floor_committed") == 0)
        return WH_SIGNED_UPDATE_PHASE_FLOOR_COMMITTED;
    if (strcmp(phase, "control_activated") == 0)
        return WH_SIGNED_UPDATE_PHASE_CONTROL_ACTIVATED;
    return WH_SIGNED_UPDATE_PHASE_PREVIOUS_CONTROL_RELEASED;
}

static void manager_event(void *context, const char *phase,
                          const wh_module_slot_info_t *telemetry,
                          const wh_module_slot_info_t *control,
                          uint64_t busy_drops)
{
    update_attempt_t *attempt = context;
    if (!attempt || !phase || !telemetry || !control) return;
    uint64_t floor = 0;
    uint64_t readback = 0;
    if (strcmp(phase, "floor_committed") == 0 ||
        strcmp(phase, "control_activated") == 0 ||
        strcmp(phase, "previous_control_released") == 0) {
        floor = attempt->ledger_readback_floor;
        readback = attempt->ledger_readback_sequence;
    }
    emit_attempt_event(attempt, manager_phase(phase),
                       WH_SIGNED_UPDATE_FAILURE_NONE,
                       attempt->manifest.payload_sha256,
                       attempt->manifest.manifest_sha256,
                       telemetry, control, busy_drops, floor, readback, true);
}

static esp_err_t reconcile_interrupted_module_update(
    wh_signed_update_state_t *state)
{
    if (!state || !state->pending) return ESP_OK;
    if (state->pending_slot == state->active_slot &&
        state->accepted_floor >= state->pending_sequence)
        return ESP_ERR_INVALID_CRC;

    wh_signed_update_manifest_t manifest = {0};
    manifest.scenario = WH_MODULE_UPDATE_SCENARIO;
    manifest.artifact_kind = WH_MODULE_UPDATE_SCENARIO;
    strncpy(manifest.campaign_id, campaign_id(), sizeof(manifest.campaign_id) - 1);
    strncpy(manifest.component, "gpio-control", sizeof(manifest.component) - 1);
    strncpy(manifest.release_version, state->pending_release_version,
            sizeof(manifest.release_version) - 1);
    strncpy(manifest.target, "esp32s3", sizeof(manifest.target) - 1);
    manifest.sequence = state->pending_sequence;
    manifest.payload_size = state->pending_payload_size;
    manifest.base_api_version = 1;
    memcpy(manifest.payload_sha256, state->pending_payload_sha256, 32);
    memcpy(manifest.manifest_sha256, state->pending_manifest_sha256, 32);
    const uint8_t attempt_id[16] = {0};
    (void)attempt_id;
    const esp_err_t abort_status = wh_signed_update_abort(&manifest);
    if (abort_status != ESP_OK) return abort_status;
    return wh_signed_update_get_state(WH_MODULE_UPDATE_SCENARIO,
                                      "gpio-control", campaign_id(), state);
}
#endif

esp_err_t wh_module_update_resolve_boot_control(
    const wh_module_image_t *embedded_control,
    wh_module_image_t *resolved_control,
    uint8_t **owned_bytes)
{
    if (!embedded_control || !resolved_control || !owned_bytes)
        return ESP_ERR_INVALID_ARG;
    *resolved_control = *embedded_control;
    *owned_bytes = NULL;
#if !CONFIG_WASMHOME_SIGNED_UPDATE
    return ESP_OK;
#else
    esp_err_t err = wh_module_slot_store_init();
    if (err != ESP_OK) return err;
    wh_signed_update_state_t state;
    err = wh_signed_update_get_state(WH_MODULE_UPDATE_SCENARIO, "gpio-control",
                                     campaign_id(), &state);
    if (err != ESP_OK) return err;
    if (state.pending) {
        const wh_signed_update_state_t interrupted = state;
        const uint64_t old_floor = state.accepted_floor;
        const uint64_t old_sequence = state.active_sequence;
        const uint32_t old_slot = state.active_slot;
        err = reconcile_interrupted_module_update(&state);
        if (err != ESP_OK || state.pending || state.accepted_floor != old_floor ||
            state.active_sequence != old_sequence || state.active_slot != old_slot)
            return err == ESP_OK ? ESP_ERR_INVALID_CRC : err;
        wh_signed_update_event_t recovery = {
            .phase = WH_SIGNED_UPDATE_PHASE_ROLLBACK_DETECTED,
            .failure = WH_SIGNED_UPDATE_FAILURE_NONE,
            .attempt_id = interrupted.pending_attempt_id,
            .origin_boot_id = interrupted.origin_boot_id,
            .accepted_floor = state.accepted_floor,
            .scenario = WH_MODULE_UPDATE_SCENARIO,
            .component_id = WH_MODULE_UPDATE_COMPONENT_ID,
            .target_version = interrupted.pending_target_version,
            .sequence = interrupted.pending_sequence,
            .release_version = interrupted.pending_release_version,
            .elapsed_us = (uint64_t)esp_timer_get_time(),
            .elapsed_scope = "boot",
            .payload_sha256 = interrupted.pending_payload_sha256,
            .manifest_sha256 = interrupted.pending_manifest_sha256,
        };
        wh_signed_update_emit_event(&recovery);
    }
    if (state.active_slot == WH_SIGNED_UPDATE_NO_ACTIVE_SLOT) {
        if (state.accepted_floor != 0 || state.active_sequence != 0 ||
            state.active_payload_size != 0)
            return ESP_ERR_INVALID_CRC;
        return ESP_OK;
    }
    if (state.active_slot >= WH_MODULE_SLOT_COUNT || state.accepted_floor != 2 ||
        state.active_sequence != 2 || state.active_target_version != 2 ||
        strcmp(state.active_release_version, "v2") != 0 ||
        state.active_payload_size == 0 ||
        state.active_payload_size > CONFIG_WASMHOME_MODULE_MAX_ARTIFACT_BYTES)
        return ESP_ERR_INVALID_CRC;

    uint8_t *buffer = allocate_module_buffer(state.active_payload_size);
    if (!buffer) return ESP_ERR_NO_MEM;
    err = wh_module_slot_store_read(WH_MODULE_UPDATE_SCENARIO, state.active_slot,
                                    state.active_payload_size, buffer,
                                    state.active_payload_size);
    uint8_t digest[32] = {0};
    if (err == ESP_OK && mbedtls_sha256(buffer, state.active_payload_size,
                                        digest, 0) != 0)
        err = ESP_FAIL;
    if (err == ESP_OK && mbedtls_ct_memcmp(digest,
            state.active_payload_sha256, sizeof(digest)) != 0)
        err = ESP_ERR_INVALID_CRC;
    memset(digest, 0, sizeof(digest));
    if (err != ESP_OK) {
        memset(buffer, 0, state.active_payload_size);
        heap_caps_free(buffer);
        return err;
    }
    *resolved_control = (wh_module_image_t){
        .bytes = buffer,
        .size = state.active_payload_size,
        .module_version = 2,
    };
    *owned_bytes = buffer;
    return ESP_OK;
#endif
}

static void process_update(update_request_t *request)
{
#if !CONFIG_WASMHOME_SIGNED_UPDATE
    (void)request;
    return;
#else
    update_attempt_t attempt = {
        .request = *request,
        .started_us = (uint64_t)esp_timer_get_time(),
        .slot_id = WH_SIGNED_UPDATE_NO_ACTIVE_SLOT,
        .selection_outcome = WH_MODULE_SELECTION_NOT_COMMITTED,
    };
    wh_module_manager_info_t initial_info;
    if (!get_manager_snapshot(&initial_info)) return;
    emit_attempt_event(&attempt, WH_SIGNED_UPDATE_PHASE_ACCEPTED,
                       WH_SIGNED_UPDATE_FAILURE_NONE, NULL, NULL,
                       &initial_info.telemetry, &initial_info.control,
                       initial_info.telemetry_runtime_busy_drops,
                       0, 0, false);

    wh_signed_update_state_t before;
    esp_err_t err = wh_signed_update_get_state(WH_MODULE_UPDATE_SCENARIO,
                                               "gpio-control", campaign_id(), &before);
    if (err != ESP_OK || before.pending || before.accepted_floor >= 2 ||
        initial_info.control.module_version != 1) {
        attempt.failure = err == ESP_OK ? WH_SIGNED_UPDATE_FAILURE_POLICY
                                        : WH_SIGNED_UPDATE_FAILURE_PERSISTENCE;
        goto failed_without_manifest;
    }
    attempt.previous_floor = before.accepted_floor;
    attempt.previous_sequence = before.active_sequence;
    attempt.previous_slot = before.active_slot;
    attempt.previous_ledger = before;
    attempt.has_previous = true;

    wh_signed_update_sink_t sink = {
        .context = &attempt,
        .begin = sink_begin,
        .write = sink_write,
        .finish = sink_finish,
        .abort = sink_abort,
    };
    err = wh_signed_update_download(WH_MODULE_UPDATE_SCENARIO, "v2", 2,
                                   request->attempt_id, &sink,
                                   update_pipeline_event, &attempt,
                                   &attempt.manifest, &attempt.failure);
    attempt.manifest_ready = attempt.manifest.sequence != 0;
    if (err != ESP_OK) goto failed_without_manifest;
    attempt.payload_verified = true;
    attempt.payload_bytes_expected = attempt.manifest.payload_size;
    attempt.payload_bytes_received = attempt.manifest.payload_size;
    if (attempt.manifest.scenario != WH_MODULE_UPDATE_SCENARIO ||
        strcmp(attempt.manifest.component, "gpio-control") != 0 ||
        strcmp(attempt.manifest.release_version, "v2") != 0 ||
        attempt.manifest.sequence != WH_MODULE_UPDATE_SEQUENCE ||
        attempt.manifest.payload_size > CONFIG_WASMHOME_MODULE_MAX_ARTIFACT_BYTES) {
        attempt.failure = WH_SIGNED_UPDATE_FAILURE_POLICY;
        goto failed_pending;
    }
    err = wh_signed_update_mark_activating(&attempt.manifest, attempt.slot_id);
    if (err != ESP_OK) {
        attempt.failure = WH_SIGNED_UPDATE_FAILURE_PERSISTENCE;
        goto failed_pending;
    }
    wh_module_manager_info_t staged_info;
    if (!get_manager_snapshot(&staged_info)) {
        attempt.failure = WH_SIGNED_UPDATE_FAILURE_PERSISTENCE;
        goto failed_pending;
    }
    emit_attempt_event(&attempt, WH_SIGNED_UPDATE_PHASE_ACTIVATING,
                       WH_SIGNED_UPDATE_FAILURE_NONE,
                       attempt.manifest.payload_sha256,
                       attempt.manifest.manifest_sha256,
                       &staged_info.telemetry, &staged_info.control,
                       staged_info.telemetry_runtime_busy_drops,
                       before.accepted_floor, 0, false);

    uint8_t *candidate_bytes = allocate_module_buffer(attempt.manifest.payload_size);
    if (!candidate_bytes) {
        attempt.failure = WH_SIGNED_UPDATE_FAILURE_SINK;
        goto failed_pending;
    }
    err = wh_module_slot_store_read(WH_MODULE_UPDATE_SCENARIO, attempt.slot_id,
                                    attempt.manifest.payload_size,
                                    candidate_bytes, attempt.manifest.payload_size);
    uint8_t digest[32] = {0};
    if (err == ESP_OK && mbedtls_sha256(candidate_bytes,
            attempt.manifest.payload_size, digest, 0) != 0)
        err = ESP_FAIL;
    if (err == ESP_OK && mbedtls_ct_memcmp(digest,
            attempt.manifest.payload_sha256, sizeof(digest)) != 0)
        err = ESP_ERR_INVALID_CRC;
    memset(digest, 0, sizeof(digest));
    if (err != ESP_OK) {
        memset(candidate_bytes, 0, attempt.manifest.payload_size);
        heap_caps_free(candidate_bytes);
        attempt.failure = WH_SIGNED_UPDATE_FAILURE_PAYLOAD_HASH;
        goto failed_pending;
    }

    const wh_module_image_t candidate = {
        .bytes = candidate_bytes,
        .size = attempt.manifest.payload_size,
        .module_version = 2,
    };
    err = wh_module_manager_activate_control(&candidate, commit_candidate,
                                              manager_event, &attempt);
    memset(candidate_bytes, 0, attempt.manifest.payload_size);
    heap_caps_free(candidate_bytes);
    if (err == ESP_OK) return;
    if (attempt.selection_outcome == WH_MODULE_SELECTION_UNKNOWN) {
        attempt.failure = WH_SIGNED_UPDATE_FAILURE_PERSISTENCE;
        goto failed_without_manifest;
    }
    attempt.failure = WH_SIGNED_UPDATE_FAILURE_SINK;
    goto failed_pending;

failed_pending:
    if (attempt.manifest_ready && attempt.selection_outcome != WH_MODULE_SELECTION_UNKNOWN) {
        wh_signed_update_state_t current;
        const esp_err_t state_status = wh_signed_update_get_state(
            WH_MODULE_UPDATE_SCENARIO, "gpio-control", campaign_id(), &current);
        if (state_status != ESP_OK) {
            attempt.failure = WH_SIGNED_UPDATE_FAILURE_PERSISTENCE;
        } else if (current.pending) {
            if (!state_active_matches_previous(&current, &attempt) ||
                !state_pending_matches_attempt(&current, &attempt)) {
                /* Never clear an unrelated or ambiguous pending selection. */
                attempt.failure = WH_SIGNED_UPDATE_FAILURE_PERSISTENCE;
            } else {
                const esp_err_t abort_status = wh_signed_update_abort(&attempt.manifest);
                (void)abort_status; /* The ledger readback below resolves an ambiguous result. */
                wh_signed_update_state_t after_abort;
                if (wh_signed_update_get_state(WH_MODULE_UPDATE_SCENARIO,
                        "gpio-control", campaign_id(), &after_abort) != ESP_OK ||
                    after_abort.pending ||
                    !state_active_matches_previous(&after_abort, &attempt))
                    attempt.failure = WH_SIGNED_UPDATE_FAILURE_PERSISTENCE;
            }
        } else if (!state_active_matches_previous(&current, &attempt) &&
                   !state_has_target(&current, &attempt.manifest, &attempt,
                                     attempt.slot_id)) {
            attempt.failure = WH_SIGNED_UPDATE_FAILURE_PERSISTENCE;
        }
    }
failed_without_manifest:
    wh_module_manager_info_t failed_info;
    if (get_manager_snapshot(&failed_info)) {
        emit_attempt_event(&attempt, WH_SIGNED_UPDATE_PHASE_UPDATE_FAILED,
                           attempt.failure == WH_SIGNED_UPDATE_FAILURE_NONE
                               ? WH_SIGNED_UPDATE_FAILURE_SINK : attempt.failure,
                           attempt.payload_verified
                               ? attempt.manifest.payload_sha256 : NULL,
                           attempt.manifest_ready ? attempt.manifest.manifest_sha256 : NULL,
                           &failed_info.telemetry, &failed_info.control,
                           failed_info.telemetry_runtime_busy_drops,
                           0, 0, false);
    }
#endif
}

static void update_worker(void *argument)
{
    (void)argument;
    update_request_t request;
    for (;;) {
        if (xQueueReceive(update_queue, &request, portMAX_DELAY) != pdTRUE) continue;
        process_update(&request);
        xSemaphoreTake(update_lock, portMAX_DELAY);
        update_active = false;
        memset(&active_request, 0, sizeof(active_request));
        xSemaphoreGive(update_lock);
    }
}

esp_err_t wh_module_update_start(void)
{
#if !CONFIG_WASMHOME_SIGNED_UPDATE
    return ESP_OK;
#else
    if (update_task) return ESP_OK;
    esp_err_t err = wh_module_slot_store_init();
    if (err != ESP_OK) return err;
    update_lock = xSemaphoreCreateMutex();
    update_queue = xQueueCreate(WH_MODULE_UPDATE_QUEUE_LENGTH,
                                sizeof(update_request_t));
    if (!update_lock || !update_queue) return ESP_ERR_NO_MEM;
    if (xTaskCreate(update_worker, "wh_module_update",
                    WH_MODULE_UPDATE_TASK_STACK_BYTES, NULL,
                    tskIDLE_PRIORITY + 2, &update_task) != pdPASS) {
        vQueueDelete(update_queue);
        update_queue = NULL;
        vSemaphoreDelete(update_lock);
        update_lock = NULL;
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
#endif
}

void wh_module_update_handle_trigger(const uint8_t *packet, size_t length)
{
    if (!packet || length != WH_HEADER_SIZE + 12 || !wh_wire_valid(packet, length) ||
        packet[5] != WH_UPDATE_TRIGGER)
        return;
    const uint64_t request_sequence = wh_read_be(packet + 24, 8);
    const uint64_t request_boot_id = wh_read_be(packet + 32, 8);
    const uint64_t target_sequence = wh_read_be(packet + WH_HEADER_SIZE, 8);
    const uint8_t scenario = packet[WH_HEADER_SIZE + 8];
    const uint8_t component = packet[WH_HEADER_SIZE + 9];
    const uint16_t target_version = (uint16_t)wh_read_be(packet + WH_HEADER_SIZE + 10, 2);
    uint8_t reason = UPDATE_REASON_NONE;
    if (request_boot_id != wh_platform_boot_id()) reason = UPDATE_REASON_WRONG_BOOT;
    else if (scenario != WH_MODULE_UPDATE_SCENARIO) reason = UPDATE_REASON_SCENARIO_MISMATCH;
    else if (component != WH_MODULE_UPDATE_COMPONENT_ID) reason = UPDATE_REASON_UNSUPPORTED_COMPONENT;
    else if (target_version != WH_MODULE_UPDATE_TARGET_VERSION) reason = UPDATE_REASON_UNSUPPORTED_VERSION;
    else if (target_sequence != WH_MODULE_UPDATE_SEQUENCE) reason = UPDATE_REASON_SEQUENCE_NOT_NEWER;
#if !CONFIG_WASMHOME_SIGNED_UPDATE
    else reason = UPDATE_REASON_UNSUPPORTED_COMPONENT;
#endif
    if (reason != UPDATE_REASON_NONE) {
        send_acceptance(packet, request_sequence, target_sequence, 2, reason);
        return;
    }
#if CONFIG_WASMHOME_SIGNED_UPDATE
    if (!update_lock || !update_queue || !update_task) {
        send_acceptance(packet, request_sequence, target_sequence, 2, UPDATE_REASON_BUSY);
        return;
    }
    xSemaphoreTake(update_lock, portMAX_DELAY);
    if (update_active) {
        const bool same_attempt =
            memcmp(active_request.attempt_id, packet + 8, 16) == 0;
        const bool exact_duplicate = same_attempt &&
            active_request.packet_length == length &&
            memcmp(active_request.packet, packet, length) == 0;
        xSemaphoreGive(update_lock);
        send_acceptance(packet, request_sequence, target_sequence,
                        exact_duplicate ? 1 : 2,
                        exact_duplicate ? UPDATE_REASON_NONE :
                        same_attempt ? UPDATE_REASON_ATTEMPT_CONFLICT : UPDATE_REASON_BUSY);
        return;
    }
    wh_signed_update_state_t state;
    esp_err_t err = wh_signed_update_get_state(WH_MODULE_UPDATE_SCENARIO,
                                               "gpio-control", campaign_id(), &state);
    wh_module_manager_info_t info;
    const bool info_ok = get_manager_snapshot(&info);
    if (err != ESP_OK || state.pending || state.accepted_floor >= WH_MODULE_UPDATE_SEQUENCE ||
        !info_ok || info.control.module_version != 1) {
        xSemaphoreGive(update_lock);
        send_acceptance(packet, request_sequence, target_sequence, 2,
                        err != ESP_OK ? UPDATE_REASON_BUSY :
                        state.pending ? UPDATE_REASON_BUSY : UPDATE_REASON_SEQUENCE_NOT_NEWER);
        return;
    }
    update_request_t request = {0};
    memcpy(request.packet, packet, length);
    request.packet_length = length;
    memcpy(request.attempt_id, packet + 8, sizeof(request.attempt_id));
    request.request_sequence = request_sequence;
    request.origin_boot_id = request_boot_id;
    request.target_sequence = target_sequence;
    update_active = true;
    active_request = request;
    /* The caller only observes acceptance after this UDP response succeeds.
       Queueing is deliberately after the ACK so TLS cannot start first. */
    const esp_err_t ack_status = send_acceptance(
        packet, request_sequence, target_sequence, 1, UPDATE_REASON_NONE);
    if (ack_status != ESP_OK) {
        update_active = false;
        memset(&active_request, 0, sizeof(active_request));
        xSemaphoreGive(update_lock);
        return;
    }
    const BaseType_t queued = xQueueSend(update_queue, &request, 0);
    if (queued != pdTRUE) {
        update_active = false;
        memset(&active_request, 0, sizeof(active_request));
        xSemaphoreGive(update_lock);
#if CONFIG_WASMHOME_SIGNED_UPDATE
        update_attempt_t failed_attempt = {
            .request = request,
            .failure = WH_SIGNED_UPDATE_FAILURE_SINK,
        };
        wh_module_manager_info_t failed_info;
        if (get_manager_snapshot(&failed_info)) {
            emit_attempt_event(&failed_attempt, WH_SIGNED_UPDATE_PHASE_UPDATE_FAILED,
                               failed_attempt.failure, NULL, NULL,
                               &failed_info.telemetry, &failed_info.control,
                               failed_info.telemetry_runtime_busy_drops, 0, 0, false);
        }
#endif
        return;
    }
    xSemaphoreGive(update_lock);
#else
    send_acceptance(packet, request_sequence, target_sequence, 2, UPDATE_REASON_UNSUPPORTED_COMPONENT);
#endif
}
