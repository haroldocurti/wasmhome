#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define WH_ABI_V1_SNAPSHOT_SIZE 16u
#define WH_ABI_V1_VERSION 1

typedef enum {
    WH_OK = 0,
    WH_ERR_INVALID = 1,
    WH_ERR_BOUNDS = 2,
    WH_ERR_FORBIDDEN = 3,
    WH_ERR_DUPLICATE = 4,
    WH_ERR_QUEUE_FULL = 5,
    WH_ERR_STATE = 6,
} wh_abi_status_t;

typedef struct {
    uint64_t sample_id;
    int32_t temperature_mC;
    uint16_t age_ms;
    uint8_t sensor_status;
} wh_abi_snapshot_v1_t;

/* Pure helpers shared by the firmware manager and host-side contract harness. */
bool wh_abi_module_version_valid(uint8_t module_version);
bool wh_abi_range_valid(uint32_t memory_size, uint32_t offset, uint32_t length);
bool wh_abi_snapshot_encode(uint8_t out[WH_ABI_V1_SNAPSHOT_SIZE],
                            const wh_abi_snapshot_v1_t *snapshot);

