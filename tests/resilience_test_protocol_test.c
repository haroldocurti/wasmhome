#include "resilience_test_protocol.h"

#include <assert.h>
#include <string.h>

int main(void)
{
    wh_resilience_fault_request_t request;
    char valid[] = " { \"case_id\" : \"NAT-ABORT\", \"attempt_id\" : "
                   "\"b78edc42-e6c3-4598-8609-55a061b1c8d6\", "
                   "\"event\":\"fault_request\" } ";
    assert(wh_resilience_parse_fault_request(valid, &request));
    assert(strcmp(request.case_id, "NAT-ABORT") == 0);
    assert(strcmp(request.attempt_text, "b78edc42-e6c3-4598-8609-55a061b1c8d6") == 0);
    static const uint8_t expected[16] = {
        0xb7, 0x8e, 0xdc, 0x42, 0xe6, 0xc3, 0x45, 0x98,
        0x86, 0x09, 0x55, 0xa0, 0x61, 0xb1, 0xc8, 0xd6,
    };
    assert(memcmp(request.attempt_id, expected, sizeof(expected)) == 0);

    char duplicate[] = "{\"event\":\"fault_request\",\"attempt_id\":"
                       "\"b78edc42-e6c3-4598-8609-55a061b1c8d6\","
                       "\"attempt_id\":\"b78edc42-e6c3-4598-8609-55a061b1c8d6\","
                       "\"case_id\":\"NAT-ABORT\"}";
    assert(!wh_resilience_parse_fault_request(duplicate, &request));
    char extra[] = "{\"event\":\"fault_request\",\"attempt_id\":"
                   "\"b78edc42-e6c3-4598-8609-55a061b1c8d6\","
                   "\"case_id\":\"NAT-ABORT\",\"url\":\"https://example.test\"}";
    assert(!wh_resilience_parse_fault_request(extra, &request));
    char escaped[] = "{\"event\":\"fault_request\",\"attempt_id\":"
                     "\"b78edc42-e6c3-4598-8609-55a061b1c8d6\","
                     "\"case_id\":\"NAT-ABORT\\u0000\"}";
    assert(!wh_resilience_parse_fault_request(escaped, &request));
    char uppercase_uuid[] = "{\"event\":\"fault_request\",\"attempt_id\":"
                             "\"B78EDC42-E6C3-4598-8609-55A061B1C8D6\","
                             "\"case_id\":\"NAT-ABORT\"}";
    assert(!wh_resilience_parse_fault_request(uppercase_uuid, &request));
    char unapproved[] = "{\"event\":\"fault_request\",\"attempt_id\":"
                        "\"b78edc42-e6c3-4598-8609-55a061b1c8d6\","
                        "\"case_id\":\"UPDATE_TRIGGER\"}";
    assert(!wh_resilience_parse_fault_request(unapproved, &request));
    char zero_uuid[] = "{\"event\":\"fault_request\",\"attempt_id\":"
                       "\"00000000-0000-0000-0000-000000000000\","
                       "\"case_id\":\"NAT-ABORT\"}";
    assert(!wh_resilience_parse_fault_request(zero_uuid, &request));
    assert(!wh_resilience_parse_fault_request(NULL, &request));
    assert(!wh_resilience_parse_fault_request(valid, NULL));
    return 0;
}
