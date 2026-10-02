#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define WH_HEADER_SIZE 48
#define WH_MAX_DATAGRAM 1200
#define WH_TELEMETRY 0x01
#define WH_CONTROL_REQUEST 0x02
#define WH_CONTROL_RESULT 0x03
#define WH_UPDATE_TRIGGER 0x10
#define WH_UPDATE_ACCEPTANCE 0x11

uint64_t wh_read_be(const uint8_t *p, size_t n);
void wh_write_be(uint8_t *p, uint64_t value, size_t n);
bool wh_wire_valid(const uint8_t *data, size_t length);
/* Body must already be encoded in network byte order. Returns 0 on error. */
size_t wh_wire_encode(uint8_t *out, size_t capacity, uint8_t type,
                      const uint8_t attempt[16], uint64_t sequence,
                      uint64_t boot_id, uint64_t monotonic_us,
                      const uint8_t *body, size_t body_size);
/* DS18B20 scratchpad CRC, resolution and signed temperature validation. */
bool wh_ds18b20_decode(const uint8_t scratchpad[9], int32_t *temperature_mC);
