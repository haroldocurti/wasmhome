#pragma once

#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"
#include "module_abi_v1.h"

#define WH_MODULE_DIGEST_SIZE 32u

typedef enum {
    WH_MODULE_ROLE_TELEMETRY = 1,
    WH_MODULE_ROLE_CONTROL = 2,
} wh_module_role_t;

typedef enum {
    WH_MODULE_SLOT_EMPTY = 0,
    WH_MODULE_SLOT_PREPARING = 1,
    WH_MODULE_SLOT_READY = 2,
    WH_MODULE_SLOT_ACTIVE = 3,
    WH_MODULE_SLOT_DRAINING = 4,
    WH_MODULE_SLOT_FAILED = 5,
} wh_module_slot_state_t;

/* The manager copies bytes before this call returns and never retains this pointer. */
typedef struct {
    const uint8_t *bytes;
    size_t size;
    uint8_t module_version;
} wh_module_image_t;

typedef struct {
    wh_module_role_t role;
    wh_module_slot_state_t state;
    uint8_t module_version;
    uint64_t generation;
    uint8_t sha256[WH_MODULE_DIGEST_SIZE];
    uint32_t in_flight;
    uint32_t artifact_bytes;
} wh_module_slot_info_t;

typedef struct {
    uint64_t allocator_live_bytes;
    uint64_t allocator_peak_bytes;
    uint64_t allocator_calls;
    uint64_t allocator_failures;
    uint64_t runtime_live_bytes;
    uint64_t linear_memory_live_bytes;
    uint64_t artifact_live_bytes;
    uint64_t control_admission_refusals;
    uint64_t telemetry_queue_full_drops;
    uint64_t telemetry_runtime_busy_drops;
    uint64_t tx_failures;
    uint64_t control_execution_failures;
    uint64_t gpio_failures;
    size_t internal_heap_total_bytes;
    size_t internal_heap_free_bytes;
    size_t internal_heap_largest_block_bytes;
    uint8_t psram_heap_available;
    size_t psram_heap_total_bytes;
    size_t psram_heap_free_bytes;
    size_t psram_heap_largest_block_bytes;
    uint64_t queue_payload_capacity_bytes;
    uint64_t steady_worker_stack_requested_bytes;
    uint32_t telemetry_stack_high_water_words;
    uint32_t control_stack_high_water_words;
    wh_module_slot_info_t telemetry;
    wh_module_slot_info_t control;
} wh_module_manager_info_t;

/* Initialize the singleton WAMR runtime and activate both role-specific images. */
esp_err_t wh_module_manager_start(const wh_module_image_t *telemetry,
                                  const wh_module_image_t *control);

/* Prepare a new control image; the active image remains usable until safe swap. */
esp_err_t wh_module_manager_prepare_control(const wh_module_image_t *candidate);

/* Stop workers, drain calls, release both slots, then destroy the singleton runtime. */
esp_err_t wh_module_manager_stop(void);

/* Installable platform request callback. It copies valid packets into bounded queues. */
void wh_module_manager_handle_request(const uint8_t *packet, size_t length);

/* Read-only accounting snapshot; returns ESP_ERR_INVALID_STATE before start. */
esp_err_t wh_module_manager_get_info(wh_module_manager_info_t *info);

