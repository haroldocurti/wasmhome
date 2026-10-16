#include "signed_update.h"

#include "esp_app_desc.h"
#include "esp_heap_caps.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_ota_ops.h"
#include "esp_partition.h"
#include "esp_sntp.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "mbedtls/constant_time.h"
#include "mbedtls/ecp.h"
#include "mbedtls/md.h"
#include "mbedtls/pk.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "platform.h"
#include "sdkconfig.h"

#include <inttypes.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

extern const uint8_t _binary_wasmhome_update_ca_pem_start[];
extern const uint8_t _binary_wasmhome_update_signing_key_der_start[];
extern const uint8_t _binary_wasmhome_update_signing_key_der_end[];

#define UPDATE_LEDGER_MAGIC 0x57485550u
#define UPDATE_LEDGER_VERSION 2u
#define UPDATE_POLICY_RECORDS 8u
#define UPDATE_HTTP_TIMEOUT_MS 10000
#define UPDATE_READ_CHUNK_BYTES 1024u
#define UPDATE_TIME_SYNC_TIMEOUT_MS 20000u

static SemaphoreHandle_t ledger_lock;
static portMUX_TYPE ledger_init_mux = portMUX_INITIALIZER_UNLOCKED;
static bool ledger_lock_initializing;
static bool sntp_started;

typedef struct {
    uint8_t used;
    uint8_t scenario;
    uint8_t campaign_sha256[32];
    uint8_t component_sha256[32];
    uint64_t accepted_floor;
    uint32_t active_slot;
    uint64_t active_sequence;
    uint32_t active_payload_size;
    uint16_t active_target_version;
    uint16_t reserved;
    uint8_t active_payload_sha256[32];
    uint8_t active_manifest_sha256[32];
    uint8_t active_attempt_id[16];
    uint64_t active_origin_boot_id;
} update_policy_record_t;

typedef struct {
    uint32_t magic;
    uint16_t version;
    uint16_t reserved;
    uint64_t generation;
    update_policy_record_t records[UPDATE_POLICY_RECORDS];
    uint8_t pending;
    uint8_t pending_phase;
    uint8_t pending_scenario;
    uint8_t pending_component_sha256[32];
    uint8_t pending_campaign_sha256[32];
    uint64_t pending_sequence;
    uint16_t pending_target_version;
    uint16_t pending_reserved;
    uint32_t pending_payload_size;
    uint32_t pending_slot;
    uint8_t pending_attempt_id[16];
    uint64_t origin_boot_id;
    char pending_release_version[WH_SIGNED_UPDATE_MAX_RELEASE_BYTES];
    uint8_t pending_payload_sha256[32];
    uint8_t pending_manifest_sha256[32];
    uint32_t crc32;
} update_ledger_t;

enum {
    PENDING_NONE = 0,
    PENDING_DOWNLOADING = 1,
    PENDING_STAGED = 2,
    PENDING_ACTIVATING = 3,
};

typedef struct {
    const uint8_t *data;
    size_t size;
    size_t offset;
} cbor_cursor_t;

typedef struct {
    wh_signed_update_manifest_t manifest;
    const uint8_t *protected_bytes;
    size_t protected_size;
    const uint8_t *manifest_bytes;
    size_t manifest_size;
    const uint8_t *signature;
    uint8_t envelope_sha256[32];
} parsed_envelope_t;

typedef struct {
    uint8_t scenario;
    uint16_t target_version;
    uint64_t expected_sequence;
    const uint8_t *attempt_id;
    const char *release_version;
    const wh_signed_update_sink_t *sink;
    wh_signed_update_event_fn event_fn;
    void *event_context;
    uint64_t origin_boot_id;
    uint64_t accepted_floor;
    uint32_t expected_bytes;
    uint32_t received_bytes;
    uint64_t event_elapsed_us_override;
    uint8_t payload_sha256[32];
    uint8_t manifest_sha256[32];
    bool manifest_known;
} update_operation_t;

static bool bytes_equal(const uint8_t *a, const uint8_t *b, size_t size)
{
    return a && b && mbedtls_ct_memcmp(a, b, size) == 0;
}

static bool bytes_nonzero(const uint8_t *value, size_t size)
{
    uint8_t combined = 0;
    for (size_t i = 0; i < size; ++i) combined |= value[i];
    return combined != 0;
}

static void hex_encode(const uint8_t *bytes, size_t size, char *out)
{
    static const char digits[] = "0123456789abcdef";
    for (size_t i = 0; i < size; ++i) {
        out[2 * i] = digits[bytes[i] >> 4];
        out[2 * i + 1] = digits[bytes[i] & 0x0f];
    }
    out[2 * size] = '\0';
}

static bool hash_sha256(const uint8_t *data, size_t size, uint8_t digest[32])
{
    const mbedtls_md_info_t *info = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
    mbedtls_md_context_t context;
    mbedtls_md_init(&context);
    const bool ok = info && mbedtls_md_setup(&context, info, 0) == 0 &&
        mbedtls_md_starts(&context) == 0 &&
        mbedtls_md_update(&context, data, size) == 0 &&
        mbedtls_md_finish(&context, digest) == 0;
    mbedtls_md_free(&context);
    if (!ok) memset(digest, 0, 32);
    return ok;
}

static const char *phase_status(wh_signed_update_phase_t phase,
                                wh_signed_update_failure_t failure)
{
    if (failure == WH_SIGNED_UPDATE_FAILURE_ENVELOPE ||
        failure == WH_SIGNED_UPDATE_FAILURE_SIGNATURE ||
        failure == WH_SIGNED_UPDATE_FAILURE_POLICY ||
        failure == WH_SIGNED_UPDATE_FAILURE_PAYLOAD_SIZE ||
        failure == WH_SIGNED_UPDATE_FAILURE_PAYLOAD_HASH) return "rejected";
    if (failure != WH_SIGNED_UPDATE_FAILURE_NONE ||
        phase == WH_SIGNED_UPDATE_PHASE_HEALTH_CHECK_FAILED ||
        phase == WH_SIGNED_UPDATE_PHASE_ROLLBACK_DETECTED) return "error";
    switch (phase) {
    case WH_SIGNED_UPDATE_PHASE_ACCEPTED:
        return "ok";
    case WH_SIGNED_UPDATE_PHASE_MANIFEST_DOWNLOAD_STARTED:
    case WH_SIGNED_UPDATE_PHASE_PAYLOAD_DOWNLOAD_STARTED:
    case WH_SIGNED_UPDATE_PHASE_PAYLOAD_DOWNLOAD_PROGRESS:
    case WH_SIGNED_UPDATE_PHASE_ACTIVATING:
    case WH_SIGNED_UPDATE_PHASE_BOOT_PENDING:
    case WH_SIGNED_UPDATE_PHASE_REBOOT_REQUESTED:
        return "in_progress";
    default:
        return "ok";
    }
}

const char *wh_signed_update_phase_name(wh_signed_update_phase_t phase)
{
    static const char *const names[] = {
        "accepted", "manifest_download_started", "manifest_downloaded",
        "signature_verified", "payload_download_started", "payload_download_progress",
        "payload_verified", "staged", "activating", "boot_pending",
        "health_check_passed", "health_check_failed", "rollback_detected",
        "activation_interrupted", "floor_committed", "reboot_requested", "candidate_loaded",
        "candidate_instantiated", "candidate_health_check", "control_activated",
        "previous_control_released", "update_failed",
    };
    return (unsigned)phase < (sizeof(names) / sizeof(names[0])) ? names[phase] : "unknown";
}

const char *wh_signed_update_failure_name(wh_signed_update_failure_t failure)
{
    static const char *const names[] = {
        "none", "argument", "transport", "http", "envelope", "signature",
        "policy", "persistence", "payload_size", "payload_hash", "sink",
    };
    return (unsigned)failure < (sizeof(names) / sizeof(names[0])) ? names[failure] : "unknown";
}

static esp_err_t render_signed_update_event(const wh_signed_update_event_t *event,
                                            char *line, size_t capacity,
                                            size_t *line_size)
{
    if (line_size) *line_size = 0;
    if (!event || ((line == NULL) != (line_size == NULL)) ||
        (line && capacity == 0) || (!line && capacity != 0))
        return ESP_ERR_INVALID_ARG;
    char attempt[33] = {0};
    char payload_hash[65] = {0};
    char running_hash[65] = {0};
    char manifest_hash[65] = {0};
    char telemetry_hash[65] = {0};
    char control_hash[65] = {0};
    char attempt_value[36] = "null";
    char payload_hash_value[68] = "null";
    char running_hash_value[68] = "null";
    char manifest_hash_value[68] = "null";
    char telemetry_hash_value[68] = "null";
    char control_hash_value[68] = "null";
    char release_value[WH_SIGNED_UPDATE_MAX_RELEASE_BYTES + 3] = "null";
    char psram_free_value[24] = "null";
    char psram_largest_value[24] = "null";
    char control_version_value[8] = "null";
    if (event->attempt_id) hex_encode(event->attempt_id, 16, attempt);
    if (event->payload_sha256) hex_encode(event->payload_sha256, 32, payload_hash);
    if (event->running_sha256) hex_encode(event->running_sha256, 32, running_hash);
    if (event->manifest_sha256) hex_encode(event->manifest_sha256, 32, manifest_hash);
    if (event->telemetry_sha256) hex_encode(event->telemetry_sha256, 32, telemetry_hash);
    if (event->control_sha256) hex_encode(event->control_sha256, 32, control_hash);
    if (event->attempt_id) snprintf(attempt_value, sizeof(attempt_value), "\"%s\"", attempt);
    if (event->payload_sha256) snprintf(payload_hash_value, sizeof(payload_hash_value), "\"%s\"", payload_hash);
    if (event->running_sha256) snprintf(running_hash_value, sizeof(running_hash_value), "\"%s\"", running_hash);
    if (event->manifest_sha256) snprintf(manifest_hash_value, sizeof(manifest_hash_value), "\"%s\"", manifest_hash);
    if (event->telemetry_sha256) snprintf(telemetry_hash_value, sizeof(telemetry_hash_value), "\"%s\"", telemetry_hash);
    if (event->control_sha256) snprintf(control_hash_value, sizeof(control_hash_value), "\"%s\"", control_hash);
    if (event->release_version) snprintf(release_value, sizeof(release_value), "\"%s\"", event->release_version);
    if (event->control_module_version)
        snprintf(control_version_value, sizeof(control_version_value), "%u",
                 event->control_module_version);
    const size_t internal_free = heap_caps_get_free_size(MALLOC_CAP_INTERNAL);
    const size_t internal_largest = heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL);
