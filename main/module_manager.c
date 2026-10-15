#include "module_manager.h"

#include "aot_runtime.h"
#include "bh_platform.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "mbedtls/sha256.h"
#include "module_update.h"
#include "platform.h"
#include "sdkconfig.h"
#include "wasm.h"
#include "wasm_export.h"
#include "wasm_runtime_common.h"

#include <inttypes.h>
#include <limits.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <string.h>

#if !defined(WASM_MEM_ALLOC_WITH_USAGE) || WASM_MEM_ALLOC_WITH_USAGE != 1
#error "The issue #12 manager requires the pinned WAMR usage-aware allocator build"
#endif
#if defined(CONFIG_WAMR_ENABLE_APP_FRAMEWORK) && CONFIG_WAMR_ENABLE_APP_FRAMEWORK
#error "The issue #12 module scenarios require CONFIG_WAMR_ENABLE_APP_FRAMEWORK=n"
#endif

#define WH_MODULE_MAGIC 0x57484d31u
#define WH_CONTROL_REPLAY_CAPACITY 64u
#define WH_CONTROL_QUEUE_CAPACITY 8u
#define WH_TELEMETRY_QUEUE_CAPACITY 4u
#define WH_CONTROL_PACKET_SIZE (WH_HEADER_SIZE + 12u)
#define WH_CONTROL_RESULT_BODY_SIZE 12u
#define WH_WASM_PAGE_BYTES 65536u
#define WH_ERROR_BUFFER_SIZE 192u
#define WH_CONTROL_EXECUTION_LIMIT_US 200000u
#define WH_TELEMETRY_EXECUTION_LIMIT_US 50000u

enum {
    WH_CONTROL_APPLIED = 0,
    WH_CONTROL_MISMATCH = 1,
    WH_CONTROL_NOT_APPLICABLE = 2,
    WH_CONTROL_EXECUTION_ERROR = 3,
};

typedef struct module_slot module_slot_t;

typedef struct {
    uint32_t magic;
    wh_module_role_t role;
    module_slot_t *slot;
    uint64_t generation;
    bool preparing;
    bool dry_run;
    bool invocation_active;
    bool import_violation;
    bool invocation_error;
    bool duplicate;
    uint8_t publish_count;
    uint8_t gpio_count;
    uint8_t requested_gpio;
    bool telemetry_reserved;
    uint64_t sample_id;
    uint64_t published_sample_id;
    uint8_t published_requested;
    wh_abi_snapshot_v1_t snapshot;
    uint8_t snapshot_le[WH_ABI_V1_SNAPSHOT_SIZE];
} module_context_t;

struct module_slot {
    wh_module_role_t role;
    wh_module_slot_state_t state;
    bool candidate;
    uint8_t module_version;
    uint64_t generation;
    uint8_t digest[WH_MODULE_DIGEST_SIZE];
    uint8_t *artifact;
    size_t artifact_size;
    wasm_module_t module;
    wasm_module_inst_t instance;
    wasm_exec_env_t exec_env;
    wasm_function_inst_t abi_version_fn;
    wasm_function_inst_t entry_fn;
    module_context_t context;
    esp_timer_handle_t watchdog;
    uint64_t watchdog_deadline_us;
    atomic_bool in_flight;
    atomic_bool timed_out;
    atomic_uint watchdog_callbacks;
    SemaphoreHandle_t watchdog_gate;
};

typedef struct {
    uint64_t magic;
    size_t bytes;
    size_t payload_bytes;
    mem_alloc_usage_t usage;
    bool external;
} allocation_header_data_t;

typedef union {
    allocation_header_data_t data;
    max_align_t alignment;
    _Alignas(8) uint8_t wasm_heap_alignment;
} allocation_header_t;

_Static_assert(_Alignof(allocation_header_t) >= 8,
               "WAMR allocation headers must be 8-byte aligned");
_Static_assert((_Alignof(allocation_header_t) &
                (_Alignof(allocation_header_t) - 1)) == 0,
               "WAMR allocation alignment must be a power of two");
_Static_assert(sizeof(allocation_header_t) % _Alignof(allocation_header_t) == 0,
               "WAMR allocation payloads must preserve header alignment");

typedef struct {
    uint8_t type;
    uint8_t attempt[16];
    uint8_t body[16];
    uint8_t body_size;
} tx_item_t;

typedef struct {
    uint8_t packet[WH_CONTROL_PACKET_SIZE];
    uint64_t sample_id;
    int32_t temperature_mC;
} control_request_t;

typedef enum {
    CONTROL_JOB_REQUEST = 1,
    CONTROL_JOB_PREPARE = 2,
    CONTROL_JOB_STOP = 3,
} control_job_kind_t;

typedef struct prepare_command {
    const wh_module_image_t *image;
    wh_module_control_commit_fn commit_fn;
    wh_module_control_event_fn event_fn;
    void *context;
    SemaphoreHandle_t done;
    esp_err_t result;
} prepare_command_t;

typedef struct {
    control_job_kind_t kind;
    control_request_t request;
    prepare_command_t *prepare;
} control_job_t;

typedef struct {
    bool valid;
    uint64_t sample_id;
    int32_t temperature_mC;
    uint8_t result[WH_CONTROL_RESULT_BODY_SIZE];
} control_replay_record_t;

static const char *TAG = "wh_module_manager";
static portMUX_TYPE manager_creation_mux = portMUX_INITIALIZER_UNLOCKED;
static portMUX_TYPE allocator_mux = portMUX_INITIALIZER_UNLOCKED;
static portMUX_TYPE reservation_mux = portMUX_INITIALIZER_UNLOCKED;
static uint64_t allocator_cap_bytes;
static uint64_t allocator_live_bytes;
static uint64_t allocator_peak_bytes;
static uint64_t allocator_calls;
static uint64_t allocator_failures;
static uint64_t allocator_runtime_bytes;
static uint64_t allocator_linear_bytes;
static uint64_t allocator_artifact_bytes;
static uint32_t allocator_magic = 0x57484131u;

static module_slot_t telemetry_slot;
static module_slot_t control_slots[2];
static module_slot_t *active_control;
static uint64_t telemetry_operation_memory_generation;
static uint64_t control_operation_memory_generation;
static uint64_t next_generation = 1;
static QueueHandle_t control_queue;
static QueueHandle_t telemetry_output_queue;
static QueueHandle_t control_output_queue;
static SemaphoreHandle_t telemetry_queue_lock;
static SemaphoreHandle_t api_lock;
static SemaphoreHandle_t manager_lock;
static SemaphoreHandle_t worker_startup;
static pthread_t telemetry_thread;
static pthread_t control_thread;
static pthread_t bootstrap_thread;
static bool bootstrap_thread_created;
static bool telemetry_thread_created;
static bool control_thread_created;
static pthread_t tx_thread;
static bool tx_thread_created;
static pthread_t shutdown_thread;
static bool shutdown_thread_created;
static bool telemetry_worker_ok;
static bool control_worker_ok;
static bool tx_worker_ok;
static atomic_bool stop_requested;
static atomic_bool tx_stop_requested;
static bool manager_started;
static bool accepting_requests;
static bool manager_degraded;
static bool handler_installed;
static bool manager_starting;
static bool manager_stopping;
static bool manager_stop_in_progress;
static bool runtime_initialized;
static uint32_t request_handlers_in_flight;
static uint32_t public_calls_in_flight;
static uint32_t control_result_reservations;
static uint32_t telemetry_output_reservations;
static uint64_t control_admission_refusals;
static uint64_t telemetry_queue_full_drops;
static uint64_t telemetry_runtime_busy_drops;
static uint64_t tx_failures;
static uint64_t control_execution_failures;
static uint64_t gpio_failures;
static uint32_t telemetry_stack_high_water;
static uint32_t control_stack_high_water;
static control_replay_record_t replay_records[WH_CONTROL_REPLAY_CAPACITY];
static size_t replay_next;
static uint64_t highest_control_sample_id;

static void slot_release(module_slot_t *slot);
static void *tx_worker_entry(void *argument);
static void update_stack_high_water(uint32_t *destination);
static void slot_info_copy(wh_module_slot_info_t *out, const module_slot_t *slot,
                           wh_module_role_t role);

static bool
manager_ensure_lock(void)
{
    SemaphoreHandle_t current;
    portENTER_CRITICAL(&manager_creation_mux);
    current = manager_lock;
    portEXIT_CRITICAL(&manager_creation_mux);
    if (current) return true;

    SemaphoreHandle_t created = xSemaphoreCreateMutex();
    if (!created) return false;
    portENTER_CRITICAL(&manager_creation_mux);
    if (!manager_lock) {
        manager_lock = created;
        created = NULL;
    }
    current = manager_lock;
    portEXIT_CRITICAL(&manager_creation_mux);
    if (created) vSemaphoreDelete(created);
    return current != NULL;
}

static bool
manager_public_enter(void)
{
    if (!manager_ensure_lock()) return false;
    xSemaphoreTake(manager_lock, portMAX_DELAY);
    if (!manager_started || manager_starting || manager_stopping) {
        xSemaphoreGive(manager_lock);
        return false;
    }
    public_calls_in_flight++;
    xSemaphoreGive(manager_lock);
    return true;
}

static void
manager_public_leave(void)
{
    xSemaphoreTake(manager_lock, portMAX_DELAY);
    if (public_calls_in_flight) public_calls_in_flight--;
    xSemaphoreGive(manager_lock);
}

static bool
add_overflow_size(size_t a, size_t b, size_t *result)
{
    if (a > SIZE_MAX - b) return true;
    *result = a + b;
    return false;
}

static bool
allocation_size_for_payload(size_t payload_bytes, size_t *allocation_bytes)
{
    size_t total_bytes;
    if (add_overflow_size(sizeof(allocation_header_t), payload_bytes,
                          &total_bytes))
        return false;

    const size_t alignment = _Alignof(allocation_header_t);
    const size_t remainder = total_bytes % alignment;
    if (remainder && add_overflow_size(total_bytes, alignment - remainder,
                                       &total_bytes))
        return false;

    *allocation_bytes = total_bytes;
    return true;
}

static bool
usage_valid(mem_alloc_usage_t usage)
{
    return usage == Alloc_For_Runtime || usage == Alloc_For_LinearMemory;
}

static uint32_t
allocator_heap_caps(mem_alloc_usage_t usage)
{
    (void)usage;
#if defined(CONFIG_SPIRAM) && CONFIG_SPIRAM
    if (usage == Alloc_For_LinearMemory)
        return MALLOC_CAP_8BIT | MALLOC_CAP_SPIRAM;
#endif
    return MALLOC_CAP_8BIT | MALLOC_CAP_INTERNAL;
}

static void
allocator_count_call(void)
{
    portENTER_CRITICAL(&allocator_mux);
    allocator_calls++;
    portEXIT_CRITICAL(&allocator_mux);
}

static bool
allocator_reserve(size_t bytes)
{
    bool accepted = false;
    portENTER_CRITICAL(&allocator_mux);
    if (allocator_cap_bytes != 0 && bytes <= allocator_cap_bytes &&
        allocator_live_bytes <= allocator_cap_bytes - bytes) {
        allocator_live_bytes += bytes;
        if (allocator_live_bytes > allocator_peak_bytes)
            allocator_peak_bytes = allocator_live_bytes;
        accepted = true;
    } else {
        allocator_failures++;
    }
    portEXIT_CRITICAL(&allocator_mux);
    return accepted;
}

static void
allocator_record_failure(void)
{
    portENTER_CRITICAL(&allocator_mux);
    allocator_failures++;
    portEXIT_CRITICAL(&allocator_mux);
}

static void
allocator_release_reservation(size_t bytes)
{
    portENTER_CRITICAL(&allocator_mux);
    if (bytes <= allocator_live_bytes) allocator_live_bytes -= bytes;
    else {
        allocator_live_bytes = 0;
        allocator_failures++;
    }
    portEXIT_CRITICAL(&allocator_mux);
}

static void
allocator_add_category(mem_alloc_usage_t usage, size_t bytes)
{
    portENTER_CRITICAL(&allocator_mux);
    if (usage == Alloc_For_Runtime) allocator_runtime_bytes += bytes;
    else allocator_linear_bytes += bytes;
    portEXIT_CRITICAL(&allocator_mux);
}

static void
allocator_sub_category(mem_alloc_usage_t usage, size_t bytes)
{
    portENTER_CRITICAL(&allocator_mux);
    uint64_t *category = usage == Alloc_For_Runtime
        ? &allocator_runtime_bytes : &allocator_linear_bytes;
    if (bytes <= *category) *category -= bytes;
    else {
        *category = 0;
        allocator_failures++;
    }
    portEXIT_CRITICAL(&allocator_mux);
}

