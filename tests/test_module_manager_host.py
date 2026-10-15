"""Host contract tests for module_manager.c with explicit ESP/WAMR shims.

The fake runtime exercises the manager's decisions and callback plumbing. It is
not evidence that the pinned WAMR build behaves the same way on ESP-IDF.
"""

import pathlib
import shutil
import subprocess
import tempfile
import textwrap
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


STUBS = {
    "sdkconfig.h": r'''
        #pragma once
        #define CONFIG_WASMHOME_MODULE_MAX_ARTIFACT_BYTES 65536u
        #define CONFIG_WASMHOME_MODULE_WASM_STACK_BYTES 8192u
        #define CONFIG_WASMHOME_MODULE_APP_HEAP_BYTES 8192u
        #define CONFIG_WASMHOME_MODULE_MAX_MEMORY_PAGES 2u
        #define CONFIG_WASMHOME_MODULE_ALLOCATOR_CAP_BYTES 524288u
        #define CONFIG_WASMHOME_MODULE_HOST_THREAD_STACK_BYTES 32768u
        #define CONFIG_SPIRAM 1
    ''',
    "esp_err.h": r'''
        #pragma once
        typedef int esp_err_t;
        #define ESP_OK 0
        #define ESP_FAIL -1
        #define ESP_ERR_INVALID_ARG 0x101
        #define ESP_ERR_INVALID_STATE 0x102
        #define ESP_ERR_NO_MEM 0x103
        #define ESP_ERR_TIMEOUT 0x104
    ''',
    "esp_heap_caps.h": r'''
        #pragma once
        #include <stddef.h>
        #define MALLOC_CAP_8BIT 1u
        #define MALLOC_CAP_INTERNAL 2u
        #define MALLOC_CAP_SPIRAM 4u
        typedef struct { size_t total_free_bytes, largest_free_block; } multi_heap_info_t;
        void *heap_caps_malloc(size_t size, unsigned caps);
        void *heap_caps_aligned_alloc(size_t alignment, size_t size, unsigned caps);
        void heap_caps_free(void *ptr);
        size_t heap_caps_get_total_size(unsigned caps);
        void heap_caps_get_info(multi_heap_info_t *info, unsigned caps);
    ''',
    "esp_log.h": r'''
        #pragma once
        #define ESP_LOGE(tag, ...) ((void)(tag))
        #define ESP_LOGW(tag, ...) ((void)(tag))
        #define ESP_LOGI(tag, ...) ((void)(tag))
    ''',
    "esp_timer.h": r'''
        #pragma once
        #include <stdint.h>
        #include "esp_err.h"
        typedef void *esp_timer_handle_t;
        typedef enum { ESP_TIMER_TASK = 0 } esp_timer_dispatch_t;
        typedef struct {
            void (*callback)(void *);
            void *arg;
            esp_timer_dispatch_t dispatch_method;
            const char *name;
        } esp_timer_create_args_t;
        int64_t esp_timer_get_time(void);
        esp_err_t esp_timer_create(const esp_timer_create_args_t *args, esp_timer_handle_t *out);
        esp_err_t esp_timer_start_once(esp_timer_handle_t timer, uint64_t timeout_us);
        esp_err_t esp_timer_stop(esp_timer_handle_t timer);
        esp_err_t esp_timer_delete(esp_timer_handle_t timer);
    ''',
    "freertos/FreeRTOS.h": r'''
        #pragma once
        #include <pthread.h>
        #include <stdint.h>
        typedef int BaseType_t;
        typedef unsigned UBaseType_t;
        typedef uint32_t TickType_t;
        typedef pthread_mutex_t portMUX_TYPE;
        #define pdTRUE 1
        #define pdFALSE 0
        #define pdPASS 1
        #define portMAX_DELAY UINT32_MAX
        #define pdMS_TO_TICKS(ms) ((TickType_t)(ms))
        #define portMUX_INITIALIZER_UNLOCKED PTHREAD_MUTEX_INITIALIZER
        #define portENTER_CRITICAL(mux) ((void)pthread_mutex_lock((mux)))
        #define portEXIT_CRITICAL(mux) ((void)pthread_mutex_unlock((mux)))
        void vTaskDelay(TickType_t ticks);
    ''',
    "freertos/queue.h": r'''
        #pragma once
        #include "freertos/FreeRTOS.h"
        typedef void *QueueHandle_t;
        QueueHandle_t xQueueCreate(UBaseType_t length, UBaseType_t item_size);
        BaseType_t xQueueSend(QueueHandle_t queue, const void *item, TickType_t wait);
        BaseType_t xQueueReceive(QueueHandle_t queue, void *item, TickType_t wait);
        UBaseType_t uxQueueMessagesWaiting(QueueHandle_t queue);
        void vQueueDelete(QueueHandle_t queue);
    ''',
    "freertos/semphr.h": r'''
        #pragma once
        #include "freertos/FreeRTOS.h"
        typedef void *SemaphoreHandle_t;
        SemaphoreHandle_t xSemaphoreCreateMutex(void);
        SemaphoreHandle_t xSemaphoreCreateBinary(void);
        SemaphoreHandle_t xSemaphoreCreateCounting(UBaseType_t max, UBaseType_t initial);
        BaseType_t xSemaphoreTake(SemaphoreHandle_t sem, TickType_t wait);
        BaseType_t xSemaphoreGive(SemaphoreHandle_t sem);
        void vSemaphoreDelete(SemaphoreHandle_t sem);
    ''',
    "freertos/task.h": r'''
        #pragma once
        #include "freertos/FreeRTOS.h"
        TickType_t xTaskGetTickCount(void);
        UBaseType_t uxTaskGetStackHighWaterMark(void *task);
        void vTaskDelayUntil(TickType_t *previous, TickType_t increment);
    ''',
    "mbedtls/sha256.h": r'''
        #pragma once
        #include <stddef.h>
        #include <stdint.h>
        int mbedtls_sha256(const unsigned char *input, size_t ilen,
                           unsigned char output[32], int is224);
    ''',
    "wasm_export.h": r'''
        #pragma once
        #include <stdbool.h>
        #include <stdint.h>
        #include <stddef.h>
        typedef enum { Alloc_For_Runtime = 0, Alloc_For_LinearMemory = 1 } mem_alloc_usage_t;
        typedef enum { WASM_I32 = 0, WASM_I64 = 1 } wasm_valkind_t;
        typedef enum {
            Wasm_Module_Bytecode = 0,
            Wasm_Module_AoT = 1,
            Package_Type_Unknown = 0xffff
        } package_type_t;
        typedef struct WASMFuncType {
            uint32_t param_count, result_count;
            wasm_valkind_t params[2], result;
        } *wasm_func_type_t;
        typedef struct WASMModuleCommon *wasm_module_t;
        typedef struct WASMModuleInstanceCommon *wasm_module_inst_t;
        typedef struct WASMExecEnv *wasm_exec_env_t;
        typedef void *wasm_function_inst_t;
        typedef struct WASMMemoryInstance *wasm_memory_inst_t;
        typedef enum {
            WASM_IMPORT_EXPORT_KIND_FUNC,
            WASM_IMPORT_EXPORT_KIND_TABLE,
            WASM_IMPORT_EXPORT_KIND_MEMORY,
            WASM_IMPORT_EXPORT_KIND_GLOBAL
        } wasm_import_export_kind_t;
        typedef struct {
            const char *module_name, *name;
            wasm_import_export_kind_t kind;
            union { wasm_func_type_t func_type; void *other; } u;
        } wasm_import_t;
        typedef struct {
            const char *name;
            wasm_import_export_kind_t kind;
            union { wasm_func_type_t func_type; void *other; } u;
        } wasm_export_t;
        typedef struct {
            wasm_valkind_t kind;
            union { int32_t i32; int64_t i64; } of;
        } wasm_val_t;
        typedef struct NativeSymbol {
            const char *symbol;
            void *func_ptr;
            const char *signature;
            void *attachment;
        } NativeSymbol;
        struct InstantiationArgs2;
        typedef struct {
            void *malloc_func, *realloc_func, *free_func;
        } AllocatorOption;
        typedef union { AllocatorOption allocator; } MemAllocOption;
        typedef struct { int mem_alloc_type; MemAllocOption mem_alloc_option; } RuntimeInitArgs;
        #define Alloc_With_Allocator 1
        #define native_raw_return_type(type, args) type *raw_ret = (type *)(args)
        #define native_raw_get_arg(type, name, args) type name = *((type *)(args++))
        #define native_raw_set_return(val) (*raw_ret = (val))
        uint32_t wasm_func_type_get_param_count(wasm_func_type_t type);
        uint32_t wasm_func_type_get_result_count(wasm_func_type_t type);
        wasm_valkind_t wasm_func_type_get_param_valkind(wasm_func_type_t type, uint32_t index);
        wasm_valkind_t wasm_func_type_get_result_valkind(wasm_func_type_t type, uint32_t index);
        int32_t wasm_runtime_get_import_count(wasm_module_t module);
        void wasm_runtime_get_import_type(wasm_module_t module, int32_t index, wasm_import_t *out);
        int32_t wasm_runtime_get_export_count(wasm_module_t module);
        void wasm_runtime_get_export_type(wasm_module_t module, int32_t index, wasm_export_t *out);
        package_type_t wasm_runtime_get_module_package_type(wasm_module_t module);
        bool wasm_runtime_full_init(RuntimeInitArgs *args);
        bool wasm_runtime_register_natives_raw(const char *module, NativeSymbol *symbols, uint32_t count);
        void wasm_runtime_destroy(void);
        bool wasm_runtime_init_thread_env(void);
        void wasm_runtime_destroy_thread_env(void);
        wasm_module_t wasm_runtime_load(uint8_t *buf, uint32_t size, char *error, uint32_t error_size);
        void wasm_runtime_unload(wasm_module_t module);
        bool wasm_runtime_instantiation_args_create(struct InstantiationArgs2 **args);
        void wasm_runtime_instantiation_args_destroy(struct InstantiationArgs2 *args);
        void wasm_runtime_instantiation_args_set_default_stack_size(struct InstantiationArgs2 *args, uint32_t value);
        void wasm_runtime_instantiation_args_set_host_managed_heap_size(struct InstantiationArgs2 *args, uint32_t value);
        void wasm_runtime_instantiation_args_set_max_memory_pages(struct InstantiationArgs2 *args, uint32_t value);
        wasm_module_inst_t wasm_runtime_instantiate_ex2(wasm_module_t module, const struct InstantiationArgs2 *args, char *error, uint32_t error_size);
        void wasm_runtime_set_custom_data(wasm_module_inst_t instance, void *data);
        wasm_exec_env_t wasm_runtime_create_exec_env(wasm_module_inst_t instance, uint32_t stack_size);
        void wasm_runtime_destroy_exec_env(wasm_exec_env_t env);
        wasm_function_inst_t wasm_runtime_lookup_function(wasm_module_inst_t instance, const char *name);
        void wasm_runtime_deinstantiate(wasm_module_inst_t instance);
        wasm_module_inst_t wasm_runtime_get_module_inst(wasm_exec_env_t env);
        void *wasm_runtime_get_custom_data(wasm_module_inst_t instance);
        wasm_memory_inst_t wasm_runtime_get_memory(wasm_module_inst_t instance, uint32_t index);
        uint64_t wasm_memory_get_cur_page_count(wasm_memory_inst_t memory);
        uint64_t wasm_memory_get_bytes_per_page(wasm_memory_inst_t memory);
        bool wasm_runtime_validate_app_addr(wasm_module_inst_t instance, uint32_t offset, uint32_t size);
        void *wasm_runtime_addr_app_to_native(wasm_module_inst_t instance, uint32_t offset);
        void wasm_runtime_clear_exception(wasm_module_inst_t instance);
        bool wasm_runtime_call_wasm_a(wasm_exec_env_t env, wasm_function_inst_t function,
                                      uint32_t result_count, wasm_val_t *results,
                                      uint32_t argument_count, wasm_val_t *arguments);
        void wasm_runtime_terminate(wasm_module_inst_t instance);
    ''',
    "wasm_runtime_common.h": r'''
        #pragma once
        #include "wasm_export.h"
    ''',
    "wasm.h": r'''
        #pragma once
        #include "wasm_export.h"
        typedef struct WASMModule { uint32_t start_function; } WASMModule;
    ''',
    "aot_runtime.h": r'''
        #pragma once
        #include "wasm_export.h"
        typedef struct AOTModule { uint32_t start_func_index; } AOTModule;
    ''',
    "bh_platform.h": r'''
        #pragma once
    ''',
    "platform.h": r'''
        #pragma once
        #include "esp_err.h"
        #include "observation_wire.h"
        typedef enum {
            WH_SENSOR_VALID = 0, WH_SENSOR_CRC_ERROR = 1,
            WH_SENSOR_DISCONNECTED = 2, WH_SENSOR_NOT_READY = 3,
            WH_SENSOR_NO_VALID_SAMPLE = 4
        } wh_sensor_status_t;
        typedef struct {
            uint64_t sample_id;
            int32_t temperature_mC;
            int64_t acquired_us;
            wh_sensor_status_t status;
        } wh_sensor_sample_t;
        typedef void (*wh_request_handler_t)(const uint8_t *, size_t);
        uint64_t wh_platform_boot_id(void);
        void wh_platform_event(const char *event, esp_err_t status);
        wh_sensor_sample_t wh_sensor_latest(void);
        esp_err_t wh_gpio_apply(uint8_t requested, uint8_t *observed);
        esp_err_t wh_observation_set_handler(wh_request_handler_t handler);
        esp_err_t wh_observation_send(uint8_t type, const uint8_t attempt[16], const uint8_t *body, size_t body_size);
    ''',
}