#if CONFIG_SPIRAM
    const size_t psram_free = heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
    const size_t psram_largest = heap_caps_get_largest_free_block(MALLOC_CAP_SPIRAM);
    snprintf(psram_free_value, sizeof(psram_free_value), "%zu", psram_free);
    snprintf(psram_largest_value, sizeof(psram_largest_value), "%zu", psram_largest);
#else
    (void)psram_free_value;
    (void)psram_largest_value;
#endif
#define WH_SIGNED_UPDATE_EVENT_ARGUMENTS \
           wh_signed_update_phase_name(event->phase), wh_signed_update_phase_name(event->phase), \
           phase_status(event->phase, event->failure), wh_signed_update_failure_name(event->failure), \
           attempt_value, wh_platform_boot_id(), event->origin_boot_id, \
           event->accepted_floor, event->ledger_readback_sequence, \
           event->scenario, event->component_id, \
           event->target_version, release_value, event->sequence, \
           event->payload_bytes_expected, event->payload_bytes_received, \
           event->current_task_stack_high_water_bytes, payload_hash_value, \
           running_hash_value, manifest_hash_value, event->elapsed_us, \
           event->elapsed_scope ? event->elapsed_scope : "attempt", internal_free, \
           internal_largest, psram_free_value, psram_largest_value, \
           event->telemetry_generation, telemetry_hash_value, \
           event->telemetry_runtime_busy_drops, event->control_generation, \
           control_version_value, control_hash_value, \
           event->health_reconciled ? "true" : "false"
#define WH_SIGNED_UPDATE_EVENT_REST \
           "\"failure\":\"%s\",\"attempt_id\":%s,\"boot_id\":\"%016" PRIx64 "\"," \
           "\"origin_boot_id\":\"%016" PRIx64 "\",\"accepted_floor\":%" PRIu64 "," \
           "\"ledger_readback_sequence\":%" PRIu64 "," \
           "\"scenario\":%u,\"component\":%u,\"target_version\":%u,\"release_version\":%s," \
           "\"sequence\":%" PRIu64 ",\"payload_bytes_expected\":%" PRIu32 "," \
           "\"payload_bytes_received\":%" PRIu32 "," \
           "\"current_task_stack_high_water_bytes\":%" PRIu32 "," \
           "\"payload_sha256\":%s," \
           "\"running_sha256\":%s," \
           "\"manifest_sha256\":%s,\"elapsed_us\":%" PRIu64 "," \
           "\"elapsed_scope\":\"%s\",\"heap\":{\"internal_free_bytes\":%zu," \
           "\"internal_largest_free_block_bytes\":%zu,\"psram_free_bytes\":%s," \
           "\"psram_largest_free_block_bytes\":%s},\"telemetry_generation\":%" PRIu64 "," \
           "\"telemetry_sha256\":%s,\"telemetry_runtime_busy_drops\":%" PRIu64 "," \
           "\"control_generation\":%" PRIu64 "," \
           "\"control_module_version\":%s,\"control_sha256\":%s," \
           "\"health_reconciled\":%s}\n"
    int written;
    if (line) {
        written = snprintf(line, capacity,
           "WASMHOME_UPDATE,{\"event\":\"%s\",\"phase\":\"%s\",\"status\":\"%s\","
           WH_SIGNED_UPDATE_EVENT_REST,
           WH_SIGNED_UPDATE_EVENT_ARGUMENTS);
    } else {
        printf("WASMHOME_UPDATE,{\"event\":\"%s\",\"phase\":\"%s\",\"status\":\"%s\","
               WH_SIGNED_UPDATE_EVENT_REST,
               WH_SIGNED_UPDATE_EVENT_ARGUMENTS);
        return ESP_OK;
    }
#undef WH_SIGNED_UPDATE_EVENT_ARGUMENTS
#undef WH_SIGNED_UPDATE_EVENT_REST
    if (written < 0 || (size_t)written >= capacity)
        return ESP_ERR_INVALID_SIZE;
    *line_size = (size_t)written;
    return ESP_OK;
}

esp_err_t wh_signed_update_format_event(const wh_signed_update_event_t *event,
                                       char *line, size_t capacity,
                                       size_t *line_size)
{
    if (line_size) *line_size = 0;
    if (!line || capacity == 0 || !line_size) return ESP_ERR_INVALID_ARG;
    return render_signed_update_event(event, line, capacity, line_size);
}

void wh_signed_update_emit_event(const wh_signed_update_event_t *event)
{
    (void)render_signed_update_event(event, NULL, 0, NULL);
}

static uint32_t crc32_bytes(const uint8_t *data, size_t size)
{
    uint32_t crc = 0xffffffffu;
    for (size_t i = 0; i < size; ++i) {
        crc ^= data[i];
        for (unsigned bit = 0; bit < 8; ++bit)
            crc = (crc >> 1) ^ (0xedb88320u & (uint32_t)-(int32_t)(crc & 1u));
    }
    return ~crc;
}

static uint32_t ledger_crc(const update_ledger_t *ledger)
{
    return crc32_bytes((const uint8_t *)ledger, offsetof(update_ledger_t, crc32));
}

static SemaphoreHandle_t get_ledger_lock(void)
{
    for (;;) {
        bool create = false;
        portENTER_CRITICAL(&ledger_init_mux);
        if (ledger_lock) {
            SemaphoreHandle_t result = ledger_lock;
            portEXIT_CRITICAL(&ledger_init_mux);
            return result;
        }
        if (!ledger_lock_initializing) {
            ledger_lock_initializing = true;
            create = true;
        }
        portEXIT_CRITICAL(&ledger_init_mux);
        if (create) {
            SemaphoreHandle_t created = xSemaphoreCreateMutex();
            portENTER_CRITICAL(&ledger_init_mux);
            ledger_lock = created;
            ledger_lock_initializing = false;
            portEXIT_CRITICAL(&ledger_init_mux);
            return created;
        }
        vTaskDelay(1);
    }
}

static esp_err_t ledger_load(update_ledger_t *ledger)
{
    if (!ledger) return ESP_ERR_INVALID_ARG;
    memset(ledger, 0, sizeof(*ledger));
    nvs_handle_t handle;
    esp_err_t err = nvs_open("wh_update", NVS_READWRITE, &handle);
    if (err != ESP_OK) return err;
    size_t size = sizeof(*ledger);
    err = nvs_get_blob(handle, "ledger", ledger, &size);
    nvs_close(handle);
    if (err == ESP_ERR_NVS_NOT_FOUND) {
        memset(ledger, 0, sizeof(*ledger));
        ledger->magic = UPDATE_LEDGER_MAGIC;
        ledger->version = UPDATE_LEDGER_VERSION;
        ledger->crc32 = ledger_crc(ledger);
        return ESP_OK;
    }
    if (err != ESP_OK) return err;
    if (size != sizeof(*ledger) || ledger->magic != UPDATE_LEDGER_MAGIC ||
        ledger->version != UPDATE_LEDGER_VERSION || ledger->reserved != 0 ||
        ledger->pending > 1 || ledger->crc32 != ledger_crc(ledger))
        return ESP_ERR_INVALID_CRC;
    return ESP_OK;
}

static esp_err_t ledger_store(update_ledger_t *ledger)
{
    if (!ledger) return ESP_ERR_INVALID_ARG;
    update_ledger_t *check = heap_caps_malloc(sizeof(*check), MALLOC_CAP_8BIT);
    if (!check) return ESP_ERR_NO_MEM;
    nvs_handle_t handle;
    esp_err_t err = nvs_open("wh_update", NVS_READWRITE, &handle);
    if (err != ESP_OK) {
        heap_caps_free(check);
        return err;
    }
    ledger->magic = UPDATE_LEDGER_MAGIC;
    ledger->version = UPDATE_LEDGER_VERSION;
    ledger->reserved = 0;
    ledger->generation++;
    ledger->crc32 = ledger_crc(ledger);
    err = nvs_set_blob(handle, "ledger", ledger, sizeof(*ledger));
    esp_err_t commit_err = err == ESP_OK ? nvs_commit(handle) : err;
    nvs_close(handle);

    /* A commit can become durable before NVS reports an I/O error. Resolve
       that ambiguity through a fresh handle before any sink can erase/write. */
    nvs_handle_t verify_handle;
    esp_err_t verify_err = nvs_open("wh_update", NVS_READONLY, &verify_handle);
    if (verify_err == ESP_OK) {
        size_t size = sizeof(*check);
        verify_err = nvs_get_blob(verify_handle, "ledger", check, &size);
        if (verify_err == ESP_OK && size == sizeof(*check) &&
            memcmp(check, ledger, sizeof(*check)) == 0) {
            nvs_close(verify_handle);
            heap_caps_free(check);
            return ESP_OK;
        }
        nvs_close(verify_handle);
    }
    heap_caps_free(check);
    if (commit_err != ESP_OK) return commit_err;
    return verify_err == ESP_OK ? ESP_ERR_INVALID_CRC : verify_err;
}

static bool namespace_hashes(const char *campaign, uint8_t scenario,
                             const char *component, uint8_t campaign_hash[32],
                             uint8_t component_hash[32])
{
    if (!campaign || !component || !hash_sha256((const uint8_t *)campaign,
            strlen(campaign), campaign_hash) ||
        !hash_sha256((const uint8_t *)component, strlen(component), component_hash))
        return false;
    (void)scenario;
    return true;
}

static int policy_index(update_ledger_t *ledger, uint8_t scenario,
                        const uint8_t campaign_hash[32],
                        const uint8_t component_hash[32], bool create)
{
    int free_index = -1;
    for (size_t i = 0; i < UPDATE_POLICY_RECORDS; ++i) {
        update_policy_record_t *record = &ledger->records[i];
        if (!record->used) {
            if (free_index < 0) free_index = (int)i;
            continue;
        }
        if (record->scenario == scenario &&
            bytes_equal(record->campaign_sha256, campaign_hash, 32) &&
            bytes_equal(record->component_sha256, component_hash, 32)) return (int)i;
    }
    if (!create || free_index < 0) return -1;
    update_policy_record_t *record = &ledger->records[free_index];
    memset(record, 0, sizeof(*record));
    record->used = 1;
    record->scenario = scenario;
    record->active_slot = WH_SIGNED_UPDATE_NO_ACTIVE_SLOT;
    memcpy(record->campaign_sha256, campaign_hash, 32);
    memcpy(record->component_sha256, component_hash, 32);
    return free_index;
}

