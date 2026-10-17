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

#if defined(CONFIG_WASMHOME_MODULE_SERVICES)
#include "module_manager.h"
#if defined(CONFIG_WASMHOME_MODULE_RESILIENCE_TEST_MODE)
#include "module_resilience_test.h"
#endif
#if defined(WASMHOME_SCENARIO_MODULE_INTERP)
extern const uint8_t service_telemetry_wasm[];
extern const uint32_t service_telemetry_wasm_len;
extern const uint8_t service_control_v1_wasm[];
extern const uint32_t service_control_v1_wasm_len;
extern const uint8_t service_control_v2_wasm[];
extern const uint32_t service_control_v2_wasm_len;
#elif defined(WASMHOME_SCENARIO_MODULE_AOT)
extern const uint8_t service_telemetry_aot[];
extern const uint32_t service_telemetry_aot_len;
extern const uint8_t service_control_v1_aot[];
extern const uint32_t service_control_v1_aot_len;
extern const uint8_t service_control_v2_aot[];
extern const uint32_t service_control_v2_aot_len;
#endif
#endif

#if defined(CONFIG_WASMHOME_RUN_BENCHMARK)
#include <pthread.h>
#endif

#if defined(CONFIG_WASMHOME_NATIVE_SERVICES)
#if !defined(WASMHOME_SCENARIO_NATIVE)
#error "Native functional services cannot be included in interpreter or AOT scenarios"
#endif
#include "native_services.h"
#endif

#if defined(CONFIG_WASMHOME_NATIVE_OTA)
#include "native_ota.h"
#endif

#if defined(CONFIG_WASMHOME_RESILIENCE_TEST_MODE)
#include "resilience_test.h"
#endif

#if defined(CONFIG_WASMHOME_RESILIENCE_TEST_MODE) || \
    defined(CONFIG_WASMHOME_MODULE_RESILIENCE_TEST_MODE)
void wasmhome_issue22_build_provenance_retain(void);
#endif

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
#elif defined(WASMHOME_SCENARIO_MODULE_INTERP)
#if !defined(CONFIG_WAMR_ENABLE_INTERP) || !defined(CONFIG_WAMR_INTERP_CLASSIC) || defined(CONFIG_WAMR_ENABLE_AOT)
#error "module-interp requires only the WAMR classic interpreter"
#endif
#elif defined(WASMHOME_SCENARIO_MODULE_AOT)
#if !defined(CONFIG_WAMR_ENABLE_AOT) || defined(CONFIG_WAMR_ENABLE_INTERP)
#error "module-aot requires only the WAMR AOT runtime"
#endif
#endif
#endif

#if defined(CONFIG_WASMHOME_RUN_BENCHMARK)
#define WARMUP_RUNS 5
#define MEASURED_RUNS 30
#define WASM_STACK_SIZE (8u * 1024u)
#define WASM_HEAP_SIZE (8u * 1024u)
#define BENCHMARK_HOST_THREAD_STACK_SIZE (8u * 1024u)

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

