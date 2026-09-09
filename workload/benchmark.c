#include "benchmark.h"

#if defined(__wasm__)
#define WASM_EXPORT(name) __attribute__((export_name(name)))
#else
#define WASM_EXPORT(name)
#endif

static uint32_t mix(uint32_t state, uint32_t sample)
{
    state ^= sample + 0x9E3779B9u + (state << 6u) + (state >> 2u);
    state = (state << 7u) | (state >> 25u);
    return state * 1664525u + 1013904223u;
}

WASM_EXPORT("benchmark_run")
uint32_t benchmark_run(void)
{
    uint32_t state = 0xC001D00Du;
    uint32_t signal = 0x12345678u;

    for (uint32_t i = 0; i < WASMHOME_BENCHMARK_ITERATIONS; ++i) {
        signal = signal * 1103515245u + 12345u;
        state = mix(state, signal ^ i);
    }

    return state;
}