static bool pending_matches(const update_ledger_t *ledger,
                            const wh_signed_update_manifest_t *manifest)
{
    uint8_t campaign_hash[32];
    uint8_t component_hash[32];
    if (!namespace_hashes(manifest->campaign_id, manifest->scenario, manifest->component,
                          campaign_hash, component_hash)) return false;
    return ledger->pending && ledger->pending_scenario == manifest->scenario &&
        ledger->pending_sequence == manifest->sequence &&
        ledger->pending_payload_size == manifest->payload_size &&
        bytes_equal(ledger->pending_campaign_sha256, campaign_hash, 32) &&
        bytes_equal(ledger->pending_component_sha256, component_hash, 32) &&
        bytes_equal(ledger->pending_manifest_sha256, manifest->manifest_sha256, 32) &&
        bytes_equal(ledger->pending_payload_sha256, manifest->payload_sha256, 32);
}

static esp_err_t ledger_prepare(const wh_signed_update_manifest_t *manifest,
                                const uint8_t attempt_id[16])
{
    uint8_t campaign_hash[32];
    uint8_t component_hash[32];
    if (!namespace_hashes(manifest->campaign_id, manifest->scenario, manifest->component,
                          campaign_hash, component_hash)) return ESP_FAIL;
    SemaphoreHandle_t lock = get_ledger_lock();
    if (!lock) return ESP_ERR_NO_MEM;
    xSemaphoreTake(lock, portMAX_DELAY);
    update_ledger_t ledger;
    esp_err_t err = ledger_load(&ledger);
    if (err == ESP_OK) {
        int index = policy_index(&ledger, manifest->scenario, campaign_hash,
                                 component_hash, true);
        if (index < 0) err = ESP_ERR_NO_MEM;
        else if (ledger.pending) err = ESP_ERR_INVALID_STATE;
        else if (manifest->sequence <= ledger.records[index].accepted_floor)
            err = ESP_ERR_INVALID_VERSION;
        else {
            ledger.pending = 1;
            ledger.pending_phase = PENDING_DOWNLOADING;
            ledger.pending_scenario = manifest->scenario;
            memcpy(ledger.pending_campaign_sha256, campaign_hash, 32);
            memcpy(ledger.pending_component_sha256, component_hash, 32);
            ledger.pending_sequence = manifest->sequence;
            ledger.pending_target_version = strcmp(manifest->release_version, "v1") == 0 ? 1 : 2;
            ledger.pending_payload_size = manifest->payload_size;
            ledger.pending_slot = WH_SIGNED_UPDATE_NO_ACTIVE_SLOT;
            if (attempt_id) memcpy(ledger.pending_attempt_id, attempt_id, 16);
            ledger.origin_boot_id = wh_platform_boot_id();
            strncpy(ledger.pending_release_version, manifest->release_version,
                    sizeof(ledger.pending_release_version) - 1);
            memcpy(ledger.pending_payload_sha256, manifest->payload_sha256, 32);
            memcpy(ledger.pending_manifest_sha256, manifest->manifest_sha256, 32);
            err = ledger_store(&ledger);
            if (err != ESP_OK) {
                /* Resolve an ambiguous commit before the caller may begin the
                   sink. The sentinel proves no slot was reserved and no
                   destructive write could have started in this attempt. */
                update_ledger_t observed;
                esp_err_t read_err = ledger_load(&observed);
                if (read_err == ESP_OK && pending_matches(&observed, manifest) &&
                    observed.pending_phase == PENDING_DOWNLOADING &&
                    observed.pending_slot == WH_SIGNED_UPDATE_NO_ACTIVE_SLOT &&
                    (!attempt_id || bytes_equal(observed.pending_attempt_id,
                                                attempt_id, 16)))
                    err = ESP_OK;
            }
        }
    }
    xSemaphoreGive(lock);
    return err;
}

static esp_err_t ledger_pending_change(const wh_signed_update_manifest_t *manifest,
                                       uint32_t slot_id, uint8_t phase)
{
    SemaphoreHandle_t lock = get_ledger_lock();
    if (!lock) return ESP_ERR_NO_MEM;
    xSemaphoreTake(lock, portMAX_DELAY);
    update_ledger_t ledger;
    esp_err_t err = ledger_load(&ledger);
    if (err == ESP_OK && !pending_matches(&ledger, manifest)) err = ESP_ERR_INVALID_STATE;
    if (err == ESP_OK) {
        if (phase == PENDING_DOWNLOADING) {
            if (ledger.pending_phase != PENDING_DOWNLOADING ||
                ledger.pending_slot != WH_SIGNED_UPDATE_NO_ACTIVE_SLOT ||
                slot_id == WH_SIGNED_UPDATE_NO_ACTIVE_SLOT)
                err = ESP_ERR_INVALID_STATE;
        } else if (phase == PENDING_STAGED) {
            if (ledger.pending_phase != PENDING_DOWNLOADING ||
                ledger.pending_slot != slot_id) err = ESP_ERR_INVALID_STATE;
        } else if (phase == PENDING_ACTIVATING) {
            if (ledger.pending_phase != PENDING_STAGED ||
                ledger.pending_slot != slot_id) err = ESP_ERR_INVALID_STATE;
        } else {
            err = ESP_ERR_INVALID_ARG;
        }
    }
    if (err == ESP_OK) {
        ledger.pending_phase = phase;
        ledger.pending_slot = slot_id;
        err = ledger_store(&ledger);
    }
    xSemaphoreGive(lock);
    return err;
}

esp_err_t wh_signed_update_mark_staged(const wh_signed_update_manifest_t *manifest,
                                       uint32_t slot_id)
{
    if (!manifest) return ESP_ERR_INVALID_ARG;
    esp_err_t err = ledger_pending_change(manifest, slot_id, PENDING_STAGED);
    return err;
}

esp_err_t wh_signed_update_mark_activating(const wh_signed_update_manifest_t *manifest,
                                           uint32_t slot_id)
{
    if (!manifest) return ESP_ERR_INVALID_ARG;
    esp_err_t err = ledger_pending_change(manifest, slot_id, PENDING_ACTIVATING);
    return err;
}

esp_err_t wh_signed_update_commit(const wh_signed_update_manifest_t *manifest,
                                  uint32_t slot_id)
{
    if (!manifest) return ESP_ERR_INVALID_ARG;
    uint8_t campaign_hash[32];
    uint8_t component_hash[32];
    if (!namespace_hashes(manifest->campaign_id, manifest->scenario, manifest->component,
                          campaign_hash, component_hash)) return ESP_FAIL;
    SemaphoreHandle_t lock = get_ledger_lock();
    if (!lock) return ESP_ERR_NO_MEM;
    xSemaphoreTake(lock, portMAX_DELAY);
    update_ledger_t ledger;
    esp_err_t err = ledger_load(&ledger);
    if (err == ESP_OK && (!pending_matches(&ledger, manifest) ||
        ledger.pending_phase != PENDING_ACTIVATING || ledger.pending_slot != slot_id))
        err = ESP_ERR_INVALID_STATE;
    if (err == ESP_OK) {
        int index = policy_index(&ledger, manifest->scenario, campaign_hash,
                                 component_hash, false);
        if (index < 0 || manifest->sequence <= ledger.records[index].accepted_floor)
            err = ESP_ERR_INVALID_VERSION;
        else {
            ledger.records[index].accepted_floor = manifest->sequence;
            ledger.records[index].active_slot = slot_id;
            ledger.records[index].active_sequence = manifest->sequence;
            ledger.records[index].active_payload_size = manifest->payload_size;
            ledger.records[index].active_target_version =
                strcmp(manifest->release_version, "v1") == 0 ? 1 : 2;
            memcpy(ledger.records[index].active_attempt_id, ledger.pending_attempt_id, 16);
            ledger.records[index].active_origin_boot_id = ledger.origin_boot_id;
            memcpy(ledger.records[index].active_payload_sha256, manifest->payload_sha256, 32);
            memcpy(ledger.records[index].active_manifest_sha256, manifest->manifest_sha256, 32);
            ledger.pending = 0;
            ledger.pending_phase = PENDING_NONE;
            ledger.pending_scenario = 0;
            ledger.pending_sequence = 0;
            ledger.pending_target_version = 0;
            ledger.pending_payload_size = 0;
            ledger.pending_slot = 0;
            ledger.origin_boot_id = 0;
            memset(ledger.pending_campaign_sha256, 0, 32);
            memset(ledger.pending_component_sha256, 0, 32);
            memset(ledger.pending_attempt_id, 0, 16);
            memset(ledger.pending_release_version, 0, sizeof(ledger.pending_release_version));
            memset(ledger.pending_payload_sha256, 0, 32);
            memset(ledger.pending_manifest_sha256, 0, 32);
            err = ledger_store(&ledger);
        }
    }
    xSemaphoreGive(lock);
    return err;
}

esp_err_t wh_signed_update_abort(const wh_signed_update_manifest_t *manifest)
{
    if (!manifest) return ESP_ERR_INVALID_ARG;
    SemaphoreHandle_t lock = get_ledger_lock();
    if (!lock) return ESP_ERR_NO_MEM;
    xSemaphoreTake(lock, portMAX_DELAY);
    update_ledger_t ledger;
    esp_err_t err = ledger_load(&ledger);
    if (err == ESP_OK && !pending_matches(&ledger, manifest)) err = ESP_ERR_INVALID_STATE;
    if (err == ESP_OK) {
        ledger.pending = 0;
        ledger.pending_phase = PENDING_NONE;
        ledger.pending_scenario = 0;
        ledger.pending_sequence = 0;
        ledger.pending_target_version = 0;
        ledger.pending_payload_size = 0;
        ledger.pending_slot = 0;
        ledger.origin_boot_id = 0;
        memset(ledger.pending_campaign_sha256, 0, 32);
        memset(ledger.pending_component_sha256, 0, 32);
        memset(ledger.pending_attempt_id, 0, 16);
        memset(ledger.pending_release_version, 0, sizeof(ledger.pending_release_version));
        memset(ledger.pending_payload_sha256, 0, 32);
        memset(ledger.pending_manifest_sha256, 0, 32);
        err = ledger_store(&ledger);
    }
    xSemaphoreGive(lock);
    return err;
}

