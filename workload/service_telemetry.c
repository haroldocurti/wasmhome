#include <stdint.h>

__attribute__((import_module("wasmhome_telemetry_v1"), import_name("snapshot")))
extern int32_t host_snapshot(uint32_t offset);
__attribute__((import_module("wasmhome_telemetry_v1"), import_name("publish_telemetry")))
extern int32_t host_publish_telemetry(void);

__attribute__((export_name("wh_abi_version")))
int32_t
wh_abi_version(void)
{
    return 1;
}

__attribute__((export_name("wh_telemetry_tick")))
int32_t
wh_telemetry_tick(void)
{
    uint8_t snapshot[16] = {0};
    int32_t result = host_snapshot((uint32_t)(uintptr_t)snapshot);
    if (result != 0) return result;
    if (snapshot[15] != 0) return 1;
    return host_publish_telemetry();
}
