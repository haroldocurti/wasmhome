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
typedef pthread_mutex_t *SemaphoreHandle_t;

static pthread_mutex_t api_mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t manager_mutex = PTHREAD_MUTEX_INITIALIZER;
static SemaphoreHandle_t api_lock = &api_mutex;
static SemaphoreHandle_t manager_lock = &manager_mutex;
static atomic_bool stop_requested;
static uint64_t telemetry_runtime_busy_drops;
static unsigned telemetry_calls;

static BaseType_t xSemaphoreTake(SemaphoreHandle_t mutex, TickType_t timeout)
{
    if (timeout == portMAX_DELAY)
        return pthread_mutex_lock(mutex) == 0 ? pdTRUE : 0;
    struct timespec deadline;
    assert(clock_gettime(CLOCK_REALTIME, &deadline) == 0);
    deadline.tv_sec += timeout / 1000u;
    deadline.tv_nsec += (long)(timeout % 1000u) * 1000000L;
    if (deadline.tv_nsec >= 1000000000L) {
        deadline.tv_sec++;
        deadline.tv_nsec -= 1000000000L;
    }
    return pthread_mutex_timedlock(mutex, &deadline) == 0 ? pdTRUE : 0;
}

static void xSemaphoreGive(SemaphoreHandle_t mutex)
{ assert(pthread_mutex_unlock(mutex) == 0); }

static void run_telemetry_tick(void) { telemetry_calls++; }

static uint64_t monotonic_ms(void)
{
    struct timespec now;
    assert(clock_gettime(CLOCK_MONOTONIC, &now) == 0);
    return (uint64_t)now.tv_sec * 1000u + (uint64_t)now.tv_nsec / 1000000u;
}

static void sleep_ms(unsigned milliseconds)
{
    struct timespec delay = {
        .tv_sec = (time_t)(milliseconds / 1000u),
        .tv_nsec = (long)(milliseconds % 1000u) * 1000000L,
    };
    while (nanosleep(&delay, &delay) != 0) { }
}

static atomic_bool holder_ready;
static unsigned holder_duration_ms;
static void *hold_api_lock(void *unused)
{
    (void)unused;
    assert(pthread_mutex_lock(api_lock) == 0);
    atomic_store_explicit(&holder_ready, true, memory_order_release);
    sleep_ms(holder_duration_ms);
    xSemaphoreGive(api_lock);
    return NULL;
}

static void wait_until_holder_ready(void)
{
    while (!atomic_load_explicit(&holder_ready, memory_order_acquire))
        sleep_ms(1);
}

static void *request_stop_soon(void *unused)
{
    (void)unused;
    sleep_ms(15);
    atomic_store_explicit(&stop_requested, true, memory_order_release);
    return NULL;
}
'''


HARNESS = r'''
int main(void)
{
    atomic_init(&stop_requested, false);
    atomic_init(&holder_ready, false);

    /* A brief owner delays the tick, which then executes under the mutex. */
    pthread_t holder;
    holder_duration_ms = 35;
    assert(pthread_create(&holder, NULL, hold_api_lock, NULL) == 0);
    wait_until_holder_ready();
    uint64_t start = monotonic_ms();
    assert(telemetry_tick_once());
    uint64_t elapsed = monotonic_ms() - start;
    assert(elapsed >= 20 && elapsed < WH_MODULE_TELEMETRY_PERIOD_MS);
    assert(telemetry_calls == 1);
    assert(telemetry_runtime_busy_drops == 0);
    assert(pthread_join(holder, NULL) == 0);

    /* Contention beyond one period produces one visible drop, within deadline. */
    atomic_store_explicit(&holder_ready, false, memory_order_release);
    holder_duration_ms = 160;
    assert(pthread_create(&holder, NULL, hold_api_lock, NULL) == 0);
    wait_until_holder_ready();
    start = monotonic_ms();
    assert(telemetry_tick_once());
    elapsed = monotonic_ms() - start;
    assert(elapsed >= 80 && elapsed < 140);
    assert(telemetry_calls == 1);
    assert(telemetry_runtime_busy_drops == 1);
    assert(pthread_join(holder, NULL) == 0);

    /* Stop during the bounded wait returns by the same deadline, without a drop. */
    atomic_store_explicit(&holder_ready, false, memory_order_release);
    atomic_store_explicit(&stop_requested, false, memory_order_release);
    holder_duration_ms = 160;
    assert(pthread_create(&holder, NULL, hold_api_lock, NULL) == 0);
    wait_until_holder_ready();
    pthread_t stopper;
    assert(pthread_create(&stopper, NULL, request_stop_soon, NULL) == 0);
    start = monotonic_ms();
    assert(!telemetry_tick_once());
    elapsed = monotonic_ms() - start;
    assert(elapsed >= 80 && elapsed < 140);
    assert(telemetry_calls == 1);
    assert(telemetry_runtime_busy_drops == 1);
    assert(pthread_join(stopper, NULL) == 0);
    assert(pthread_join(holder, NULL) == 0);
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
            result = subprocess.run([str(executable)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