static void memory_event(const char *phase, const char *phase_status,
                         int repetition)
{
    char line[1024];
    char repetition_json[16];
    const uint64_t boot_id = wh_platform_boot_id();
    const size_t internal_total = heap_caps_get_total_size(MALLOC_CAP_INTERNAL);
    multi_heap_info_t internal_info = {0};
    heap_caps_get_info(&internal_info, MALLOC_CAP_INTERNAL);
    const size_t internal_free = internal_info.total_free_bytes;
    const size_t internal_largest = internal_info.largest_free_block;
    if (repetition > 0) {
        (void)snprintf(repetition_json, sizeof(repetition_json), "%d", repetition);
    } else {
        (void)snprintf(repetition_json, sizeof(repetition_json), "null");
    }
#if defined(CONFIG_SPIRAM) && CONFIG_SPIRAM
    const size_t psram_total = heap_caps_get_total_size(MALLOC_CAP_SPIRAM);
    multi_heap_info_t psram_info = {0};
    heap_caps_get_info(&psram_info, MALLOC_CAP_SPIRAM);
    const size_t psram_free = psram_info.total_free_bytes;
    const size_t psram_largest = psram_info.largest_free_block;
    const int written = snprintf(
        line, sizeof(line),
        "WASMHOME_MEMORY,{\"schema\":\"wasmhome-memory-v1\",\"scenario\":\"%s\",\"boot_id\":\"%016" PRIx64
        "\",\"phase\":\"%s\",\"phase_status\":\"%s\",\"units\":\"bytes\",\"scope\":\"dynamic_heap\","
        "\"internal\":{\"capability\":\"MALLOC_CAP_INTERNAL\",\"status\":\"measured\",\"total_bytes\":%zu,\"free_bytes\":%zu,\"largest_free_block_bytes\":%zu,\"phase_peak_free_bytes\":null,\"phase_peak_status\":\"not_instrumented\"},"
        "\"psram\":{\"capability\":\"MALLOC_CAP_SPIRAM\",\"status\":\"measured\",\"total_bytes\":%zu,\"free_bytes\":%zu,\"largest_free_block_bytes\":%zu,\"phase_peak_free_bytes\":null,\"phase_peak_status\":\"not_instrumented\"},"
        "\"static_memory\":{\"status\":\"external_elf\",\"source\":\"ELF\",\"bytes\":null},\"repetition\":%s}",
        scenario_name, boot_id, phase, phase_status, internal_total, internal_free,
        internal_largest, psram_total, psram_free, psram_largest, repetition_json);
#else
    const int written = snprintf(
        line, sizeof(line),
        "WASMHOME_MEMORY,{\"schema\":\"wasmhome-memory-v1\",\"scenario\":\"%s\",\"boot_id\":\"%016" PRIx64
        "\",\"phase\":\"%s\",\"phase_status\":\"%s\",\"units\":\"bytes\",\"scope\":\"dynamic_heap\","
        "\"internal\":{\"capability\":\"MALLOC_CAP_INTERNAL\",\"status\":\"measured\",\"total_bytes\":%zu,\"free_bytes\":%zu,\"largest_free_block_bytes\":%zu,\"phase_peak_free_bytes\":null,\"phase_peak_status\":\"not_instrumented\"},"
        "\"psram\":{\"capability\":\"MALLOC_CAP_SPIRAM\",\"status\":\"not_applicable\",\"total_bytes\":null,\"free_bytes\":null,\"largest_free_block_bytes\":null,\"phase_peak_free_bytes\":null,\"phase_peak_status\":\"not_applicable\"},"
        "\"static_memory\":{\"status\":\"external_elf\",\"source\":\"ELF\",\"bytes\":null},\"repetition\":%s}",
        scenario_name, boot_id, phase, phase_status, internal_total, internal_free,
        internal_largest, repetition_json);
#endif
    if (written < 0 || (size_t)written >= sizeof(line)) {
        ESP_LOGE(TAG, "memory event formatting failed for phase %s", phase);
        return;
    }
    printf("%s\n", line);
}

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
    memory_event("after_load", "not_applicable", 0);
    memory_event("after_instantiate", "not_applicable", 0);
    memory_event("after_exec_env_lookup", "not_applicable", 0);
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
        memory_event("after_load", "empty_artifact", 0);
        ESP_LOGE(TAG, "empty artifact");
        return false;
    }
    runner->artifact_buffer = os_malloc(artifact_len);
    if (!runner->artifact_buffer) {
        memory_event("after_load", "allocation_failed", 0);
        ESP_LOGE(TAG, "artifact buffer allocation failed");
        return false;
    }
    memcpy(runner->artifact_buffer, artifact_start, artifact_len);
    int64_t started = esp_timer_get_time();
    runner->module = wasm_runtime_load(runner->artifact_buffer, artifact_len,
                                       error, sizeof(error));
    runner->load_us = esp_timer_get_time() - started;
    runner->heap_after_load = heap_caps_get_free_size(MALLOC_CAP_INTERNAL);
    memory_event("after_load", runner->module ? "ok" : "failed", 0);
    if (!runner->module) {
        ESP_LOGE(TAG, "load failed: %s", error);
        return false;
    }

    started = esp_timer_get_time();
    runner->instance = wasm_runtime_instantiate(
        runner->module, WASM_STACK_SIZE, WASM_HEAP_SIZE, error, sizeof(error));
    runner->instantiate_us = esp_timer_get_time() - started;
    runner->heap_after_instantiate = heap_caps_get_free_size(MALLOC_CAP_INTERNAL);
    memory_event("after_instantiate", runner->instance ? "ok" : "failed", 0);
    if (!runner->instance) {
        ESP_LOGE(TAG, "instantiate failed: %s", error);
        return false;
    }

    runner->exec_env = wasm_runtime_create_exec_env(runner->instance, WASM_STACK_SIZE);
    runner->function = wasm_runtime_lookup_function(runner->instance, "benchmark_run");
    memory_event("after_exec_env_lookup",
                 runner->exec_env && runner->function ? "ok" : "failed", 0);
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
#endif