esp_err_t wh_signed_update_get_state(uint8_t scenario, const char *component,
                                     const char *campaign_id,
                                     wh_signed_update_state_t *state_out)
{
    if (!scenario || !component || !campaign_id || !state_out) return ESP_ERR_INVALID_ARG;
    memset(state_out, 0, sizeof(*state_out));
    uint8_t campaign_hash[32];
    uint8_t component_hash[32];
    if (!namespace_hashes(campaign_id, scenario, component, campaign_hash, component_hash))
        return ESP_FAIL;
    SemaphoreHandle_t lock = get_ledger_lock();
    if (!lock) return ESP_ERR_NO_MEM;
    xSemaphoreTake(lock, portMAX_DELAY);
    update_ledger_t *ledger = heap_caps_malloc(sizeof(*ledger), MALLOC_CAP_8BIT);
    if (!ledger) {
        xSemaphoreGive(lock);
        return ESP_ERR_NO_MEM;
    }
    esp_err_t err = ledger_load(ledger);
    if (err == ESP_OK) {
        state_out->scenario = scenario;
        strncpy(state_out->component, component, sizeof(state_out->component) - 1);
        state_out->active_slot = WH_SIGNED_UPDATE_NO_ACTIVE_SLOT;
        int index = policy_index(ledger, scenario, campaign_hash, component_hash, false);
        if (index >= 0) {
            state_out->accepted_floor = ledger->records[index].accepted_floor;
            state_out->active_slot = ledger->records[index].active_slot;
            state_out->active_sequence = ledger->records[index].active_sequence;
            state_out->active_payload_size = ledger->records[index].active_payload_size;
            state_out->active_target_version = ledger->records[index].active_target_version;
            memcpy(state_out->active_attempt_id,
                   ledger->records[index].active_attempt_id, 16);
            state_out->active_origin_boot_id =
                ledger->records[index].active_origin_boot_id;
            if (state_out->active_target_version == 1)
                strncpy(state_out->active_release_version, "v1",
                        sizeof(state_out->active_release_version) - 1);
            else if (state_out->active_target_version == 2)
                strncpy(state_out->active_release_version, "v2",
                        sizeof(state_out->active_release_version) - 1);
            memcpy(state_out->active_payload_sha256,
                   ledger->records[index].active_payload_sha256, 32);
            memcpy(state_out->active_manifest_sha256,
                   ledger->records[index].active_manifest_sha256, 32);
        }
        if (ledger->pending && ledger->pending_scenario == scenario &&
            bytes_equal(ledger->pending_campaign_sha256, campaign_hash, 32) &&
            bytes_equal(ledger->pending_component_sha256, component_hash, 32)) {
            state_out->pending = true;
            state_out->pending_phase = ledger->pending_phase;
            state_out->pending_sequence = ledger->pending_sequence;
            state_out->pending_slot = ledger->pending_slot;
            state_out->pending_target_version = ledger->pending_target_version;
            state_out->pending_payload_size = ledger->pending_payload_size;
            memcpy(state_out->pending_attempt_id, ledger->pending_attempt_id, 16);
            state_out->origin_boot_id = ledger->origin_boot_id;
            strncpy(state_out->pending_release_version, ledger->pending_release_version,
                    sizeof(state_out->pending_release_version) - 1);
            memcpy(state_out->pending_payload_sha256, ledger->pending_payload_sha256, 32);
            memcpy(state_out->pending_manifest_sha256, ledger->pending_manifest_sha256, 32);
        }
    }
    xSemaphoreGive(lock);
    heap_caps_free(ledger);
    return err;
}

static bool cbor_read_head(cbor_cursor_t *cursor, uint8_t expected_major,
                           uint64_t *value)
{
    if (!cursor || cursor->offset >= cursor->size) return false;
    const uint8_t initial = cursor->data[cursor->offset++];
    const uint8_t major = initial >> 5;
    const uint8_t additional = initial & 0x1fu;
    if (major != expected_major) return false;
    uint64_t result;
    if (additional < 24) result = additional;
    else {
        unsigned width;
        if (additional == 24) width = 1;
        else if (additional == 25) width = 2;
        else if (additional == 26) width = 4;
        else if (additional == 27) width = 8;
        else return false;
        if (cursor->offset > cursor->size || width > cursor->size - cursor->offset)
            return false;
        result = 0;
        for (unsigned i = 0; i < width; ++i)
            result = (result << 8) | cursor->data[cursor->offset++];
        const uint64_t minimum = width == 1 ? 24u : width == 2 ? 256u :
                                 width == 4 ? 65536u : 4294967296ULL;
        if (result < minimum) return false;
    }
    if (value) *value = result;
    return true;
}

static bool cbor_read_bytes(cbor_cursor_t *cursor, uint8_t major,
                            const uint8_t **bytes, size_t *size)
{
    uint64_t length;
    if (!cbor_read_head(cursor, major, &length) || length > SIZE_MAX ||
        cursor->offset > cursor->size || (size_t)length > cursor->size - cursor->offset)
        return false;
    if (bytes) *bytes = cursor->data + cursor->offset;
    if (size) *size = (size_t)length;
    cursor->offset += (size_t)length;
    return true;
}

static bool cbor_read_text(cbor_cursor_t *cursor, char *out, size_t capacity)
{
    const uint8_t *bytes;
    size_t size;
    if (!cbor_read_bytes(cursor, 3, &bytes, &size) || !size || size >= capacity ||
        memchr(bytes, 0, size)) return false;
    for (size_t i = 0; i < size; ++i) {
        if (bytes[i] < 0x20 || bytes[i] > 0x7e) return false;
    }
    memcpy(out, bytes, size);
    out[size] = '\0';
    return true;
}

static bool cbor_read_uint(cbor_cursor_t *cursor, uint64_t expected_key,
                           uint64_t *value)
{
    uint64_t key;
    return cbor_read_head(cursor, 0, &key) && key == expected_key &&
           cbor_read_head(cursor, 0, value);
}

static bool cbor_read_manifest(const uint8_t *data, size_t size,
                               wh_signed_update_manifest_t *manifest)
{
    if (!data || !manifest) return false;
    memset(manifest, 0, sizeof(*manifest));
    cbor_cursor_t cursor = {.data = data, .size = size};
    uint64_t count;
    uint64_t value;
    const uint8_t *bytes;
    size_t bytes_size;
    if (!cbor_read_head(&cursor, 5, &count) || count != 12) return false;
    if (!cbor_read_uint(&cursor, 1, &value) || value != 1) return false;
    if (!cbor_read_head(&cursor, 0, &value) || value != 2 ||
        !cbor_read_text(&cursor, manifest->campaign_id, sizeof(manifest->campaign_id))) return false;
    if (!cbor_read_uint(&cursor, 3, &value) || value < 1 || value > 3) return false;
    manifest->scenario = (uint8_t)value;
    if (!cbor_read_uint(&cursor, 4, &value) || value != manifest->scenario) return false;
    manifest->artifact_kind = (uint8_t)value;
    if (!cbor_read_head(&cursor, 0, &value) || value != 5 ||
        !cbor_read_text(&cursor, manifest->component, sizeof(manifest->component))) return false;
    if (!cbor_read_head(&cursor, 0, &value) || value != 6) return false;
    if (!cbor_read_text(&cursor, manifest->release_version,
                        sizeof(manifest->release_version))) return false;
    if (!cbor_read_uint(&cursor, 7, &manifest->sequence) || !manifest->sequence) return false;
    if (!cbor_read_uint(&cursor, 8, &value) || value > UINT32_MAX) return false;
    manifest->payload_size = (uint32_t)value;
    if (!cbor_read_head(&cursor, 0, &value) || value != 9 ||
        !cbor_read_bytes(&cursor, 2, &bytes, &bytes_size) || bytes_size != 32) return false;
    memcpy(manifest->payload_sha256, bytes, 32);
    if (!cbor_read_head(&cursor, 0, &value) || value != 10) return false;
    if (!cbor_read_text(&cursor, manifest->target, sizeof(manifest->target))) return false;
    if (!cbor_read_uint(&cursor, 11, &value) || value > UINT32_MAX) return false;
    manifest->base_api_version = (uint32_t)value;
    /* Re-read key 12 explicitly; its value is the nullable toolchain string. */
    if (!cbor_read_head(&cursor, 0, &value) || value != 12 || cursor.offset >= cursor.size)
        return false;
    if (cursor.data[cursor.offset] == 0xf6) {
        cursor.offset++;
        manifest->toolchain_id[0] = '\0';
    } else if (!cbor_read_text(&cursor, manifest->toolchain_id,
                               sizeof(manifest->toolchain_id))) return false;
    return cursor.offset == cursor.size;
}

static bool cbor_write_head(uint8_t *out, size_t capacity, size_t *offset,
                            uint8_t major, uint64_t value)
{
    if (!out || !offset || *offset >= capacity) return false;
    if (value < 24) {
        out[(*offset)++] = (uint8_t)((major << 5) | value);
        return true;
    }
    unsigned width = value <= UINT8_MAX ? 1 : value <= UINT16_MAX ? 2 :
                     value <= UINT32_MAX ? 4 : 8;
    if (capacity - *offset < width + 1) return false;
    out[(*offset)++] = (uint8_t)((major << 5) |
        (width == 1 ? 24 : width == 2 ? 25 : width == 4 ? 26 : 27));
    for (unsigned i = 0; i < width; ++i)
        out[(*offset)++] = (uint8_t)(value >> ((width - i - 1) * 8));
    return true;
}

static bool der_append_integer(const uint8_t raw[32], uint8_t *out,
                               size_t capacity, size_t *offset)
{
    size_t first = 0;
    while (first < 31 && raw[first] == 0) first++;
    const size_t length = 32 - first;
    const bool prefix = (raw[first] & 0x80u) != 0;
    const size_t required = 2 + length + (prefix ? 1u : 0u);
    if (*offset + required > capacity) return false;
    out[(*offset)++] = 0x02;
    out[(*offset)++] = (uint8_t)(length + (prefix ? 1u : 0u));
    if (prefix) out[(*offset)++] = 0;
    memcpy(out + *offset, raw + first, length);
    *offset += length;
    return true;
}

static bool raw_ecdsa_to_der(const uint8_t raw[64], uint8_t der[72], size_t *der_size)
{
    static const uint8_t order[32] = {
        0xff,0xff,0xff,0xff,0x00,0x00,0x00,0x00,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,
        0xbc,0xe6,0xfa,0xad,0xa7,0x17,0x9e,0x84,0xf3,0xb9,0xca,0xc2,0xfc,0x63,0x25,0x51,
    };
    static const uint8_t half_order[32] = {
        0x7f,0xff,0xff,0xff,0x80,0x00,0x00,0x00,0x7f,0xff,0xff,0xff,0xff,0xff,0xff,0xff,
        0xde,0x73,0x7d,0x56,0xd3,0x8b,0xcf,0x42,0x79,0xdc,0xe5,0x61,0x7e,0x31,0x92,0xa8,
    };
    const uint8_t *r = raw;
    const uint8_t *s = raw + 32;
    if (!bytes_nonzero(r, 32) || !bytes_nonzero(s, 32) ||
        memcmp(r, order, 32) >= 0 || memcmp(s, order, 32) >= 0 ||
        memcmp(s, half_order, 32) > 0) return false;
    size_t offset = 2;
    if (!der_append_integer(r, der, 72, &offset) ||
        !der_append_integer(s, der, 72, &offset) || offset - 2 > 127) return false;
    der[0] = 0x30;
    der[1] = (uint8_t)(offset - 2);
    *der_size = offset;
    return true;
}

