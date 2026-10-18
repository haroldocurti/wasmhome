#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#define WH_MODULE_SLOT_ID_A 0u
#define WH_MODULE_SLOT_ID_B 1u
#define WH_MODULE_SLOT_COUNT 2u

/* Slots are raw payload partitions. The signed-update state stored in NVS
 * owns the active/pending selection and payload metadata. */
esp_err_t wh_module_slot_store_init(void);
esp_err_t wh_module_slot_store_begin(uint8_t scenario, uint32_t slot_id,
                                     uint32_t expected_size);
esp_err_t wh_module_slot_store_write(const uint8_t *data, size_t size);
esp_err_t wh_module_slot_store_finish(uint8_t scenario, uint32_t slot_id,
                                      uint32_t expected_size,
                                      const uint8_t expected_sha256[32]);
esp_err_t wh_module_slot_store_abort(void);
esp_err_t wh_module_slot_store_read(uint8_t scenario, uint32_t slot_id,
                                    uint32_t size, uint8_t *destination,
                                    size_t destination_size);