#if defined(CONFIG_WASMHOME_RUN_BENCHMARK)
static void run_benchmark(void)
{
    memory_event("before_runtime", "measured", 0);
    const size_t heap_before_runtime = heap_caps_get_free_size(MALLOC_CAP_INTERNAL);
#if !defined(WASMHOME_SCENARIO_NATIVE)
    RuntimeInitArgs args;
    memset(&args, 0, sizeof(args));
    args.mem_alloc_type = Alloc_With_Allocator;
    args.mem_alloc_option.allocator.malloc_func = (void *)os_malloc;
    args.mem_alloc_option.allocator.realloc_func = (void *)os_realloc;
    args.mem_alloc_option.allocator.free_func = (void *)os_free;
    if (!wasm_runtime_full_init(&args)) {
        memory_event("after_init", "failed", 0);
        memory_event("after_release", "not_applicable", 0);
        ESP_LOGE(TAG, "WAMR initialization failed");
        return;
    }
    memory_event("after_init", "ok", 0);
#else
    memory_event("after_init", "not_applicable", 0);
#endif

    runner_t runner;
    if (!runner_open(&runner)) {
        runner_close(&runner);
#if !defined(WASMHOME_SCENARIO_NATIVE)
        wasm_runtime_destroy();
#endif
        memory_event("after_release", "failed", 0);
        return;
    }

    uint32_t checksum = 0;
    bool valid = false;
    for (int i = 0; i < WARMUP_RUNS; ++i) {
        if (!runner_call(&runner, &checksum) || checksum != WASMHOME_EXPECTED_CHECKSUM) {
            memory_event("after_warmup", "failed", 0);
            ESP_LOGE(TAG, "warmup checksum/execution failed");
            goto cleanup;
        }
    }

    valid = true;
    memory_event("after_warmup", "ok", 0);
    ESP_LOGI(TAG, "E1 preliminary calls: scenario=%s warmup=%d measured=%d; one load, not independent updates",
             scenario_name, WARMUP_RUNS, MEASURED_RUNS);
    printf("WASMHOME_CSV,scenario,repetition,load_us,instantiate_us,execute_us,heap_before,heap_after_load,heap_after_instantiate,heap_min,checksum,status\n");
    for (int repetition = 1; repetition <= MEASURED_RUNS; ++repetition) {
        memory_event("before_execution", "measured", repetition);
        int64_t started = esp_timer_get_time();
        bool called = runner_call(&runner, &checksum);
        int64_t execute_us = esp_timer_get_time() - started;
        const char *status = called && checksum == WASMHOME_EXPECTED_CHECKSUM ? "ok" : "invalid";
        if (!called || checksum != WASMHOME_EXPECTED_CHECKSUM) valid = false;
        memory_event("execution", called ? (checksum == WASMHOME_EXPECTED_CHECKSUM ? "ok" : "checksum_invalid") : "call_failed", repetition);
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
    memory_event("after_release", valid ? "ok" : "failed", 0);
    ESP_LOGI(TAG, "E1 scenario=%s status=%s heap_before_runtime=%u heap_after_release=%u",
             scenario_name, valid ? "ok" : "invalid", (unsigned)heap_before_runtime,
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL));
}