HARNESS = r'''
#define _POSIX_C_SOURCE 200809L
#include "module_manager.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "mbedtls/sha256.h"
#include "platform.h"
#include "wasm_export.h"
#include "wasm_runtime_common.h"
#include "wasm.h"
#include "aot_runtime.h"

#include <assert.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

enum {
    IMG_TELEMETRY = 0x11, IMG_TELEMETRY_NO_PUBLISH = 0x12,
    IMG_TELEMETRY_DUPLICATE = 0x13, IMG_TELEMETRY_BAD_OFFSET = 0x14,
    IMG_CONTROL_V1 = 0x21, IMG_CONTROL_V2 = 0x22,
    IMG_CONTROL_NO_PUBLISH = 0x23, IMG_CONTROL_BAD_PUBLISH = 0x24,
    IMG_CONTROL_TRAP_AFTER_STAGE = 0x25, IMG_CONTROL_BAD_IMPORT = 0x26,
    IMG_CONTROL_START_WASM = 0x27, IMG_CONTROL_START_AOT = 0x28,
    IMG_CONTROL_UNKNOWN_PACKAGE = 0x29, IMG_CONTROL_BAD_ABI = 0x2a,
    IMG_CONTROL_IMPORT_DURING_ABI = 0x2b, IMG_CONTROL_LOAD_FAIL = 0x2c,
    IMG_CONTROL_INSTANTIATE_FAIL = 0x2d, IMG_CONTROL_EXEC_ENV_FAIL = 0x2e,
    IMG_CONTROL_LOOKUP_FAIL = 0x2f, IMG_TELEMETRY_BAD_IMPORT = 0x30,
    IMG_CONTROL_BAD_EXPORT = 0x31, IMG_CONTROL_GPIO_FAIL = 0x32,
};

typedef struct {
    pthread_mutex_t lock;
    pthread_cond_t ready;
    unsigned count, max;
} fake_sem_t;
typedef struct {
    pthread_mutex_t lock;
    pthread_cond_t readable, writable;
    unsigned capacity, item_size, head, count;
    unsigned char *items;
} fake_queue_t;
typedef struct { void (*callback)(void *); void *arg; } fake_timer_t;
typedef struct { uint32_t dummy; } fake_instantiation_args_t;

struct WASMModuleCommon {
    uint32_t start_function, start_func_index;
    uint8_t id;
    package_type_t package;
    int32_t import_count, export_count;
    wasm_import_t imports[4];
    wasm_export_t exports[4];
};
typedef struct { wasm_module_inst_t instance; } fake_exec_env_t;
typedef struct { uint8_t bytes[65536]; } fake_memory_t;
struct WASMModuleInstanceCommon {
    wasm_module_t module;
    void *custom_data;
    fake_memory_t *memory;
};
typedef struct { const char *name; } fake_function_t;
struct WASMMemoryInstance { fake_memory_t *memory; };

static struct WASMFuncType type_v_i32 = {0, 1, {0, 0}, WASM_I32};
static struct WASMFuncType type_v_i64 = {0, 1, {0, 0}, WASM_I64};
static struct WASMFuncType type_i_i = {1, 1, {WASM_I32, 0}, WASM_I32};
static struct WASMFuncType type_ii_i = {2, 1, {WASM_I64, WASM_I32}, WASM_I32};
static NativeSymbol telemetry_symbols[4], control_symbols[4];
static uint32_t telemetry_symbol_count, control_symbol_count;
static NativeSymbol *registered_symbols(const char *module, uint32_t *count)
{
    if (strcmp(module, "wasmhome_telemetry_v1") == 0) { *count = telemetry_symbol_count; return telemetry_symbols; }
    *count = control_symbol_count; return control_symbols;
}

static pthread_mutex_t send_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t send_ready = PTHREAD_COND_INITIALIZER;
typedef struct { uint8_t type, attempt[16], body[16]; size_t size; } sent_record_t;
static sent_record_t sent[256];
static size_t sent_count;
static atomic_uint gpio_calls;
static atomic_int gpio_mode;
static atomic_uint event_count;
static atomic_uint live_modules, live_instances, live_exec_envs, live_timers;
static atomic_uint instantiate_calls, start_probe_count, control_call_count;
static atomic_bool block_control, control_entered, release_control;
static atomic_int fail_next_send;
static wh_request_handler_t installed_handler;
static wh_sensor_sample_t sensor_sample = {77, 26400, 0, WH_SENSOR_VALID};
static RuntimeInitArgs captured_init_args;
static fake_timer_t *timer_handles[3];
static unsigned timer_handle_count;

static void wait_flag(atomic_bool *flag)
{
    struct timespec pause = {.tv_sec = 0, .tv_nsec = 1000000};
    for (unsigned i = 0; i < 3000 && !atomic_load(flag); ++i) nanosleep(&pause, NULL);
    assert(atomic_load(flag));
}

QueueHandle_t xQueueCreate(UBaseType_t length, UBaseType_t item_size)
{
    fake_queue_t *q = calloc(1, sizeof(*q));
    if (!q) return NULL;
    q->capacity = length; q->item_size = item_size;
    q->items = calloc(length, item_size);
    pthread_mutex_init(&q->lock, NULL); pthread_cond_init(&q->readable, NULL); pthread_cond_init(&q->writable, NULL);
    if (!q->items) { free(q); return NULL; }
    return q;
}
BaseType_t xQueueSend(QueueHandle_t handle, const void *item, TickType_t wait)
{
    fake_queue_t *q = handle; if (!q) return pdFALSE;
    pthread_mutex_lock(&q->lock);
    while (q->count == q->capacity) {
        if (!wait) { pthread_mutex_unlock(&q->lock); return pdFALSE; }
        pthread_cond_wait(&q->writable, &q->lock);
    }
    unsigned tail = (q->head + q->count) % q->capacity;
    memcpy(q->items + (size_t)tail * q->item_size, item, q->item_size); q->count++;
    pthread_cond_signal(&q->readable); pthread_mutex_unlock(&q->lock); return pdTRUE;
}
BaseType_t xQueueReceive(QueueHandle_t handle, void *item, TickType_t wait)
{
    fake_queue_t *q = handle; if (!q) return pdFALSE;
    struct timespec deadline = {0};
    if (wait != 0 && wait != portMAX_DELAY) {
        clock_gettime(CLOCK_REALTIME, &deadline);
        deadline.tv_sec += wait / 1000;
        deadline.tv_nsec += (long)(wait % 1000) * 1000000;
        if (deadline.tv_nsec >= 1000000000) { deadline.tv_sec++; deadline.tv_nsec -= 1000000000; }
    }
    pthread_mutex_lock(&q->lock);
    while (!q->count) {
        if (!wait) { pthread_mutex_unlock(&q->lock); return pdFALSE; }
        int result = wait == portMAX_DELAY
            ? pthread_cond_wait(&q->readable, &q->lock)
            : pthread_cond_timedwait(&q->readable, &q->lock, &deadline);
        if (result != 0) { pthread_mutex_unlock(&q->lock); return pdFALSE; }
    }
    memcpy(item, q->items + (size_t)q->head * q->item_size, q->item_size);
    q->head = (q->head + 1) % q->capacity; q->count--;
    pthread_cond_signal(&q->writable); pthread_mutex_unlock(&q->lock); return pdTRUE;
}
UBaseType_t uxQueueMessagesWaiting(QueueHandle_t handle)
{
    fake_queue_t *q = handle; if (!q) return 0;
    pthread_mutex_lock(&q->lock); unsigned count = q->count; pthread_mutex_unlock(&q->lock); return count;
}
void vQueueDelete(QueueHandle_t handle)
{
    fake_queue_t *q = handle; if (!q) return;
    pthread_cond_destroy(&q->readable); pthread_cond_destroy(&q->writable); pthread_mutex_destroy(&q->lock); free(q->items); free(q);
}
static SemaphoreHandle_t sem_create(unsigned max, unsigned initial)
{
    fake_sem_t *s = calloc(1, sizeof(*s)); if (!s) return NULL;
    pthread_mutex_init(&s->lock, NULL); pthread_cond_init(&s->ready, NULL); s->max = max; s->count = initial; return s;
}
SemaphoreHandle_t xSemaphoreCreateMutex(void) { return sem_create(1, 1); }
SemaphoreHandle_t xSemaphoreCreateBinary(void) { return sem_create(1, 0); }
SemaphoreHandle_t xSemaphoreCreateCounting(UBaseType_t max, UBaseType_t initial) { return sem_create(max, initial); }
BaseType_t xSemaphoreTake(SemaphoreHandle_t handle, TickType_t wait)
{
    fake_sem_t *s = handle; if (!s) return pdFALSE;
    pthread_mutex_lock(&s->lock);
    while (!s->count) { if (!wait) { pthread_mutex_unlock(&s->lock); return pdFALSE; } pthread_cond_wait(&s->ready, &s->lock); }
    s->count--; pthread_mutex_unlock(&s->lock); return pdTRUE;
}
BaseType_t xSemaphoreGive(SemaphoreHandle_t handle)
{
    fake_sem_t *s = handle; if (!s) return pdFALSE;
    pthread_mutex_lock(&s->lock); if (s->count == s->max) { pthread_mutex_unlock(&s->lock); return pdFALSE; }
    s->count++; pthread_cond_signal(&s->ready); pthread_mutex_unlock(&s->lock); return pdTRUE;
}
void vSemaphoreDelete(SemaphoreHandle_t handle)
{
    fake_sem_t *s = handle; if (!s) return; pthread_cond_destroy(&s->ready); pthread_mutex_destroy(&s->lock); free(s);
}
static void sleep_ms(unsigned ms) { struct timespec t = {.tv_sec = ms / 1000, .tv_nsec = (long)(ms % 1000) * 1000000}; nanosleep(&t, NULL); }
void vTaskDelay(TickType_t ticks) { sleep_ms(ticks ? ticks : 1); }
TickType_t xTaskGetTickCount(void) { return (TickType_t)(esp_timer_get_time() / 1000); }
UBaseType_t uxTaskGetStackHighWaterMark(void *task) { (void)task; return 100; }
void vTaskDelayUntil(TickType_t *previous, TickType_t increment)
{
    *previous += increment; sleep_ms(increment ? increment : 1);
}
static void *fake_heap_allocate(size_t n, size_t alignment, bool misalign_to_four)
{
    if (!n) n = 1;
    if (n > SIZE_MAX - sizeof(void *) - alignment) return NULL;
    uint8_t *base = malloc(n + sizeof(void *) + alignment);
    if (!base) return NULL;
    uintptr_t address = (uintptr_t)(base + sizeof(void *));
    if (misalign_to_four) {
        address += (4u - (address & 7u) + 8u) & 7u;
    } else {
        address = (address + alignment - 1u) & ~(uintptr_t)(alignment - 1u);
    }
    void *result = (void *)address;
    memcpy((uint8_t *)result - sizeof(base), &base, sizeof(base));
    return result;
}
static unsigned fake_aligned_alloc_caps;
void *heap_caps_malloc(size_t n, unsigned caps)
{
    (void)caps;
    return fake_heap_allocate(n, 8, true);
}
void *heap_caps_aligned_alloc(size_t alignment, size_t n, unsigned caps)
{
    if (!alignment || (alignment & (alignment - 1u)) || n % alignment) return NULL;
    fake_aligned_alloc_caps = caps;
    return fake_heap_allocate(n, alignment, false);
}
void heap_caps_free(void *p)
{
    if (!p) return;
    void *base;
    memcpy(&base, (uint8_t *)p - sizeof(base), sizeof(base));
    free(base);
}
size_t heap_caps_get_total_size(unsigned caps) { (void)caps; return 262144; }
void heap_caps_get_info(multi_heap_info_t *info, unsigned caps) { (void)caps; info->total_free_bytes = 123456; info->largest_free_block = 65432; }
int64_t esp_timer_get_time(void)
{
    struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return (int64_t)t.tv_sec * 1000000 + t.tv_nsec / 1000;
}
esp_err_t esp_timer_create(const esp_timer_create_args_t *args, esp_timer_handle_t *out)
{
    fake_timer_t *t = calloc(1, sizeof(*t)); if (!t) return ESP_ERR_NO_MEM; t->callback = args->callback; t->arg = args->arg; *out = t;
    assert(timer_handle_count < sizeof(timer_handles) / sizeof(timer_handles[0])); timer_handles[timer_handle_count++] = t;
    atomic_fetch_add(&live_timers, 1); return ESP_OK;
}
esp_err_t esp_timer_start_once(esp_timer_handle_t timer, uint64_t timeout) { (void)timeout; return timer ? ESP_OK : ESP_FAIL; }
esp_err_t esp_timer_stop(esp_timer_handle_t timer) { return timer ? ESP_OK : ESP_FAIL; }
esp_err_t esp_timer_delete(esp_timer_handle_t timer) { if (!timer) return ESP_FAIL; free(timer); atomic_fetch_sub(&live_timers, 1); return ESP_OK; }
static void fake_fire_timer(unsigned index)
{
    assert(index < timer_handle_count && timer_handles[index]);
    timer_handles[index]->callback(timer_handles[index]->arg);
}
int mbedtls_sha256(const unsigned char *in, size_t n, unsigned char out[32], int is224)
{
    (void)is224; uint32_t h = 2166136261u; for (size_t i = 0; i < n; ++i) h = (h ^ in[i]) * 16777619u;
    for (unsigned i = 0; i < 32; ++i) { h = h * 1664525u + 1013904223u; out[i] = (uint8_t)(h >> 24); } return 0;
}

uint32_t wasm_func_type_get_param_count(wasm_func_type_t t) { return t ? t->param_count : 0; }
uint32_t wasm_func_type_get_result_count(wasm_func_type_t t) { return t ? t->result_count : 0; }
wasm_valkind_t wasm_func_type_get_param_valkind(wasm_func_type_t t, uint32_t i) { return t->params[i]; }
wasm_valkind_t wasm_func_type_get_result_valkind(wasm_func_type_t t, uint32_t i) { (void)i; return t->result; }
int32_t wasm_runtime_get_import_count(wasm_module_t m) { return m->import_count; }
void wasm_runtime_get_import_type(wasm_module_t m, int32_t i, wasm_import_t *out) { *out = m->imports[i]; }
int32_t wasm_runtime_get_export_count(wasm_module_t m) { return m->export_count; }
void wasm_runtime_get_export_type(wasm_module_t m, int32_t i, wasm_export_t *out) { *out = m->exports[i]; }
package_type_t wasm_runtime_get_module_package_type(wasm_module_t m) { return m->package; }
bool wasm_runtime_full_init(RuntimeInitArgs *args) { captured_init_args = *args; return true; }
bool wasm_runtime_register_natives_raw(const char *name, NativeSymbol *symbols, uint32_t count)
{
    if (strcmp(name, "wasmhome_telemetry_v1") == 0) { memcpy(telemetry_symbols, symbols, count * sizeof(*symbols)); telemetry_symbol_count = count; }
    else { memcpy(control_symbols, symbols, count * sizeof(*symbols)); control_symbol_count = count; }
    return true;
}
void wasm_runtime_destroy(void) { telemetry_symbol_count = control_symbol_count = 0; }
bool wasm_runtime_init_thread_env(void) { return true; }
void wasm_runtime_destroy_thread_env(void) { }

static void set_import(wasm_module_t m, int i, const char *module, const char *name, wasm_func_type_t type)
{
    m->imports[i].module_name = module; m->imports[i].name = name; m->imports[i].kind = WASM_IMPORT_EXPORT_KIND_FUNC; m->imports[i].u.func_type = type;
}
static void module_metadata(wasm_module_t m)
{
    static const char *const tele_names[] = {"snapshot", "monotonic_us", "publish_telemetry"};
    static const char *const ctrl_names[] = {"monotonic_us", "gpio_request", "publish_control"};
    m->import_count = 3;
    if (m->id == IMG_TELEMETRY_BAD_IMPORT) set_import(m, 0, "wasi_snapshot_preview1", "fd_write", &type_i_i);
    else if (m->id == IMG_CONTROL_BAD_IMPORT) set_import(m, 0, "wasmhome_telemetry_v1", "snapshot", &type_i_i);
    else if (m->id == IMG_TELEMETRY || m->id == IMG_TELEMETRY_NO_PUBLISH || m->id == IMG_TELEMETRY_DUPLICATE || m->id == IMG_TELEMETRY_BAD_OFFSET) {
        set_import(m, 0, "wasmhome_telemetry_v1", tele_names[0], &type_i_i);
        set_import(m, 1, "wasmhome_telemetry_v1", tele_names[1], &type_v_i64);
        set_import(m, 2, "wasmhome_telemetry_v1", tele_names[2], &type_v_i32);
    } else {
        set_import(m, 0, "wasmhome_control_v1", ctrl_names[0], &type_v_i64);
        set_import(m, 1, "wasmhome_control_v1", ctrl_names[1], &type_i_i);
        set_import(m, 2, "wasmhome_control_v1", ctrl_names[2], &type_ii_i);
    }
    m->export_count = 2;
    m->exports[0] = (wasm_export_t){.name = "wh_abi_version", .kind = WASM_IMPORT_EXPORT_KIND_FUNC, .u.func_type = (wasm_func_type_t)&type_v_i32};
    const bool telemetry = m->id == IMG_TELEMETRY || m->id == IMG_TELEMETRY_NO_PUBLISH || m->id == IMG_TELEMETRY_DUPLICATE || m->id == IMG_TELEMETRY_BAD_OFFSET || m->id == IMG_TELEMETRY_BAD_IMPORT;
    m->exports[1] = (wasm_export_t){.name = telemetry ? "wh_telemetry_tick" : "wh_control_request", .kind = WASM_IMPORT_EXPORT_KIND_FUNC, .u.func_type = (wasm_func_type_t)(telemetry ? &type_v_i32 : &type_ii_i)};
    if (telemetry && m->id != IMG_TELEMETRY_BAD_IMPORT) {
        m->exports[2] = (wasm_export_t){.name = "memory", .kind = WASM_IMPORT_EXPORT_KIND_MEMORY}; m->export_count = 3;
    }
    if (m->id == IMG_CONTROL_BAD_EXPORT) m->exports[1].name = "unexpected_export";
}
wasm_module_t wasm_runtime_load(uint8_t *buf, uint32_t size, char *error, uint32_t error_size)
{
    (void)error; (void)error_size; if (!buf || !size || buf[0] == IMG_CONTROL_LOAD_FAIL) return NULL;
    struct WASMModuleCommon *m = calloc(1, sizeof(*m)); if (!m) return NULL; m->id = buf[0]; m->package = Wasm_Module_Bytecode;
    m->start_function = UINT32_MAX; m->start_func_index = UINT32_MAX;
    if (m->id == IMG_CONTROL_START_WASM) m->start_function = 0;
    if (m->id == IMG_CONTROL_START_AOT) { m->package = Wasm_Module_AoT; m->start_function = 0; }
    if (m->id == IMG_CONTROL_UNKNOWN_PACKAGE) m->package = Package_Type_Unknown;
    module_metadata(m); atomic_fetch_add(&live_modules, 1); return m;
}
void wasm_runtime_unload(wasm_module_t m) { if (m) { free(m); atomic_fetch_sub(&live_modules, 1); } }
bool wasm_runtime_instantiation_args_create(struct InstantiationArgs2 **out) { *out = calloc(1, sizeof(fake_instantiation_args_t)); return *out != NULL; }
void wasm_runtime_instantiation_args_destroy(struct InstantiationArgs2 *args) { free(args); }
void wasm_runtime_instantiation_args_set_default_stack_size(struct InstantiationArgs2 *a, uint32_t v) { (void)a; (void)v; }
void wasm_runtime_instantiation_args_set_host_managed_heap_size(struct InstantiationArgs2 *a, uint32_t v) { (void)a; (void)v; }
void wasm_runtime_instantiation_args_set_max_memory_pages(struct InstantiationArgs2 *a, uint32_t v) { (void)a; (void)v; }
wasm_module_inst_t wasm_runtime_instantiate_ex2(wasm_module_t m, const struct InstantiationArgs2 *args, char *error, uint32_t error_size)
{
    (void)args; (void)error; (void)error_size; atomic_fetch_add(&instantiate_calls, 1);
    if (m->id == IMG_CONTROL_INSTANTIATE_FAIL) return NULL;
    struct WASMModuleInstanceCommon *inst = calloc(1, sizeof(*inst)); if (!inst) return NULL;
    inst->memory = calloc(1, sizeof(*inst->memory)); if (!inst->memory) { free(inst); return NULL; }
    inst->module = m; atomic_fetch_add(&live_instances, 1); return inst;
}
void wasm_runtime_set_custom_data(wasm_module_inst_t i, void *p) { i->custom_data = p; }
wasm_exec_env_t wasm_runtime_create_exec_env(wasm_module_inst_t i, uint32_t stack)
{
    (void)stack; if (i->module->id == IMG_CONTROL_EXEC_ENV_FAIL) return NULL;
    fake_exec_env_t *e = calloc(1, sizeof(*e)); if (!e) return NULL; e->instance = i; atomic_fetch_add(&live_exec_envs, 1); return (wasm_exec_env_t)e;
}
void wasm_runtime_destroy_exec_env(wasm_exec_env_t env) { if (env) { free(env); atomic_fetch_sub(&live_exec_envs, 1); } }
static fake_function_t abi_function = {"wh_abi_version"}, telemetry_function = {"wh_telemetry_tick"}, control_function = {"wh_control_request"};
wasm_function_inst_t wasm_runtime_lookup_function(wasm_module_inst_t i, const char *name)
{
    if (i->module->id == IMG_CONTROL_LOOKUP_FAIL && strcmp(name, "wh_control_request") == 0) return NULL;
    if (strcmp(name, "wh_abi_version") == 0) return &abi_function;
    if (strcmp(name, "wh_telemetry_tick") == 0) return &telemetry_function;
    if (strcmp(name, "wh_control_request") == 0) return &control_function;
    return NULL;
}
void wasm_runtime_deinstantiate(wasm_module_inst_t i) { if (i) { free(i->memory); free(i); atomic_fetch_sub(&live_instances, 1); } }
wasm_module_inst_t wasm_runtime_get_module_inst(wasm_exec_env_t e) { return e ? ((fake_exec_env_t *)e)->instance : NULL; }
void *wasm_runtime_get_custom_data(wasm_module_inst_t i) { return i ? i->custom_data : NULL; }
wasm_memory_inst_t wasm_runtime_get_memory(wasm_module_inst_t i, uint32_t index) { (void)index; static struct WASMMemoryInstance mem; mem.memory = i->memory; return &mem; }
uint64_t wasm_memory_get_cur_page_count(wasm_memory_inst_t m) { return m ? 1 : 0; }
uint64_t wasm_memory_get_bytes_per_page(wasm_memory_inst_t m) { return m ? 65536 : 0; }
bool wasm_runtime_validate_app_addr(wasm_module_inst_t i, uint32_t off, uint32_t size) { return i && off <= 65536 && size <= 65536 - off; }
void *wasm_runtime_addr_app_to_native(wasm_module_inst_t i, uint32_t off) { return wasm_runtime_validate_app_addr(i, off, 0) ? i->memory->bytes + off : NULL; }
void wasm_runtime_clear_exception(wasm_module_inst_t i) { (void)i; }
void wasm_runtime_terminate(wasm_module_inst_t i) { (void)i; }

static uint32_t invoke_i32(const char *module, const char *name, wasm_exec_env_t env, uint32_t arg)
{
    uint32_t count = 0; NativeSymbol *symbols = registered_symbols(module, &count);
    for (uint32_t i = 0; i < count; ++i) if (strcmp(symbols[i].symbol, name) == 0) {
        uint64_t raw[1] = {arg}; ((void (*)(wasm_exec_env_t, uint64_t *))symbols[i].func_ptr)(env, raw); return (uint32_t)raw[0];
    }
    assert(!"native import missing"); return UINT32_MAX;
}
static uint64_t invoke_i64_0(const char *module, const char *name, wasm_exec_env_t env)
{
    uint32_t count = 0; NativeSymbol *symbols = registered_symbols(module, &count);
    for (uint32_t i = 0; i < count; ++i) if (strcmp(symbols[i].symbol, name) == 0) {
        uint64_t raw[1] = {0}; ((void (*)(wasm_exec_env_t, uint64_t *))symbols[i].func_ptr)(env, raw); return raw[0];
    }
    assert(!"native import missing"); return UINT64_MAX;
}
static uint32_t invoke_i64_i32(const char *module, const char *name, wasm_exec_env_t env, uint64_t a, uint32_t b)
{
    uint32_t count = 0; NativeSymbol *symbols = registered_symbols(module, &count);
    for (uint32_t i = 0; i < count; ++i) if (strcmp(symbols[i].symbol, name) == 0) {
        uint64_t raw[2] = {a, b}; ((void (*)(wasm_exec_env_t, uint64_t *))symbols[i].func_ptr)(env, raw); return (uint32_t)raw[0];
    }
    assert(!"native import missing"); return UINT32_MAX;
}
bool wasm_runtime_call_wasm_a(wasm_exec_env_t env, wasm_function_inst_t function,
                              uint32_t results_count, wasm_val_t *results,
                              uint32_t argc, wasm_val_t *argv)
{
    assert(env && function && results_count == 1); fake_exec_env_t *e = (fake_exec_env_t *)env; wasm_module_inst_t inst = e->instance;
    struct WASMModuleCommon *m = inst->module; fake_function_t *f = function; results[0].kind = WASM_I32;
    if (strcmp(f->name, "wh_abi_version") == 0) {
        atomic_fetch_add(&start_probe_count, 1);
        if (m->id == IMG_CONTROL_IMPORT_DURING_ABI) (void)invoke_i64_0("wasmhome_control_v1", "monotonic_us", env);
        results[0].of.i32 = m->id == IMG_CONTROL_BAD_ABI ? 2 : 1; return true;
    }
    if (strcmp(f->name, "wh_telemetry_tick") == 0) {
        assert(argc == 0);
        uint32_t offset = m->id == IMG_TELEMETRY_BAD_OFFSET ? 65530u : 16u;
        uint32_t snap = invoke_i32("wasmhome_telemetry_v1", "snapshot", env, offset);
        (void)invoke_i64_0("wasmhome_telemetry_v1", "monotonic_us", env);
        if (m->id == IMG_TELEMETRY_NO_PUBLISH || m->id == IMG_TELEMETRY_BAD_OFFSET) {
            if (m->id == IMG_TELEMETRY_BAD_OFFSET) (void)invoke_i32("wasmhome_telemetry_v1", "publish_telemetry", env, 0);
            results[0].of.i32 = 0; return true;
        }
        uint32_t first = invoke_i32("wasmhome_telemetry_v1", "publish_telemetry", env, 0);
        if (m->id == IMG_TELEMETRY_DUPLICATE) (void)invoke_i32("wasmhome_telemetry_v1", "publish_telemetry", env, 0);
        results[0].of.i32 = (snap == WH_OK && first == WH_OK) ? 0 : 0; return true;
    }
    assert(strcmp(f->name, "wh_control_request") == 0 && argc == 2);
    atomic_fetch_add(&control_call_count, 1);
    if (atomic_load(&block_control)) {
        atomic_store(&control_entered, true);
        while (!atomic_load(&release_control)) sleep_ms(1);
    }
    uint64_t sample_id = (uint64_t)argv[0].of.i64;
    int32_t temperature = argv[1].of.i32;
    uint32_t level = temperature >= (m->id == IMG_CONTROL_V2 ? 30000 : 25000) ? 1u : 0u;
    uint32_t staged = invoke_i32("wasmhome_control_v1", "gpio_request", env, level);
    if (m->id != IMG_CONTROL_NO_PUBLISH) {
        uint32_t published = m->id == IMG_CONTROL_BAD_PUBLISH ? level ^ 1u : level;
        (void)invoke_i64_i32("wasmhome_control_v1", "publish_control", env, sample_id, published);
    }
    (void)invoke_i64_0("wasmhome_control_v1", "monotonic_us", env);
    results[0].of.i32 = m->id == IMG_CONTROL_TRAP_AFTER_STAGE ? 1 : (staged == WH_OK ? 0 : 1);
    return true;
}

int64_t esp_timer_get_time(void);
esp_err_t wh_observation_set_handler(wh_request_handler_t handler) { installed_handler = handler; return ESP_OK; }
uint64_t wh_platform_boot_id(void) { return 0x1111222233334444ULL; }
void wh_platform_event(const char *event, esp_err_t status) { (void)event; (void)status; atomic_fetch_add(&event_count, 1); }
wh_sensor_sample_t wh_sensor_latest(void) { sensor_sample.acquired_us = esp_timer_get_time() - 25000; return sensor_sample; }
esp_err_t wh_gpio_apply(uint8_t requested, uint8_t *observed)
{
    atomic_fetch_add(&gpio_calls, 1); int mode = atomic_load(&gpio_mode);
    if (mode < 0) return ESP_FAIL;
    *observed = mode == 1 ? (uint8_t)(requested ^ 1u) : requested;
    return ESP_OK;
}
esp_err_t wh_observation_send(uint8_t type, const uint8_t attempt[16], const uint8_t *body, size_t size)
{
    if (atomic_exchange(&fail_next_send, 0)) return ESP_FAIL;
    assert(size <= sizeof(sent[0].body)); pthread_mutex_lock(&send_lock);
    assert(sent_count < sizeof(sent) / sizeof(sent[0])); sent_record_t *r = &sent[sent_count++];
    r->type = type; memcpy(r->attempt, attempt, 16); memcpy(r->body, body, size); r->size = size;
    pthread_cond_broadcast(&send_ready); pthread_mutex_unlock(&send_lock); return ESP_OK;
}

static sent_record_t wait_sent_type(uint8_t type, size_t after)
{
    pthread_mutex_lock(&send_lock);
    for (unsigned t = 0; t < 5000; ++t) {
        for (size_t i = after; i < sent_count; ++i) if (sent[i].type == type) { sent_record_t found = sent[i]; pthread_mutex_unlock(&send_lock); return found; }
        struct timespec until; clock_gettime(CLOCK_REALTIME, &until); until.tv_nsec += 1000000;
        if (until.tv_nsec >= 1000000000) { until.tv_sec++; until.tv_nsec -= 1000000000; }
        pthread_cond_timedwait(&send_ready, &send_lock, &until);
    }
    pthread_mutex_unlock(&send_lock); assert(!"timed out waiting for observation send"); return (sent_record_t){0};
}
static size_t current_sent_count(void) { pthread_mutex_lock(&send_lock); size_t n = sent_count; pthread_mutex_unlock(&send_lock); return n; }
static size_t control_send_count_since(size_t after)
{
    size_t count = 0;
    pthread_mutex_lock(&send_lock);
    for (size_t i = after; i < sent_count; ++i) count += sent[i].type == WH_CONTROL_RESULT;
    pthread_mutex_unlock(&send_lock);
    return count;
}
static wh_module_image_t image(uint8_t id, uint8_t version)
{
    static uint8_t bytes[256][4]; bytes[id][0] = id; bytes[id][1] = 0xaa; bytes[id][2] = 0xbb;
    return (wh_module_image_t){.bytes = bytes[id], .size = 3, .module_version = version};
}
static void send_control(uint64_t sample_id, int32_t temp, uint8_t attempt_id)
{
    uint8_t body[12] = {0}, packet[WH_HEADER_SIZE + sizeof(body)], attempt[16] = {0};
    attempt[0] = attempt_id; wh_write_be(body, sample_id, 8); wh_write_be(body + 8, (uint32_t)temp, 4);
    size_t n = wh_wire_encode(packet, sizeof(packet), WH_CONTROL_REQUEST, attempt, sample_id,
                              wh_platform_boot_id(), (uint64_t)esp_timer_get_time(), body, sizeof(body));
    assert(n == sizeof(packet)); installed_handler(packet, n);
}
static uint64_t read_be(const uint8_t *p, size_t n) { uint64_t value = 0; for (size_t i = 0; i < n; ++i) value = (value << 8) | p[i]; return value; }
static void start_valid(void)
{
    atomic_store(&gpio_calls, 0);
    atomic_store(&gpio_mode, 0);
    atomic_store(&block_control, false);
    atomic_store(&control_entered, false);
    atomic_store(&release_control, true);
    wh_module_image_t tele = image(IMG_TELEMETRY, 1), ctrl = image(IMG_CONTROL_V1, 1);
    assert(wh_module_manager_start(&tele, &ctrl) == ESP_OK); assert(installed_handler != NULL);
}
static void assert_fully_released(void)
{
    assert(atomic_load(&live_modules) == 0); assert(atomic_load(&live_instances) == 0);
    assert(atomic_load(&live_exec_envs) == 0);
    /* One watchdog timer per static slot is intentionally retained across
       manager stops, matching the pinned IDF's asynchronous delete semantics. */
    assert(atomic_load(&live_timers) <= 3);
}

static void test_metadata_rejection_and_cleanup(void)
{
    const uint8_t bad_ids[] = {IMG_CONTROL_START_WASM, IMG_CONTROL_START_AOT,
        IMG_CONTROL_UNKNOWN_PACKAGE, IMG_CONTROL_BAD_IMPORT, IMG_CONTROL_BAD_EXPORT,
        IMG_CONTROL_BAD_ABI, IMG_CONTROL_LOAD_FAIL, IMG_CONTROL_INSTANTIATE_FAIL,
        IMG_CONTROL_EXEC_ENV_FAIL, IMG_CONTROL_LOOKUP_FAIL};
    for (size_t i = 0; i < sizeof(bad_ids); ++i) {
        unsigned before_instantiate = atomic_load(&instantiate_calls);
        wh_module_image_t tele = image(IMG_TELEMETRY, 1), ctrl = image(bad_ids[i], 1);
        assert(wh_module_manager_start(&tele, &ctrl) != ESP_OK);
        assert_fully_released();
        if (bad_ids[i] == IMG_CONTROL_START_WASM || bad_ids[i] == IMG_CONTROL_START_AOT || bad_ids[i] == IMG_CONTROL_UNKNOWN_PACKAGE)
            assert(atomic_load(&instantiate_calls) == before_instantiate + 1); /* telemetry only */
    }
    wh_module_image_t bad_tele = image(IMG_TELEMETRY_BAD_IMPORT, 1), ctrl = image(IMG_CONTROL_V1, 1);
    unsigned before = atomic_load(&instantiate_calls);
    assert(wh_module_manager_start(&bad_tele, &ctrl) != ESP_OK);
    assert(atomic_load(&instantiate_calls) == before); /* start/import preflight precedes instantiate */
    assert_fully_released();
    wh_module_image_t invalid_version = image(IMG_CONTROL_V1, 3);
    assert(wh_module_manager_start(&bad_tele, &invalid_version) == ESP_ERR_INVALID_ARG);
}

static void test_callback_contract_replay_swap_and_allocator(void)
{
    fprintf(stderr, "  start\n");
    start_valid();
    fprintf(stderr, "  started\n");
    wh_module_manager_info_t info; assert(wh_module_manager_get_info(&info) == ESP_OK);
    assert(info.telemetry.state == WH_MODULE_SLOT_ACTIVE && info.control.state == WH_MODULE_SLOT_ACTIVE);
    assert(info.telemetry.module_version == 1 && info.control.module_version == 1);
    assert(info.internal_heap_total_bytes == 262144 && info.queue_payload_capacity_bytes > 0);
    assert(info.steady_worker_stack_requested_bytes == 98304);
    assert(info.artifact_live_bytes == 6 && info.allocator_live_bytes == info.artifact_live_bytes);
    size_t sent_start = current_sent_count();
    sent_record_t telemetry = wait_sent_type(WH_TELEMETRY, sent_start);
    fprintf(stderr, "  telemetry\n");
    assert(telemetry.size == 16 && read_be(telemetry.body, 8) == sensor_sample.sample_id);
    assert((int32_t)(uint32_t)read_be(telemetry.body + 8, 4) == sensor_sample.temperature_mC);
    assert(telemetry.body[15] == 0);

    typedef void *(*alloc_fn)(mem_alloc_usage_t, unsigned int);
    typedef void *(*realloc_fn)(mem_alloc_usage_t, bool, void *, unsigned int);
    typedef void (*free_fn)(mem_alloc_usage_t, void *);
    alloc_fn alloc = (alloc_fn)captured_init_args.mem_alloc_option.allocator.malloc_func;
    realloc_fn reallocate = (realloc_fn)captured_init_args.mem_alloc_option.allocator.realloc_func;
    free_fn release = (free_fn)captured_init_args.mem_alloc_option.allocator.free_func;
    assert(alloc && reallocate && release);
    unsigned char *allocation = alloc(Alloc_For_Runtime, 64); assert(allocation); assert(((uintptr_t)allocation & 7u) == 0); memset(allocation, 0x5a, 64);
    allocation = reallocate(Alloc_For_Runtime, false, allocation, 96); assert(allocation);
    assert(((uintptr_t)allocation & 7u) == 0);
    for (unsigned i = 0; i < 64; ++i) assert(allocation[i] == 0x5a);
    void *failed_growth = reallocate(Alloc_For_Runtime, false, allocation, 600000); assert(!failed_growth);
    assert(allocation[0] == 0x5a); release(Alloc_For_Runtime, allocation);
    assert(!alloc(Alloc_For_Runtime, 600000));
    assert(fake_aligned_alloc_caps == (MALLOC_CAP_8BIT | MALLOC_CAP_INTERNAL));
    unsigned char *linear = alloc(Alloc_For_LinearMemory, 32); assert(linear);
    assert(((uintptr_t)linear & 7u) == 0);
    assert(fake_aligned_alloc_caps == (MALLOC_CAP_8BIT | MALLOC_CAP_SPIRAM));
    linear = reallocate(Alloc_For_LinearMemory, false, linear, 64); assert(linear);
    assert(((uintptr_t)linear & 7u) == 0);
    assert(fake_aligned_alloc_caps == (MALLOC_CAP_8BIT | MALLOC_CAP_SPIRAM));
    release(Alloc_For_LinearMemory, linear);
    assert(wh_module_manager_get_info(&info) == ESP_OK);
    assert(info.allocator_live_bytes == info.artifact_live_bytes);
    assert(info.allocator_failures >= 2 && info.allocator_calls >= 5);

    fprintf(stderr, "  first control\n");
    size_t base = current_sent_count(); send_control(1, 32000, 1);
    sent_record_t result = wait_sent_type(WH_CONTROL_RESULT, base);
    fprintf(stderr, "  first result version/request/observed/status=%u/%u/%u/%u\n",
            (unsigned)result.body[8], (unsigned)result.body[9],
            (unsigned)result.body[10], (unsigned)result.body[11]);
    assert(result.size == 12 && read_be(result.body, 8) == 1);
    assert(result.body[8] == 1 && result.body[9] == 1 && result.body[10] == 1 && result.body[11] == 0);
    assert(atomic_load(&gpio_calls) == 1);
    fprintf(stderr, "  first assertions passed\n");
    base = current_sent_count(); send_control(1, 32000, 2);
    sent_record_t replay = wait_sent_type(WH_CONTROL_RESULT, base);
    fprintf(stderr, "  replay result\n");
    assert(memcmp(result.body, replay.body, 12) == 0 && atomic_load(&gpio_calls) == 1);
    base = current_sent_count(); send_control(1, 23000, 3); sleep_ms(80);
    assert(control_send_count_since(base) == 0 && atomic_load(&gpio_calls) == 1); /* conflicting replay is silent */

    wh_module_image_t forbidden = image(IMG_CONTROL_IMPORT_DURING_ABI, 2);
    fprintf(stderr, "  forbidden prepare\n");
    assert(wh_module_manager_prepare_control(&forbidden) != ESP_OK);
    fprintf(stderr, "  forbidden done\n");
    assert(wh_module_manager_get_info(&info) == ESP_OK && info.control.module_version == 1 && info.control.state == WH_MODULE_SLOT_ACTIVE);
    wh_module_image_t candidate = image(IMG_CONTROL_V2, 2);
    fprintf(stderr, "  valid prepare\n");
    assert(wh_module_manager_prepare_control(&candidate) == ESP_OK);
    fprintf(stderr, "  valid prepared\n");
    fprintf(stderr, "  get info after swap\n");
    assert(wh_module_manager_get_info(&info) == ESP_OK && info.control.module_version == 2 && info.control.state == WH_MODULE_SLOT_ACTIVE);
    fprintf(stderr, "  info after swap\n");
    base = current_sent_count(); send_control(2, 27000, 4);
    fprintf(stderr, "  sent request 2\n");
    result = wait_sent_type(WH_CONTROL_RESULT, base);
    fprintf(stderr, "  received request 2\n");
    assert(result.body[8] == 2 && result.body[9] == 0 && result.body[10] == 0 && result.body[11] == 0);
    assert(atomic_load(&gpio_calls) == 2);
    fprintf(stderr, "  request 2 assertions passed\n");

    /* An admitted request holds the old instance live until its call returns. */
    atomic_store(&block_control, true); atomic_store(&release_control, false); atomic_store(&control_entered, false);
    send_control(3, 32000, 5); wait_flag(&control_entered);
    fprintf(stderr, "  request 3 entered\n");
    atomic_store(&release_control, true); atomic_store(&block_control, false);
    base = current_sent_count(); send_control(4, 32000, 6); (void)wait_sent_type(WH_CONTROL_RESULT, base);
    fprintf(stderr, "  request 4 received\n");

    fprintf(stderr, "  stop manager\n");
    assert(wh_module_manager_stop() == ESP_OK); assert(installed_handler == NULL);
    fprintf(stderr, "  manager stopped\n");
    assert_fully_released();
    assert(wh_module_manager_get_info(&info) == ESP_ERR_INVALID_STATE);
}

static void test_control_failure_outputs_and_safe_swap_wait(void)
{
    start_valid();
    size_t base = current_sent_count(); send_control(1, 32000, 1);
    sent_record_t ok = wait_sent_type(WH_CONTROL_RESULT, base); assert(ok.body[9] == 1 && ok.body[11] == 0);

    wh_module_image_t no_publish = image(IMG_CONTROL_NO_PUBLISH, 1);
    assert(wh_module_manager_prepare_control(&no_publish) == ESP_OK);
    base = current_sent_count(); send_control(2, 32000, 2);
    sent_record_t failure = wait_sent_type(WH_CONTROL_RESULT, base);
    assert(failure.body[9] == 255 && failure.body[10] == 255 && failure.body[11] == 3);
    assert(atomic_load(&gpio_calls) == 1);

    wh_module_image_t bad_publish = image(IMG_CONTROL_BAD_PUBLISH, 2);
    assert(wh_module_manager_prepare_control(&bad_publish) == ESP_OK);
    base = current_sent_count(); send_control(3, 32000, 3);
    failure = wait_sent_type(WH_CONTROL_RESULT, base);
    assert(failure.body[9] == 255 && failure.body[10] == 255 && failure.body[11] == 3);
    assert(atomic_load(&gpio_calls) == 1);

    wh_module_image_t trap = image(IMG_CONTROL_TRAP_AFTER_STAGE, 1);
    assert(wh_module_manager_prepare_control(&trap) == ESP_OK);
    base = current_sent_count(); send_control(4, 32000, 4);
    failure = wait_sent_type(WH_CONTROL_RESULT, base);
    assert(failure.body[9] == 1 && failure.body[10] == 255 && failure.body[11] == 3);
    assert(atomic_load(&gpio_calls) == 1);

    wh_module_image_t gpio_fail = image(IMG_CONTROL_GPIO_FAIL, 2);
    assert(wh_module_manager_prepare_control(&gpio_fail) == ESP_OK);
    atomic_store(&gpio_mode, -1); base = current_sent_count(); send_control(5, 32000, 5);
    failure = wait_sent_type(WH_CONTROL_RESULT, base);
    assert(failure.body[9] == 1 && failure.body[10] == 255 && failure.body[11] == 3);
    atomic_store(&gpio_mode, 0);
    wh_module_manager_info_t info; assert(wh_module_manager_get_info(&info) == ESP_OK && info.gpio_failures == 1);
    assert(wh_module_manager_stop() == ESP_OK); assert_fully_released();
}

typedef struct { wh_module_image_t image; esp_err_t result; atomic_bool done; } prepare_thread_arg_t;
static void *prepare_thread(void *arg)
{
    prepare_thread_arg_t *p = arg; p->result = wh_module_manager_prepare_control(&p->image); atomic_store(&p->done, true); return NULL;
}
static void test_swap_waits_for_inflight_call(void)
{
    start_valid(); atomic_store(&block_control, true); atomic_store(&release_control, false); atomic_store(&control_entered, false);
    size_t base = current_sent_count(); send_control(1, 32000, 1); wait_flag(&control_entered);
    prepare_thread_arg_t arg = {.image = image(IMG_CONTROL_V2, 2), .result = ESP_FAIL}; atomic_init(&arg.done, false);
    pthread_t thread; assert(pthread_create(&thread, NULL, prepare_thread, &arg) == 0); sleep_ms(40);
    assert(!atomic_load(&arg.done)); /* serialized behind the active invocation */
    assert(atomic_load(&live_modules) == 2); /* candidate was not loaded before the drain point */
    atomic_store(&release_control, true); atomic_store(&block_control, false);
    (void)wait_sent_type(WH_CONTROL_RESULT, base); assert(pthread_join(thread, NULL) == 0); assert(arg.result == ESP_OK);
    assert(atomic_load(&live_modules) == 2); /* telemetry and replacement only */
    assert(wh_module_manager_stop() == ESP_OK); assert_fully_released();
}

static void test_late_watchdog_callback_is_ignored_for_current_deadline(void)
{
    start_valid();
    atomic_store(&block_control, true); atomic_store(&release_control, false);
    atomic_store(&control_entered, false);
    size_t base = current_sent_count(); send_control(1, 32000, 1); wait_flag(&control_entered);
    /* Simulate an expired callback that was queued by the prior one-shot and
       reaches the persistent slot while this invocation is still before its deadline. */
    fake_fire_timer(1);
    atomic_store(&release_control, true); atomic_store(&block_control, false);
    sent_record_t result = wait_sent_type(WH_CONTROL_RESULT, base);
    assert(result.body[9] == 1 && result.body[10] == 1 && result.body[11] == 0);
    assert(atomic_load(&gpio_calls) == 1);
    assert(wh_module_manager_stop() == ESP_OK); assert_fully_released();
}

static void test_telemetry_rejection_modes(void)
{
    const uint8_t telemetry_ids[] = {IMG_TELEMETRY_NO_PUBLISH, IMG_TELEMETRY_DUPLICATE, IMG_TELEMETRY_BAD_OFFSET};
    for (size_t j = 0; j < sizeof(telemetry_ids); ++j) {
        size_t base = current_sent_count(); wh_module_image_t tele = image(telemetry_ids[j], 1), ctrl = image(IMG_CONTROL_V1, 1);
        assert(wh_module_manager_start(&tele, &ctrl) == ESP_OK); sleep_ms(180);
        pthread_mutex_lock(&send_lock); size_t end = sent_count; unsigned telemetry_count = 0;
        for (size_t i = base; i < end; ++i) telemetry_count += sent[i].type == WH_TELEMETRY;
        pthread_mutex_unlock(&send_lock); assert(telemetry_count == 0);
        assert(wh_module_manager_stop() == ESP_OK); assert_fully_released();
    }
}

int main(void)
{
    fprintf(stderr, "metadata\n");
    test_metadata_rejection_and_cleanup();
    fprintf(stderr, "callbacks\n");
    test_callback_contract_replay_swap_and_allocator();
    fprintf(stderr, "control failures\n");
    test_control_failure_outputs_and_safe_swap_wait();
    fprintf(stderr, "swap wait\n");
    test_swap_waits_for_inflight_call();
    fprintf(stderr, "late watchdog\n");
    test_late_watchdog_callback_is_ignored_for_current_deadline();
    fprintf(stderr, "telemetry failures\n");
    test_telemetry_rejection_modes();
    puts("module manager host contract harness: PASS");
    return 0;
}
'''


