#pragma once
#include "esp_err.h"
#include "observation_wire.h"

typedef enum {
    WH_SENSOR_VALID = 0, WH_SENSOR_CRC_ERROR = 1, WH_SENSOR_DISCONNECTED = 2,
    WH_SENSOR_NOT_READY = 3, WH_SENSOR_NO_VALID_SAMPLE = 4
} wh_sensor_status_t;

typedef struct {
    uint64_t sample_id;
    int32_t temperature_mC;
    int64_t acquired_us;
    wh_sensor_status_t status;
} wh_sensor_sample_t;

/* Called on the UDP task. Handler owns semantic rejection, deduplication and
 * service execution. It must finish promptly and must not retain data. */
typedef void (*wh_request_handler_t)(const uint8_t *data, size_t length);
esp_err_t wh_platform_start(void);
uint64_t wh_platform_boot_id(void);
void wh_platform_event(const char *event, esp_err_t status);
wh_sensor_sample_t wh_sensor_latest(void);
esp_err_t wh_gpio_apply(uint8_t requested, uint8_t *observed);
esp_err_t wh_observation_set_handler(wh_request_handler_t handler);
esp_err_t wh_observation_send(uint8_t type, const uint8_t attempt[16],
                              const uint8_t *body, size_t body_size);
esp_err_t wh_sensor_start(void);
