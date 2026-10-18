#pragma once

#include "esp_err.h"
#include "module_manager.h"
#include "signed_update.h"

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* Resolve a previously committed module slot before the WAMR runtime starts.
 * owned_bytes is released by the caller after module_manager_start copies it. */
esp_err_t wh_module_update_resolve_boot_control(
    const wh_module_image_t *embedded_control,
    wh_module_image_t *resolved_control,
    uint8_t **owned_bytes);

/* Starts the bounded update worker. Must run before the observation handler is
 * installed so a trigger never performs TLS or flash work on the UDP task. */
esp_err_t wh_module_update_start(void);

typedef struct {
    esp_err_t status;
    esp_err_t cleanup_status;
    wh_signed_update_failure_t failure;
    char stage[32];
    uint64_t previous_floor;
    uint64_t observed_floor;
    uint32_t previous_slot;
    uint32_t candidate_slot;
    uint32_t payload_bytes_written;
    bool expected_rejection;
    bool real_envelope_validator;
    bool active_slot_unchanged;
    bool active_version_unchanged;
    bool accepted_sequence_unchanged;
    bool nvs_floor_checked;
    bool inactive_slot_only;
    bool payload_invalidated;
} wh_module_update_resilience_result_t;

/* Read-only setup gate and closed ENV-SIG/HASH/OLD fixture path. */
esp_err_t wh_module_update_resilience_preflight(const char *case_id);
esp_err_t wh_module_update_run_resilience_fixture(
    const char *case_id, const uint8_t attempt_id[16],
    wh_module_update_resilience_result_t *result);

/* Returns promptly after validating, acknowledging/rejecting and queueing a
 * module UPDATE_TRIGGER. */
void wh_module_update_handle_trigger(const uint8_t *packet, size_t length);