class ModuleManagerHostTests(unittest.TestCase):
    def test_manager_contract_against_host_shims(self):
        compiler = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
        if compiler is None:
            self.skipTest("A host C compiler is required for the module manager shim test")
        with tempfile.TemporaryDirectory(prefix="wasmhome-module-manager-") as temporary:
            temp = pathlib.Path(temporary)
            stub_root = temp / "stubs"
            for relative, contents in STUBS.items():
                target = stub_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(textwrap.dedent(contents).lstrip(), encoding="utf-8")
            harness = temp / "module_manager_harness.c"
            executable = temp / "module_manager_harness"
            harness.write_text(HARNESS, encoding="utf-8")
            command = [
                compiler, "-std=c11", "-D_POSIX_C_SOURCE=200809L",
                "-DWASM_MEM_ALLOC_WITH_USAGE=1", "-Wall", "-Wextra",
                f"-I{stub_root}", f"-I{ROOT / 'main'}", str(harness),
                str(ROOT / "main/module_manager.c"),
                str(ROOT / "main/module_abi_v1.c"),
                str(ROOT / "main/observation_wire.c"), "-pthread", "-o", str(executable),
            ]
            build = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            self.assertNotIn("warning:", build.stderr.lower(), build.stderr)
            try:
                result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=8)
            except subprocess.TimeoutExpired as error:
                stdout = error.stdout.decode() if isinstance(error.stdout, bytes) else error.stdout or ""
                stderr = error.stderr.decode() if isinstance(error.stderr, bytes) else error.stderr or ""
                self.fail(f"host harness timed out\nstdout:\n{stdout}\nstderr:\n{stderr}")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("PASS", result.stdout)
            self.assertIn('"phase":"candidate_overlap"', result.stdout)
            self.assertIn('"role":"control_candidate"', result.stdout)
            self.assertIn('"persistent_watchdog_slots":', result.stdout)
            self.assertIn('"phase":"first_telemetry_operation"', result.stdout)
            self.assertIn('"phase":"first_control_operation"', result.stdout)
            self.assertIn('"phase":"manager_stopped"', result.stdout)


if __name__ == "__main__":
    unittest.main()