static bool verify_cose_signature(const parsed_envelope_t *parsed)
{
    uint8_t sig_structure[WH_SIGNED_UPDATE_MAX_ENVELOPE_BYTES + 32u];
    size_t offset = 0;
    const char context[] = "Signature1";
    if (!cbor_write_head(sig_structure, sizeof(sig_structure), &offset, 4, 4) ||
        !cbor_write_head(sig_structure, sizeof(sig_structure), &offset, 3,
                         sizeof(context) - 1)) return false;
    memcpy(sig_structure + offset, context, sizeof(context) - 1);
    offset += sizeof(context) - 1;
    if (!cbor_write_head(sig_structure, sizeof(sig_structure), &offset, 2,
                         parsed->protected_size)) return false;
    if (parsed->protected_size > sizeof(sig_structure) - offset) return false;
    memcpy(sig_structure + offset, parsed->protected_bytes, parsed->protected_size);
    offset += parsed->protected_size;
    if (!cbor_write_head(sig_structure, sizeof(sig_structure), &offset, 2, 0) ||
        !cbor_write_head(sig_structure, sizeof(sig_structure), &offset, 2,
                         parsed->manifest_size) ||
        parsed->manifest_size > sizeof(sig_structure) - offset)
        return false;
    memcpy(sig_structure + offset, parsed->manifest_bytes, parsed->manifest_size);
    offset += parsed->manifest_size;

    uint8_t digest[32];
    uint8_t der_signature[72];
    size_t der_signature_size = 0;
    if (!hash_sha256(sig_structure, offset, digest)) return false;
    if (!raw_ecdsa_to_der(parsed->signature, der_signature, &der_signature_size)) return false;

    mbedtls_pk_context key;
    mbedtls_pk_init(&key);
    const size_t key_size = (size_t)(_binary_wasmhome_update_signing_key_der_end -
                                    _binary_wasmhome_update_signing_key_der_start);
    int result = mbedtls_pk_parse_public_key(&key,
                    _binary_wasmhome_update_signing_key_der_start, key_size);
    bool verified = false;
    if (result == 0 && mbedtls_pk_can_do(&key, MBEDTLS_PK_ECKEY)) {
        const mbedtls_ecp_keypair *ec = mbedtls_pk_ec(key);
        if (ec && ec->MBEDTLS_PRIVATE(grp).id == MBEDTLS_ECP_DP_SECP256R1 &&
            mbedtls_pk_verify(&key, MBEDTLS_MD_SHA256, digest, sizeof(digest),
                              der_signature, der_signature_size) == 0) verified = true;
    }
    mbedtls_pk_free(&key);
    uint8_t public_key_hash[32];
    if (!hash_sha256(_binary_wasmhome_update_signing_key_der_start,
                     key_size, public_key_hash)) return false;
    return verified && bytes_equal(public_key_hash, parsed->manifest.key_id, 8);
}

static bool parse_envelope(const uint8_t *data, size_t size,
                           parsed_envelope_t *parsed,
                           wh_signed_update_failure_t *failure_out)
{
    if (failure_out) *failure_out = WH_SIGNED_UPDATE_FAILURE_ENVELOPE;
    if (!parsed) return false;
    memset(parsed, 0, sizeof(*parsed));
    if (!data || size > WH_SIGNED_UPDATE_MAX_ENVELOPE_BYTES || size < 3 ||
        data[0] != 0xd2) return false;
    cbor_cursor_t cursor = {.data = data, .size = size, .offset = 1};
    uint64_t count;
    size_t bytes_size;
    if (!cbor_read_head(&cursor, 4, &count) || count != 4 ||
        !cbor_read_bytes(&cursor, 2, &parsed->protected_bytes,
                         &parsed->protected_size)) return false;
    if (parsed->protected_size != 13 || parsed->protected_bytes[0] != 0xa2 ||
        parsed->protected_bytes[1] != 0x01 || parsed->protected_bytes[2] != 0x26 ||
        parsed->protected_bytes[3] != 0x04 || parsed->protected_bytes[4] != 0x48)
        return false;
    memcpy(parsed->manifest.key_id, parsed->protected_bytes + 5, 8);
    if (cursor.offset >= cursor.size || cursor.data[cursor.offset++] != 0xa0 ||
        !cbor_read_bytes(&cursor, 2, &parsed->manifest_bytes,
                         &parsed->manifest_size) ||
        !cbor_read_bytes(&cursor, 2, &parsed->signature, &bytes_size) ||
        bytes_size != 64 || cursor.offset != cursor.size ||
        !cbor_read_manifest(parsed->manifest_bytes, parsed->manifest_size,
                            &parsed->manifest)) return false;
    memcpy(parsed->manifest.key_id, parsed->protected_bytes + 5, 8);
    if (!hash_sha256(data, size, parsed->envelope_sha256)) return false;
    memcpy(parsed->manifest.envelope_sha256, parsed->envelope_sha256, 32);
    if (!hash_sha256(parsed->manifest_bytes, parsed->manifest_size,
                     parsed->manifest.manifest_sha256)) return false;
    if (!verify_cose_signature(parsed)) {
        if (failure_out) *failure_out = WH_SIGNED_UPDATE_FAILURE_SIGNATURE;
        return false;
    }
    if (failure_out) *failure_out = WH_SIGNED_UPDATE_FAILURE_NONE;
    return true;
}

static bool valid_campaign(const char *campaign)
{
    if (!campaign || !*campaign || strlen(campaign) >= WH_SIGNED_UPDATE_MAX_CAMPAIGN_BYTES)
        return false;
    for (const unsigned char *p = (const unsigned char *)campaign; *p; ++p)
        if (!((*p >= 'a' && *p <= 'z') || (*p >= '0' && *p <= '9') ||
              *p == '.' || *p == '_' || *p == '-')) return false;
    return (campaign[0] >= 'a' && campaign[0] <= 'z') ||
           (campaign[0] >= '0' && campaign[0] <= '9');
}

static uint32_t maximum_payload_size(uint8_t scenario)
{
    if (scenario == 1) return 7u * 1024u * 1024u;
    if (scenario == 2 || scenario == 3) return 65536u;
    return 0;
}

static const char *payload_relative_path(uint8_t scenario, const char *release)
{
    if (scenario == 1) return strcmp(release, "v1") == 0
        ? "scenario-1/firmware/v1/firmware.bin"
        : "scenario-1/firmware/v2/firmware.bin";
    if (scenario == 2) return strcmp(release, "v1") == 0
        ? "scenario-2/gpio-control/v1/control.wasm"
        : "scenario-2/gpio-control/v2/control.wasm";
    if (scenario == 3) return strcmp(release, "v1") == 0
        ? "scenario-3/gpio-control/v1/control.aot"
        : "scenario-3/gpio-control/v2/control.aot";
    return NULL;
}

static const char *component_for_scenario(uint8_t scenario)
{
    return scenario == 1 ? "firmware" :
           (scenario == 2 || scenario == 3) ? "gpio-control" : NULL;
}

static void emit_operation_event(update_operation_t *operation,
                                 wh_signed_update_phase_t phase,
                                 wh_signed_update_failure_t failure,
                                 const uint8_t *payload_hash)
{
    wh_signed_update_event_t event = {
        .phase = phase,
        .failure = failure,
        .attempt_id = operation->attempt_id,
        .origin_boot_id = operation->origin_boot_id,
        .accepted_floor = operation->accepted_floor,
        .scenario = operation->scenario,
        .component_id = 1,
        .target_version = operation->target_version,
        .sequence = operation->expected_sequence,
        .release_version = operation->release_version,
        .payload_bytes_expected = operation->expected_bytes,
        .payload_bytes_received = operation->received_bytes,
        .current_task_stack_high_water_bytes =
            (uint32_t)(uxTaskGetStackHighWaterMark(NULL) * sizeof(StackType_t)),
        .elapsed_us = operation->event_elapsed_us_override
            ? operation->event_elapsed_us_override : (uint64_t)esp_timer_get_time(),
        .elapsed_scope = "boot",
        .payload_sha256 = payload_hash,
        .manifest_sha256 = operation->manifest_known ? operation->manifest_sha256 : NULL,
    };
    operation->event_elapsed_us_override = 0;
    if (operation->event_fn) operation->event_fn(operation->event_context, &event);
    wh_signed_update_emit_event(&event);
}

static esp_err_t ensure_wall_clock(void)
{
    time_t now = time(NULL);
    struct tm utc = {0};
    if (now > 0 && gmtime_r(&now, &utc) && utc.tm_year >= 126) return ESP_OK;
    if (!sntp_started) {
        esp_sntp_setoperatingmode(SNTP_OPMODE_POLL);
        esp_sntp_setservername(0, CONFIG_WASMHOME_UPDATE_TIME_SERVER);
        esp_sntp_init();
        sntp_started = true;
    }
    for (unsigned waited = 0; waited < UPDATE_TIME_SYNC_TIMEOUT_MS; waited += 250) {
        vTaskDelay(pdMS_TO_TICKS(250));
        now = time(NULL);
        if (now > 0 && gmtime_r(&now, &utc) && utc.tm_year >= 126 &&
            esp_sntp_get_sync_status() == SNTP_SYNC_STATUS_COMPLETED) return ESP_OK;
    }
    return ESP_ERR_TIMEOUT;
}

static esp_err_t make_url(const char *relative, char *url, size_t capacity)
{
    if (!valid_campaign(CONFIG_WASMHOME_UPDATE_CAMPAIGN_ID) || !relative || !url)
        return ESP_ERR_INVALID_ARG;
    esp_ip4_addr_t ip = {0};
    if (esp_netif_str_to_ip4(CONFIG_WASMHOME_UPDATE_SERVER_IPV4, &ip) != ESP_OK)
        return ESP_ERR_INVALID_ARG;
    const int length = snprintf(url, capacity, "https://%s:%u/%s/%s",
        CONFIG_WASMHOME_UPDATE_SERVER_IPV4, (unsigned)CONFIG_WASMHOME_UPDATE_SERVER_PORT,
        CONFIG_WASMHOME_UPDATE_CAMPAIGN_ID, relative);
    return length > 0 && (size_t)length < capacity ? ESP_OK : ESP_ERR_INVALID_SIZE;
}

