"""Host tests for the AOT signed-module adapter's ledger and trigger boundaries."""

import pathlib
import shutil
import subprocess
import tempfile
import textwrap
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


CONTRACT = r'''
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

typedef int esp_err_t;
#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_ERR_INVALID_ARG 0x101
#define ESP_ERR_INVALID_STATE 0x102
#define ESP_ERR_INVALID_CRC 0x104
#define ESP_ERR_NO_MEM 0x105
#define ESP_ERR_NOT_FOUND 0x106
#define ESP_ERR_INVALID_SIZE 0x107
#define CONFIG_WASMHOME_SIGNED_UPDATE 1
#define CONFIG_WASMHOME_MODULE_MAX_ARTIFACT_BYTES 65536u
#define CONFIG_WASMHOME_UPDATE_CAMPAIGN_ID "issues16-18-bench-20261015"
#define WH_SIGNED_UPDATE_EVENT_LINE_MAX_BYTES 1536u
#define CONFIG_SPIRAM 0
#define MALLOC_CAP_SPIRAM 1u
#define MALLOC_CAP_8BIT 2u
#define MALLOC_CAP_INTERNAL 4u

typedef void *QueueHandle_t;
typedef void *SemaphoreHandle_t;
typedef void *TaskHandle_t;
typedef int BaseType_t;
typedef unsigned UBaseType_t;
typedef unsigned StackType_t;
typedef void (*TaskFunction_t)(void *);
#define pdTRUE 1
#define pdFALSE 0
#define pdPASS 1
#define portMAX_DELAY UINT32_MAX
#define tskIDLE_PRIORITY 0u

#define WH_HEADER_SIZE 48u
#define WH_UPDATE_TRIGGER 0x10u
#define WH_UPDATE_ACCEPTANCE 0x11u
#define WH_MODULE_SLOT_ID_A 0u
#define WH_MODULE_SLOT_ID_B 1u
#define WH_MODULE_SLOT_COUNT 2u
bool wh_wire_valid(const uint8_t *packet, size_t length);
uint64_t wh_read_be(const uint8_t *packet, size_t length);
void wh_write_be(uint8_t *packet, uint64_t value, size_t length);

typedef struct {
    uint8_t module_version;
    uint64_t generation;
    uint8_t sha256[32];
} wh_module_slot_info_t;
typedef struct {
    wh_module_slot_info_t telemetry, control;
    uint64_t telemetry_runtime_busy_drops;
} wh_module_manager_info_t;
typedef struct {
    const uint8_t *bytes;
    size_t size;
    uint8_t module_version;
} wh_module_image_t;
typedef enum {
    WH_MODULE_SELECTION_NOT_COMMITTED = 0,
    WH_MODULE_SELECTION_COMMITTED = 1,
    WH_MODULE_SELECTION_UNKNOWN = 2,
} wh_module_selection_outcome_t;
typedef esp_err_t (*wh_module_control_commit_fn)(
    void *, const wh_module_slot_info_t *, const wh_module_slot_info_t *,
    wh_module_selection_outcome_t *);
typedef void (*wh_module_control_event_fn)(
    void *, const char *, const wh_module_slot_info_t *,
    const wh_module_slot_info_t *, uint64_t);
esp_err_t wh_module_manager_get_info(wh_module_manager_info_t *info);
esp_err_t wh_module_manager_activate_control(
    const wh_module_image_t *candidate, wh_module_control_commit_fn commit,
    wh_module_control_event_fn event, void *context);

typedef enum {
    WH_SIGNED_UPDATE_FAILURE_NONE = 0,
    WH_SIGNED_UPDATE_FAILURE_ARGUMENT,
    WH_SIGNED_UPDATE_FAILURE_TRANSPORT,
    WH_SIGNED_UPDATE_FAILURE_HTTP,
    WH_SIGNED_UPDATE_FAILURE_ENVELOPE,
    WH_SIGNED_UPDATE_FAILURE_SIGNATURE,
    WH_SIGNED_UPDATE_FAILURE_POLICY,
    WH_SIGNED_UPDATE_FAILURE_PERSISTENCE,
    WH_SIGNED_UPDATE_FAILURE_PAYLOAD_SIZE,
    WH_SIGNED_UPDATE_FAILURE_PAYLOAD_HASH,
    WH_SIGNED_UPDATE_FAILURE_SINK,
} wh_signed_update_failure_t;
typedef struct {
    uint8_t scenario, artifact_kind;
    char campaign_id[64], component[32], release_version[32], target[16];
    char toolchain_id[128];
    uint64_t sequence;
    uint32_t payload_size, base_api_version;
    uint8_t payload_sha256[32], envelope_sha256[32], manifest_sha256[32], key_id[8];
} wh_signed_update_manifest_t;
typedef enum {
    WH_SIGNED_UPDATE_PHASE_ACCEPTED = 0,
    WH_SIGNED_UPDATE_PHASE_MANIFEST_DOWNLOAD_STARTED,
    WH_SIGNED_UPDATE_PHASE_MANIFEST_DOWNLOADED,
    WH_SIGNED_UPDATE_PHASE_SIGNATURE_VERIFIED,
    WH_SIGNED_UPDATE_PHASE_PAYLOAD_DOWNLOAD_STARTED,
    WH_SIGNED_UPDATE_PHASE_PAYLOAD_DOWNLOAD_PROGRESS,
    WH_SIGNED_UPDATE_PHASE_PAYLOAD_VERIFIED,
    WH_SIGNED_UPDATE_PHASE_STAGED,
    WH_SIGNED_UPDATE_PHASE_ACTIVATING,
    WH_SIGNED_UPDATE_PHASE_BOOT_PENDING,
    WH_SIGNED_UPDATE_PHASE_HEALTH_CHECK_PASSED,
    WH_SIGNED_UPDATE_PHASE_HEALTH_CHECK_FAILED,
    WH_SIGNED_UPDATE_PHASE_ROLLBACK_DETECTED,
    WH_SIGNED_UPDATE_PHASE_FLOOR_COMMITTED,
    WH_SIGNED_UPDATE_PHASE_REBOOT_REQUESTED,
    WH_SIGNED_UPDATE_PHASE_CANDIDATE_LOADED,
    WH_SIGNED_UPDATE_PHASE_CANDIDATE_INSTANTIATED,
    WH_SIGNED_UPDATE_PHASE_CANDIDATE_HEALTH_CHECK,
    WH_SIGNED_UPDATE_PHASE_CONTROL_ACTIVATED,
    WH_SIGNED_UPDATE_PHASE_PREVIOUS_CONTROL_RELEASED,
    WH_SIGNED_UPDATE_PHASE_UPDATE_FAILED,
} wh_signed_update_phase_t;
typedef struct {
    wh_signed_update_phase_t phase;
    wh_signed_update_failure_t failure;
    const uint8_t *attempt_id;
    uint64_t origin_boot_id, accepted_floor, ledger_readback_sequence;
    uint8_t scenario, component_id;
    uint16_t target_version;
    uint64_t sequence;
    const char *release_version;
    uint32_t payload_bytes_expected, payload_bytes_received;
    uint32_t current_task_stack_high_water_bytes;
    uint64_t elapsed_us;
    const char *elapsed_scope;
    const uint8_t *payload_sha256, *running_sha256, *manifest_sha256;
    uint64_t telemetry_generation;
    const uint8_t *telemetry_sha256;
    uint64_t telemetry_runtime_busy_drops, control_generation;
    uint8_t control_module_version;
    const uint8_t *control_sha256;
} wh_signed_update_event_t;
typedef void (*wh_signed_update_event_fn)(void *, wh_signed_update_event_t *);
typedef struct {
    void *context;
    esp_err_t (*begin)(void *, const wh_signed_update_manifest_t *, uint32_t *);
    esp_err_t (*write)(void *, const uint8_t *, size_t);
    esp_err_t (*finish)(void *, const wh_signed_update_manifest_t *, uint32_t *);
    esp_err_t (*abort)(void *);
} wh_signed_update_sink_t;
typedef struct {
    bool pending;
    uint8_t scenario;
    char component[32];
    uint64_t accepted_floor, active_sequence, pending_sequence;
    uint32_t active_slot, pending_slot, active_payload_size;
    uint16_t active_target_version, pending_target_version;
    uint32_t pending_payload_size;
    uint8_t pending_attempt_id[16];
    uint64_t origin_boot_id, active_origin_boot_id;
    char pending_release_version[32], active_release_version[32];
    uint8_t active_payload_sha256[32], active_manifest_sha256[32], active_attempt_id[16];
    uint8_t pending_payload_sha256[32], pending_manifest_sha256[32];
    uint8_t pending_phase;
} wh_signed_update_state_t;
#define WH_SIGNED_UPDATE_NO_ACTIVE_SLOT UINT32_MAX
esp_err_t wh_signed_update_download(uint8_t, const char *, uint64_t,
                                    const uint8_t[16], const wh_signed_update_sink_t *,
                                    wh_signed_update_event_fn, void *,
                                    wh_signed_update_manifest_t *,
                                    wh_signed_update_failure_t *);
esp_err_t wh_signed_update_mark_activating(const wh_signed_update_manifest_t *, uint32_t);
esp_err_t wh_signed_update_mark_staged(const wh_signed_update_manifest_t *, uint32_t);
esp_err_t wh_signed_update_commit(const wh_signed_update_manifest_t *, uint32_t);
esp_err_t wh_signed_update_abort(const wh_signed_update_manifest_t *);
esp_err_t wh_signed_update_get_state(uint8_t, const char *, const char *,
                                     wh_signed_update_state_t *);
void wh_signed_update_emit_event(const wh_signed_update_event_t *event);
esp_err_t wh_signed_update_format_event(const wh_signed_update_event_t *event,
                                        char *line, size_t capacity,
                                        size_t *line_size);
esp_err_t wh_module_manager_emit_or_defer_log_line(const char *line, size_t size);
void wh_module_manager_mark_deferred_log_failure(void);

esp_err_t wh_module_slot_store_init(void);
esp_err_t wh_module_slot_store_begin(uint8_t, uint32_t, uint32_t);
esp_err_t wh_module_slot_store_write(const uint8_t *, size_t);
esp_err_t wh_module_slot_store_finish(uint8_t, uint32_t, uint32_t, const uint8_t[32]);
esp_err_t wh_module_slot_store_abort(void);
esp_err_t wh_module_slot_store_read(uint8_t, uint32_t, uint32_t, uint8_t *, size_t);
uint64_t wh_platform_boot_id(void);
void wh_platform_event(const char *, esp_err_t);
esp_err_t wh_observation_send(uint8_t, const uint8_t[16], const uint8_t *, size_t);

SemaphoreHandle_t xSemaphoreCreateMutex(void);
BaseType_t xSemaphoreTake(SemaphoreHandle_t, uint32_t);
BaseType_t xSemaphoreGive(SemaphoreHandle_t);
void vSemaphoreDelete(SemaphoreHandle_t);
QueueHandle_t xQueueCreate(unsigned, size_t);
BaseType_t xQueueReceive(QueueHandle_t, void *, uint32_t);
BaseType_t xQueueSend(QueueHandle_t, const void *, uint32_t);
void vQueueDelete(QueueHandle_t);
BaseType_t xTaskCreate(TaskFunction_t, const char *, uint32_t, void *, UBaseType_t,
                       TaskHandle_t *);
UBaseType_t uxTaskGetStackHighWaterMark(TaskHandle_t);
int64_t esp_timer_get_time(void);
void *heap_caps_malloc(size_t, unsigned);
void heap_caps_free(void *);
size_t heap_caps_get_total_size(unsigned);
size_t heap_caps_get_free_size(unsigned);
size_t heap_caps_get_largest_free_block(unsigned);
int mbedtls_ct_memcmp(const void *, const void *, size_t);
typedef struct { uint8_t bytes[64]; } mbedtls_sha256_context;
void mbedtls_sha256_init(mbedtls_sha256_context *);
void mbedtls_sha256_free(mbedtls_sha256_context *);
int mbedtls_sha256_starts(mbedtls_sha256_context *, int);
int mbedtls_sha256_update(mbedtls_sha256_context *, const unsigned char *, size_t);
int mbedtls_sha256_finish(mbedtls_sha256_context *, unsigned char[32]);
int mbedtls_sha256(const unsigned char *, size_t, unsigned char[32], int);
'''


