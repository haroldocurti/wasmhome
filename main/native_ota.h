#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

typedef struct {
    uint8_t attempt_id[16];
    uint64_t request_boot_id;
    uint64_t request_sequence;
    uint64_t target_sequence;
    uint8_t scenario;
    uint8_t component;
    uint16_t target_version;
} wh_native_ota_request_t;

/* Starts a worker that waits until the UDP layer has sent ACCEPTED. */
esp_err_t wh_native_ota_start(void);
/* Reconcile a pending native trial on a dedicated boot task and report its
   measured remaining stack in bytes. Services must not start before this ends. */
esp_err_t wh_native_ota_reconcile_boot(uint32_t *stack_high_water_bytes);
/* Reserve one request without starting any download or flash erase. */
esp_err_t wh_native_ota_reserve(const wh_native_ota_request_t *request);
esp_err_t wh_native_ota_start_reserved(void);
void wh_native_ota_cancel_reserved(void);
esp_err_t wh_native_ota_queue_health_check(void);
/* Queue a fixed resilience fixture on the OTA worker without touching UDP v1. */
esp_err_t wh_native_ota_run_resilience_fixture(
    const char *case_id, const uint8_t attempt_id[16]);