static esp_err_t http_get(const char *relative, uint8_t *buffer, size_t capacity,
                          size_t expected_size, size_t *received_out)
{
    if (!buffer || !capacity || !received_out) return ESP_ERR_INVALID_ARG;
    esp_err_t err = ensure_wall_clock();
    if (err != ESP_OK) return err;
    char url[320];
    err = make_url(relative, url, sizeof(url));
    if (err != ESP_OK) return err;
    esp_http_client_config_t config = {
        .url = url,
        .cert_pem = (const char *)_binary_wasmhome_update_ca_pem_start,
        .common_name = CONFIG_WASMHOME_UPDATE_SERVER_NAME,
        .timeout_ms = UPDATE_HTTP_TIMEOUT_MS,
        .disable_auto_redirect = true,
        .buffer_size = UPDATE_READ_CHUNK_BYTES,
        .buffer_size_tx = 512,
        .keep_alive_enable = false,
        .addr_type = HTTP_ADDR_TYPE_INET,
    };
    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (!client) return ESP_ERR_NO_MEM;
    err = esp_http_client_open(client, 0);
    if (err == ESP_OK) {
        const int64_t content_length = esp_http_client_fetch_headers(client);
        const int status = esp_http_client_get_status_code(client);
        if (status != 200) err = ESP_ERR_HTTP_BASE + status;
        else if (content_length < 0 || (uint64_t)content_length > capacity ||
                 (expected_size != SIZE_MAX && (uint64_t)content_length != expected_size))
            err = ESP_ERR_INVALID_SIZE;
        else {
            size_t received = 0;
            while (received < (size_t)content_length) {
                const size_t remaining = (size_t)content_length - received;
                const size_t chunk = remaining < UPDATE_READ_CHUNK_BYTES
                    ? remaining : UPDATE_READ_CHUNK_BYTES;
                const int count = esp_http_client_read(client,
                    (char *)buffer + received, (int)chunk);
                if (count <= 0) { err = ESP_FAIL; break; }
                received += (size_t)count;
            }
            if (err == ESP_OK && received != (size_t)content_length) err = ESP_ERR_INVALID_SIZE;
            if (err == ESP_OK) *received_out = received;
        }
    }
    esp_http_client_close(client);
    esp_http_client_cleanup(client);
    return err;
}

static bool validate_manifest_policy(const parsed_envelope_t *parsed,
                                     uint8_t scenario,
                                     const char *release_version,
                                     uint64_t expected_sequence)
{
    const wh_signed_update_manifest_t *manifest = &parsed->manifest;
    const char *component = component_for_scenario(scenario);
    if (!component || manifest->scenario != scenario ||
        manifest->artifact_kind != scenario || strcmp(manifest->component, component) != 0 ||
        strcmp(manifest->campaign_id, CONFIG_WASMHOME_UPDATE_CAMPAIGN_ID) != 0 ||
        strcmp(manifest->target, "esp32s3") != 0 || manifest->sequence != expected_sequence ||
        manifest->base_api_version != 1 ||
        strcmp(manifest->release_version, release_version) != 0 ||
        !manifest->payload_size || manifest->payload_size > maximum_payload_size(scenario) ||
        (scenario == 3 && strcmp(manifest->toolchain_id,
                                 CONFIG_WASMHOME_UPDATE_AOT_TOOLCHAIN_ID) != 0) ||
        (scenario != 3 && manifest->toolchain_id[0] != '\0')) return false;
    if (scenario == 1 && manifest->payload_size < 16) return false;
    return true;
}

static esp_err_t read_manifest(update_operation_t *operation,
                               parsed_envelope_t *parsed,
                               wh_signed_update_failure_t *failure_out)
{
    uint8_t envelope[WH_SIGNED_UPDATE_MAX_ENVELOPE_BYTES];
    size_t received = 0;
    const char *payload_path = payload_relative_path(operation->scenario,
                                                    operation->release_version);
    if (!payload_path) return ESP_ERR_INVALID_ARG;
    char manifest_path[160];
    const char *slash = strrchr(payload_path, '/');
    if (!slash || (size_t)(slash - payload_path) >= sizeof(manifest_path)) return ESP_ERR_INVALID_ARG;
    memcpy(manifest_path, payload_path, (size_t)(slash - payload_path));
    manifest_path[slash - payload_path] = '\0';
    const size_t prefix_size = strlen(manifest_path);
    if (snprintf(manifest_path + prefix_size, sizeof(manifest_path) - prefix_size,
                 "/manifest.cose") >= (int)(sizeof(manifest_path) - prefix_size))
        return ESP_ERR_INVALID_SIZE;
    esp_err_t err = http_get(manifest_path, envelope, sizeof(envelope), SIZE_MAX, &received);
    if (err != ESP_OK) {
        if (failure_out) *failure_out = (err >= ESP_ERR_HTTP_BASE && err < ESP_ERR_HTTP_BASE + 256)
            ? WH_SIGNED_UPDATE_FAILURE_HTTP : WH_SIGNED_UPDATE_FAILURE_TRANSPORT;
        return err;
    }
    if (!received) {
        if (failure_out) *failure_out = WH_SIGNED_UPDATE_FAILURE_ENVELOPE;
        return ESP_ERR_INVALID_SIZE;
    }
    operation->event_elapsed_us_override = (uint64_t)esp_timer_get_time();
    const bool envelope_valid = parse_envelope(envelope, received, parsed, failure_out);
    if (parsed->manifest_size &&
        bytes_nonzero(parsed->manifest.manifest_sha256, 32)) {
        memcpy(operation->manifest_sha256, parsed->manifest.manifest_sha256, 32);
        operation->manifest_known = true;
    }
    if (envelope_valid) operation->expected_bytes = parsed->manifest.payload_size;
    bool policy_valid = false;
    if (envelope_valid)
        policy_valid = validate_manifest_policy(parsed, operation->scenario,
            operation->release_version, operation->expected_sequence);
    if (envelope_valid && !policy_valid && failure_out)
        *failure_out = WH_SIGNED_UPDATE_FAILURE_POLICY;
    emit_operation_event(operation, WH_SIGNED_UPDATE_PHASE_MANIFEST_DOWNLOADED,
                         envelope_valid && policy_valid ? WH_SIGNED_UPDATE_FAILURE_NONE :
                             (failure_out ? *failure_out : WH_SIGNED_UPDATE_FAILURE_ENVELOPE),
                         NULL);
    if (!envelope_valid) return ESP_ERR_INVALID_ARG;
    if (!policy_valid) return ESP_ERR_INVALID_VERSION;
    memcpy(operation->manifest_sha256, parsed->manifest.manifest_sha256, 32);
    operation->manifest_known = true;
    if (failure_out) *failure_out = WH_SIGNED_UPDATE_FAILURE_NONE;
    return ESP_OK;
}

static void update_failure_cleanup(update_operation_t *operation,
                                   const wh_signed_update_manifest_t *manifest,
                                   bool pending, bool sink_started,
                                   wh_signed_update_failure_t failure,
                                   wh_signed_update_phase_t phase)
{
    if (sink_started) operation->sink->abort(operation->sink->context);
    if (pending && manifest) (void)wh_signed_update_abort(manifest);
    emit_operation_event(operation, phase, failure, NULL);
}

