#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define WH_NATIVE_CONTROL_REPLAY_CAPACITY 64
#define WH_NATIVE_UPDATE_REPLAY_CAPACITY 16

enum {
    WH_NATIVE_SENSOR_VALID = 0,
    WH_NATIVE_SENSOR_CRC_ERROR = 1,
    WH_NATIVE_SENSOR_DISCONNECTED = 2,
    WH_NATIVE_SENSOR_NOT_READY = 3,
    WH_NATIVE_SENSOR_NO_VALID_SAMPLE = 4,
};

enum {
    WH_NATIVE_CONTROL_APPLIED = 0,
    WH_NATIVE_CONTROL_MISMATCH = 1,
    WH_NATIVE_CONTROL_NOT_APPLICABLE = 2,
    WH_NATIVE_CONTROL_EXECUTION_ERROR = 3,
};

enum {
    WH_NATIVE_SCENARIO_NATIVE = 1,
    WH_NATIVE_COMPONENT_CONTROL = 1,
    WH_NATIVE_ACCEPTANCE_REJECTED = 2,
    WH_NATIVE_REASON_NONE = 0,
    WH_NATIVE_REASON_WRONG_BOOT = 1,
    WH_NATIVE_REASON_SCENARIO_MISMATCH = 2,
    WH_NATIVE_REASON_UNSUPPORTED_COMPONENT = 3,
    WH_NATIVE_REASON_UNSUPPORTED_VERSION = 4,
    WH_NATIVE_REASON_SEQUENCE_NOT_NEWER = 5,
    WH_NATIVE_REASON_BUSY = 6,
    WH_NATIVE_REASON_ATTEMPT_CONFLICT = 7,
};

typedef struct {
    uint64_t sample_id;
    int32_t input_temperature_mC;
    uint8_t requested_gpio;
    uint8_t observed_gpio;
    uint8_t status;
    bool valid;
} wh_native_control_record_t;

typedef struct {
    bool valid;
    uint8_t attempt_id[16];
    uint64_t request_boot_id;
    uint64_t request_sequence;
    uint64_t target_sequence;
    uint8_t scenario;
    uint8_t component;
    uint16_t target_version;
    uint8_t reason;
} wh_native_update_record_t;

typedef struct {
    uint8_t module_version;
    uint8_t requested_gpio;
    uint64_t highest_sample_id;
    size_t next_control_record;
    wh_native_control_record_t control_records[WH_NATIVE_CONTROL_REPLAY_CAPACITY];
    size_t update_record_count;
    wh_native_update_record_t update_records[WH_NATIVE_UPDATE_REPLAY_CAPACITY];
} wh_native_services_state_t;

typedef struct {
    uint8_t module_version;
    uint8_t requested_gpio;
    uint8_t observed_gpio;
    uint8_t status;
} wh_native_control_result_t;

typedef enum {
    WH_NATIVE_CONTROL_INVALID = 0,
    WH_NATIVE_CONTROL_NEW = 1,
    WH_NATIVE_CONTROL_REPLAY = 2,
    WH_NATIVE_CONTROL_CONFLICT = 3,
    WH_NATIVE_CONTROL_STALE = 4,
} wh_native_control_disposition_t;

typedef enum {
    WH_NATIVE_UPDATE_INVALID = 0,
    WH_NATIVE_UPDATE_NEW_REJECTION = 1,
    WH_NATIVE_UPDATE_REPLAY_REJECTION = 2,
    WH_NATIVE_UPDATE_ATTEMPT_CONFLICT = 3,
    WH_NATIVE_UPDATE_CAPACITY_FULL = 4,
} wh_native_update_disposition_t;

typedef bool (*wh_native_gpio_apply_fn)(void *context, uint8_t requested,
                                        uint8_t *observed);

bool wh_native_services_state_init(wh_native_services_state_t *state,
                                   uint8_t module_version);
wh_native_control_disposition_t wh_native_control_request(
    wh_native_services_state_t *state, uint64_t sample_id,
    int32_t input_temperature_mC, wh_native_gpio_apply_fn apply_gpio,
    void *gpio_context, wh_native_control_result_t *result);
wh_native_update_disposition_t wh_native_update_reject(
    wh_native_services_state_t *state, const uint8_t attempt_id[16],
    uint64_t request_boot_id, uint64_t request_sequence,
    uint64_t target_sequence, uint8_t scenario, uint8_t component,
    uint16_t target_version, uint64_t current_boot_id, uint8_t *reason);
bool wh_native_telemetry_body(uint8_t body[16], uint64_t sample_id,
                              int32_t temperature_mC, uint64_t acquired_us,
                              uint8_t sensor_status, uint64_t now_us);

#ifndef WASMHOME_NATIVE_SERVICES_HOST
#include "esp_err.h"
esp_err_t wh_native_services_start(void);
#endif
