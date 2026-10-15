#pragma once
#include "esp_err.h"
#include "observation_wire.h"
#include <stdint.h>

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

typedef struct {
    uint64_t calls;
    uint64_t sendto_calls;
    uint64_t accepted;
    uint64_t failed;
    /* Calls skipped because socket, network, or sequence was unavailable. */
    uint64_t not_ready;
    uint64_t socket_unavailable;
    uint64_t network_unavailable;
    uint64_t sequence_exhausted;
    uint64_t encode_failed;
    /* Most recent sendto() failure, retained even after later successful sends. */
    int32_t last_failure_result;
    int32_t last_failure_errno;
    uint64_t last_failure_sequence;
} wh_observation_tx_type_stats_t;

typedef struct {
    wh_observation_tx_type_stats_t telemetry;
    wh_observation_tx_type_stats_t control_result;
    wh_observation_tx_type_stats_t update_acceptance;
} wh_observation_tx_stats_t;

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
/* Snapshot cumulative counters under the transport lock. */
esp_err_t wh_platform_observation_tx_stats(wh_observation_tx_stats_t *out);
esp_err_t wh_sensor_start(void);