esp_err_t wh_signed_update_download(uint8_t scenario, const char *release_version,
                                    uint64_t expected_sequence,
                                    const uint8_t attempt_id[16],
                                    const wh_signed_update_sink_t *sink,
                                    wh_signed_update_event_fn event_fn,
                                    void *event_context,
                                    wh_signed_update_manifest_t *manifest_out,
                                    wh_signed_update_failure_t *failure_out)
{
    if (failure_out) *failure_out = WH_SIGNED_UPDATE_FAILURE_NONE;
    if (!release_version || (strcmp(release_version, "v1") != 0 &&
        strcmp(release_version, "v2") != 0) || !expected_sequence || !attempt_id ||
        !bytes_nonzero(attempt_id, 16) || !sink || !sink->begin || !sink->write ||
        !sink->finish || !sink->abort || !manifest_out || !failure_out ||
        !maximum_payload_size(scenario)) return ESP_ERR_INVALID_ARG;
    update_operation_t operation = {
        .scenario = scenario,
        .target_version = strcmp(release_version, "v1") == 0 ? 1 : 2,
        .expected_sequence = expected_sequence,
        .attempt_id = attempt_id,
        .release_version = release_version,
        .sink = sink,
        .event_fn = event_fn,
        .event_context = event_context,
        .origin_boot_id = wh_platform_boot_id(),
    };
    const char *component = component_for_scenario(scenario);
    wh_signed_update_state_t prior_state;
    esp_err_t err = wh_signed_update_get_state(scenario, component,
        CONFIG_WASMHOME_UPDATE_CAMPAIGN_ID, &prior_state);
    if (err != ESP_OK) {
        *failure_out = WH_SIGNED_UPDATE_FAILURE_PERSISTENCE;
        return err;
    }
    operation.accepted_floor = prior_state.accepted_floor;
    emit_operation_event(&operation, WH_SIGNED_UPDATE_PHASE_MANIFEST_DOWNLOAD_STARTED,
                         WH_SIGNED_UPDATE_FAILURE_NONE, NULL);
    parsed_envelope_t parsed;
    wh_signed_update_failure_t stage_failure = WH_SIGNED_UPDATE_FAILURE_NONE;
    err = read_manifest(&operation, &parsed, &stage_failure);
    if (err != ESP_OK) {
        *failure_out = stage_failure == WH_SIGNED_UPDATE_FAILURE_NONE
            ? WH_SIGNED_UPDATE_FAILURE_TRANSPORT : stage_failure;
        update_failure_cleanup(&operation, NULL, false, false, *failure_out,
                               WH_SIGNED_UPDATE_PHASE_UPDATE_FAILED);
        return err;
    }
    *manifest_out = parsed.manifest;
    emit_operation_event(&operation, WH_SIGNED_UPDATE_PHASE_SIGNATURE_VERIFIED,
                         WH_SIGNED_UPDATE_FAILURE_NONE, NULL);
    err = ledger_prepare(manifest_out, attempt_id);
    if (err != ESP_OK) {
        *failure_out = err == ESP_ERR_INVALID_VERSION ? WH_SIGNED_UPDATE_FAILURE_POLICY :
                       WH_SIGNED_UPDATE_FAILURE_PERSISTENCE;
        emit_operation_event(&operation, WH_SIGNED_UPDATE_PHASE_UPDATE_FAILED,
                             *failure_out, NULL);
        return err;
    }
    uint32_t slot_id = WH_SIGNED_UPDATE_NO_ACTIVE_SLOT;
    err = sink->begin(sink->context, manifest_out, &slot_id);
    if (err != ESP_OK) {
        *failure_out = WH_SIGNED_UPDATE_FAILURE_SINK;
        update_failure_cleanup(&operation, manifest_out, true, false, *failure_out,
                               WH_SIGNED_UPDATE_PHASE_UPDATE_FAILED);
        return err;
    }
    bool sink_started = true;
    err = ledger_pending_change(manifest_out, slot_id, PENDING_DOWNLOADING);
    if (err != ESP_OK) {
        *failure_out = WH_SIGNED_UPDATE_FAILURE_PERSISTENCE;
        update_failure_cleanup(&operation, manifest_out, true, sink_started, *failure_out,
                               WH_SIGNED_UPDATE_PHASE_UPDATE_FAILED);
        return err;
    }
    emit_operation_event(&operation, WH_SIGNED_UPDATE_PHASE_PAYLOAD_DOWNLOAD_STARTED,
                         WH_SIGNED_UPDATE_FAILURE_NONE, NULL);
    const char *payload_path = payload_relative_path(scenario, release_version);
    char url[320];
    err = make_url(payload_path, url, sizeof(url));
    if (err == ESP_OK) err = ensure_wall_clock();
    if (err != ESP_OK) {
        *failure_out = WH_SIGNED_UPDATE_FAILURE_TRANSPORT;
        update_failure_cleanup(&operation, manifest_out, true, sink_started, *failure_out,
                               WH_SIGNED_UPDATE_PHASE_UPDATE_FAILED);
        return err;
    }
    esp_http_client_config_t config = {
        .url = url,
        .cert_pem = (const char *)_binary_wasmhome_update_ca_pem_start,
        .common_name = CONFIG_WASMHOME_UPDATE_SERVER_NAME,
        .timeout_ms = UPDATE_HTTP_TIMEOUT_MS,
        .disable_auto_redirect = true,
        .buffer_size = UPDATE_READ_CHUNK_BYTES,
        .buffer_size_tx = 512,
        .keep_alive_enable = false,
        .addr_type = HTTP_ADDR_TYPE_INET,
    };
    esp_http_client_handle_t client = esp_http_client_init(&config);
    wh_signed_update_failure_t transfer_failure = WH_SIGNED_UPDATE_FAILURE_TRANSPORT;
    if (!client) err = ESP_ERR_NO_MEM;
    if (err == ESP_OK) err = esp_http_client_open(client, 0);
    int64_t content_length = -1;
    if (err == ESP_OK) {
        content_length = esp_http_client_fetch_headers(client);
        if (esp_http_client_get_status_code(client) != 200) {
            err = ESP_ERR_HTTP_BASE + esp_http_client_get_status_code(client);
            transfer_failure = WH_SIGNED_UPDATE_FAILURE_HTTP;
        } else if (content_length < 0 ||
                   (uint64_t)content_length != manifest_out->payload_size) {
            err = ESP_ERR_INVALID_SIZE;
            transfer_failure = WH_SIGNED_UPDATE_FAILURE_PAYLOAD_SIZE;
        }
    }
    mbedtls_md_context_t hash_context;
    mbedtls_md_init(&hash_context);
    const mbedtls_md_info_t *sha = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
    if (err == ESP_OK && (!sha || mbedtls_md_setup(&hash_context, sha, 0) != 0 ||
        mbedtls_md_starts(&hash_context) != 0)) {
        err = ESP_FAIL;
        transfer_failure = WH_SIGNED_UPDATE_FAILURE_PAYLOAD_HASH;
    }
    uint8_t chunk[UPDATE_READ_CHUNK_BYTES];
    uint32_t last_progress_bytes = 0;
    const uint32_t progress_interval_bytes = 64u * 1024u;
    while (err == ESP_OK && operation.received_bytes < operation.expected_bytes) {
        const uint32_t remaining = operation.expected_bytes - operation.received_bytes;
        const size_t request = remaining < sizeof(chunk) ? remaining : sizeof(chunk);
        const int count = esp_http_client_read(client, (char *)chunk, (int)request);
        if (count <= 0) { err = ESP_FAIL; break; }
        if (mbedtls_md_update(&hash_context, chunk, (size_t)count) != 0) {
            err = ESP_FAIL;
            transfer_failure = WH_SIGNED_UPDATE_FAILURE_PAYLOAD_HASH;
            break;
        }
        if (sink->write(sink->context, chunk, (size_t)count) != ESP_OK) {
            err = ESP_FAIL;
            transfer_failure = WH_SIGNED_UPDATE_FAILURE_SINK;
            break;
        }
        operation.received_bytes += (uint32_t)count;
        if (operation.received_bytes - last_progress_bytes >= progress_interval_bytes ||
            operation.received_bytes == operation.expected_bytes) {
            emit_operation_event(&operation,
                WH_SIGNED_UPDATE_PHASE_PAYLOAD_DOWNLOAD_PROGRESS,
                WH_SIGNED_UPDATE_FAILURE_NONE, NULL);
            last_progress_bytes = operation.received_bytes;
        }
    }
    if (err == ESP_OK && mbedtls_md_finish(&hash_context, operation.payload_sha256) != 0) {
        err = ESP_FAIL;
        transfer_failure = WH_SIGNED_UPDATE_FAILURE_PAYLOAD_HASH;
    }
    mbedtls_md_free(&hash_context);
    if (client) {
        esp_http_client_close(client);
        esp_http_client_cleanup(client);
    }
    if (err != ESP_OK) {
        *failure_out = transfer_failure;
        update_failure_cleanup(&operation, manifest_out, true, sink_started, *failure_out,
                               WH_SIGNED_UPDATE_PHASE_UPDATE_FAILED);
        return err;
    }
    if (operation.received_bytes != operation.expected_bytes) {
        *failure_out = WH_SIGNED_UPDATE_FAILURE_PAYLOAD_SIZE;
        update_failure_cleanup(&operation, manifest_out, true, sink_started, *failure_out,
                               WH_SIGNED_UPDATE_PHASE_UPDATE_FAILED);
        return ESP_ERR_INVALID_SIZE;
    }
    if (!bytes_equal(operation.payload_sha256, manifest_out->payload_sha256, 32)) {
        *failure_out = WH_SIGNED_UPDATE_FAILURE_PAYLOAD_HASH;
        update_failure_cleanup(&operation, manifest_out, true, sink_started, *failure_out,
                               WH_SIGNED_UPDATE_PHASE_UPDATE_FAILED);
        return ESP_ERR_INVALID_CRC;
    }
    emit_operation_event(&operation, WH_SIGNED_UPDATE_PHASE_PAYLOAD_VERIFIED,
                         WH_SIGNED_UPDATE_FAILURE_NONE, operation.payload_sha256);
    err = sink->finish(sink->context, manifest_out, &slot_id);
    if (err == ESP_OK) err = wh_signed_update_mark_staged(manifest_out, slot_id);
    if (err != ESP_OK) {
        *failure_out = err == ESP_ERR_INVALID_STATE ? WH_SIGNED_UPDATE_FAILURE_PERSISTENCE :
                       WH_SIGNED_UPDATE_FAILURE_SINK;
        update_failure_cleanup(&operation, manifest_out, true, sink_started, *failure_out,
                               WH_SIGNED_UPDATE_PHASE_UPDATE_FAILED);
        return err;
    }
    emit_operation_event(&operation, WH_SIGNED_UPDATE_PHASE_STAGED,
                         WH_SIGNED_UPDATE_FAILURE_NONE, operation.payload_sha256);
    return ESP_OK;
}

#if defined(CONFIG_WASMHOME_NATIVE_OTA)
static wh_signed_update_manifest_t manifest_from_state(const wh_signed_update_state_t *state)
{
    wh_signed_update_manifest_t manifest = {0};
    manifest.scenario = state->scenario;
    manifest.artifact_kind = state->scenario;
    strncpy(manifest.campaign_id, CONFIG_WASMHOME_UPDATE_CAMPAIGN_ID,
            sizeof(manifest.campaign_id) - 1);
    strncpy(manifest.component, state->component, sizeof(manifest.component) - 1);
    strncpy(manifest.release_version, state->pending_release_version,
            sizeof(manifest.release_version) - 1);
    strncpy(manifest.target, "esp32s3", sizeof(manifest.target) - 1);
    manifest.sequence = state->pending_sequence;
    manifest.payload_size = state->pending_payload_size;
    manifest.base_api_version = 1;
    memcpy(manifest.payload_sha256, state->pending_payload_sha256, 32);
    memcpy(manifest.manifest_sha256, state->pending_manifest_sha256, 32);
    return manifest;
}

static wh_signed_update_event_t event_from_state(const wh_signed_update_state_t *state,
                                                  wh_signed_update_phase_t phase,
                                                  wh_signed_update_failure_t failure)
{
    const bool pending = state->pending;
    const uint8_t *attempt_id = pending ? state->pending_attempt_id : state->active_attempt_id;
    const uint16_t target_version = pending ? state->pending_target_version
                                            : state->active_target_version;
    const uint64_t sequence = pending ? state->pending_sequence : state->active_sequence;
    const char *release = pending ? state->pending_release_version
                                  : state->active_release_version;
    const uint32_t payload_size = pending ? state->pending_payload_size
                                          : state->active_payload_size;
    const uint8_t *payload_hash = pending ? state->pending_payload_sha256
                                          : state->active_payload_sha256;
    const uint8_t *manifest_hash = pending ? state->pending_manifest_sha256
                                           : state->active_manifest_sha256;
    const uint64_t origin_boot_id = pending ? state->origin_boot_id
                                            : state->active_origin_boot_id;
    return (wh_signed_update_event_t){
        .phase = phase,
        .failure = failure,
        .attempt_id = bytes_nonzero(attempt_id, 16) ? attempt_id : NULL,
        .origin_boot_id = origin_boot_id,
        .accepted_floor = state->accepted_floor,
        .ledger_readback_sequence = state->active_sequence,
        .scenario = state->scenario,
        .component_id = 1,
        .target_version = target_version,
        .sequence = sequence,
        .release_version = release,
        .payload_bytes_expected = payload_size,
        .payload_bytes_received = payload_size,
        .current_task_stack_high_water_bytes =
            (uint32_t)(uxTaskGetStackHighWaterMark(NULL) * sizeof(StackType_t)),
        .elapsed_us = (uint64_t)esp_timer_get_time(),
        .elapsed_scope = "boot",
        .payload_sha256 = payload_hash,
        .manifest_sha256 = manifest_hash,
    };
}