static void *
wamr_malloc(mem_alloc_usage_t usage, unsigned int requested_bytes)
{
    allocator_count_call();
    size_t total_bytes;
    if (!usage_valid(usage) ||
        !allocation_size_for_payload(requested_bytes, &total_bytes)) {
        allocator_record_failure();
        return NULL;
    }
    if (!allocator_reserve(total_bytes))
        return NULL;

    /* heap_caps_malloc only guarantees 4-byte alignment on this Xtensa target. */
    allocation_header_t *header = heap_caps_aligned_alloc(
        _Alignof(allocation_header_t), total_bytes,
        allocator_heap_caps(usage));
    if (!header) {
        allocator_release_reservation(total_bytes);
        allocator_record_failure();
        return NULL;
    }
    header->data = (allocation_header_data_t){
        .magic = ((uint64_t)allocator_magic << 32) | allocator_magic,
        .bytes = total_bytes,
        .payload_bytes = requested_bytes,
        .usage = usage,
        .external = false,
    };
    allocator_add_category(usage, total_bytes);
    return header + 1;
}

static void *
wamr_realloc(mem_alloc_usage_t usage, bool full_size_mmaped, void *pointer,
             unsigned int requested_bytes)
{
    allocator_count_call();
    if (full_size_mmaped) {
        allocator_record_failure();
        return NULL;
    }
    if (!pointer) {
        size_t total_bytes;
        if (!usage_valid(usage) ||
            !allocation_size_for_payload(requested_bytes, &total_bytes)) {
            allocator_record_failure();
            return NULL;
        }
        if (!allocator_reserve(total_bytes)) return NULL;
        allocation_header_t *new_header = heap_caps_aligned_alloc(
            _Alignof(allocation_header_t), total_bytes,
            allocator_heap_caps(usage));
        if (!new_header) {
            allocator_release_reservation(total_bytes);
            allocator_record_failure();
            return NULL;
        }
        new_header->data = (allocation_header_data_t){
            .magic = ((uint64_t)allocator_magic << 32) | allocator_magic,
            .bytes = total_bytes,
            .payload_bytes = requested_bytes,
            .usage = usage,
            .external = false,
        };
        allocator_add_category(usage, total_bytes);
        return new_header + 1;
    }
    if (requested_bytes == 0) {
        allocation_header_t *header = (allocation_header_t *)pointer - 1;
        if (!usage_valid(usage) || header->data.external ||
            header->data.magic != (((uint64_t)allocator_magic << 32) | allocator_magic) ||
            header->data.usage != usage) {
            allocator_record_failure();
            return NULL;
        }
        size_t old_bytes = header->data.bytes;
        header->data.magic = 0;
        heap_caps_free(header);
        allocator_release_reservation(old_bytes);
        allocator_sub_category(usage, old_bytes);
        return NULL;
    }
    if (!usage_valid(usage)) {
        allocator_record_failure();
        return NULL;
    }

    allocation_header_t *old_header = (allocation_header_t *)pointer - 1;
    if (old_header->data.magic != (((uint64_t)allocator_magic << 32) | allocator_magic) ||
        old_header->data.external || old_header->data.usage != usage) {
        allocator_record_failure();
        return NULL;
    }
    size_t old_bytes = old_header->data.bytes;
    size_t total_bytes;
    if (!allocation_size_for_payload(requested_bytes, &total_bytes)) {
        allocator_record_failure();
        return NULL;
    }
    if (!allocator_reserve(total_bytes))
        return NULL;

    allocation_header_t *new_header = heap_caps_aligned_alloc(
        _Alignof(allocation_header_t), total_bytes,
        allocator_heap_caps(usage));
    if (!new_header) {
        allocator_release_reservation(total_bytes);
        allocator_record_failure();
        return NULL;
    }
    const size_t copy_bytes = old_header->data.payload_bytes < requested_bytes
        ? old_header->data.payload_bytes : requested_bytes;
    memcpy(new_header + 1, pointer, copy_bytes);
    new_header->data = (allocation_header_data_t){
        .magic = ((uint64_t)allocator_magic << 32) | allocator_magic,
        .bytes = total_bytes,
        .payload_bytes = requested_bytes,
        .usage = usage,
        .external = false,
    };
    old_header->data.magic = 0;
    heap_caps_free(old_header);
    allocator_release_reservation(old_bytes);
    allocator_sub_category(usage, old_bytes);
    allocator_add_category(usage, total_bytes);
    return new_header + 1;
}

static void
wamr_free(mem_alloc_usage_t usage, void *pointer)
{
    allocator_count_call();
    if (!pointer) return;
    allocation_header_t *header = (allocation_header_t *)pointer - 1;
    if (!usage_valid(usage) || header->data.magic !=
        (((uint64_t)allocator_magic << 32) | allocator_magic) ||
        header->data.external || header->data.usage != usage) {
        allocator_record_failure();
        return;
    }
    const size_t bytes = header->data.bytes;
    header->data.magic = 0;
    heap_caps_free(header);
    allocator_release_reservation(bytes);
    allocator_sub_category(usage, bytes);
}

static bool
artifact_reserve(size_t bytes)
{
    if (!allocator_reserve(bytes)) return false;
    portENTER_CRITICAL(&allocator_mux);
    allocator_artifact_bytes += bytes;
    portEXIT_CRITICAL(&allocator_mux);
    return true;
}

static void
artifact_release(size_t bytes)
{
    portENTER_CRITICAL(&allocator_mux);
    if (bytes <= allocator_artifact_bytes) allocator_artifact_bytes -= bytes;
    else {
        allocator_artifact_bytes = 0;
        allocator_failures++;
    }
    portEXIT_CRITICAL(&allocator_mux);
    allocator_release_reservation(bytes);
}

static bool
allocator_snapshot(wh_module_manager_info_t *info)
{
    if (!info) return false;
    portENTER_CRITICAL(&allocator_mux);
    info->allocator_live_bytes = allocator_live_bytes;
    info->allocator_peak_bytes = allocator_peak_bytes;
    info->allocator_calls = allocator_calls;
    info->allocator_failures = allocator_failures;
    info->runtime_live_bytes = allocator_runtime_bytes;
    info->linear_memory_live_bytes = allocator_linear_bytes;
    info->artifact_live_bytes = allocator_artifact_bytes;
    portEXIT_CRITICAL(&allocator_mux);
    return true;
}

static const char *
memory_role_name(wh_module_role_t role, bool candidate)
{
    if (candidate) return "control_candidate";
    if (role == WH_MODULE_ROLE_TELEMETRY) return "telemetry";
    if (role == WH_MODULE_ROLE_CONTROL) return "control";
    return "manager";
}

/* Emit snapshots at lifecycle boundaries so board evidence can distinguish
 * baseline, runtime, each slot, candidate overlap, and release. Heap values
 * come from ESP-IDF; WAMR counters are separate to avoid summing both views. */
static void
module_memory_event(const char *phase, const char *role, const char *status)
{
    multi_heap_info_t internal = {0};
    heap_caps_get_info(&internal, MALLOC_CAP_INTERNAL);
    const size_t internal_total = heap_caps_get_total_size(MALLOC_CAP_INTERNAL);
    wh_module_manager_info_t allocation = {0};
    (void)allocator_snapshot(&allocation);
    uint64_t queue_payload_bytes = 0;
    if (control_queue)
        queue_payload_bytes += WH_CONTROL_QUEUE_CAPACITY * sizeof(control_job_t);
    if (control_output_queue)
        queue_payload_bytes += WH_CONTROL_QUEUE_CAPACITY * sizeof(tx_item_t);
    if (telemetry_output_queue)
        queue_payload_bytes += WH_TELEMETRY_QUEUE_CAPACITY * sizeof(tx_item_t);
    const module_slot_t *watchdog_slots[] = {
        &telemetry_slot, &control_slots[0], &control_slots[1],
    };
    uint32_t persistent_watchdog_slots = 0;
    for (size_t i = 0; i < sizeof(watchdog_slots) / sizeof(watchdog_slots[0]); ++i)
        if (watchdog_slots[i]->watchdog && watchdog_slots[i]->watchdog_gate)
            persistent_watchdog_slots++;
    const uint64_t steady_worker_stack_bytes =
        3u * (uint64_t)CONFIG_WASMHOME_MODULE_HOST_THREAD_STACK_BYTES;
    uint32_t telemetry_hwm_words = 0;
    uint32_t control_hwm_words = 0;
    if (manager_lock && xSemaphoreTake(manager_lock, portMAX_DELAY) == pdTRUE) {
        telemetry_hwm_words = telemetry_stack_high_water;
        control_hwm_words = control_stack_high_water;
        xSemaphoreGive(manager_lock);
    }
#if defined(CONFIG_SPIRAM) && CONFIG_SPIRAM
    multi_heap_info_t psram = {0};
    heap_caps_get_info(&psram, MALLOC_CAP_SPIRAM);
    const size_t psram_total = heap_caps_get_total_size(MALLOC_CAP_SPIRAM);
    printf("WASMHOME_MODULE_MEMORY,{\"schema\":\"wasmhome-module-memory-v1\",\"phase\":\"%s\",\"role\":\"%s\",\"status\":\"%s\",\"units\":\"bytes\","
           "\"internal_heap\":{\"status\":\"measured\",\"total\":%zu,\"free\":%zu,\"largest\":%zu},"
           "\"psram_heap\":{\"status\":\"measured\",\"total\":%zu,\"free\":%zu,\"largest\":%zu},"
           "\"wamr_allocator\":{\"live\":%" PRIu64 ",\"peak\":%" PRIu64 ",\"calls\":%" PRIu64 ",\"failures\":%" PRIu64 ",\"runtime\":%" PRIu64 ",\"linear_memory\":%" PRIu64 ",\"artifacts\":%" PRIu64 "},"
           "\"manager\":{\"queue_payload_capacity\":%" PRIu64 ",\"steady_worker_stack_requested\":%" PRIu64 ",\"persistent_watchdog_slots\":%u,\"telemetry_stack_hwm_words\":%u,\"control_stack_hwm_words\":%u},"
           "\"limits\":{\"allocator_live_cap\":%" PRIu64 ",\"artifact_per_slot\":%u,\"linear_memory_per_slot\":%" PRIu64 ",\"wasm_stack_per_slot\":%u,\"app_heap_per_slot\":%u}}\n",
           phase, role, status, internal_total, internal.total_free_bytes,
           internal.largest_free_block, psram_total, psram.total_free_bytes,
           psram.largest_free_block, allocation.allocator_live_bytes,
           allocation.allocator_peak_bytes, allocation.allocator_calls,
           allocation.allocator_failures, allocation.runtime_live_bytes,
           allocation.linear_memory_live_bytes, allocation.artifact_live_bytes,
           queue_payload_bytes, steady_worker_stack_bytes,
           (unsigned)persistent_watchdog_slots,
           (unsigned)telemetry_hwm_words, (unsigned)control_hwm_words,
           (uint64_t)CONFIG_WASMHOME_MODULE_ALLOCATOR_CAP_BYTES,
           (unsigned)CONFIG_WASMHOME_MODULE_MAX_ARTIFACT_BYTES,
           (uint64_t)CONFIG_WASMHOME_MODULE_MAX_MEMORY_PAGES * WH_WASM_PAGE_BYTES,
           (unsigned)CONFIG_WASMHOME_MODULE_WASM_STACK_BYTES,
           (unsigned)CONFIG_WASMHOME_MODULE_APP_HEAP_BYTES);
#else
    printf("WASMHOME_MODULE_MEMORY,{\"schema\":\"wasmhome-module-memory-v1\",\"phase\":\"%s\",\"role\":\"%s\",\"status\":\"%s\",\"units\":\"bytes\","
           "\"internal_heap\":{\"status\":\"measured\",\"total\":%zu,\"free\":%zu,\"largest\":%zu},"
           "\"psram_heap\":{\"status\":\"not_applicable\",\"total\":null,\"free\":null,\"largest\":null},"
           "\"wamr_allocator\":{\"live\":%" PRIu64 ",\"peak\":%" PRIu64 ",\"calls\":%" PRIu64 ",\"failures\":%" PRIu64 ",\"runtime\":%" PRIu64 ",\"linear_memory\":%" PRIu64 ",\"artifacts\":%" PRIu64 "},"
           "\"manager\":{\"queue_payload_capacity\":%" PRIu64 ",\"steady_worker_stack_requested\":%" PRIu64 ",\"persistent_watchdog_slots\":%u,\"telemetry_stack_hwm_words\":%u,\"control_stack_hwm_words\":%u},"
           "\"limits\":{\"allocator_live_cap\":%" PRIu64 ",\"artifact_per_slot\":%u,\"linear_memory_per_slot\":%" PRIu64 ",\"wasm_stack_per_slot\":%u,\"app_heap_per_slot\":%u}}\n",
           phase, role, status, internal_total, internal.total_free_bytes,
           internal.largest_free_block, allocation.allocator_live_bytes,
           allocation.allocator_peak_bytes, allocation.allocator_calls,
           allocation.allocator_failures, allocation.runtime_live_bytes,
           allocation.linear_memory_live_bytes, allocation.artifact_live_bytes,
           queue_payload_bytes, steady_worker_stack_bytes,
           (unsigned)persistent_watchdog_slots,
           (unsigned)telemetry_hwm_words, (unsigned)control_hwm_words,
           (uint64_t)CONFIG_WASMHOME_MODULE_ALLOCATOR_CAP_BYTES,
           (unsigned)CONFIG_WASMHOME_MODULE_MAX_ARTIFACT_BYTES,
           (uint64_t)CONFIG_WASMHOME_MODULE_MAX_MEMORY_PAGES * WH_WASM_PAGE_BYTES,
           (unsigned)CONFIG_WASMHOME_MODULE_WASM_STACK_BYTES,
           (unsigned)CONFIG_WASMHOME_MODULE_APP_HEAP_BYTES);
#endif
}

