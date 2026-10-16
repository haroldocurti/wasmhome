"""Exercise the telemetry mutex wait/drop behavior from the firmware source."""

import pathlib
import shutil
import subprocess
import tempfile
import textwrap
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


PRELUDE = r'''
#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <time.h>

#define WH_MODULE_TELEMETRY_PERIOD_MS 100u
#define pdMS_TO_TICKS(ms) ((TickType_t)(ms))
#define pdTRUE 1
#define portMAX_DELAY UINT32_MAX
typedef uint32_t TickType_t;
typedef int BaseType_t;
typedef struct {
    pthread_mutex_t guard;
    pthread_cond_t changed;
    bool held;
    unsigned wait_entries;
    unsigned timeout_requests;
    unsigned timed_out_waits;
    TickType_t last_timeout_ticks;
} host_semaphore_t;
typedef host_semaphore_t *SemaphoreHandle_t;

static host_semaphore_t api_semaphore = {
    .guard = PTHREAD_MUTEX_INITIALIZER,
    .changed = PTHREAD_COND_INITIALIZER,
};
static host_semaphore_t manager_semaphore = {
    .guard = PTHREAD_MUTEX_INITIALIZER,
    .changed = PTHREAD_COND_INITIALIZER,
};
static SemaphoreHandle_t api_lock = &api_semaphore;
static SemaphoreHandle_t manager_lock = &manager_semaphore;
static atomic_bool stop_requested;
static uint64_t telemetry_runtime_busy_drops;
static unsigned telemetry_calls;
static bool tick_result;

static void make_deadline(struct timespec *deadline, TickType_t timeout)
{
    assert(clock_gettime(CLOCK_REALTIME, deadline) == 0);
    deadline->tv_sec += timeout / 1000u;
    deadline->tv_nsec += (long)(timeout % 1000u) * 1000000L;
    if (deadline->tv_nsec >= 1000000000L) {
        deadline->tv_sec++;
        deadline->tv_nsec -= 1000000000L;
    }
}

static BaseType_t xSemaphoreTake(SemaphoreHandle_t semaphore,
                                TickType_t timeout)
{
    assert(pthread_mutex_lock(&semaphore->guard) == 0);
    if (semaphore == api_lock && timeout != portMAX_DELAY) {
        semaphore->timeout_requests++;
        semaphore->last_timeout_ticks = timeout;
    }

    if (semaphore->held && semaphore == api_lock && timeout != portMAX_DELAY) {
        semaphore->wait_entries++;
        assert(pthread_cond_broadcast(&semaphore->changed) == 0);
        struct timespec deadline;
        make_deadline(&deadline, timeout);
        while (semaphore->held) {
            int result = pthread_cond_timedwait(&semaphore->changed,
                                                &semaphore->guard, &deadline);
            if (result == ETIMEDOUT) {
                semaphore->timed_out_waits++;
                assert(pthread_mutex_unlock(&semaphore->guard) == 0);
                return 0;
            }
            assert(result == 0);
        }
    } else {
        while (semaphore->held) {
            assert(timeout == portMAX_DELAY);
            assert(pthread_cond_wait(&semaphore->changed,
                                     &semaphore->guard) == 0);
        }
    }

    semaphore->held = true;
    assert(pthread_mutex_unlock(&semaphore->guard) == 0);
    return pdTRUE;
}

static void xSemaphoreGive(SemaphoreHandle_t semaphore)
{
    assert(pthread_mutex_lock(&semaphore->guard) == 0);
    assert(semaphore->held);
    semaphore->held = false;
    assert(pthread_cond_broadcast(&semaphore->changed) == 0);
    assert(pthread_mutex_unlock(&semaphore->guard) == 0);
}

static void run_telemetry_tick(void) { telemetry_calls++; }

static void host_semaphore_hold_api(void)
{
    assert(pthread_mutex_lock(&api_lock->guard) == 0);
    assert(!api_lock->held);
    api_lock->held = true;
    assert(pthread_mutex_unlock(&api_lock->guard) == 0);
}

static void wait_for_api_wait(unsigned expected)
{
    assert(pthread_mutex_lock(&api_lock->guard) == 0);
    while (api_lock->wait_entries < expected)
        assert(pthread_cond_wait(&api_lock->changed, &api_lock->guard) == 0);
    assert(pthread_mutex_unlock(&api_lock->guard) == 0);
}

static bool telemetry_tick_once(void);

static void *telemetry_tick_worker(void *unused)
{
    (void)unused;
    tick_result = telemetry_tick_once();
    return NULL;
}
'''


