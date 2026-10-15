#include "module_abi_v1.h"

#include <limits.h>

bool
wh_abi_module_version_valid(uint8_t module_version)
{
    return module_version == 1 || module_version == 2;
}

bool
wh_abi_range_valid(uint32_t memory_size, uint32_t offset, uint32_t length)
{
    const uint64_t end = (uint64_t)offset + (uint64_t)length;
    return end <= (uint64_t)memory_size;
}

bool
wh_abi_snapshot_encode(uint8_t out[WH_ABI_V1_SNAPSHOT_SIZE],
                       const wh_abi_snapshot_v1_t *snapshot)
{
    if (!out || !snapshot || snapshot->sensor_status > 4) return false;

    for (uint32_t i = 0; i < 8; ++i)
        out[i] = (uint8_t)(snapshot->sample_id >> (i * 8));

    const uint32_t temperature = (uint32_t)snapshot->temperature_mC;
    for (uint32_t i = 0; i < 4; ++i)
        out[8 + i] = (uint8_t)(temperature >> (i * 8));

    out[12] = (uint8_t)snapshot->age_ms;
    out[13] = (uint8_t)(snapshot->age_ms >> 8);
    out[14] = snapshot->sensor_status;
    out[15] = 0;
    return true;
}