static bool
function_type_is(wasm_func_type_t type, const wasm_valkind_t *parameters,
                 uint32_t parameter_count, wasm_valkind_t result)
{
    if (!type || wasm_func_type_get_param_count(type) != parameter_count ||
        wasm_func_type_get_result_count(type) != 1 ||
        wasm_func_type_get_result_valkind(type, 0) != result)
        return false;
    for (uint32_t i = 0; i < parameter_count; ++i) {
        if (wasm_func_type_get_param_valkind(type, i) != parameters[i]) return false;
    }
    return true;
}

static bool
import_is_expected(wh_module_role_t role, const char *module_name,
                   const char *name, wasm_func_type_t type)
{
    static const wasm_valkind_t p_i32[] = { WASM_I32 };
    static const wasm_valkind_t p_i64_i32[] = { WASM_I64, WASM_I32 };
    if (!module_name || !name) return false;
    if (role == WH_MODULE_ROLE_TELEMETRY) {
        if (strcmp(module_name, "wasmhome_telemetry_v1") != 0) return false;
        if (strcmp(name, "snapshot") == 0)
            return function_type_is(type, p_i32, 1, WASM_I32);
        if (strcmp(name, "monotonic_us") == 0)
            return function_type_is(type, NULL, 0, WASM_I64);
        if (strcmp(name, "publish_telemetry") == 0)
            return function_type_is(type, NULL, 0, WASM_I32);
        return false;
    }
    if (role == WH_MODULE_ROLE_CONTROL) {
        if (strcmp(module_name, "wasmhome_control_v1") != 0) return false;
        if (strcmp(name, "monotonic_us") == 0)
            return function_type_is(type, NULL, 0, WASM_I64);
        if (strcmp(name, "gpio_request") == 0)
            return function_type_is(type, p_i32, 1, WASM_I32);
        if (strcmp(name, "publish_control") == 0)
            return function_type_is(type, p_i64_i32, 2, WASM_I32);
    }
    return false;
}

static bool
validate_imports(wasm_module_t module, wh_module_role_t role)
{
    const int32_t count = wasm_runtime_get_import_count(module);
    if (count < 0 || count > 3) return false;
    for (int32_t i = 0; i < count; ++i) {
        wasm_import_t import_type;
        memset(&import_type, 0, sizeof(import_type));
        wasm_runtime_get_import_type(module, i, &import_type);
        if (import_type.kind != WASM_IMPORT_EXPORT_KIND_FUNC ||
            !import_is_expected(role, import_type.module_name, import_type.name,
                                import_type.u.func_type))
            return false;
        for (int32_t j = 0; j < i; ++j) {
            wasm_import_t previous;
            memset(&previous, 0, sizeof(previous));
            wasm_runtime_get_import_type(module, j, &previous);
            if (previous.module_name && previous.name &&
                strcmp(previous.module_name, import_type.module_name) == 0 &&
                strcmp(previous.name, import_type.name) == 0)
                return false;
        }
    }
    return true;
}

static bool
validate_exports(wasm_module_t module, wh_module_role_t role)
{
    const char *entry_name = role == WH_MODULE_ROLE_TELEMETRY
        ? "wh_telemetry_tick" : "wh_control_request";
    const char *opposite_name = role == WH_MODULE_ROLE_TELEMETRY
        ? "wh_control_request" : "wh_telemetry_tick";
    static const wasm_valkind_t p_control[] = { WASM_I64, WASM_I32 };
    const int32_t count = wasm_runtime_get_export_count(module);
    if (count < 2 || count > 3) return false;
    bool abi_seen = false, entry_seen = false, memory_seen = false;
    for (int32_t i = 0; i < count; ++i) {
        wasm_export_t export_type;
        memset(&export_type, 0, sizeof(export_type));
        wasm_runtime_get_export_type(module, i, &export_type);
        if (!export_type.name) return false;
        if (strcmp(export_type.name, "wh_abi_version") == 0) {
            if (abi_seen || export_type.kind != WASM_IMPORT_EXPORT_KIND_FUNC ||
                !function_type_is(export_type.u.func_type, NULL, 0, WASM_I32))
                return false;
            abi_seen = true;
        } else if (strcmp(export_type.name, entry_name) == 0) {
            if (entry_seen || export_type.kind != WASM_IMPORT_EXPORT_KIND_FUNC)
                return false;
            if (role == WH_MODULE_ROLE_TELEMETRY) {
                if (!function_type_is(export_type.u.func_type, NULL, 0, WASM_I32))
                    return false;
            } else if (!function_type_is(export_type.u.func_type, p_control, 2, WASM_I32)) {
                return false;
            }
            entry_seen = true;
        } else if (strcmp(export_type.name, "memory") == 0) {
            if (memory_seen || export_type.kind != WASM_IMPORT_EXPORT_KIND_MEMORY)
                return false;
            memory_seen = true;
        } else if (strcmp(export_type.name, opposite_name) == 0 ||
                   strcmp(export_type.name, "wh_abi_version") == 0) {
            return false;
        } else {
            return false;
        }
    }
    return abi_seen && entry_seen;
}

static bool
module_has_no_start(wasm_module_t module)
{
    switch (wasm_runtime_get_module_package_type(module)) {
    case Wasm_Module_Bytecode: {
        const WASMModule *bytecode = (const WASMModule *)module;
        return bytecode->start_function == UINT32_MAX;
    }
    case Wasm_Module_AoT: {
        const AOTModule *aot = (const AOTModule *)module;
        return aot->start_func_index == UINT32_MAX;
    }
    default:
        return false;
    }
}

static void
watchdog_callback(void *argument)
{
    module_slot_t *slot = argument;
    atomic_fetch_add_explicit(&slot->watchdog_callbacks, 1, memory_order_acq_rel);
    if (slot->watchdog_gate && xSemaphoreTake(slot->watchdog_gate, portMAX_DELAY) == pdTRUE) {
        const bool in_flight = atomic_load_explicit(&slot->in_flight, memory_order_acquire);
        const uint64_t now_us = (uint64_t)esp_timer_get_time();
        if (in_flight && slot->watchdog_deadline_us != 0 &&
            now_us >= slot->watchdog_deadline_us && slot->instance) {
            atomic_store_explicit(&slot->timed_out, true, memory_order_release);
            wasm_runtime_terminate(slot->instance);
        }
        xSemaphoreGive(slot->watchdog_gate);
    }
    atomic_fetch_sub_explicit(&slot->watchdog_callbacks, 1, memory_order_acq_rel);
}

static bool
call_with_limit(module_slot_t *slot, wasm_function_inst_t function,
                uint32_t argument_count, wasm_val_t *arguments,
                uint64_t limit_us, int32_t *return_value)
{
    if (!slot || !slot->exec_env || !slot->instance || !function ||
        !slot->watchdog || !slot->watchdog_gate ||
        atomic_load_explicit(&slot->in_flight, memory_order_acquire))
        return false;
    if (xSemaphoreTake(slot->watchdog_gate, portMAX_DELAY) != pdTRUE)
        return false;
    atomic_store_explicit(&slot->timed_out, false, memory_order_release);
    slot->watchdog_deadline_us = (uint64_t)esp_timer_get_time() + limit_us;
    atomic_store_explicit(&slot->in_flight, true, memory_order_release);
    if (esp_timer_start_once(slot->watchdog, limit_us) != ESP_OK) {
        atomic_store_explicit(&slot->in_flight, false, memory_order_release);
        slot->watchdog_deadline_us = 0;
        xSemaphoreGive(slot->watchdog_gate);
        return false;
    }
    xSemaphoreGive(slot->watchdog_gate);

    wasm_val_t result = {.kind = WASM_I32};
    wasm_runtime_clear_exception(slot->instance);
    const int64_t started = esp_timer_get_time();
    const bool call_ok = wasm_runtime_call_wasm_a(slot->exec_env, function, 1,
                                                   &result, argument_count,
                                                   arguments);
    /* Publish completion before stopping the timer. A callback already queued
     * by the expiry can arrive later; the persistent gate and deadline check
     * make that stale callback harmless to this slot's next generation. */
    atomic_store_explicit(&slot->in_flight, false, memory_order_release);
    const int64_t elapsed = esp_timer_get_time() - started;

    (void)esp_timer_stop(slot->watchdog);
    if (xSemaphoreTake(slot->watchdog_gate, portMAX_DELAY) == pdTRUE) {
        slot->watchdog_deadline_us = 0;
        xSemaphoreGive(slot->watchdog_gate);
    }
    while (atomic_load_explicit(&slot->watchdog_callbacks, memory_order_acquire) != 0)
        vTaskDelay(1);

    if (return_value) *return_value = result.of.i32;
    return call_ok && !atomic_load_explicit(&slot->timed_out, memory_order_acquire) &&
        elapsed <= (int64_t)limit_us && result.kind == WASM_I32;
}

