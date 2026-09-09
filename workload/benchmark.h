#ifndef WASMHOME_BENCHMARK_H
#define WASMHOME_BENCHMARK_H

#include <stdint.h>

#define WASMHOME_BENCHMARK_ITERATIONS 2000u
#define WASMHOME_EXPECTED_CHECKSUM 0xDBB2F0D4u

uint32_t benchmark_run(void);

#endif
