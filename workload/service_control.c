#include <stdint.h>

#ifndef WH_CONTROL_THRESHOLD_MC
#error "WH_CONTROL_THRESHOLD_MC must be supplied by the artifact generator"
#endif

__attribute__((import_module("wasmhome_control_v1"), import_name("gpio_request")))
extern int32_t host_gpio_request(int32_t level);
__attribute__((import_module("wasmhome_control_v1"), import_name("publish_control")))
extern int32_t host_publish_control(int64_t sample_id, int32_t requested);

static int32_t requested_level;

__attribute__((export_name("wh_abi_version")))
int32_t
wh_abi_version(void)
{
    return 1;
}

__attribute__((export_name("wh_control_request")))
int32_t
wh_control_request(int64_t sample_id, int32_t temperature_mC)
{
    if (sample_id <= 0 || temperature_mC < -55000 || temperature_mC > 125000)
        return 1;

    if (temperature_mC >= WH_CONTROL_THRESHOLD_MC + 500)
        requested_level = 1;
    else if (temperature_mC <= WH_CONTROL_THRESHOLD_MC - 500)
        requested_level = 0;

    int32_t result = host_gpio_request(requested_level);
    if (result != 0) return result;
    return host_publish_control(sample_id, requested_level);
}