static esp_err_t
slot_load(module_slot_t *slot, wh_module_role_t role,
          const wh_module_image_t *image, bool candidate,
          wh_module_control_event_fn event_fn, void *event_context)
{
    if (!slot || !image || !image->bytes || image->size == 0 ||
        image->size > CONFIG_WASMHOME_MODULE_MAX_ARTIFACT_BYTES ||
        image->size > UINT32_MAX || !wh_abi_module_version_valid(image->module_version))
        return ESP_ERR_INVALID_ARG;
    /* The slot and watchdog callback argument have static lifetime. Keep the
     * timer and gate across generations: IDF 5.2.6 dispatches timer deletion
     * asynchronously, so freeing/reinitializing callback state here can race
     * a callback that has expired but has not entered yet. */
    slot->role = 0;
    slot->state = WH_MODULE_SLOT_EMPTY;
    slot->candidate = false;
    slot->module_version = 0;
    slot->generation = 0;
    memset(slot->digest, 0, sizeof(slot->digest));
    slot->artifact = NULL;
    slot->artifact_size = 0;
    slot->module = NULL;
    slot->exec_env = NULL;
    slot->abi_version_fn = NULL;
    slot->entry_fn = NULL;
    memset(&slot->context, 0, sizeof(slot->context));
    atomic_store_explicit(&slot->in_flight, false, memory_order_release);
    atomic_store_explicit(&slot->timed_out, false, memory_order_release);
    slot->role = role;
    slot->state = WH_MODULE_SLOT_PREPARING;
    slot->candidate = candidate;
    slot->module_version = image->module_version;
    slot->generation = next_generation++;
    if (slot->generation == 0) slot->generation = next_generation++;

    if (!artifact_reserve(image->size)) {
        module_memory_event("artifact_copy", memory_role_name(role, candidate), "failed");
        return ESP_ERR_NO_MEM;
    }
    slot->artifact = heap_caps_malloc(image->size, MALLOC_CAP_8BIT | MALLOC_CAP_INTERNAL);
    if (!slot->artifact) {
        artifact_release(image->size);
        allocator_record_failure();
        module_memory_event("artifact_copy", memory_role_name(role, candidate), "failed");
        return ESP_ERR_NO_MEM;
    }
    slot->artifact_size = image->size;
    memcpy(slot->artifact, image->bytes, image->size);
    module_memory_event("artifact_copied", memory_role_name(role, candidate), "ok");
    if (mbedtls_sha256(slot->artifact, slot->artifact_size, slot->digest, 0) != 0) {
        slot_release(slot);
        return ESP_FAIL;
    }

    if (!slot->watchdog_gate) slot->watchdog_gate = xSemaphoreCreateMutex();
    if (!slot->watchdog_gate) {
        slot_release(slot);
        return ESP_ERR_NO_MEM;
    }
    esp_timer_create_args_t watchdog_args = {
        .callback = watchdog_callback,
        .arg = slot,
        .dispatch_method = ESP_TIMER_TASK,
        .name = role == WH_MODULE_ROLE_TELEMETRY ? "wh_wasm_telemetry" : "wh_wasm_control",
    };
    if (!slot->watchdog && esp_timer_create(&watchdog_args, &slot->watchdog) != ESP_OK) {
        slot_release(slot);
        return ESP_FAIL;
    }

    char error[WH_ERROR_BUFFER_SIZE] = {0};
    slot->module = wasm_runtime_load(slot->artifact, (uint32_t)slot->artifact_size,
                                     error, sizeof(error));
    if (!slot->module) {
        ESP_LOGE(TAG, "load failed (%d): %s", (int)role, error);
        module_memory_event("module_load", memory_role_name(role, candidate), "failed");
        slot_release(slot);
        return ESP_FAIL;
    }
    module_memory_event("module_load", memory_role_name(role, candidate), "ok");
    if (candidate && role == WH_MODULE_ROLE_CONTROL && event_fn) {
        wh_module_slot_info_t telemetry_info, control_info;
        slot_info_copy(&telemetry_info, &telemetry_slot, WH_MODULE_ROLE_TELEMETRY);
        slot_info_copy(&control_info, slot, WH_MODULE_ROLE_CONTROL);
        xSemaphoreTake(manager_lock, portMAX_DELAY);
        const uint64_t busy_drops = telemetry_runtime_busy_drops;
        xSemaphoreGive(manager_lock);
        event_fn(event_context, "candidate_loaded", &telemetry_info, &control_info,
                 busy_drops);
    }
    const bool no_start = module_has_no_start(slot->module);
    const bool imports_valid = validate_imports(slot->module, role);
    const bool exports_valid = validate_exports(slot->module, role);
    if (!no_start || !imports_valid || !exports_valid) {
        ESP_LOGE(TAG,
                 "preflight rejected role=%d package=%d no_start=%d imports=%d exports=%d",
                 (int)role,
                 (int)wasm_runtime_get_module_package_type(slot->module),
                 (int)no_start, (int)imports_valid, (int)exports_valid);
        module_memory_event("module_preflight", memory_role_name(role, candidate), "failed");
        slot_release(slot);
        return ESP_ERR_INVALID_ARG;
    }

    struct InstantiationArgs2 *args = NULL;
    if (!wasm_runtime_instantiation_args_create(&args) || !args) {
        slot_release(slot);
        return ESP_ERR_NO_MEM;
    }
    wasm_runtime_instantiation_args_set_default_stack_size(
        args, CONFIG_WASMHOME_MODULE_WASM_STACK_BYTES);
    wasm_runtime_instantiation_args_set_host_managed_heap_size(
        args, CONFIG_WASMHOME_MODULE_APP_HEAP_BYTES);
    wasm_runtime_instantiation_args_set_max_memory_pages(
        args, CONFIG_WASMHOME_MODULE_MAX_MEMORY_PAGES);
    slot->instance = wasm_runtime_instantiate_ex2(slot->module, args, error,
                                                  sizeof(error));
    wasm_runtime_instantiation_args_destroy(args);
    if (!slot->instance) {
        ESP_LOGE(TAG, "instantiate failed (%d): %s", (int)role, error);
        module_memory_event("module_instantiate", memory_role_name(role, candidate), "failed");
        slot_release(slot);
        return ESP_FAIL;
    }
    module_memory_event("module_instantiate", memory_role_name(role, candidate), "ok");
    if (candidate && role == WH_MODULE_ROLE_CONTROL && event_fn) {
        wh_module_slot_info_t telemetry_info, control_info;
        slot_info_copy(&telemetry_info, &telemetry_slot, WH_MODULE_ROLE_TELEMETRY);
        slot_info_copy(&control_info, slot, WH_MODULE_ROLE_CONTROL);
        xSemaphoreTake(manager_lock, portMAX_DELAY);
        const uint64_t busy_drops = telemetry_runtime_busy_drops;
        xSemaphoreGive(manager_lock);
        event_fn(event_context, "candidate_instantiated", &telemetry_info, &control_info,
                 busy_drops);
    }

    slot->context = (module_context_t){
        .magic = WH_MODULE_MAGIC,
        .role = role,
        .slot = slot,
        .generation = slot->generation,
        .preparing = true,
    };
    wasm_runtime_set_custom_data(slot->instance, &slot->context);
    slot->exec_env = wasm_runtime_create_exec_env(
        slot->instance, CONFIG_WASMHOME_MODULE_WASM_STACK_BYTES);
    if (!slot->exec_env) {
        slot_release(slot);
        return ESP_ERR_NO_MEM;
    }
    slot->abi_version_fn = wasm_runtime_lookup_function(slot->instance, "wh_abi_version");
    slot->entry_fn = wasm_runtime_lookup_function(
        slot->instance, role == WH_MODULE_ROLE_TELEMETRY
            ? "wh_telemetry_tick" : "wh_control_request");
    if (!slot->abi_version_fn || !slot->entry_fn) {
        module_memory_event("exec_env_and_exports", memory_role_name(role, candidate), "failed");
        slot_release(slot);
        return ESP_ERR_INVALID_ARG;
    }
    module_memory_event("exec_env_and_exports", memory_role_name(role, candidate), "ok");

    int32_t abi_version = -1;
    slot->context.invocation_active = true;
    const bool abi_call_ok = call_with_limit(slot, slot->abi_version_fn, 0, NULL,
                                              WH_CONTROL_EXECUTION_LIMIT_US,
                                              &abi_version);
    slot->context.invocation_active = false;
    slot->context.preparing = false;
    if (!abi_call_ok || abi_version != WH_ABI_V1_VERSION ||
        slot->context.import_violation) {
        ESP_LOGE(TAG, "ABI version probe rejected role=%d", (int)role);
        module_memory_event("module_abi_probe", memory_role_name(role, candidate), "failed");
        slot_release(slot);
        return ESP_ERR_INVALID_ARG;
    }
    slot->state = candidate ? WH_MODULE_SLOT_PREPARING : WH_MODULE_SLOT_READY;
    module_memory_event("module_ready", memory_role_name(role, candidate), "ok");
    return ESP_OK;
}

static void
slot_release(module_slot_t *slot)
{
    if (!slot) return;
    if (atomic_load_explicit(&slot->in_flight, memory_order_acquire)) return;
    const char *role_name = memory_role_name(slot->role, slot->candidate);
    module_memory_event("slot_release_begin", role_name, "measured");
    /* Keep timer and gate allocated for the static slot lifetime. IDF 5.2.6
     * queues timer deletion asynchronously and has no blocking-stop API. */
    if (slot->watchdog) (void)esp_timer_stop(slot->watchdog);
    wasm_module_inst_t instance = slot->instance;
    if (slot->watchdog_gate &&
        xSemaphoreTake(slot->watchdog_gate, portMAX_DELAY) == pdTRUE) {
        if (atomic_load_explicit(&slot->in_flight, memory_order_acquire)) {
            xSemaphoreGive(slot->watchdog_gate);
            return;
        }
        slot->watchdog_deadline_us = 0;
        instance = slot->instance;
        slot->instance = NULL;
        xSemaphoreGive(slot->watchdog_gate);
    } else {
        if (atomic_load_explicit(&slot->in_flight, memory_order_acquire)) return;
        slot->watchdog_deadline_us = 0;
        slot->instance = NULL;
    }
    while (atomic_load_explicit(&slot->watchdog_callbacks, memory_order_acquire) != 0)
        vTaskDelay(1);
    if (slot->exec_env) {
        wasm_runtime_destroy_exec_env(slot->exec_env);
        slot->exec_env = NULL;
    }
    if (instance) wasm_runtime_deinstantiate(instance);
    if (slot->module) {
        wasm_runtime_unload(slot->module);
        slot->module = NULL;
    }
    if (slot->artifact) {
        size_t size = slot->artifact_size;
        heap_caps_free(slot->artifact);
        slot->artifact = NULL;
        artifact_release(size);
    }
    slot->role = 0;
    slot->state = WH_MODULE_SLOT_EMPTY;
    slot->candidate = false;
    slot->module_version = 0;
    slot->generation = 0;
    memset(slot->digest, 0, sizeof(slot->digest));
    slot->artifact = NULL;
    slot->artifact_size = 0;
    slot->module = NULL;
    slot->exec_env = NULL;
    slot->abi_version_fn = NULL;
    slot->entry_fn = NULL;
    memset(&slot->context, 0, sizeof(slot->context));
    atomic_store_explicit(&slot->in_flight, false, memory_order_release);
    atomic_store_explicit(&slot->timed_out, false, memory_order_release);
    module_memory_event("slot_released", role_name, "measured");
}


static module_context_t *
callback_context(wasm_exec_env_t exec_env, wh_module_role_t role)
{
    if (!exec_env) return NULL;
    wasm_module_inst_t instance = wasm_runtime_get_module_inst(exec_env);
    module_context_t *context = instance
        ? wasm_runtime_get_custom_data(instance) : NULL;
    if (!context || context->magic != WH_MODULE_MAGIC ||
        context->role != role || !context->slot ||
        context->slot->role != role || context->slot->generation != context->generation ||
        &context->slot->context != context) {
        if (context && context->magic == WH_MODULE_MAGIC) {
            context->invocation_error = true;
            context->import_violation = true;
        }
        return NULL;
    }
    const bool dry_run_candidate = context->dry_run && context->slot->candidate &&
        context->slot->state == WH_MODULE_SLOT_PREPARING;
    if (context->preparing || !context->invocation_active ||
        (context->slot->state != WH_MODULE_SLOT_ACTIVE && !dry_run_candidate)) {
        context->invocation_error = true;
        context->import_violation = true;
        return NULL;
    }
    return context;
}

static bool
reserve_telemetry_output(void)
{
    bool reserved = false;
    if (!telemetry_queue_lock ||
        xSemaphoreTake(telemetry_queue_lock, 0) != pdTRUE) {
        portENTER_CRITICAL(&reservation_mux);
        telemetry_queue_full_drops++;
        portEXIT_CRITICAL(&reservation_mux);
        return false;
    }
    if (telemetry_output_queue &&
        uxQueueMessagesWaiting(telemetry_output_queue) +
            telemetry_output_reservations < WH_TELEMETRY_QUEUE_CAPACITY) {
        telemetry_output_reservations++;
        reserved = true;
    } else {
        portENTER_CRITICAL(&reservation_mux);
        telemetry_queue_full_drops++;
        portEXIT_CRITICAL(&reservation_mux);
    }
    xSemaphoreGive(telemetry_queue_lock);
    return reserved;
}

static void
release_telemetry_reservation(void)
{
    if (!telemetry_queue_lock ||
        xSemaphoreTake(telemetry_queue_lock, portMAX_DELAY) != pdTRUE)
        return;
    if (telemetry_output_reservations) telemetry_output_reservations--;
    xSemaphoreGive(telemetry_queue_lock);
}

static void
native_snapshot(wasm_exec_env_t exec_env, uint64_t *args)
{
    native_raw_return_type(uint32_t, args);
    native_raw_get_arg(uint32_t, offset, args);
    module_context_t *context = callback_context(exec_env, WH_MODULE_ROLE_TELEMETRY);
    uint32_t status = WH_ERR_STATE;
    if (context) {
        wasm_module_inst_t instance = wasm_runtime_get_module_inst(exec_env);
        wasm_memory_inst_t memory = wasm_runtime_get_memory(instance, 0);
        const uint64_t page_count = memory ? wasm_memory_get_cur_page_count(memory) : 0;
        const uint64_t page_size = memory ? wasm_memory_get_bytes_per_page(memory) : 0;
        const uint64_t memory_size = page_count * page_size;
        if (!memory || page_size == 0 || memory_size > UINT32_MAX ||
            !wh_abi_range_valid((uint32_t)memory_size, offset,
                                WH_ABI_V1_SNAPSHOT_SIZE) ||
            !wasm_runtime_validate_app_addr(instance, offset,
                                            WH_ABI_V1_SNAPSHOT_SIZE)) {
            status = WH_ERR_BOUNDS;
        } else if (!wh_abi_snapshot_encode(context->snapshot_le,
                                           &context->snapshot)) {
            context->invocation_error = true;
            status = WH_ERR_INVALID;
        } else {
            void *guest = wasm_runtime_addr_app_to_native(instance, offset);
            if (!guest) {
                status = WH_ERR_BOUNDS;
            } else {
                memcpy(guest, context->snapshot_le, WH_ABI_V1_SNAPSHOT_SIZE);
                status = WH_OK;
            }
        }
        if (status != WH_OK) context->invocation_error = true;
    }
    native_raw_set_return(status);
}

static void
native_monotonic_telemetry(wasm_exec_env_t exec_env, uint64_t *args)
{
    native_raw_return_type(uint64_t, args);
    (void)args;
    module_context_t *context = callback_context(exec_env, WH_MODULE_ROLE_TELEMETRY);
    native_raw_set_return(context ? (uint64_t)esp_timer_get_time() : UINT64_MAX);
}

static void
native_publish_telemetry(wasm_exec_env_t exec_env, uint64_t *args)
{
    native_raw_return_type(uint32_t, args);
    (void)args;
    module_context_t *context = callback_context(exec_env, WH_MODULE_ROLE_TELEMETRY);
    uint32_t status = WH_ERR_STATE;
    if (context) {
        if (context->publish_count != 0) {
            context->duplicate = true;
            context->invocation_error = true;
            status = WH_ERR_DUPLICATE;
        } else if (!reserve_telemetry_output()) {
            context->invocation_error = true;
            status = WH_ERR_QUEUE_FULL;
        } else {
            context->publish_count = 1;
            context->telemetry_reserved = true;
            status = WH_OK;
        }
    }
    native_raw_set_return(status);
}

