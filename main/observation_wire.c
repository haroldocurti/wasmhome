#include "observation_wire.h"
#include <limits.h>
#include <string.h>

uint64_t wh_read_be(const uint8_t *p, size_t n)
{
    uint64_t value = 0;
    for (size_t i = 0; i < n; ++i) value = (value << 8) | p[i];
    return value;
}

void wh_write_be(uint8_t *p, uint64_t value, size_t n)
{
    while (n) { p[--n] = (uint8_t)value; value >>= 8; }
}

static int64_t signed_temperature(const uint8_t *p)
{
    uint64_t value = wh_read_be(p, 4);
    return value >= 0x80000000ULL ? (int64_t)value - 0x100000000LL : (int64_t)value;
}

bool wh_wire_valid(const uint8_t *d, size_t n)
{
    if (!d || n < WH_HEADER_SIZE || n > WH_MAX_DATAGRAM ||
        memcmp(d, "WHOB", 4) || d[4] != 1 ||
        !wh_read_be(d + 24, 8) || !wh_read_be(d + 32, 8)) return false;
    size_t body_size;
    switch (d[5]) {
    case WH_TELEMETRY: body_size = 16; break;
    case WH_CONTROL_REQUEST: case WH_CONTROL_RESULT:
    case WH_UPDATE_TRIGGER: body_size = 12; break;
    case WH_UPDATE_ACCEPTANCE: body_size = 20; break;
    default: return false;
    }
    if (wh_read_be(d + 6, 2) != body_size || n != WH_HEADER_SIZE + body_size) return false;
    const uint8_t *b = d + WH_HEADER_SIZE;
    uint64_t id = wh_read_be(b, 8);
    if (d[5] == WH_UPDATE_TRIGGER || d[5] == WH_UPDATE_ACCEPTANCE) {
        uint8_t any = 0;
        for (size_t i = 8; i < 24; ++i) any |= d[i];
        if (!any) return false;
    }
    if (d[5] == WH_TELEMETRY) {
        int64_t t = signed_temperature(b + 8);
        uint16_t age = (uint16_t)wh_read_be(b + 12, 2);
        if (b[14] > 4 || b[15]) return false;
        if (!id) return t == INT32_MIN && age == UINT16_MAX && b[14] != 0;
        return t >= -55000 && t <= 125000 && age != UINT16_MAX && b[14] != 4;
    }
    if (!id) return false;
    if (d[5] == WH_CONTROL_REQUEST) {
        int64_t t = signed_temperature(b + 8);
        return t >= -55000 && t <= 125000;
    }
    if (d[5] == WH_CONTROL_RESULT) {
        uint8_t requested = b[9], observed = b[10];
        if ((b[8] != 1 && b[8] != 2) ||
            (requested > 1 && requested != 255) || (observed > 1 && observed != 255)) return false;
        switch (b[11]) {
        case 0: return requested <= 1 && requested == observed;
        case 1: return requested <= 1 && observed <= 1 && requested != observed;
        case 2: return requested == 255 && observed == 255;
        case 3: return observed == 255;
        default: return false;
        }
    }
    if (d[5] == WH_UPDATE_TRIGGER)
        return b[8] >= 1 && b[8] <= 3 && b[9] && wh_read_be(b + 10, 2);
    if (!wh_read_be(b + 8, 8) || b[18] || b[19]) return false;
    return (b[16] == 1 && b[17] == 0) || (b[16] == 2 && b[17] >= 1 && b[17] <= 7);
}

size_t wh_wire_encode(uint8_t *out, size_t capacity, uint8_t type,
                      const uint8_t attempt[16], uint64_t sequence,
                      uint64_t boot_id, uint64_t monotonic_us,
                      const uint8_t *body, size_t body_size)
{
    if (!out || !attempt || !body || body_size > 20 || capacity < WH_HEADER_SIZE + body_size) return 0;
    memcpy(out, "WHOB", 4);
    out[4] = 1; out[5] = type;
    wh_write_be(out + 6, body_size, 2);
    memcpy(out + 8, attempt, 16);
    wh_write_be(out + 24, sequence, 8);
    wh_write_be(out + 32, boot_id, 8);
    wh_write_be(out + 40, monotonic_us, 8);
    memcpy(out + WH_HEADER_SIZE, body, body_size);
    size_t n = WH_HEADER_SIZE + body_size;
    return wh_wire_valid(out, n) ? n : 0;
}

bool wh_ds18b20_decode(const uint8_t s[9], int32_t *temperature_mC)
{
    if (!s || !temperature_mC) return false;
    uint8_t crc = 0;
    for (size_t i = 0; i < 8; ++i) {
        uint8_t byte = s[i];
        for (int bit = 0; bit < 8; ++bit) {
            uint8_t mix = (crc ^ byte) & 1;
            crc >>= 1;
            if (mix) crc ^= 0x8c;
            byte >>= 1;
        }
    }
    /* Reject power-on/default resolution and corrupted configuration. */
    if (crc != s[8] || s[4] != 0x3f) return false;
    uint16_t raw = ((uint16_t)s[1] << 8) | (s[0] & 0xfc);
    int32_t signed_raw = raw >= 0x8000 ? (int32_t)raw - 0x10000 : raw;
    int32_t t = signed_raw * 1000 / 16;
    if (t < -55000 || t > 125000) return false;
    *temperature_mC = t;
    return true;
}
