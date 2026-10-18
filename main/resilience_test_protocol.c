#include "resilience_test_protocol.h"

#include <stddef.h>
#include <string.h>

static void skip_space(const char **cursor)
{
    while (**cursor == ' ' || **cursor == '\t' || **cursor == '\r' || **cursor == '\n')
        ++*cursor;
}

static bool read_json_string(const char **cursor, char *out, size_t capacity)
{
    if (**cursor != '"' || capacity == 0) return false;
    ++*cursor;
    size_t used = 0;
    while (**cursor && **cursor != '"') {
        const unsigned char ch = (unsigned char)**cursor;
        if (ch < 0x20 || ch > 0x7e || ch == '\\' || used + 1 >= capacity)
            return false;
        out[used++] = (char)ch;
        ++*cursor;
    }
    if (**cursor != '"') return false;
    ++*cursor;
    out[used] = '\0';
    return true;
}

static bool parse_uuid(const char *text, uint8_t bytes[16])
{
    if (!text || strlen(text) != 36) return false;
    static const uint8_t hyphen_positions[] = {8, 13, 18, 23};
    size_t source = 0;
    uint8_t any = 0;
    for (size_t i = 0; i < sizeof(hyphen_positions); ++i)
        if (text[hyphen_positions[i]] != '-') return false;
    for (size_t i = 0; i < 36;) {
        if (text[i] == '-') {
            ++i;
            continue;
        }
        if (source >= 16) return false;
        const char hi = text[i++];
        const char lo = text[i++];
        int high = hi >= '0' && hi <= '9' ? hi - '0' :
                   hi >= 'a' && hi <= 'f' ? hi - 'a' + 10 : -1;
        int low = lo >= '0' && lo <= '9' ? lo - '0' :
                  lo >= 'a' && lo <= 'f' ? lo - 'a' + 10 : -1;
        if (high < 0 || low < 0) return false;
        bytes[source] = (uint8_t)((high << 4) | low);
        any |= bytes[source++];
    }
    return source == 16 && any != 0;
}

static bool approved_case(const char *case_id)
{
    static const char *const cases[] = {
        "ENV-SIG", "ENV-HASH", "ENV-OLD", "WASM-ZERO", "WASM-OOB",
        "WASM-DIV0", "NAT-ABORT",
    };
    for (size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); ++i)
        if (strcmp(case_id, cases[i]) == 0) return true;
    return false;
}

bool wh_resilience_parse_fault_request(char *line,
                                       wh_resilience_fault_request_t *request)
{
    if (!line || !request) return false;
    const char *cursor = line;
    char key[32];
    char value[64];
    bool have_event = false, have_attempt = false, have_case = false;
    memset(request, 0, sizeof(*request));
    skip_space(&cursor);
    if (*cursor++ != '{') return false;
    skip_space(&cursor);
    while (*cursor && *cursor != '}') {
        if (!read_json_string(&cursor, key, sizeof(key))) return false;
        skip_space(&cursor);
        if (*cursor++ != ':') return false;
        skip_space(&cursor);
        if (!read_json_string(&cursor, value, sizeof(value))) return false;
        if (strcmp(key, "event") == 0) {
            if (have_event || strcmp(value, "fault_request") != 0) return false;
            have_event = true;
        } else if (strcmp(key, "attempt_id") == 0) {
            if (have_attempt || strlen(value) != 36) return false;
            memcpy(request->attempt_text, value, sizeof(request->attempt_text));
            have_attempt = true;
        } else if (strcmp(key, "case_id") == 0) {
            if (have_case || strlen(value) >= sizeof(request->case_id)) return false;
            memcpy(request->case_id, value, strlen(value) + 1);
            have_case = true;
        } else {
            return false;
        }
        skip_space(&cursor);
        if (*cursor == ',') {
            ++cursor;
            skip_space(&cursor);
            continue;
        }
        if (*cursor != '}') return false;
    }
    if (*cursor++ != '}') return false;
    skip_space(&cursor);
    if (*cursor || !have_event || !have_attempt || !have_case ||
        !approved_case(request->case_id) ||
        !parse_uuid(request->attempt_text, request->attempt_id))
        return false;
    return true;
}