static void
native_monotonic_control(wasm_exec_env_t exec_env, uint64_t *args)
{
    native_raw_return_type(uint64_t, args);
    (void)args;
    module_context_t *context = callback_context(exec_env, WH_MODULE_ROLE_CONTROL);
    native_raw_set_return(context ? (uint64_t)esp_timer_get_time() : UINT64_MAX);
}

static void
native_gpio_request(wasm_exec_env_t exec_env, uint64_t *args)
{
    native_raw_return_type(uint32_t, args);
    native_raw_get_arg(uint32_t, level, args);
    module_context_t *context = callback_context(exec_env, WH_MODULE_ROLE_CONTROL);
    uint32_t status = WH_ERR_STATE;
    if (context) {
        if (level > 1) {
            context->invocation_error = true;
            status = WH_ERR_INVALID;
        } else if (context->gpio_count != 0) {
            context->duplicate = true;
            context->invocation_error = true;
            status = WH_ERR_DUPLICATE;
        } else {
            context->gpio_count = 1;
            context->requested_gpio = (uint8_t)level;
            status = WH_OK;
        }
    }
    native_raw_set_return(status);
}

static void
native_publish_control(wasm_exec_env_t exec_env, uint64_t *args)
{
    native_raw_return_type(uint32_t, args);
    native_raw_get_arg(uint64_t, sample_id, args);
    native_raw_get_arg(uint32_t, requested, args);
    module_context_t *context = callback_context(exec_env, WH_MODULE_ROLE_CONTROL);
    uint32_t status = WH_ERR_STATE;
    if (context) {
        if (context->publish_count != 0) {
            context->duplicate = true;
            context->invocation_error = true;
            status = WH_ERR_DUPLICATE;
        } else if (requested > 1 || sample_id != context->sample_id) {
            context->invocation_error = true;
            status = WH_ERR_INVALID;
        } else {
            context->publish_count = 1;
            context->published_sample_id = sample_id;
            context->published_requested = (uint8_t)requested;
            status = WH_OK;
        }
    }
    native_raw_set_return(status);
}

static NativeSymbol telemetry_symbols[] = {
    { "snapshot", native_snapshot, "(i)i", NULL },
    { "monotonic_us", native_monotonic_telemetry, "()I", NULL },
    { "publish_telemetry", native_publish_telemetry, "()i", NULL },
};

static NativeSymbol control_symbols[] = {
    { "monotonic_us", native_monotonic_control, "()I", NULL },
    { "gpio_request", native_gpio_request, "(i)i", NULL },
    { "publish_control", native_publish_control, "(Ii)i", NULL },
};

static void
increment_control_reservation(void)
{
    if (control_result_reservations) control_result_reservations--;
}

static bool
enqueue_control_output(const tx_item_t *item, bool reservation_held)
{
    bool sent = false;
    xSemaphoreTake(manager_lock, portMAX_DELAY);
    if (reservation_held) increment_control_reservation();
    if (control_output_queue && xQueueSend(control_output_queue, item, 0) == pdTRUE)
        sent = true;
    if (!sent) tx_failures++;
    xSemaphoreGive(manager_lock);
    return sent;
}

static bool
enqueue_telemetry_output(const tx_item_t *item)
{
    bool sent = false;
    if (!telemetry_queue_lock ||
        xSemaphoreTake(telemetry_queue_lock, portMAX_DELAY) != pdTRUE)
        return false;
    if (telemetry_output_queue && telemetry_output_reservations &&
        xQueueSend(telemetry_output_queue, item, 0) == pdTRUE) {
        telemetry_output_reservations--;
        sent = true;
    } else if (telemetry_output_reservations) {
        telemetry_output_reservations--;
        portENTER_CRITICAL(&reservation_mux);
        telemetry_queue_full_drops++;
        portEXIT_CRITICAL(&reservation_mux);
    }
    xSemaphoreGive(telemetry_queue_lock);
    return sent;
}

static void
replay_store(uint64_t sample_id, int32_t temperature_mC,
             const uint8_t result[WH_CONTROL_RESULT_BODY_SIZE])
{
    xSemaphoreTake(manager_lock, portMAX_DELAY);
    control_replay_record_t *record = &replay_records[replay_next];
    record->valid = true;
    record->sample_id = sample_id;
    record->temperature_mC = temperature_mC;
    memcpy(record->result, result, WH_CONTROL_RESULT_BODY_SIZE);
    replay_next = (replay_next + 1) % WH_CONTROL_REPLAY_CAPACITY;
    if (sample_id > highest_control_sample_id)
        highest_control_sample_id = sample_id;
    xSemaphoreGive(manager_lock);
}

static control_replay_record_t *
replay_find(uint64_t sample_id)
{
    for (size_t i = 0; i < WH_CONTROL_REPLAY_CAPACITY; ++i) {
        if (replay_records[i].valid && replay_records[i].sample_id == sample_id)
            return &replay_records[i];
    }
    return NULL;
}

void
wh_module_manager_handle_request(const uint8_t *packet, size_t length)
{
    if (!packet || !wh_wire_valid(packet, length)) {
        wh_platform_event("module_control_request_invalid", ESP_ERR_INVALID_ARG);
        return;
    }
    if (packet[5] == WH_UPDATE_TRIGGER) {
        wh_module_update_handle_trigger(packet, length);
        return;
    }
    if (length != WH_CONTROL_PACKET_SIZE || packet[5] != WH_CONTROL_REQUEST) {
        wh_platform_event("module_control_request_invalid", ESP_ERR_INVALID_ARG);
        return;
    }

    xSemaphoreTake(manager_lock, portMAX_DELAY);
    if (!manager_started || !accepting_requests || !control_queue) {
        xSemaphoreGive(manager_lock);
        return;
    }
    request_handlers_in_flight++;
    xSemaphoreGive(manager_lock);

    control_request_t request = {0};
    memcpy(request.packet, packet, WH_CONTROL_PACKET_SIZE);
    request.sample_id = wh_read_be(packet + WH_HEADER_SIZE, 8);
    const uint32_t temperature_bits = (uint32_t)wh_read_be(packet + WH_HEADER_SIZE + 8, 4);
    request.temperature_mC = temperature_bits <= INT32_MAX
        ? (int32_t)temperature_bits
        : (int32_t)((int64_t)temperature_bits - 0x100000000LL);
    if (request.sample_id == 0 || request.temperature_mC < -55000 ||
        request.temperature_mC > 125000) {
        wh_platform_event("module_control_request_invalid", ESP_ERR_INVALID_ARG);
        xSemaphoreTake(manager_lock, portMAX_DELAY);
        if (request_handlers_in_flight) request_handlers_in_flight--;
        xSemaphoreGive(manager_lock);
        return;
    }

    bool admitted = false;
    bool replayed = false;
    tx_item_t replay_item = {0};
    control_replay_record_t cached_copy = {0};
    xSemaphoreTake(manager_lock, portMAX_DELAY);
    control_replay_record_t *cached = replay_find(request.sample_id);
    if (cached) {
        if (cached->temperature_mC == request.temperature_mC &&
            control_output_queue &&
            uxQueueMessagesWaiting(control_output_queue) +
                control_result_reservations < WH_CONTROL_QUEUE_CAPACITY) {
            control_result_reservations++;
            cached_copy = *cached;
            replayed = true;
        } else {
            control_admission_refusals++;
        }
    } else if (request.sample_id > highest_control_sample_id && control_queue &&
               control_output_queue &&
               uxQueueMessagesWaiting(control_output_queue) +
                   control_result_reservations < WH_CONTROL_QUEUE_CAPACITY) {
        control_result_reservations++;
        control_job_t job = {.kind = CONTROL_JOB_REQUEST, .request = request};
        if (xQueueSend(control_queue, &job, 0) == pdTRUE) {
            admitted = true;
            highest_control_sample_id = request.sample_id;
        } else {
            increment_control_reservation();
            control_admission_refusals++;
        }
    } else {
        control_admission_refusals++;
    }
    xSemaphoreGive(manager_lock);

    if (replayed) {
        replay_item.type = WH_CONTROL_RESULT;
        memcpy(replay_item.attempt, packet + 8, sizeof(replay_item.attempt));
        memcpy(replay_item.body, cached_copy.result, WH_CONTROL_RESULT_BODY_SIZE);
        replay_item.body_size = WH_CONTROL_RESULT_BODY_SIZE;
        (void)enqueue_control_output(&replay_item, true);
    } else if (!admitted) {
        /* Conflicts and stale IDs are intentionally never executed or answered. */
    }

    xSemaphoreTake(manager_lock, portMAX_DELAY);
    if (request_handlers_in_flight) request_handlers_in_flight--;
    xSemaphoreGive(manager_lock);
}

static void
control_result_body(uint8_t body[WH_CONTROL_RESULT_BODY_SIZE], uint64_t sample_id,
                    uint8_t version, uint8_t requested, uint8_t observed,
                    uint8_t status)
{
    wh_write_be(body, sample_id, 8);
    body[8] = version;
    body[9] = requested;
    body[10] = observed;
    body[11] = status;
}

static bool
telemetry_body_from_sample(const wh_abi_snapshot_v1_t *snapshot,
                           uint8_t body[WH_ABI_V1_SNAPSHOT_SIZE])
{
    if (!snapshot || snapshot->sensor_status > WH_SENSOR_NO_VALID_SAMPLE)
        return false;
    wh_write_be(body, snapshot->sample_id, 8);
    wh_write_be(body + 8, (uint32_t)snapshot->temperature_mC, 4);
    wh_write_be(body + 12, snapshot->age_ms, 2);
    body[14] = snapshot->sensor_status;
    body[15] = 0;
    return true;
}

static wh_abi_snapshot_v1_t
capture_snapshot(void)
{
    const wh_sensor_sample_t sample = wh_sensor_latest();
    const int64_t now = esp_timer_get_time();
    wh_abi_snapshot_v1_t snapshot = {
        .sample_id = sample.sample_id,
        .temperature_mC = sample.temperature_mC,
        .age_ms = UINT16_MAX,
        .sensor_status = (uint8_t)sample.status,
    };
    if (sample.sample_id == 0) {
        snapshot.temperature_mC = INT32_MIN;
        if (snapshot.sensor_status == WH_SENSOR_VALID)
            snapshot.sensor_status = WH_SENSOR_NOT_READY;
    } else if (sample.acquired_us >= 0 && now >= sample.acquired_us) {
        uint64_t age = (uint64_t)(now - sample.acquired_us) / 1000;
        if (age >= UINT16_MAX) age = UINT16_MAX - 1;
        snapshot.age_ms = (uint16_t)age;
    }
    return snapshot;
}

static void
run_telemetry_tick(void)
{
    module_slot_t *slot = &telemetry_slot;
    wh_abi_snapshot_v1_t snapshot = capture_snapshot();
    module_context_t *context = &slot->context;
    context->snapshot = snapshot;
    context->publish_count = 0;
    context->duplicate = false;
    context->invocation_error = false;
    context->import_violation = false;
    context->telemetry_reserved = false;
    context->preparing = false;
    context->invocation_active = true;
    int32_t result = -1;
    const bool call_ok = call_with_limit(slot, slot->entry_fn, 0, NULL,
                                         WH_TELEMETRY_EXECUTION_LIMIT_US, &result);
    context->invocation_active = false;
    if (call_ok && result == 0 && !context->invocation_error &&
        !context->duplicate && context->publish_count == 1 &&
        context->telemetry_reserved) {
        tx_item_t item = {.type = WH_TELEMETRY, .body_size = 16};
        if (telemetry_body_from_sample(&snapshot, item.body)) {
            (void)enqueue_telemetry_output(&item);
        } else {
            release_telemetry_reservation();
        }
    } else if (context->telemetry_reserved) {
        release_telemetry_reservation();
    }
    context->telemetry_reserved = false;
    if (telemetry_operation_memory_generation != slot->generation) {
        telemetry_operation_memory_generation = slot->generation;
        update_stack_high_water(&telemetry_stack_high_water);
        module_memory_event("first_telemetry_operation", "telemetry",
                            call_ok && result == 0 && !context->invocation_error
                                ? "ok" : "failed");
    }
    if (!call_ok || result != 0 || context->invocation_error)
        wh_platform_event("module_telemetry_execution_failed", ESP_FAIL);
}