HARNESS = r'''
int main(void)
{
    atomic_init(&stop_requested, false);
    pthread_t worker;

    /* The tick has entered its wait before the short holder releases the lock. */
    host_semaphore_hold_api();
    assert(pthread_create(&worker, NULL, telemetry_tick_worker, NULL) == 0);
    wait_for_api_wait(1);
    xSemaphoreGive(api_lock);
    assert(pthread_join(worker, NULL) == 0);
    assert(tick_result);
    assert(telemetry_calls == 1);
    assert(telemetry_runtime_busy_drops == 0);

    /* Holding through the timed wait proves timeout and increments one drop. */
    host_semaphore_hold_api();
    uint64_t start_ns;
    struct timespec start;
    assert(clock_gettime(CLOCK_MONOTONIC, &start) == 0);
    start_ns = (uint64_t)start.tv_sec * 1000000000u +
               (uint64_t)start.tv_nsec;
    assert(pthread_create(&worker, NULL, telemetry_tick_worker, NULL) == 0);
    wait_for_api_wait(2);
    assert(pthread_join(worker, NULL) == 0);
    struct timespec end;
    assert(clock_gettime(CLOCK_MONOTONIC, &end) == 0);
    uint64_t end_ns = (uint64_t)end.tv_sec * 1000000000u +
                      (uint64_t)end.tv_nsec;
    uint64_t elapsed_ms = (end_ns - start_ns) / 1000000u;
    assert(elapsed_ms >= 50 && elapsed_ms < 1000);
    assert(tick_result);
    assert(telemetry_calls == 1);
    assert(telemetry_runtime_busy_drops == 1);
    xSemaphoreGive(api_lock);

    /* Stop during an outstanding wait stays deadline-bounded and adds no drop. */
    atomic_store_explicit(&stop_requested, false, memory_order_release);
    host_semaphore_hold_api();
    assert(pthread_create(&worker, NULL, telemetry_tick_worker, NULL) == 0);
    wait_for_api_wait(3);
    atomic_store_explicit(&stop_requested, true, memory_order_release);
    assert(pthread_join(worker, NULL) == 0);
    assert(!tick_result);
    assert(telemetry_calls == 1);
    assert(telemetry_runtime_busy_drops == 1);
    xSemaphoreGive(api_lock);

    assert(api_lock->timeout_requests == 3);
    assert(api_lock->last_timeout_ticks == 100);
    assert(api_lock->timed_out_waits == 2);
    return 0;
}
'''


def extract_function(source, signature):
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 0
    in_string = False
    in_char = False
    escaped = False
    in_line_comment = False
    in_block_comment = False
    for index in range(opening, len(source)):
        char = source[index]
        next_char = source[index + 1] if index + 1 < len(source) else ""
        if in_line_comment:
            if char == "\n":
                in_line_comment = False
            continue
        if in_block_comment:
            if char == "*" and next_char == "/":
                in_block_comment = False
            continue
        if escaped:
            escaped = False
            continue
        if (in_string or in_char) and char == "\\":
            escaped = True
            continue
        if in_string:
            if char == '"':
                in_string = False
            continue
        if in_char:
            if char == "'":
                in_char = False
            continue
        if char == "/" and next_char == "/":
            in_line_comment = True
            continue
        if char == "/" and next_char == "*":
            in_block_comment = True
            continue
        if char == '"':
            in_string = True
            continue
        if char == "'":
            in_char = True
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"unterminated function: {signature}")


class ModuleTelemetryLockHostTests(unittest.TestCase):
    def test_waits_for_short_contention_and_drops_at_one_period_deadline(self):
        compiler = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
        if compiler is None:
            self.skipTest("A host C compiler is required for the telemetry lock test")

        source = (ROOT / "main/module_manager.c").read_text(encoding="utf-8")
        thread = extract_function(source, "static void *\ntelemetry_thread_entry(")
        self.assertIn("telemetry_tick_once()", thread)
        self.assertIn("vTaskDelayUntil(&cycle,", thread)
        self.assertIn("pdMS_TO_TICKS(WH_MODULE_TELEMETRY_PERIOD_MS)", thread)
        self.assertEqual(source.count("#define WH_MODULE_TELEMETRY_PERIOD_MS 100u"), 1)

        harness = "\n".join([
            PRELUDE,
            extract_function(source, "static bool\ntelemetry_tick_once("),
            HARNESS,
        ])
        with tempfile.TemporaryDirectory(prefix="wasmhome-telemetry-lock-") as temp_name:
            temp = pathlib.Path(temp_name)
            source_path = temp / "telemetry_lock_harness.c"
            executable = temp / "telemetry_lock_harness"
            source_path.write_text(textwrap.dedent(harness), encoding="utf-8")
            build = subprocess.run(
                [compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-pedantic",
                 "-pthread", str(source_path), "-o", str(executable)],
                capture_output=True, text=True,
            )
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            result = subprocess.run(
                [str(executable)], capture_output=True, text=True, timeout=3,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
