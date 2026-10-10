#include <inttypes.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"

#include "benchmark.h"
#include "platform.h"
#include "sdkconfig.h"

#if !defined(WASMHOME_SCENARIO_NATIVE)
#include "bh_platform.h"
#include "wasm_export.h"

/* Refuse a mislabeled scenario even when sdkconfig selected a different mode. */
#if defined(WASMHOME_SCENARIO_INTERP)
#if !defined(CONFIG_WAMR_ENABLE_INTERP) || !defined(CONFIG_WAMR_INTERP_CLASSIC) || defined(CONFIG_WAMR_INTERP_FAST) || defined(CONFIG_WAMR_ENABLE_AOT)
#error "Scenario B requires only the WAMR classic interpreter"
#endif
#elif defined(WASMHOME_SCENARIO_AOT)
#if !defined(CONFIG_WAMR_ENABLE_AOT) || defined(CONFIG_WAMR_ENABLE_INTERP)
#error "Scenario C requires only the WAMR AOT runtime"
#endif
#endif
#endif

#define WARMUP_RUNS 5
#define MEASURED_RUNS 30
#define WASM_STACK_SIZE (8u * 1024u)
#define WASM_HEAP_SIZE (8u * 1024u)

static const char *TAG = "wasmhome";

#if defined(WASMHOME_SCENARIO_NATIVE)
static const char *scenario_name = "native";
#elif defined(WASMHOME_SCENARIO_INTERP)
static const char *scenario_name = "interp";
extern const uint8_t benchmark_wasm[];
extern const uint32_t benchmark_wasm_len;
#elif defined(WASMHOME_SCENARIO_AOT)
static const char *scenario_name = "aot";
extern const uint8_t benchmark_aot[];
extern const uint32_t benchmark_aot_len;
#endif

typedef struct {
    int64_t load_us;
    int64_t instantiate_us;
    size_t heap_before;
    size_t heap_after_load;
    size_t heap_after_instantiate;
#if !defined(WASMHOME_SCENARIO_NATIVE)
    wasm_module_t module;
    wasm_module_inst_t instance;
    wasm_exec_env_t exec_env;
    wasm_function_inst_t function;
    uint8_t *artifact_buffer;
#endif
} runner_t;

static bool runner_open(runner_t *runner)
{
    memset(runner, 0, sizeof(*runner));
    runner->heap_before = heap_caps_get_free_size(MALLOC_CAP_INTERNAL);

#if defined(WASMHOME_SCENARIO_NATIVE)
    runner->heap_after_load = runner->heap_before;
    runner->heap_after_instantiate = runner->heap_before;
    return true;
#else
    const uint8_t *artifact_start;
    uint32_t artifact_len;
    char error[192] = {0};

#if defined(WASMHOME_SCENARIO_INTERP)
    artifact_start = benchmark_wasm;
    artifact_len = benchmark_wasm_len;
#else
    artifact_start = benchmark_aot;
    artifact_len = benchmark_aot_len;
#endif

    /* WAMR may modify the bytes and retains them until unload. os_malloc in
     * the ESP-IDF port provides 8-byte alignment, including AOT's 4-byte need. */
    if (!artifact_len) {
        ESP_LOGE(TAG, "empty artifact");
        return false;
    }
    runner->artifact_buffer = os_malloc(artifact_len);
    if (!runner->artifact_buffer) {
        ESP_LOGE(TAG, "artifact buffer allocation failed");
        return false;
    }
    memcpy(runner->artifact_buffer, artifact_start, artifact_len);
    int64_t started = esp_timer_get_time();
    runner->module = wasm_runtime_load(runner->artifact_buffer, artifact_len,
                                       error, sizeof(error));
    runner->load_us = esp_timer_get_time() - started;
    runner->heap_after_load = heap_caps_get_free_size(MALLOC_CAP_INTERNAL);
    if (!runner->module) {
        ESP_LOGE(TAG, "load failed: %s", error);
        return false;
    }

    started = esp_timer_get_time();
    runner->instance = wasm_runtime_instantiate(
        runner->module, WASM_STACK_SIZE, WASM_HEAP_SIZE, error, sizeof(error));
    runner->instantiate_us = esp_timer_get_time() - started;
    runner->heap_after_instantiate = heap_caps_get_free_size(MALLOC_CAP_INTERNAL);
    if (!runner->instance) {
        ESP_LOGE(TAG, "instantiate failed: %s", error);
        return false;
    }

    runner->exec_env = wasm_runtime_create_exec_env(runner->instance, WASM_STACK_SIZE);
    runner->function = wasm_runtime_lookup_function(runner->instance, "benchmark_run");
    if (!runner->exec_env || !runner->function) {
        ESP_LOGE(TAG, "export benchmark_run was not found");
        return false;
    }
    return true;
#endif
}