static void
execute_control_request(const control_request_t *request)
{
    module_slot_t *slot = active_control;
    uint8_t requested = 255;
    uint8_t observed = 255;
    uint8_t status = WH_CONTROL_EXECUTION_ERROR;
    if (!slot || slot->state != WH_MODULE_SLOT_ACTIVE) {
        control_execution_failures++;
    } else {
        module_context_t *context = &slot->context;
        context->sample_id = request->sample_id;
        context->gpio_count = 0;
        context->publish_count = 0;
        context->requested_gpio = 255;
        context->published_sample_id = 0;
        context->published_requested = 255;
        context->duplicate = false;
        context->invocation_error = false;
        context->import_violation = false;
        context->preparing = false;
        context->invocation_active = true;
        wasm_val_t arguments[2] = {
            {.kind = WASM_I64, .of.i64 = (int64_t)request->sample_id},
            {.kind = WASM_I32, .of.i32 = request->temperature_mC},
        };
        int32_t result = -1;
        const bool call_ok = call_with_limit(slot, slot->entry_fn, 2, arguments,
                                             WH_CONTROL_EXECUTION_LIMIT_US, &result);
        context->invocation_active = false;
        const bool valid_staging = call_ok && result == 0 &&
            !context->invocation_error && !context->duplicate &&
            context->gpio_count == 1 && context->publish_count == 1 &&
            context->published_sample_id == context->sample_id &&
            context->published_requested == context->requested_gpio;
        if (valid_staging) {
            requested = context->requested_gpio;
            if (wh_gpio_apply(requested, &observed) == ESP_OK) {
                status = observed == requested ? WH_CONTROL_APPLIED : WH_CONTROL_MISMATCH;
            } else {
                observed = 255;
                status = WH_CONTROL_EXECUTION_ERROR;
                gpio_failures++;
            }
        } else {
            if (context->gpio_count == 1 && context->publish_count == 1 &&
                context->published_sample_id == context->sample_id &&
                context->published_requested == context->requested_gpio &&
                !context->invocation_error && !context->duplicate)
                requested = context->requested_gpio;
            control_execution_failures++;
        }
        context->gpio_count = 0;
        context->publish_count = 0;
    }

    uint8_t body[WH_CONTROL_RESULT_BODY_SIZE];
    control_result_body(body, request->sample_id, slot ? slot->module_version : 1,
                        requested, observed, status);
    replay_store(request->sample_id, request->temperature_mC, body);
    tx_item_t item = {.type = WH_CONTROL_RESULT, .body_size = WH_CONTROL_RESULT_BODY_SIZE};
    memcpy(item.attempt, request->packet + 8, sizeof(item.attempt));
    memcpy(item.body, body, sizeof(body));
    (void)enqueue_control_output(&item, true);
    if (slot && control_operation_memory_generation != slot->generation) {
        control_operation_memory_generation = slot->generation;
        update_stack_high_water(&control_stack_high_water);
        module_memory_event("first_control_operation", "control",
                            status == WH_CONTROL_APPLIED || status == WH_CONTROL_MISMATCH
                                ? "ok" : "failed");
    }
}

static bool
initialize_runtime(void)
{
    RuntimeInitArgs args;
    memset(&args, 0, sizeof(args));
    args.mem_alloc_type = Alloc_With_Allocator;
    args.mem_alloc_option.allocator.malloc_func = (void *)wamr_malloc;
    args.mem_alloc_option.allocator.realloc_func = (void *)wamr_realloc;
    args.mem_alloc_option.allocator.free_func = (void *)wamr_free;
    if (!wasm_runtime_full_init(&args)) return false;
    runtime_initialized = true;
    if (!wasm_runtime_register_natives_raw("wasmhome_telemetry_v1",
            telemetry_symbols, sizeof(telemetry_symbols) / sizeof(telemetry_symbols[0])) ||
        !wasm_runtime_register_natives_raw("wasmhome_control_v1",
            control_symbols, sizeof(control_symbols) / sizeof(control_symbols[0]))) {
        wasm_runtime_destroy();
        runtime_initialized = false;
        return false;
    }
    module_memory_event("runtime_initialized", "manager", "ok");
    return true;
}

typedef struct {
    const wh_module_image_t *telemetry;
    const wh_module_image_t *control;
} bootstrap_args_t;

static void *
runtime_bootstrap(void *argument)
{
    bootstrap_args_t *images = argument;
    esp_err_t result = ESP_FAIL;
    if (!wasm_runtime_init_thread_env()) return (void *)(intptr_t)result;
    if (initialize_runtime() &&
        slot_load(&telemetry_slot, WH_MODULE_ROLE_TELEMETRY,
                  images->telemetry, false, NULL, NULL) == ESP_OK &&
        slot_load(&control_slots[0], WH_MODULE_ROLE_CONTROL,
                  images->control, false, NULL, NULL) == ESP_OK) {
        telemetry_slot.state = WH_MODULE_SLOT_ACTIVE;
        control_slots[0].state = WH_MODULE_SLOT_ACTIVE;
        active_control = &control_slots[0];
        module_memory_event("services_active", "manager", "ok");
        result = ESP_OK;
    } else {
        slot_release(&control_slots[0]);
        slot_release(&telemetry_slot);
        if (runtime_initialized) {
            wasm_runtime_destroy();
            runtime_initialized = false;
        }
    }
    wasm_runtime_destroy_thread_env();
    return (void *)(intptr_t)result;
}

static void
worker_start_report(bool ok, bool *field)
{
    xSemaphoreTake(manager_lock, portMAX_DELAY);
    *field = ok;
    xSemaphoreGive(manager_lock);
    xSemaphoreGive(worker_startup);
}

static void
update_stack_high_water(uint32_t *destination)
{
    const uint32_t words = (uint32_t)uxTaskGetStackHighWaterMark(NULL);
    xSemaphoreTake(manager_lock, portMAX_DELAY);
    if (words != 0 && (*destination == 0 || words < *destination))
        *destination = words;
    xSemaphoreGive(manager_lock);
}

static void *
telemetry_thread_entry(void *argument)
{
    (void)argument;
    const bool env_ok = wasm_runtime_init_thread_env();
    worker_start_report(env_ok, &telemetry_worker_ok);
    if (!env_ok) return NULL;

    TickType_t cycle = xTaskGetTickCount();
    while (!atomic_load_explicit(&stop_requested, memory_order_acquire)) {
        if (xSemaphoreTake(api_lock, 0) == pdTRUE) {
            run_telemetry_tick();
            xSemaphoreGive(api_lock);
        } else {
            xSemaphoreTake(manager_lock, portMAX_DELAY);
            telemetry_runtime_busy_drops++;
            xSemaphoreGive(manager_lock);
        }
        update_stack_high_water(&telemetry_stack_high_water);
        vTaskDelayUntil(&cycle, pdMS_TO_TICKS(100));
    }
    wasm_runtime_destroy_thread_env();
    return NULL;
}

static void
emit_control_manager_event(wh_module_control_event_fn event_fn,
                           void *event_context, const char *phase,
                           const module_slot_t *control)
{
    if (!event_fn) return;
    wh_module_slot_info_t telemetry_info, control_info;
    slot_info_copy(&telemetry_info, &telemetry_slot, WH_MODULE_ROLE_TELEMETRY);
    slot_info_copy(&control_info, control, WH_MODULE_ROLE_CONTROL);
    xSemaphoreTake(manager_lock, portMAX_DELAY);
    const uint64_t busy_drops = telemetry_runtime_busy_drops;
    xSemaphoreGive(manager_lock);
    event_fn(event_context, phase, &telemetry_info, &control_info, busy_drops);
}

static esp_err_t
candidate_control_health_check(module_slot_t *slot)
{
    static const int32_t temperatures[] = {23000, 32000, 27000, 23000};
    if (!slot || (slot->module_version != 1 && slot->module_version != 2) ||
        slot->state != WH_MODULE_SLOT_PREPARING)
        return ESP_ERR_INVALID_ARG;
    const uint8_t expected[] = {
        0, 1, (uint8_t)(slot->module_version == 1 ? 1 : 0), 0,
    };

    module_context_t *context = &slot->context;
    context->dry_run = true;
    for (size_t i = 0; i < sizeof(temperatures) / sizeof(temperatures[0]); ++i) {
        context->sample_id = i + 1;
        context->gpio_count = 0;
        context->publish_count = 0;
        context->requested_gpio = 255;
        context->published_sample_id = 0;
        context->published_requested = 255;
        context->duplicate = false;
        context->invocation_error = false;
        context->import_violation = false;
        context->preparing = false;
        context->invocation_active = true;
        wasm_val_t arguments[2] = {
            {.kind = WASM_I64, .of.i64 = (int64_t)context->sample_id},
            {.kind = WASM_I32, .of.i32 = temperatures[i]},
        };
        int32_t result = -1;
        const bool call_ok = call_with_limit(slot, slot->entry_fn, 2, arguments,
                                             WH_CONTROL_EXECUTION_LIMIT_US,
                                             &result);
        context->invocation_active = false;
        const bool valid = call_ok && result == 0 && !context->invocation_error &&
            !context->duplicate && context->gpio_count == 1 &&
            context->publish_count == 1 &&
            context->published_sample_id == context->sample_id &&
            context->requested_gpio == expected[i] &&
            context->published_requested == expected[i];
        if (!valid) {
            context->dry_run = false;
            context->gpio_count = 0;
            context->publish_count = 0;
            return ESP_ERR_INVALID_STATE;
        }
    }
    context->dry_run = false;
    context->gpio_count = 0;
    context->publish_count = 0;
    return ESP_OK;
}

static esp_err_t
prepare_control_on_worker(const wh_module_image_t *image,
                          wh_module_control_commit_fn commit_fn,
                          wh_module_control_event_fn event_fn,
                          void *event_context)
{
    module_slot_t *old_slot = active_control;
    if (!old_slot || old_slot->state != WH_MODULE_SLOT_ACTIVE)
        return ESP_ERR_INVALID_STATE;
    module_slot_t *new_slot = old_slot == &control_slots[0]
        ? &control_slots[1] : &control_slots[0];
    if (atomic_load_explicit(&old_slot->in_flight, memory_order_acquire))
        return ESP_ERR_INVALID_STATE;
    if (new_slot->state != WH_MODULE_SLOT_EMPTY) slot_release(new_slot);

    esp_err_t result = slot_load(new_slot, WH_MODULE_ROLE_CONTROL, image, true,
                                 commit_fn ? event_fn : NULL, event_context);
    if (result != ESP_OK) return result;
    new_slot->state = commit_fn ? WH_MODULE_SLOT_PREPARING : WH_MODULE_SLOT_READY;
    if (commit_fn) {
        result = candidate_control_health_check(new_slot);
        if (result != ESP_OK) {
            slot_release(new_slot);
            return result;
        }
        emit_control_manager_event(event_fn, event_context,
                                  "candidate_health_check", new_slot);
    }
    if (atomic_load_explicit(&old_slot->in_flight, memory_order_acquire)) {
        slot_release(new_slot);
        return ESP_ERR_INVALID_STATE;
    }

    xSemaphoreTake(manager_lock, portMAX_DELAY);
    if (active_control != old_slot || old_slot->state != WH_MODULE_SLOT_ACTIVE) {
        xSemaphoreGive(manager_lock);
        slot_release(new_slot);
        return ESP_ERR_INVALID_STATE;
    }
    xSemaphoreGive(manager_lock);

    if (commit_fn) {
        wh_module_slot_info_t telemetry_info, control_info;
        slot_info_copy(&telemetry_info, &telemetry_slot, WH_MODULE_ROLE_TELEMETRY);
        slot_info_copy(&control_info, new_slot, WH_MODULE_ROLE_CONTROL);
        wh_module_selection_outcome_t outcome = WH_MODULE_SELECTION_UNKNOWN;
        result = commit_fn(event_context, &telemetry_info, &control_info, &outcome);
        if (outcome == WH_MODULE_SELECTION_UNKNOWN ||
            (result == ESP_OK && outcome != WH_MODULE_SELECTION_COMMITTED)) {
            /* The NVS write may have completed even if its readback failed.
               Keep both resident modules and fail closed until boot recovery
               selects the durable ledger entry. */
            xSemaphoreTake(manager_lock, portMAX_DELAY);
            manager_degraded = true;
            accepting_requests = false;
            xSemaphoreGive(manager_lock);
            return result == ESP_OK ? ESP_ERR_INVALID_STATE : result;
        }
        if (outcome != WH_MODULE_SELECTION_COMMITTED) {
            slot_release(new_slot);
            return result == ESP_OK ? ESP_ERR_INVALID_STATE : result;
        }
        result = ESP_OK; /* A verified ledger readback wins over an ambiguous write status. */
        emit_control_manager_event(event_fn, event_context, "floor_committed", new_slot);
    }

    module_memory_event("candidate_overlap", "control_candidate", "ready");
    xSemaphoreTake(manager_lock, portMAX_DELAY);
    if (active_control != old_slot || old_slot->state != WH_MODULE_SLOT_ACTIVE) {
        xSemaphoreGive(manager_lock);
        slot_release(new_slot);
        return ESP_ERR_INVALID_STATE;
    }
    old_slot->state = WH_MODULE_SLOT_DRAINING;
    new_slot->state = WH_MODULE_SLOT_ACTIVE;
    active_control = new_slot;
    xSemaphoreGive(manager_lock);
    if (commit_fn)
        emit_control_manager_event(event_fn, event_context, "control_activated", new_slot);
    slot_release(old_slot);
    module_memory_event("control_replaced_old_released", "control", "ok");
    if (commit_fn)
        emit_control_manager_event(event_fn, event_context,
                                   "previous_control_released", new_slot);
    return ESP_OK;
}

