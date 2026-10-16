#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

/* Available only in the opt-in native bench build. */
esp_err_t wh_resilience_test_start(void);
void wh_resilience_test_note_service_result(bool telemetry, bool correct);
void wh_resilience_test_note_control_request(const uint8_t attempt_id[16],
                                            bool correct);
void wh_resilience_test_telemetry_attempt(uint8_t attempt_id[16]);