static bool runner_call(runner_t *runner, uint32_t *checksum)
{
    *checksum = 0;
#if defined(WASMHOME_SCENARIO_NATIVE)
    (void)runner;
    *checksum = benchmark_run();
    return true;
#else
    uint32_t argv[1] = {0};
    if (!wasm_runtime_call_wasm(runner->exec_env, runner->function, 0, argv)) {
        ESP_LOGE(TAG, "execution failed: %s",
                 wasm_runtime_get_exception(runner->instance));
        return false;
    }
    *checksum = argv[0];
    return true;
#endif
}

static void runner_close(runner_t *runner)
{
#if !defined(WASMHOME_SCENARIO_NATIVE)
    if (runner->exec_env) wasm_runtime_destroy_exec_env(runner->exec_env);
    if (runner->instance) wasm_runtime_deinstantiate(runner->instance);
    if (runner->module) wasm_runtime_unload(runner->module);
    if (runner->artifact_buffer) os_free(runner->artifact_buffer);
#else
    (void)runner;
#endif
    memset(runner, 0, sizeof(*runner));
}

void app_main(void)
{
    ESP_ERROR_CHECK(wh_platform_start());
#if defined(CONFIG_WASMHOME_PLATFORM_SELFTEST)
    extern esp_err_t wh_platform_selftest_start(void);
    ESP_ERROR_CHECK(wh_platform_selftest_start());
#endif
#if defined(CONFIG_WASMHOME_OBSERVER_SELFTEST)
    extern esp_err_t wh_observer_selftest_start(void);
    ESP_ERROR_CHECK(wh_observer_selftest_start());
#endif
#if !defined(CONFIG_WASMHOME_RUN_BENCHMARK)
    return;
#endif
    const size_t heap_before_runtime = heap_caps_get_free_size(MALLOC_CAP_INTERNAL);
#if !defined(WASMHOME_SCENARIO_NATIVE)
    RuntimeInitArgs args;
    memset(&args, 0, sizeof(args));
    args.mem_alloc_type = Alloc_With_Allocator;
    args.mem_alloc_option.allocator.malloc_func = (void *)os_malloc;
    args.mem_alloc_option.allocator.realloc_func = (void *)os_realloc;
    args.mem_alloc_option.allocator.free_func = (void *)os_free;
    if (!wasm_runtime_full_init(&args)) {
        ESP_LOGE(TAG, "WAMR initialization failed");
        return;
    }
#endif

    runner_t runner;
    if (!runner_open(&runner)) {
        runner_close(&runner);
#if !defined(WASMHOME_SCENARIO_NATIVE)
        wasm_runtime_destroy();
#endif
        return;
    }

    uint32_t checksum = 0;
    bool valid = false;
    for (int i = 0; i < WARMUP_RUNS; ++i) {
        if (!runner_call(&runner, &checksum) || checksum != WASMHOME_EXPECTED_CHECKSUM) {
            ESP_LOGE(TAG, "warmup checksum/execution failed");
            goto cleanup;
        }
    }

    valid = true;
    ESP_LOGI(TAG, "E1 preliminary calls: scenario=%s warmup=%d measured=%d; one load, not independent updates",
             scenario_name, WARMUP_RUNS, MEASURED_RUNS);
    printf("WASMHOME_CSV,scenario,repetition,load_us,instantiate_us,execute_us,heap_before,heap_after_load,heap_after_instantiate,heap_min,checksum,status\n");
    for (int repetition = 1; repetition <= MEASURED_RUNS; ++repetition) {
        int64_t started = esp_timer_get_time();
        bool called = runner_call(&runner, &checksum);
        int64_t execute_us = esp_timer_get_time() - started;
        const char *status = called && checksum == WASMHOME_EXPECTED_CHECKSUM ? "ok" : "invalid";
        if (!called || checksum != WASMHOME_EXPECTED_CHECKSUM) valid = false;
        printf("WASMHOME_CSV,%s,%d,%" PRId64 ",%" PRId64 ",%" PRId64
               ",%u,%u,%u,%u,0x%08" PRIX32 ",%s\n",
               scenario_name, repetition, runner.load_us, runner.instantiate_us,
               execute_us, (unsigned)runner.heap_before,
               (unsigned)runner.heap_after_load,
               (unsigned)runner.heap_after_instantiate,
               (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL),
               checksum, status);
    }

cleanup:
    runner_close(&runner);
#if !defined(WASMHOME_SCENARIO_NATIVE)
    wasm_runtime_destroy();
#endif
    ESP_LOGI(TAG, "E1 scenario=%s status=%s heap_before_runtime=%u heap_after_release=%u",
             scenario_name, valid ? "ok" : "invalid", (unsigned)heap_before_runtime,
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL));
}