static void *
control_thread_entry(void *argument)
{
    (void)argument;
    const bool env_ok = wasm_runtime_init_thread_env();
    worker_start_report(env_ok, &control_worker_ok);
    if (!env_ok) return NULL;

    for (;;) {
        control_job_t job;
        if (xQueueReceive(control_queue, &job, portMAX_DELAY) != pdTRUE) continue;
        if (job.kind == CONTROL_JOB_STOP) break;
        if (xSemaphoreTake(api_lock, portMAX_DELAY) != pdTRUE) {
            if (job.kind == CONTROL_JOB_PREPARE && job.prepare) {
                job.prepare->result = ESP_ERR_INVALID_STATE;
                xSemaphoreGive(job.prepare->done);
            }
            continue;
        }
        if (job.kind == CONTROL_JOB_REQUEST) {
            execute_control_request(&job.request);
        } else if (job.kind == CONTROL_JOB_PREPARE && job.prepare) {
            job.prepare->result = prepare_control_on_worker(
                job.prepare->image, job.prepare->commit_fn,
                job.prepare->event_fn, job.prepare->context);
            xSemaphoreGive(job.prepare->done);
        }
        xSemaphoreGive(api_lock);
        update_stack_high_water(&control_stack_high_water);
    }
    wasm_runtime_destroy_thread_env();
    return NULL;
}

static void *
tx_worker_entry(void *argument)
{
    (void)argument;
    worker_start_report(true, &tx_worker_ok);
    for (;;) {
        tx_item_t item;
        bool found = control_output_queue &&
            xQueueReceive(control_output_queue, &item, 0) == pdTRUE;
        if (!found && telemetry_output_queue)
            found = xQueueReceive(telemetry_output_queue, &item,
                                  pdMS_TO_TICKS(20)) == pdTRUE;
        if (found) {
            esp_err_t result = wh_observation_send(item.type, item.attempt,
                                                   item.body, item.body_size);
            if (result != ESP_OK) {
                xSemaphoreTake(manager_lock, portMAX_DELAY);
                tx_failures++;
                xSemaphoreGive(manager_lock);
            }
            continue;
        }
        if (atomic_load_explicit(&tx_stop_requested, memory_order_acquire) &&
            uxQueueMessagesWaiting(control_output_queue) == 0 &&
            uxQueueMessagesWaiting(telemetry_output_queue) == 0)
            break;
    }
    return NULL;
}

static void *
shutdown_runtime_thread(void *argument)
{
    (void)argument;
    if (!runtime_initialized) return (void *)(intptr_t)ESP_OK;
    if (!wasm_runtime_init_thread_env()) return (void *)(intptr_t)ESP_FAIL;
    if (xSemaphoreTake(api_lock, portMAX_DELAY) != pdTRUE) {
        wasm_runtime_destroy_thread_env();
        return (void *)(intptr_t)ESP_FAIL;
    }
    if (active_control) {
        module_slot_t *active = active_control;
        active_control = NULL;
        slot_release(active);
    }
    slot_release(&control_slots[0]);
    slot_release(&control_slots[1]);
    slot_release(&telemetry_slot);
    if (runtime_initialized) {
        wasm_runtime_destroy();
        runtime_initialized = false;
    }
    module_memory_event("runtime_destroyed", "manager", "measured");
    xSemaphoreGive(api_lock);
    wasm_runtime_destroy_thread_env();
    return (void *)(intptr_t)ESP_OK;
}

static bool
pthread_start(pthread_t *thread, void *(*entry)(void *), void *argument,
              const char *name)
{
    pthread_attr_t attributes;
    if (pthread_attr_init(&attributes) != 0) return false;
    const size_t stack_bytes = CONFIG_WASMHOME_MODULE_HOST_THREAD_STACK_BYTES;
    int error = pthread_attr_setstacksize(&attributes, stack_bytes);
    (void)name;
    if (error == 0) error = pthread_create(thread, &attributes, entry, argument);
    (void)pthread_attr_destroy(&attributes);
    return error == 0;
}

static void
manager_delete_resources(void)
{
    if (control_queue) { vQueueDelete(control_queue); control_queue = NULL; }
    if (telemetry_output_queue) { vQueueDelete(telemetry_output_queue); telemetry_output_queue = NULL; }
    if (control_output_queue) { vQueueDelete(control_output_queue); control_output_queue = NULL; }
    if (telemetry_queue_lock) { vSemaphoreDelete(telemetry_queue_lock); telemetry_queue_lock = NULL; }
    if (worker_startup) { vSemaphoreDelete(worker_startup); worker_startup = NULL; }
    if (api_lock) { vSemaphoreDelete(api_lock); api_lock = NULL; }
    telemetry_output_reservations = 0;
    control_result_reservations = 0;
}

static void
manager_expose_failed_start_for_stop(void)
{
    xSemaphoreTake(manager_lock, portMAX_DELAY);
    manager_started = true;
    manager_starting = false;
    manager_stopping = true;
    manager_stop_in_progress = false;
    accepting_requests = false;
    xSemaphoreGive(manager_lock);
}

static bool
join_thread(pthread_t thread, const char *name)
{
    void *thread_result = NULL;
    (void)name;
    if (pthread_join(thread, &thread_result) != 0) {
        ESP_LOGE(TAG, "pthread_join failed for %s", name);
        return false;
    }
    return true;
}

/* Called only after new public calls have been rejected and existing ones drained. */
static esp_err_t
manager_stop_internal(void)
{
    esp_err_t handler_result = ESP_OK;
    xSemaphoreTake(manager_lock, portMAX_DELAY);
    accepting_requests = false;
    const bool installed = handler_installed;
    xSemaphoreGive(manager_lock);

    if (installed) {
        handler_result = wh_observation_set_handler(NULL);
        if (handler_result != ESP_OK) {
            ESP_LOGE(TAG, "failed to unregister observation handler: %s",
                     esp_err_to_name(handler_result));
            return handler_result;
        }
        xSemaphoreTake(manager_lock, portMAX_DELAY);
        handler_installed = false;
        xSemaphoreGive(manager_lock);
    }

    for (;;) {
        xSemaphoreTake(manager_lock, portMAX_DELAY);
        const uint32_t handlers = request_handlers_in_flight;
        xSemaphoreGive(manager_lock);
        if (handlers == 0) break;
        vTaskDelay(1);
    }

    if (bootstrap_thread_created) {
        void *bootstrap_result = NULL;
        if (pthread_join(bootstrap_thread, &bootstrap_result) != 0) {
            ESP_LOGE(TAG, "pthread_join failed for runtime bootstrap");
            return ESP_FAIL;
        }
        bootstrap_thread_created = false;
        (void)bootstrap_result;
    }

    atomic_store_explicit(&stop_requested, true, memory_order_release);
    if (control_thread_created) {
        control_job_t stop = {.kind = CONTROL_JOB_STOP};
        if (!control_queue || xQueueSend(control_queue, &stop, portMAX_DELAY) != pdTRUE)
            return ESP_FAIL;
    }

    bool lanes_joined = true;
    if (telemetry_thread_created &&
        join_thread(telemetry_thread, "telemetry"))
        telemetry_thread_created = false;
    else if (telemetry_thread_created)
        lanes_joined = false;
    if (control_thread_created && join_thread(control_thread, "control"))
        control_thread_created = false;
    else if (control_thread_created)
        lanes_joined = false;
    if (!lanes_joined) return ESP_FAIL;

    atomic_store_explicit(&tx_stop_requested, true, memory_order_release);
    if (tx_thread_created) {
        if (!join_thread(tx_thread, "transmit")) return ESP_FAIL;
        tx_thread_created = false;
    }

    if (!shutdown_thread_created) {
        if (!pthread_start(&shutdown_thread, shutdown_runtime_thread, NULL,
                           "wh_wasm_down"))
            return ESP_FAIL;
        shutdown_thread_created = true;
    }
    void *shutdown_result = NULL;
    if (pthread_join(shutdown_thread, &shutdown_result) != 0) {
        ESP_LOGE(TAG, "pthread_join failed for runtime shutdown");
        return ESP_FAIL;
    }
    shutdown_thread_created = false;
    const esp_err_t shutdown_status = (esp_err_t)(intptr_t)shutdown_result;
    if (shutdown_status != ESP_OK) {
        ESP_LOGE(TAG, "runtime shutdown worker failed: %s",
                 esp_err_to_name(shutdown_status));
        return shutdown_status;
    }

    memset(replay_records, 0, sizeof(replay_records));
    manager_delete_resources();
    module_memory_event("manager_stopped", "manager", "measured");
    xSemaphoreTake(manager_lock, portMAX_DELAY);
    manager_started = false;
    manager_starting = false;
    manager_stopping = false;
    manager_stop_in_progress = false;
    accepting_requests = false;
    handler_installed = false;
    xSemaphoreGive(manager_lock);
    return ESP_OK;
}

