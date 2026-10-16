#pragma once

#include "esp_err.h"
#include "module_manager.h"

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

/* Returns promptly after validating, acknowledging/rejecting and queueing a
 * module UPDATE_TRIGGER. */
void wh_module_update_handle_trigger(const uint8_t *packet, size_t length);