static void *benchmark_thread(void *unused)
{
    (void)unused;
    run_benchmark();
    return NULL;
}

static bool start_benchmark_thread(void)
{
    pthread_attr_t attributes;
    pthread_t thread;
    int error = pthread_attr_init(&attributes);
    if (error != 0) {
        ESP_LOGE(TAG, "pthread_attr_init failed: %d", error);
        return false;
    }

    error = pthread_attr_setstacksize(&attributes, BENCHMARK_HOST_THREAD_STACK_SIZE);
    if (error == 0) {
        error = pthread_attr_setdetachstate(&attributes, PTHREAD_CREATE_DETACHED);
    }
    if (error == 0) {
        error = pthread_create(&thread, &attributes, benchmark_thread, NULL);
    }
    const int destroy_error = pthread_attr_destroy(&attributes);
    if (error != 0) {
        ESP_LOGE(TAG, "benchmark pthread creation failed: %d", error);
        return false;
    }
    if (destroy_error != 0) {
        ESP_LOGE(TAG, "pthread_attr_destroy failed after benchmark start: %d", destroy_error);
    }
    return true;
}
#endif

void app_main(void)
{
#if defined(CONFIG_WASMHOME_RESILIENCE_TEST_MODE) || \
    defined(CONFIG_WASMHOME_MODULE_RESILIENCE_TEST_MODE)
    wasmhome_issue22_build_provenance_retain();
#endif
    ESP_ERROR_CHECK(wh_platform_start());
#if defined(CONFIG_WASMHOME_NATIVE_OTA)
    uint32_t reconcile_stack_remaining = 0;
    esp_err_t reconcile_err = wh_native_ota_reconcile_boot(&reconcile_stack_remaining);
    ESP_LOGI("wasmhome", "signed_update_boot_stack_remaining_bytes=%" PRIu32,
             reconcile_stack_remaining);
    ESP_ERROR_CHECK(reconcile_err);
#endif
#if defined(CONFIG_WASMHOME_MODULE_SERVICES)
#if defined(WASMHOME_SCENARIO_MODULE_INTERP)
    const wh_module_image_t telemetry = {
        .bytes = service_telemetry_wasm,
        .size = service_telemetry_wasm_len,
        .module_version = 1,
    };
    const wh_module_image_t control = CONFIG_WASMHOME_MODULE_CONTROL_VERSION == 1
        ? (wh_module_image_t){.bytes = service_control_v1_wasm,
                              .size = service_control_v1_wasm_len,
                              .module_version = 1}
        : (wh_module_image_t){.bytes = service_control_v2_wasm,
                              .size = service_control_v2_wasm_len,
                              .module_version = 2};
#else
    const wh_module_image_t telemetry = {
        .bytes = service_telemetry_aot,
        .size = service_telemetry_aot_len,
        .module_version = 1,
    };
    const wh_module_image_t control = CONFIG_WASMHOME_MODULE_CONTROL_VERSION == 1
        ? (wh_module_image_t){.bytes = service_control_v1_aot,
                              .size = service_control_v1_aot_len,
                              .module_version = 1}
        : (wh_module_image_t){.bytes = service_control_v2_aot,
                              .size = service_control_v2_aot_len,
                              .module_version = 2};
#endif
    ESP_ERROR_CHECK(wh_module_manager_start(&telemetry, &control));
#if defined(CONFIG_WASMHOME_MODULE_RESILIENCE_TEST_MODE)
    ESP_ERROR_CHECK(wh_module_resilience_test_start());
#endif
    return;
#endif
#if defined(CONFIG_WASMHOME_NATIVE_SERVICES)
    ESP_ERROR_CHECK(wh_native_services_start());
#endif
#if defined(CONFIG_WASMHOME_RESILIENCE_TEST_MODE)
    ESP_ERROR_CHECK(wh_resilience_test_start());
#endif
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
#else
    if (!start_benchmark_thread()) {
        ESP_LOGE(TAG, "E1 benchmark was not started");
    }
#endif
}