esp_err_t
wh_module_manager_start(const wh_module_image_t *telemetry,
                        const wh_module_image_t *control)
{
    if (!telemetry || !control || !telemetry->bytes || !control->bytes ||
        !wh_abi_module_version_valid(telemetry->module_version) ||
        !wh_abi_module_version_valid(control->module_version) ||
        telemetry->size == 0 || control->size == 0 ||
        telemetry->size > CONFIG_WASMHOME_MODULE_MAX_ARTIFACT_BYTES ||
        control->size > CONFIG_WASMHOME_MODULE_MAX_ARTIFACT_BYTES)
        return ESP_ERR_INVALID_ARG;
    if (!manager_ensure_lock()) return ESP_ERR_NO_MEM;
    xSemaphoreTake(manager_lock, portMAX_DELAY);
    if (manager_started || manager_starting || manager_stopping) {
        xSemaphoreGive(manager_lock);
        return ESP_ERR_INVALID_STATE;
    }
    manager_starting = true;
    manager_degraded = false;
    telemetry_stack_high_water = control_stack_high_water = 0;
    xSemaphoreGive(manager_lock);
    portENTER_CRITICAL(&allocator_mux);
    allocator_cap_bytes = CONFIG_WASMHOME_MODULE_ALLOCATOR_CAP_BYTES;
    allocator_live_bytes = allocator_peak_bytes = allocator_calls = allocator_failures = 0;
    allocator_runtime_bytes = allocator_linear_bytes = allocator_artifact_bytes = 0;
    portEXIT_CRITICAL(&allocator_mux);
    module_memory_event("baseline", "manager", "measured");

    api_lock = xSemaphoreCreateMutex();
    telemetry_queue_lock = xSemaphoreCreateMutex();
    worker_startup = xSemaphoreCreateCounting(3, 0);
    control_queue = xQueueCreate(WH_CONTROL_QUEUE_CAPACITY, sizeof(control_job_t));
    telemetry_output_queue = xQueueCreate(WH_TELEMETRY_QUEUE_CAPACITY, sizeof(tx_item_t));
    control_output_queue = xQueueCreate(WH_CONTROL_QUEUE_CAPACITY, sizeof(tx_item_t));
    if (!api_lock || !telemetry_queue_lock || !worker_startup || !control_queue ||
        !telemetry_output_queue || !control_output_queue) {
        module_memory_event("manager_resource_setup", "manager", "failed");
        manager_delete_resources();
        xSemaphoreTake(manager_lock, portMAX_DELAY);
        manager_starting = false;
        xSemaphoreGive(manager_lock);
        return ESP_ERR_NO_MEM;
    }

    memset(replay_records, 0, sizeof(replay_records));
    replay_next = 0;
    highest_control_sample_id = 0;
    control_admission_refusals = telemetry_queue_full_drops = 0;
    telemetry_runtime_busy_drops = tx_failures = 0;
    control_execution_failures = gpio_failures = 0;
    telemetry_operation_memory_generation = 0;
    control_operation_memory_generation = 0;
    module_memory_event("manager_resources_ready", "manager", "ok");
    atomic_init(&stop_requested, false);
    atomic_init(&tx_stop_requested, false);
    telemetry_thread_created = control_thread_created = tx_thread_created = false;
    bootstrap_thread_created = false;
    shutdown_thread_created = false;
    telemetry_worker_ok = control_worker_ok = tx_worker_ok = false;

    wh_module_image_t resolved_control = *control;
    uint8_t *restored_control_bytes = NULL;
    esp_err_t restore_status = wh_module_update_resolve_boot_control(
        control, &resolved_control, &restored_control_bytes);
    if (restore_status == ESP_OK)
        restore_status = wh_module_update_start();
    if (restore_status != ESP_OK) {
        if (restored_control_bytes) heap_caps_free(restored_control_bytes);
        manager_delete_resources();
        xSemaphoreTake(manager_lock, portMAX_DELAY);
        manager_starting = false;
        xSemaphoreGive(manager_lock);
        return restore_status;
    }
    if (!resolved_control.bytes || resolved_control.size == 0 ||
        resolved_control.size > CONFIG_WASMHOME_MODULE_MAX_ARTIFACT_BYTES ||
        !wh_abi_module_version_valid(resolved_control.module_version)) {
        if (restored_control_bytes) heap_caps_free(restored_control_bytes);
        manager_delete_resources();
        xSemaphoreTake(manager_lock, portMAX_DELAY);
        manager_starting = false;
        xSemaphoreGive(manager_lock);
        return ESP_ERR_INVALID_SIZE;
    }
    bootstrap_args_t bootstrap = {.telemetry = telemetry, .control = &resolved_control};
    void *bootstrap_result = NULL;
    if (!pthread_start(&bootstrap_thread, runtime_bootstrap, &bootstrap,
                       "wh_wasm_boot")) {
        if (restored_control_bytes) heap_caps_free(restored_control_bytes);
        manager_delete_resources();
        xSemaphoreTake(manager_lock, portMAX_DELAY);
        manager_starting = false;
        xSemaphoreGive(manager_lock);
        return ESP_FAIL;
    }
    bootstrap_thread_created = true;
    if (pthread_join(bootstrap_thread, &bootstrap_result) != 0) {
        ESP_LOGE(TAG, "pthread_join failed for runtime bootstrap");
        manager_expose_failed_start_for_stop();
        return ESP_FAIL;
    }
    bootstrap_thread_created = false;
    if (restored_control_bytes) {
        memset(restored_control_bytes, 0, resolved_control.size);
        heap_caps_free(restored_control_bytes);
        restored_control_bytes = NULL;
    }
    if ((esp_err_t)(intptr_t)bootstrap_result != ESP_OK) {
        manager_delete_resources();
        xSemaphoreTake(manager_lock, portMAX_DELAY);
        manager_starting = false;
        xSemaphoreGive(manager_lock);
        return (esp_err_t)(intptr_t)bootstrap_result;
    }

    accepting_requests = false;
    if (pthread_start(&telemetry_thread, telemetry_thread_entry, NULL,
                      "wh_telemetry"))
        telemetry_thread_created = true;
    if (telemetry_thread_created &&
        pthread_start(&control_thread, control_thread_entry, NULL, "wh_control"))
        control_thread_created = true;
    if (control_thread_created &&
        pthread_start(&tx_thread, tx_worker_entry, NULL, "wh_tx"))
        tx_thread_created = true;
    if (!telemetry_thread_created || !control_thread_created || !tx_thread_created) {
        xSemaphoreTake(manager_lock, portMAX_DELAY);
        manager_stopping = true;
        xSemaphoreGive(manager_lock);
        const esp_err_t cleanup = manager_stop_internal();
        if (cleanup != ESP_OK) {
            manager_expose_failed_start_for_stop();
            return cleanup;
        }
        return ESP_FAIL;
    }
    for (unsigned i = 0; i < 3; ++i)
        if (xSemaphoreTake(worker_startup, pdMS_TO_TICKS(5000)) != pdTRUE) {
            xSemaphoreTake(manager_lock, portMAX_DELAY);
            manager_stopping = true;
            xSemaphoreGive(manager_lock);
            const esp_err_t cleanup = manager_stop_internal();
            if (cleanup != ESP_OK) {
                manager_expose_failed_start_for_stop();
                return cleanup;
            }
            return cleanup == ESP_OK ? ESP_ERR_TIMEOUT : cleanup;
        }
    if (!telemetry_worker_ok || !control_worker_ok || !tx_worker_ok) {
        xSemaphoreTake(manager_lock, portMAX_DELAY);
        manager_stopping = true;
        xSemaphoreGive(manager_lock);
        const esp_err_t cleanup = manager_stop_internal();
        if (cleanup != ESP_OK) {
            manager_expose_failed_start_for_stop();
            return cleanup;
        }
        return cleanup == ESP_OK ? ESP_FAIL : cleanup;
    }

    esp_err_t result = wh_observation_set_handler(wh_module_manager_handle_request);
    if (result != ESP_OK) {
        xSemaphoreTake(manager_lock, portMAX_DELAY);
        manager_stopping = true;
        xSemaphoreGive(manager_lock);
        const esp_err_t cleanup = manager_stop_internal();
        if (cleanup != ESP_OK) {
            manager_expose_failed_start_for_stop();
            return cleanup;
        }
        return cleanup == ESP_OK ? result : cleanup;
    }
    xSemaphoreTake(manager_lock, portMAX_DELAY);
    handler_installed = true;
    manager_started = true;
    manager_starting = false;
    accepting_requests = true;
    xSemaphoreGive(manager_lock);
    module_memory_event("workers_ready", "manager", "ok");
    wh_platform_event("module_manager_ready", ESP_OK);
    return ESP_OK;
}

esp_err_t
wh_module_manager_prepare_control(const wh_module_image_t *candidate)
{
    if (!candidate || !candidate->bytes || candidate->size == 0 ||
        candidate->size > CONFIG_WASMHOME_MODULE_MAX_ARTIFACT_BYTES ||
        !wh_abi_module_version_valid(candidate->module_version))
        return ESP_ERR_INVALID_ARG;
    if (!manager_public_enter()) return ESP_ERR_INVALID_STATE;
    xSemaphoreTake(manager_lock, portMAX_DELAY);
    const bool degraded = manager_degraded;
    xSemaphoreGive(manager_lock);
    if (degraded) {
        manager_public_leave();
        return ESP_ERR_INVALID_STATE;
    }
    if (!control_queue) {
        manager_public_leave();
        return ESP_ERR_INVALID_STATE;
    }
    SemaphoreHandle_t done = xSemaphoreCreateBinary();
    if (!done) {
        manager_public_leave();
        return ESP_ERR_NO_MEM;
    }
    prepare_command_t command = {.image = candidate, .done = done, .result = ESP_FAIL};
    control_job_t job = {.kind = CONTROL_JOB_PREPARE, .prepare = &command};
    if (xQueueSend(control_queue, &job, 0) != pdTRUE) {
        vSemaphoreDelete(done);
        manager_public_leave();
        return ESP_FAIL;
    }
    if (xSemaphoreTake(done, portMAX_DELAY) != pdTRUE) {
        vSemaphoreDelete(done);
        manager_public_leave();
        return ESP_FAIL;
    }
    esp_err_t result = command.result;
    vSemaphoreDelete(done);
    manager_public_leave();
    return result;
}

esp_err_t
wh_module_manager_activate_control(
    const wh_module_image_t *candidate,
    wh_module_control_commit_fn commit_fn,
    wh_module_control_event_fn event_fn,
    void *context)
{
    if (!candidate || !candidate->bytes || candidate->size == 0 ||
        candidate->size > CONFIG_WASMHOME_MODULE_MAX_ARTIFACT_BYTES ||
        !wh_abi_module_version_valid(candidate->module_version) ||
        !commit_fn || !event_fn)
        return ESP_ERR_INVALID_ARG;
    if (!manager_public_enter()) return ESP_ERR_INVALID_STATE;
    xSemaphoreTake(manager_lock, portMAX_DELAY);
    const bool degraded = manager_degraded;
    xSemaphoreGive(manager_lock);
    if (degraded || !control_queue) {
        manager_public_leave();
        return ESP_ERR_INVALID_STATE;
    }

    SemaphoreHandle_t done = xSemaphoreCreateBinary();
    if (!done) {
        manager_public_leave();
        return ESP_ERR_NO_MEM;
    }
    prepare_command_t command = {
        .image = candidate,
        .commit_fn = commit_fn,
        .event_fn = event_fn,
        .context = context,
        .done = done,
        .result = ESP_FAIL,
    };
    control_job_t job = {.kind = CONTROL_JOB_PREPARE, .prepare = &command};
    if (xQueueSend(control_queue, &job, 0) != pdTRUE) {
        vSemaphoreDelete(done);
        manager_public_leave();
        return ESP_ERR_INVALID_STATE;
    }
    if (xSemaphoreTake(done, portMAX_DELAY) != pdTRUE) {
        vSemaphoreDelete(done);
        manager_public_leave();
        return ESP_FAIL;
    }
    const esp_err_t result = command.result;
    vSemaphoreDelete(done);
    manager_public_leave();
    return result;
}

esp_err_t
wh_module_manager_stop(void)
{
    if (!manager_ensure_lock()) return ESP_ERR_NO_MEM;
    xSemaphoreTake(manager_lock, portMAX_DELAY);
    if (!manager_started || manager_starting || manager_stop_in_progress) {
        xSemaphoreGive(manager_lock);
        return ESP_ERR_INVALID_STATE;
    }
    manager_stopping = true;
    manager_stop_in_progress = true;
    accepting_requests = false;
    xSemaphoreGive(manager_lock);

    for (;;) {
        xSemaphoreTake(manager_lock, portMAX_DELAY);
        const uint32_t calls = public_calls_in_flight;
        xSemaphoreGive(manager_lock);
        if (calls == 0) break;
        vTaskDelay(1);
    }
    const esp_err_t result = manager_stop_internal();
    if (result != ESP_OK) {
        xSemaphoreTake(manager_lock, portMAX_DELAY);
        manager_stop_in_progress = false;
        xSemaphoreGive(manager_lock);
    }
    return result;
}

static void
slot_info_copy(wh_module_slot_info_t *out, const module_slot_t *slot,
               wh_module_role_t role)
{
    memset(out, 0, sizeof(*out));
    out->role = role;
    if (!slot) return;
    out->state = slot->state;
    out->module_version = slot->module_version;
    out->generation = slot->generation;
    memcpy(out->sha256, slot->digest, WH_MODULE_DIGEST_SIZE);
    out->in_flight = atomic_load_explicit(&slot->in_flight, memory_order_acquire) ? 1 : 0;
    out->artifact_bytes = slot->artifact_size > UINT32_MAX
        ? UINT32_MAX : (uint32_t)slot->artifact_size;
}

esp_err_t
wh_module_manager_get_info(wh_module_manager_info_t *info)
{
    if (!info || !manager_public_enter())
        return ESP_ERR_INVALID_STATE;
    if (!api_lock || xSemaphoreTake(api_lock, portMAX_DELAY) != pdTRUE) {
        manager_public_leave();
        return ESP_FAIL;
    }
    xSemaphoreTake(manager_lock, portMAX_DELAY);
    memset(info, 0, sizeof(*info));
    (void)allocator_snapshot(info);
    info->control_admission_refusals = control_admission_refusals;
    info->telemetry_runtime_busy_drops = telemetry_runtime_busy_drops;
    info->tx_failures = tx_failures;
    info->control_execution_failures = control_execution_failures;
    info->gpio_failures = gpio_failures;
    slot_info_copy(&info->telemetry, &telemetry_slot, WH_MODULE_ROLE_TELEMETRY);
    slot_info_copy(&info->control, active_control, WH_MODULE_ROLE_CONTROL);
    info->telemetry_stack_high_water_words = telemetry_stack_high_water;
    info->control_stack_high_water_words = control_stack_high_water;
    multi_heap_info_t internal_heap = {0};
    heap_caps_get_info(&internal_heap, MALLOC_CAP_INTERNAL);
    info->internal_heap_total_bytes = heap_caps_get_total_size(MALLOC_CAP_INTERNAL);
    info->internal_heap_free_bytes = internal_heap.total_free_bytes;
    info->internal_heap_largest_block_bytes = internal_heap.largest_free_block;
    info->queue_payload_capacity_bytes =
        (control_queue ? WH_CONTROL_QUEUE_CAPACITY * sizeof(control_job_t) : 0) +
        (control_output_queue ? WH_CONTROL_QUEUE_CAPACITY * sizeof(tx_item_t) : 0) +
        (telemetry_output_queue ? WH_TELEMETRY_QUEUE_CAPACITY * sizeof(tx_item_t) : 0);
    info->steady_worker_stack_requested_bytes =
        3u * (uint64_t)CONFIG_WASMHOME_MODULE_HOST_THREAD_STACK_BYTES;
#if defined(CONFIG_SPIRAM) && CONFIG_SPIRAM
    info->psram_heap_available = 1;
    multi_heap_info_t psram_heap = {0};
    heap_caps_get_info(&psram_heap, MALLOC_CAP_SPIRAM);
    info->psram_heap_total_bytes = heap_caps_get_total_size(MALLOC_CAP_SPIRAM);
    info->psram_heap_free_bytes = psram_heap.total_free_bytes;
    info->psram_heap_largest_block_bytes = psram_heap.largest_free_block;
#endif
    portENTER_CRITICAL(&reservation_mux);
    info->telemetry_queue_full_drops = telemetry_queue_full_drops;
    portEXIT_CRITICAL(&reservation_mux);
    xSemaphoreGive(manager_lock);
    xSemaphoreGive(api_lock);
    manager_public_leave();
    return ESP_OK;
}