HARNESS = r'''
#define WASMHOME_SCENARIO_MODULE_AOT 1
#include "module_update.c"
#include <assert.h>
#include <stdio.h>

static wh_signed_update_state_t ledger;
static wh_module_manager_info_t manager_info;
static esp_err_t get_status = ESP_OK;
static esp_err_t commit_status = ESP_FAIL;
static bool commit_to_target;
static unsigned abort_calls, store_begin_calls, queue_calls, send_calls, event_calls;
static bool send_fails, queue_fails;
static uint8_t send_order[8];
static size_t send_order_count;
static uint8_t last_acceptance[20];

bool wh_wire_valid(const uint8_t *packet, size_t length)
{ return packet && length == WH_HEADER_SIZE + 12; }
uint64_t wh_read_be(const uint8_t *packet, size_t length)
{
    uint64_t value = 0;
    for (size_t i = 0; i < length; ++i) value = (value << 8) | packet[i];
    return value;
}
void wh_write_be(uint8_t *packet, uint64_t value, size_t length)
{
    for (size_t i = 0; i < length; ++i) packet[length - i - 1] = (uint8_t)(value >> (i * 8));
}
esp_err_t wh_signed_update_get_state(uint8_t scenario, const char *component,
                                     const char *campaign,
                                     wh_signed_update_state_t *out)
{
    assert(scenario == 3 && strcmp(component, "gpio-control") == 0);
    assert(strcmp(campaign, CONFIG_WASMHOME_UPDATE_CAMPAIGN_ID) == 0);
    if (get_status != ESP_OK) return get_status;
    *out = ledger;
    return ESP_OK;
}
esp_err_t wh_signed_update_commit(const wh_signed_update_manifest_t *manifest,
                                  uint32_t slot)
{
    if (commit_status != ESP_OK || !commit_to_target) return commit_status;
    ledger.pending = false;
    ledger.active_slot = slot;
    ledger.accepted_floor = manifest->sequence;
    ledger.active_sequence = manifest->sequence;
    ledger.active_payload_size = manifest->payload_size;
    ledger.active_target_version = 2;
    ledger.active_origin_boot_id = ledger.origin_boot_id;
    strncpy(ledger.active_release_version, manifest->release_version,
            sizeof(ledger.active_release_version) - 1);
    memcpy(ledger.active_attempt_id, ledger.pending_attempt_id, 16);
    memcpy(ledger.active_payload_sha256, manifest->payload_sha256, 32);
    memcpy(ledger.active_manifest_sha256, manifest->manifest_sha256, 32);
    return ESP_OK;
}
esp_err_t wh_signed_update_abort(const wh_signed_update_manifest_t *manifest)
{
    assert(manifest && ledger.pending);
    abort_calls++;
    ledger.pending = false;
    return ESP_OK;
}
esp_err_t wh_module_slot_store_begin(uint8_t scenario, uint32_t slot, uint32_t size)
{
    assert(scenario == 3 && size > 0);
    store_begin_calls++;
    assert(slot < WH_MODULE_SLOT_COUNT);
    return ESP_OK;
}
esp_err_t wh_module_manager_get_info(wh_module_manager_info_t *info)
{ *info = manager_info; return ESP_OK; }
esp_err_t wh_observation_send(uint8_t type, const uint8_t attempt[16],
                              const uint8_t *body, size_t size)
{
    assert(type == WH_UPDATE_ACCEPTANCE && attempt && body && size == 20);
    send_calls++;
    send_order[send_order_count++] = 1;
    memcpy(last_acceptance, body, size);
    return send_fails ? ESP_FAIL : ESP_OK;
}
BaseType_t xQueueSend(QueueHandle_t queue, const void *item, uint32_t wait)
{
    assert(queue && item && wait == 0);
    queue_calls++;
    send_order[send_order_count++] = 2;
    return queue_fails ? pdFALSE : pdTRUE;
}
BaseType_t xQueueReceive(QueueHandle_t queue, void *item, uint32_t wait)
{ (void)queue; (void)item; (void)wait; return pdFALSE; }
SemaphoreHandle_t xSemaphoreCreateMutex(void) { return (void *)1; }
BaseType_t xSemaphoreTake(SemaphoreHandle_t lock, uint32_t wait)
{ (void)lock; (void)wait; return pdTRUE; }
BaseType_t xSemaphoreGive(SemaphoreHandle_t lock) { (void)lock; return pdTRUE; }
void vSemaphoreDelete(SemaphoreHandle_t lock) { (void)lock; }
QueueHandle_t xQueueCreate(unsigned length, size_t item_size)
{ (void)length; (void)item_size; return (void *)2; }
void vQueueDelete(QueueHandle_t queue) { (void)queue; }
BaseType_t xTaskCreate(TaskFunction_t fn, const char *name, uint32_t stack,
                       void *arg, UBaseType_t priority, TaskHandle_t *out)
{ (void)fn; (void)name; (void)stack; (void)arg; (void)priority; *out = (void *)3; return pdPASS; }
UBaseType_t uxTaskGetStackHighWaterMark(TaskHandle_t task) { (void)task; return 128; }
int64_t esp_timer_get_time(void) { return 9000; }
uint64_t wh_platform_boot_id(void) { return 0x1234; }
void wh_platform_event(const char *name, esp_err_t error)
{ assert(name && error != ESP_OK); }
void wh_signed_update_emit_event(const wh_signed_update_event_t *event)
{ assert(event); event_calls++; }
esp_err_t wh_signed_update_format_event(const wh_signed_update_event_t *event,
                                        char *line, size_t capacity,
                                        size_t *line_size)
{
    assert(event && line && capacity && line_size);
    const char record[] = "WASMHOME_UPDATE,{\"phase\":\"candidate_loaded\"}\n";
    if (sizeof(record) > capacity) return ESP_ERR_INVALID_SIZE;
    memcpy(line, record, sizeof(record) - 1);
    *line_size = sizeof(record) - 1;
    return ESP_OK;
}
esp_err_t wh_module_manager_emit_or_defer_log_line(const char *line, size_t size)
{ assert(line && size); return ESP_OK; }
void wh_module_manager_mark_deferred_log_failure(void) { }
void *heap_caps_malloc(size_t size, unsigned caps) { (void)caps; return malloc(size); }
void heap_caps_free(void *p) { free(p); }
size_t heap_caps_get_total_size(unsigned caps) { (void)caps; return 0; }
size_t heap_caps_get_free_size(unsigned caps) { (void)caps; return 0; }
size_t heap_caps_get_largest_free_block(unsigned caps) { (void)caps; return 0; }
int mbedtls_ct_memcmp(const void *a, const void *b, size_t n)
{ const uint8_t *x=a,*y=b; uint8_t v=0; for(size_t i=0;i<n;i++) v|=x[i]^y[i]; return v; }
void mbedtls_sha256_init(mbedtls_sha256_context *c) { memset(c, 0, sizeof(*c)); }
void mbedtls_sha256_free(mbedtls_sha256_context *c) { (void)c; }
int mbedtls_sha256_starts(mbedtls_sha256_context *c, int mode) { (void)c; (void)mode; return 0; }
int mbedtls_sha256_update(mbedtls_sha256_context *c,const unsigned char *p,size_t n)
{ (void)c; (void)p; (void)n; return 0; }
int mbedtls_sha256_finish(mbedtls_sha256_context *c,unsigned char out[32])
{ (void)c; memset(out, 0, 32); return 0; }
int mbedtls_sha256(const unsigned char *p,size_t n,unsigned char out[32],int mode)
{ (void)p; (void)n; (void)mode; memset(out, 0, 32); return 0; }
esp_err_t wh_module_manager_activate_control(const wh_module_image_t *image,
    wh_module_control_commit_fn commit, wh_module_control_event_fn event, void *ctx)
{ (void)image; (void)commit; (void)event; (void)ctx; return ESP_FAIL; }
esp_err_t wh_signed_update_download(uint8_t a, const char *b, uint64_t c,
    const uint8_t d[16], const wh_signed_update_sink_t *e,
    wh_signed_update_event_fn f, void *g, wh_signed_update_manifest_t *h,
    wh_signed_update_failure_t *i)
{ (void)a;(void)b;(void)c;(void)d;(void)e;(void)f;(void)g;(void)h;(void)i;return ESP_FAIL; }
esp_err_t wh_signed_update_mark_activating(const wh_signed_update_manifest_t *m,uint32_t s)
{ (void)m;(void)s;return ESP_FAIL; }
esp_err_t wh_signed_update_mark_staged(const wh_signed_update_manifest_t *m,uint32_t s)
{ (void)m;(void)s;return ESP_FAIL; }
esp_err_t wh_module_slot_store_init(void) { return ESP_OK; }
esp_err_t wh_module_slot_store_write(const uint8_t *p,size_t n) { (void)p;(void)n;return ESP_FAIL; }
esp_err_t wh_module_slot_store_finish(uint8_t a,uint32_t b,uint32_t c,const uint8_t d[32])
{ (void)a;(void)b;(void)c;(void)d;return ESP_FAIL; }
esp_err_t wh_module_slot_store_abort(void) { return ESP_OK; }
esp_err_t wh_module_slot_store_read(uint8_t a,uint32_t b,uint32_t c,uint8_t *d,size_t e)
{ (void)a;(void)b;(void)c;(void)d;(void)e;return ESP_FAIL; }

static wh_signed_update_manifest_t make_manifest(void)
{
    wh_signed_update_manifest_t m = {0};
    m.scenario = m.artifact_kind = 3;
    strcpy(m.campaign_id, CONFIG_WASMHOME_UPDATE_CAMPAIGN_ID);
    strcpy(m.component, "gpio-control"); strcpy(m.release_version, "v2");
    strcpy(m.target, "esp32s3"); m.sequence = 2; m.payload_size = 1200;
    m.base_api_version = 1;
    memset(m.payload_sha256, 0x22, 32); memset(m.manifest_sha256, 0x33, 32);
    return m;
}
static wh_signed_update_state_t make_v1_state(void)
{
    wh_signed_update_state_t s = {0};
    s.scenario = 3; strcpy(s.component, "gpio-control");
    s.accepted_floor = s.active_sequence = 1; s.active_slot = 0;
    s.active_payload_size = 900; s.active_target_version = 1;
    s.active_origin_boot_id = 0x1233;
    strcpy(s.active_release_version, "v1");
    memset(s.active_attempt_id, 0x44, 16);
    memset(s.active_payload_sha256, 0x55, 32);
    memset(s.active_manifest_sha256, 0x66, 32);
    return s;
}
static void set_pending(wh_signed_update_state_t *s,
                        const wh_signed_update_manifest_t *m,
                        const uint8_t attempt_id[16], uint32_t slot)
{
    s->pending = true; s->pending_slot = slot;
    s->pending_sequence = m->sequence; s->pending_target_version = 2;
    s->pending_payload_size = m->payload_size; s->origin_boot_id = 0x1234;
    strcpy(s->pending_release_version, "v2");
    memcpy(s->pending_attempt_id, attempt_id, 16);
    memcpy(s->pending_payload_sha256, m->payload_sha256, 32);
    memcpy(s->pending_manifest_sha256, m->manifest_sha256, 32);
}
static void test_begin_binds_pending_before_slot_reservation(void)
{
    wh_signed_update_manifest_t m = make_manifest();
    update_attempt_t attempt = {0};
    memset(attempt.request.attempt_id, 0x11, 16);
    attempt.request.origin_boot_id = 0x1234;
    ledger = make_v1_state(); set_pending(&ledger, &m, attempt.request.attempt_id,
                                         WH_SIGNED_UPDATE_NO_ACTIVE_SLOT);
    uint32_t slot = WH_SIGNED_UPDATE_NO_ACTIVE_SLOT;
    store_begin_calls = 0;
    assert(sink_begin(&attempt, &m, &slot) == ESP_OK);
    assert(slot == 1 && attempt.slot_id == 1 && store_begin_calls == 1);

    ledger.pending_attempt_id[0] ^= 1;
    assert(sink_begin(&attempt, &m, &slot) == ESP_ERR_INVALID_STATE);
    assert(store_begin_calls == 1); /* An unrelated pending record is not erased. */
}
static void test_commit_readback_classifies_and_fails_closed(void)
{
    wh_signed_update_manifest_t m = make_manifest();
    update_attempt_t attempt = {0};
    attempt.manifest = m; attempt.manifest_ready = true; attempt.slot_id = 1;
    memset(attempt.request.attempt_id, 0x11, 16);
    attempt.request.origin_boot_id = 0x1234;
    attempt.previous_ledger = make_v1_state();
    attempt.previous_floor = 1; attempt.previous_sequence = 1;
    attempt.previous_slot = 0;
    wh_module_slot_info_t telemetry = {.generation=8};
    wh_module_slot_info_t candidate = {.module_version=2};
    memcpy(candidate.sha256, m.payload_sha256, 32);
    wh_module_selection_outcome_t outcome;

    ledger = attempt.previous_ledger;
    set_pending(&ledger, &m, attempt.request.attempt_id, attempt.slot_id);
    commit_status = ESP_FAIL; commit_to_target = false; get_status = ESP_OK;
    abort_calls = 0;
    assert(commit_candidate(&attempt, &telemetry, &candidate, &outcome) == ESP_FAIL);
    assert(outcome == WH_MODULE_SELECTION_NOT_COMMITTED);
    assert(abort_calls == 1 && !ledger.pending);
    assert(ledger.active_slot == 0 && ledger.accepted_floor == 1 &&
           ledger.active_sequence == 1 && ledger.active_target_version == 1);
    assert(memcmp(ledger.active_payload_sha256,
                  attempt.previous_ledger.active_payload_sha256, 32) == 0);

    ledger = attempt.previous_ledger;
    set_pending(&ledger, &m, attempt.request.attempt_id, attempt.slot_id);
    get_status = ESP_FAIL; abort_calls = 0;
    assert(commit_candidate(&attempt, &telemetry, &candidate, &outcome) == ESP_FAIL);
    assert(outcome == WH_MODULE_SELECTION_UNKNOWN);
    assert(abort_calls == 0 && ledger.pending); /* Never clear without readback. */

    ledger = attempt.previous_ledger;
    set_pending(&ledger, &m, attempt.request.attempt_id, attempt.slot_id);
    get_status = ESP_OK; commit_status = ESP_OK; commit_to_target = true;
    assert(commit_candidate(&attempt, &telemetry, &candidate, &outcome) == ESP_OK);
    assert(outcome == WH_MODULE_SELECTION_COMMITTED && !ledger.pending);
    assert(ledger.active_slot == 1 && ledger.accepted_floor == 2 &&
           ledger.active_sequence == 2 && ledger.active_target_version == 2);
    assert(attempt.ledger_readback_floor == 2 && attempt.ledger_readback_sequence == 2);
}
static void make_trigger(uint8_t packet[WH_HEADER_SIZE + 12])
{
    memset(packet, 0, WH_HEADER_SIZE + 12);
    packet[5] = WH_UPDATE_TRIGGER;
    memset(packet + 8, 0x99, 16);
    wh_write_be(packet + 24, 7, 8); wh_write_be(packet + 32, 0x1234, 8);
    wh_write_be(packet + WH_HEADER_SIZE, 2, 8);
    packet[WH_HEADER_SIZE + 8] = 3; packet[WH_HEADER_SIZE + 9] = 1;
    wh_write_be(packet + WH_HEADER_SIZE + 10, 2, 2);
}
static void test_ack_precedes_worker_and_conflicts_compare_request(void)
{
    update_lock = (void *)1; update_queue = (void *)2; update_task = (void *)3;
    update_active = false; memset(&active_request, 0, sizeof(active_request));
    ledger = (wh_signed_update_state_t){.scenario=3,
        .active_slot=WH_SIGNED_UPDATE_NO_ACTIVE_SLOT};
    strcpy(ledger.component, "gpio-control");
    manager_info.control.module_version = 1;
    uint8_t packet[WH_HEADER_SIZE + 12]; make_trigger(packet);
    send_fails = queue_fails = false; send_order_count = queue_calls = send_calls = 0;
    wh_module_update_handle_trigger(packet, sizeof(packet));
    assert(send_calls == 1 && queue_calls == 1);
    assert(send_order_count == 2 && send_order[0] == 1 && send_order[1] == 2);
    assert(last_acceptance[16] == 1 && last_acceptance[17] == 0);

    const unsigned prior_sends = send_calls;
    wh_module_update_handle_trigger(packet, sizeof(packet));
    assert(send_calls == prior_sends + 1 && queue_calls == 1);
    assert(last_acceptance[16] == 1 && last_acceptance[17] == 0);
    packet[24] ^= 1; /* Same UUID, different request sequence is a conflict. */
    wh_module_update_handle_trigger(packet, sizeof(packet));
    assert(last_acceptance[16] == 2 && last_acceptance[17] == 7);

    update_active = false; memset(&active_request, 0, sizeof(active_request));
    make_trigger(packet); send_fails = true; queue_calls = 0;
    wh_module_update_handle_trigger(packet, sizeof(packet));
    assert(queue_calls == 0 && !update_active); /* No download after failed ACK. */

    send_fails = false; queue_fails = true; queue_calls = 0; event_calls = 0;
    wh_module_update_handle_trigger(packet, sizeof(packet));
    assert(queue_calls == 1 && !update_active && event_calls == 1);
    assert(WH_MODULE_UPDATE_TASK_STACK_BYTES == 32u * 1024u);
}
int main(void)
{
    test_begin_binds_pending_before_slot_reservation();
    test_commit_readback_classifies_and_fails_closed();
    test_ack_precedes_worker_and_conflicts_compare_request();
    puts("module update adapter contract harness: PASS");
    return 0;
}
'''