static esp_err_t hash_partition_payload(const esp_partition_t *partition,
                                        uint32_t payload_size, uint8_t digest[32])
{
    if (!partition || !payload_size || payload_size > partition->size || !digest)
        return ESP_ERR_INVALID_SIZE;
    const mbedtls_md_info_t *sha = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
    if (!sha) return ESP_FAIL;
    mbedtls_md_context_t context;
    mbedtls_md_init(&context);
    esp_err_t err = mbedtls_md_setup(&context, sha, 0) == 0 &&
        mbedtls_md_starts(&context) == 0 ? ESP_OK : ESP_FAIL;
    uint8_t chunk[512];
    for (uint32_t offset = 0; err == ESP_OK && offset < payload_size;) {
        size_t amount = payload_size - offset;
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

static esp_err_t verify_native_candidate(const wh_signed_update_state_t *state,
                                         const esp_partition_t *partition,
                                         uint8_t running_sha256[32])
{
    if (!state || !state->pending || !partition || !running_sha256 ||
        state->pending_phase != PENDING_ACTIVATING ||
        partition->address != state->pending_slot ||
        state->pending_target_version != 2 ||
        strcmp(state->pending_release_version, CONFIG_WASMHOME_UPDATE_RELEASE_VERSION) != 0)
        return ESP_ERR_INVALID_STATE;
    esp_app_desc_t app_desc = {0};
    esp_err_t err = esp_ota_get_partition_description(partition, &app_desc);
    if (err != ESP_OK) return err;
    if (strcmp(app_desc.version, state->pending_release_version) != 0) return ESP_ERR_INVALID_VERSION;
    err = hash_partition_payload(partition, state->pending_payload_size, running_sha256);
    if (err != ESP_OK) return err;
    return bytes_equal(running_sha256, state->pending_payload_sha256, 32)
        ? ESP_OK : ESP_ERR_INVALID_CRC;
}

static void emit_floor_committed(const wh_signed_update_state_t *state)
{
    wh_signed_update_event_t event = event_from_state(state,
        WH_SIGNED_UPDATE_PHASE_FLOOR_COMMITTED, WH_SIGNED_UPDATE_FAILURE_NONE);
    event.accepted_floor = state->accepted_floor;
    event.ledger_readback_sequence = state->active_sequence;
    event.running_sha256 = state->active_payload_sha256;
    wh_signed_update_emit_event(&event);
}

static const esp_partition_t *app_partition_at(uint32_t address)
{
    esp_partition_iterator_t iterator = esp_partition_find(
        ESP_PARTITION_TYPE_APP, ESP_PARTITION_SUBTYPE_ANY, NULL);
    const esp_partition_t *match = NULL;
    while (iterator) {
        const esp_partition_t *partition = esp_partition_get(iterator);
        if (partition && partition->address == address) {
            match = partition;
            break;
        }
        iterator = esp_partition_next(iterator);
    }
    esp_partition_iterator_release(iterator);
    return match;
}
#endif

esp_err_t wh_signed_update_reconcile_native_boot(void)
{
#if defined(CONFIG_WASMHOME_NATIVE_OTA)
    wh_signed_update_state_t state;
    esp_err_t err = wh_signed_update_get_state(1, "firmware",
        CONFIG_WASMHOME_UPDATE_CAMPAIGN_ID, &state);
    if (err != ESP_OK || !state.pending) return err;
    const esp_partition_t *running = esp_ota_get_running_partition();
    if (!running) return ESP_ERR_NOT_FOUND;
    if (running->address != state.pending_slot) {
        wh_signed_update_manifest_t manifest = manifest_from_state(&state);
        bool rollback = false;
        bool activation_was_not_selected = false;
        if (state.pending_phase == PENDING_ACTIVATING &&
            state.pending_slot != WH_SIGNED_UPDATE_NO_ACTIVE_SLOT) {
            const esp_partition_t *candidate = app_partition_at(state.pending_slot);
            const esp_partition_t *boot = esp_ota_get_boot_partition();
            esp_ota_img_states_t candidate_state;
            esp_err_t candidate_err = candidate
                ? esp_ota_get_state_partition(candidate, &candidate_state)
                : ESP_ERR_NOT_FOUND;
            rollback = candidate_err == ESP_OK &&
                (candidate_state == ESP_OTA_IMG_ABORTED ||
                 candidate_state == ESP_OTA_IMG_INVALID);
            activation_was_not_selected = candidate_err == ESP_OK && boot &&
                boot->address == running->address &&
                candidate_state == ESP_OTA_IMG_NEW;
        }
        wh_signed_update_event_t event = event_from_state(&state,
            rollback ? WH_SIGNED_UPDATE_PHASE_ROLLBACK_DETECTED
                     : (state.pending_phase == PENDING_ACTIVATING
                        ? WH_SIGNED_UPDATE_PHASE_ACTIVATION_INTERRUPTED
                        : WH_SIGNED_UPDATE_PHASE_UPDATE_FAILED),
            rollback ? WH_SIGNED_UPDATE_FAILURE_POLICY
                     : WH_SIGNED_UPDATE_FAILURE_PERSISTENCE);
        wh_signed_update_emit_event(&event);
        if (rollback || activation_was_not_selected ||
            state.pending_phase != PENDING_ACTIVATING)
            return wh_signed_update_abort(&manifest);
        /* The bootloader did not prove rollback and selection state is
           inconclusive. Keep pending to avoid claiming recovery or losing it. */
        return ESP_OK;
    }
    esp_ota_img_states_t image_state;
    err = esp_ota_get_state_partition(running, &image_state);
    if (err != ESP_OK) return err;
    uint8_t running_sha256[32];
    err = verify_native_candidate(&state, running, running_sha256);
    if (err != ESP_OK) {
        wh_signed_update_event_t event = event_from_state(&state,
            WH_SIGNED_UPDATE_PHASE_HEALTH_CHECK_FAILED,
            err == ESP_ERR_INVALID_CRC ? WH_SIGNED_UPDATE_FAILURE_PAYLOAD_HASH
                                       : WH_SIGNED_UPDATE_FAILURE_POLICY);
        wh_signed_update_emit_event(&event);
        if (image_state == ESP_OTA_IMG_PENDING_VERIFY)
            return esp_ota_mark_app_invalid_rollback_and_reboot();
        return err;
    }
    if (image_state == ESP_OTA_IMG_PENDING_VERIFY) {
        wh_signed_update_event_t event = event_from_state(&state,
            WH_SIGNED_UPDATE_PHASE_BOOT_PENDING, WH_SIGNED_UPDATE_FAILURE_NONE);
        event.running_sha256 = running_sha256;
        wh_signed_update_emit_event(&event);
        return ESP_OK;
    }
    if (image_state == ESP_OTA_IMG_VALID) {
        wh_signed_update_event_t health_event = event_from_state(&state,
            WH_SIGNED_UPDATE_PHASE_HEALTH_CHECK_PASSED,
            WH_SIGNED_UPDATE_FAILURE_NONE);
        health_event.running_sha256 = running_sha256;
        health_event.health_reconciled = true;
        wh_signed_update_emit_event(&health_event);
        wh_signed_update_manifest_t manifest = manifest_from_state(&state);
        err = wh_signed_update_commit(&manifest, state.pending_slot);
        wh_signed_update_state_t committed;
        if (wh_signed_update_get_state(1, "firmware",
                CONFIG_WASMHOME_UPDATE_CAMPAIGN_ID, &committed) == ESP_OK &&
            !committed.pending && committed.active_slot == state.pending_slot &&
            committed.active_sequence == state.pending_sequence &&
            bytes_equal(committed.active_payload_sha256,
                        state.pending_payload_sha256, 32)) {
            emit_floor_committed(&committed);
            return ESP_OK;
        }
        return err == ESP_OK ? ESP_ERR_INVALID_CRC : err;
    }
    return ESP_ERR_INVALID_STATE;
#else
    return ESP_ERR_NOT_SUPPORTED;
#endif
}

esp_err_t wh_signed_update_mark_native_healthy(void)
{
#if defined(CONFIG_WASMHOME_NATIVE_OTA)
    wh_signed_update_state_t state;
    esp_err_t err = wh_signed_update_get_state(1, "firmware",
        CONFIG_WASMHOME_UPDATE_CAMPAIGN_ID, &state);
    if (err != ESP_OK || !state.pending) return err;
    const esp_partition_t *running = esp_ota_get_running_partition();
    if (!running || running->address != state.pending_slot) return ESP_ERR_INVALID_STATE;
    esp_ota_img_states_t image_state;
    err = esp_ota_get_state_partition(running, &image_state);
    if (err != ESP_OK) return err;
    wh_signed_update_manifest_t manifest = manifest_from_state(&state);
    uint8_t running_sha256[32];
    err = verify_native_candidate(&state, running, running_sha256);
    if (err != ESP_OK) {
        wh_signed_update_event_t event = event_from_state(&state,
            WH_SIGNED_UPDATE_PHASE_HEALTH_CHECK_FAILED,
            err == ESP_ERR_INVALID_CRC ? WH_SIGNED_UPDATE_FAILURE_PAYLOAD_HASH
                                       : WH_SIGNED_UPDATE_FAILURE_POLICY);
        event.running_sha256 = NULL;
        wh_signed_update_emit_event(&event);
        if (image_state == ESP_OTA_IMG_PENDING_VERIFY)
            return esp_ota_mark_app_invalid_rollback_and_reboot();
        return err;
    }
    if (image_state == ESP_OTA_IMG_PENDING_VERIFY) {
        wh_signed_update_event_t event = event_from_state(&state,
            WH_SIGNED_UPDATE_PHASE_HEALTH_CHECK_PASSED, WH_SIGNED_UPDATE_FAILURE_NONE);
        event.running_sha256 = running_sha256;
        wh_signed_update_emit_event(&event);
        err = esp_ota_mark_app_valid_cancel_rollback();
        if (err != ESP_OK) return err;
    } else if (image_state != ESP_OTA_IMG_VALID) return ESP_ERR_INVALID_STATE;
    err = wh_signed_update_commit(&manifest, state.pending_slot);
    wh_signed_update_state_t committed;
    if (wh_signed_update_get_state(1, "firmware",
            CONFIG_WASMHOME_UPDATE_CAMPAIGN_ID, &committed) == ESP_OK &&
        !committed.pending && committed.active_slot == state.pending_slot &&
        committed.active_sequence == state.pending_sequence &&
        bytes_equal(committed.active_payload_sha256, state.pending_payload_sha256, 32)) {
        emit_floor_committed(&committed);
        return ESP_OK;
    }
    return err == ESP_OK ? ESP_ERR_INVALID_CRC : err;
#else
    return ESP_ERR_NOT_SUPPORTED;
#endif
}
