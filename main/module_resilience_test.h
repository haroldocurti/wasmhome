#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

esp_err_t wh_module_resilience_test_start(void);
void wh_module_resilience_test_note_udp_tx(uint8_t type,
                                           const uint8_t attempt_id[16],
                                           bool sent);
