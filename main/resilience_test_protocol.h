#pragma once

#include <stdbool.h>
#include <stdint.h>

typedef struct {
    uint8_t attempt_id[16];
    char attempt_text[37];
    char case_id[16];
} wh_resilience_fault_request_t;

/* Closed, bounded serial protocol. No escaped values or unknown keys accepted. */
bool wh_resilience_parse_fault_request(char *line,
                                       wh_resilience_fault_request_t *request);