class ModuleUpdateAdapterHostTests(unittest.TestCase):
    def test_aot_adapter_commit_ack_and_pending_contract(self):
        compiler = shutil.which("cc") or shutil.which("gcc")
        if not compiler:
            self.skipTest("host C compiler unavailable")
        with tempfile.TemporaryDirectory(prefix="wh-module-update-host-") as temporary:
            directory = pathlib.Path(temporary)
            (directory / "module_update.c").write_text(
                (ROOT / "main/module_update.c").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            (directory / "contract_stubs.h").write_text(CONTRACT, encoding="utf-8")
            wrappers = [
                "module_update.h", "module_manager.h", "esp_err.h", "esp_heap_caps.h",
                "esp_timer.h", "freertos/FreeRTOS.h", "freertos/queue.h",
                "freertos/semphr.h", "freertos/task.h", "mbedtls/constant_time.h",
                "mbedtls/sha256.h", "module_slot_store.h", "observation_wire.h",
                "platform.h", "sdkconfig.h", "signed_update.h",
            ]
            for name in wrappers:
                wrapper = directory / name
                wrapper.parent.mkdir(parents=True, exist_ok=True)
                wrapper.write_text('#include "contract_stubs.h"\n', encoding="utf-8")
            (directory / "harness.c").write_text(HARNESS, encoding="utf-8")
            executable = directory / "adapter-test"
            compile_result = subprocess.run(
                [compiler, "-std=c11", "-Wno-unused-function", "-Wno-unused-variable",
                 "-ffunction-sections", "-fdata-sections", "-Wl,--gc-sections",
                 "-I", str(directory), str(directory / "harness.c"), "-o", str(executable)],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(compile_result.returncode, 0,
                             compile_result.stdout + compile_result.stderr)
            run_result = subprocess.run([str(executable)], text=True,
                                        capture_output=True, check=False)
            self.assertEqual(run_result.returncode, 0,
                             run_result.stdout + run_result.stderr)
            self.assertIn("module update adapter contract harness: PASS", run_result.stdout)


if __name__ == "__main__":
    unittest.main()
