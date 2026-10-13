"""Host C coverage for the native service rules and UDP-v1 payloads."""

import pathlib
import shutil
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


HARNESS = r'''
#include "native_services.h"
#include "observation_wire.h"

#include <assert.h>
#include <pthread.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

typedef struct {
    unsigned calls;
    unsigned mode;
} gpio_fixture_t;

static bool fake_gpio(void *context, uint8_t requested, uint8_t *observed)
{
    gpio_fixture_t *fixture = context;
    fixture->calls++;
    if (fixture->mode == 1) return false;
    *observed = fixture->mode == 2 ? (uint8_t)(requested ^ 1u) : requested;
    return true;
}

static void check_hysteresis_vector(uint8_t version, const int32_t *inputs,
                                   const uint8_t *expected, size_t count)
{
    wh_native_services_state_t state;
    gpio_fixture_t gpio = {0};
    wh_native_control_result_t result;
    assert(wh_native_services_state_init(&state, version));
    for (size_t i = 0; i < count; ++i) {
        assert(wh_native_control_request(&state, i + 1, inputs[i], fake_gpio,
                                         &gpio, &result) == WH_NATIVE_CONTROL_NEW);
        assert(result.module_version == version);
        assert(result.requested_gpio == expected[i]);
        assert(result.observed_gpio == expected[i]);
        assert(result.status == WH_NATIVE_CONTROL_APPLIED);
    }
    assert(gpio.calls == count);
}

static void test_hysteresis(void)
{
    const int32_t v1_edges[] = {24000, 25500, 25000, 24500};
    const uint8_t v1_edges_expected[] = {0, 1, 1, 0};
    const int32_t v2_edges[] = {29000, 30500, 30000, 29500};
    const uint8_t v2_edges_expected[] = {0, 1, 1, 0};
    const int32_t v1_distinguishes[] = {23000, 32000, 27000};
    const uint8_t v1_distinguishes_expected[] = {0, 1, 1};
    const int32_t v2_distinguishes[] = {23000, 32000, 27000};
    const uint8_t v2_distinguishes_expected[] = {0, 1, 0};
    const int32_t v1_low_band[] = {24999, 25000, 25499};
    const uint8_t low_expected[] = {0, 0, 0};
    const int32_t v2_low_band[] = {29999, 30000, 30499};

    check_hysteresis_vector(1, v1_edges, v1_edges_expected, 4);
    check_hysteresis_vector(2, v2_edges, v2_edges_expected, 4);
    check_hysteresis_vector(1, v1_distinguishes, v1_distinguishes_expected, 3);
    check_hysteresis_vector(2, v2_distinguishes, v2_distinguishes_expected, 3);
    check_hysteresis_vector(1, v1_low_band, low_expected, 3);
    check_hysteresis_vector(2, v2_low_band, low_expected, 3);
}

static void test_control_dedup(void)
{
    wh_native_services_state_t state;
    gpio_fixture_t gpio = {0};
    wh_native_control_result_t first, replay, conflict;
    assert(wh_native_services_state_init(&state, 1));
    assert(wh_native_control_request(&state, 10, 32000, fake_gpio, &gpio, &first)
           == WH_NATIVE_CONTROL_NEW);
    assert(first.requested_gpio == 1 && first.observed_gpio == 1);
    assert(wh_native_control_request(&state, 10, 32000, fake_gpio, &gpio, &replay)
           == WH_NATIVE_CONTROL_REPLAY);
    assert(memcmp(&first, &replay, sizeof(first)) == 0);
    assert(gpio.calls == 1);
    assert(wh_native_control_request(&state, 10, 23000, fake_gpio, &gpio, &conflict)
           == WH_NATIVE_CONTROL_CONFLICT);
    assert(gpio.calls == 1);

    for (uint64_t id = 11; id < 11 + WH_NATIVE_CONTROL_REPLAY_CAPACITY; ++id)
        assert(wh_native_control_request(&state, id, 25000, fake_gpio, &gpio, &replay)
               == WH_NATIVE_CONTROL_NEW);
    assert(wh_native_control_request(&state, 10, 32000, fake_gpio, &gpio, &replay)
           == WH_NATIVE_CONTROL_STALE);
    assert(gpio.calls == WH_NATIVE_CONTROL_REPLAY_CAPACITY + 1);
    assert(wh_native_control_request(&state, 0, 32000, fake_gpio, &gpio, &replay)
           == WH_NATIVE_CONTROL_INVALID);
}

static void test_control_errors_and_codec(void)
{
    wh_native_services_state_t state;
    gpio_fixture_t gpio = {.mode = 2};
    wh_native_control_result_t result;
    assert(wh_native_services_state_init(&state, 2));
    assert(wh_native_control_request(&state, 1, 32000, fake_gpio, &gpio, &result)
           == WH_NATIVE_CONTROL_NEW);
    assert(result.module_version == 2 && result.requested_gpio == 1);
    assert(result.observed_gpio == 0 && result.status == WH_NATIVE_CONTROL_MISMATCH);
    assert(wh_native_control_request(&state, 1, 32000, fake_gpio, &gpio, &result)
           == WH_NATIVE_CONTROL_REPLAY);
    assert(result.observed_gpio == 0 && result.status == WH_NATIVE_CONTROL_MISMATCH);
    assert(gpio.calls == 1);

    gpio.mode = 1;
    assert(wh_native_control_request(&state, 2, 29000, fake_gpio, &gpio, &result)
           == WH_NATIVE_CONTROL_NEW);
    assert(result.requested_gpio == 0 && result.observed_gpio == 255);
    assert(result.status == WH_NATIVE_CONTROL_EXECUTION_ERROR);

    uint8_t body[12] = {0};
    uint8_t datagram[WH_HEADER_SIZE + sizeof(body)];
    const uint8_t attempt[16] = {0};
    wh_write_be(body, 2, 8);
    body[8] = result.module_version;
    body[9] = result.requested_gpio;
    body[10] = result.observed_gpio;
    body[11] = result.status;
    size_t length = wh_wire_encode(datagram, sizeof(datagram), WH_CONTROL_RESULT,
                                   attempt, 1, 7, 9000, body, sizeof(body));
    assert(length == sizeof(datagram));
    assert(wh_wire_valid(datagram, length));
    assert(wh_read_be(datagram + WH_HEADER_SIZE, 8) == 2);
}

static void test_telemetry_body(void)
{
    uint8_t body[16];
    uint8_t datagram[WH_HEADER_SIZE + sizeof(body)];
    const uint8_t attempt[16] = {0};

    assert(wh_native_telemetry_body(body, 7, 25250, 100000, WH_NATIVE_SENSOR_VALID, 600000));
    assert(wh_read_be(body, 8) == 7);
    assert((int32_t)(uint32_t)wh_read_be(body + 8, 4) == 25250);
    assert(wh_read_be(body + 12, 2) == 500 && body[14] == WH_NATIVE_SENSOR_VALID && body[15] == 0);
    size_t length = wh_wire_encode(datagram, sizeof(datagram), WH_TELEMETRY,
                                   attempt, 1, 9, 600000, body, sizeof(body));
    assert(length == sizeof(datagram) && wh_wire_valid(datagram, length));

    assert(wh_native_telemetry_body(body, 7, 25250, 100000,
                                    WH_NATIVE_SENSOR_CRC_ERROR, 1300000));
    assert(wh_read_be(body + 12, 2) == 1200);
    assert(body[14] == WH_NATIVE_SENSOR_CRC_ERROR);
    assert(wh_native_telemetry_body(body, 7, 25250, 100000,
                                    WH_NATIVE_SENSOR_VALID, 100000000));
    assert(wh_read_be(body + 12, 2) == 65534);

    assert(wh_native_telemetry_body(body, 0, 0, 0,
                                    WH_NATIVE_SENSOR_NO_VALID_SAMPLE, 10));
    assert(wh_read_be(body, 8) == 0);
    assert((int32_t)(uint32_t)wh_read_be(body + 8, 4) == INT32_MIN);
    assert(wh_read_be(body + 12, 2) == UINT16_MAX);
    assert(wh_wire_valid(wh_wire_encode(datagram, sizeof(datagram), WH_TELEMETRY,
                                        attempt, 2, 9, 700000, body, sizeof(body))
                         ? datagram : NULL,
                         WH_HEADER_SIZE + sizeof(body)));

    assert(!wh_native_telemetry_body(body, 7, 25250, 200, WH_NATIVE_SENSOR_VALID, 100));
    assert(!wh_native_telemetry_body(body, 7, 25250, 100,
                                     WH_NATIVE_SENSOR_NO_VALID_SAMPLE, 1000));
    assert(!wh_native_telemetry_body(body, 0, 0, 0, WH_NATIVE_SENSOR_VALID, 1000));
}

static void test_update_rejection(void)
{
    wh_native_services_state_t state;
    const uint8_t attempt[16] = {1};
    uint8_t reason = 0;
    assert(wh_native_services_state_init(&state, 1));
    assert(wh_native_update_reject(&state, attempt, 7, 1, 2, 1, 1, 2, 7, &reason)
           == WH_NATIVE_UPDATE_NEW_REJECTION);
    assert(reason == WH_NATIVE_REASON_BUSY);
    assert(wh_native_update_reject(&state, attempt, 7, 1, 2, 1, 1, 2, 7, &reason)
           == WH_NATIVE_UPDATE_REPLAY_REJECTION);
    assert(reason == WH_NATIVE_REASON_BUSY);
    assert(wh_native_update_reject(&state, attempt, 7, 2, 2, 1, 1, 2, 7, &reason)
           == WH_NATIVE_UPDATE_ATTEMPT_CONFLICT);
    assert(reason == WH_NATIVE_REASON_ATTEMPT_CONFLICT);

    uint8_t acceptance[20] = {0};
    uint8_t datagram[WH_HEADER_SIZE + sizeof(acceptance)];
    const uint8_t response_attempt[16] = {1};
    wh_write_be(acceptance, 1, 8);
    wh_write_be(acceptance + 8, 2, 8);
    acceptance[16] = WH_NATIVE_ACCEPTANCE_REJECTED;
    acceptance[17] = reason;
    size_t encoded = wh_wire_encode(datagram, sizeof(datagram), WH_UPDATE_ACCEPTANCE,
                                    response_attempt, 1, 7, 9000,
                                    acceptance, sizeof(acceptance));
    assert(encoded == sizeof(datagram) && wh_wire_valid(datagram, encoded));
    assert(wh_read_be(datagram + WH_HEADER_SIZE, 8) == 1);

    const uint8_t other_attempt[16] = {2};
    assert(wh_native_update_reject(&state, other_attempt, 6, 2, 3, 1, 1, 2, 7, &reason)
           == WH_NATIVE_UPDATE_NEW_REJECTION);
    assert(reason == WH_NATIVE_REASON_WRONG_BOOT);
    const uint8_t third_attempt[16] = {3};
    assert(wh_native_update_reject(&state, third_attempt, 7, 3, 4, 3, 1, 2, 7, &reason)
           == WH_NATIVE_UPDATE_NEW_REJECTION);
    assert(reason == WH_NATIVE_REASON_SCENARIO_MISMATCH);
    const uint8_t repeated_sequence_attempt[16] = {4};
    assert(wh_native_update_reject(&state, repeated_sequence_attempt, 7, 3, 5, 1, 1, 2, 7, &reason)
           == WH_NATIVE_UPDATE_NEW_REJECTION);
    assert(reason == WH_NATIVE_REASON_BUSY);
    const uint8_t regressed_sequence_attempt[16] = {6};
    assert(wh_native_update_reject(&state, regressed_sequence_attempt, 7, 2, 6, 1, 1, 2, 7, &reason)
           == WH_NATIVE_UPDATE_NEW_REJECTION);
    assert(reason == WH_NATIVE_REASON_BUSY);
    assert(state.update_record_count == 5);
    assert(state.update_records[3].request_sequence == 3);
    assert(state.update_records[4].request_sequence == 2);
    assert(wh_native_update_reject(&state, attempt, 7, 1, 2, 1, 1, 2, 7, &reason)
           == WH_NATIVE_UPDATE_REPLAY_REJECTION);
    assert(reason == WH_NATIVE_REASON_BUSY);
    const uint8_t fresh_attempt[16] = {5};
    assert(wh_native_update_reject(&state, fresh_attempt, 7, 4, 6, 1, 1, 2, 7, &reason)
           == WH_NATIVE_UPDATE_NEW_REJECTION);
    assert(reason == WH_NATIVE_REASON_BUSY);
}

static void test_update_replay_capacity(void)
{
    wh_native_services_state_t state;
    uint8_t reason = 99;
    uint8_t attempts[WH_NATIVE_UPDATE_REPLAY_CAPACITY][16] = {{0}};
    assert(wh_native_services_state_init(&state, 1));
    for (size_t i = 0; i < WH_NATIVE_UPDATE_REPLAY_CAPACITY; ++i) {
        attempts[i][0] = (uint8_t)(i + 1);
        assert(wh_native_update_reject(&state, attempts[i], 7, i + 1, i + 2,
                                       1, 1, 2, 7, &reason)
               == WH_NATIVE_UPDATE_NEW_REJECTION);
        assert(reason == WH_NATIVE_REASON_BUSY);
    }
    assert(state.update_record_count == WH_NATIVE_UPDATE_REPLAY_CAPACITY);
    wh_native_update_record_t saved[WH_NATIVE_UPDATE_REPLAY_CAPACITY];
    memcpy(saved, state.update_records, sizeof(saved));

    assert(wh_native_update_reject(&state, attempts[0], 7, 1, 2, 1, 1, 2, 7, &reason)
           == WH_NATIVE_UPDATE_REPLAY_REJECTION);
    assert(reason == WH_NATIVE_REASON_BUSY);
    assert(wh_native_update_reject(&state, attempts[0], 7, 17, 2, 1, 1, 2, 7, &reason)
           == WH_NATIVE_UPDATE_ATTEMPT_CONFLICT);
    assert(reason == WH_NATIVE_REASON_ATTEMPT_CONFLICT);

    const uint8_t unknown_attempt[16] = {99};
    reason = 99;
    assert(wh_native_update_reject(&state, unknown_attempt, 7,
                                    WH_NATIVE_UPDATE_REPLAY_CAPACITY + 1, 99,
                                    1, 1, 2, 7, &reason)
           == WH_NATIVE_UPDATE_CAPACITY_FULL);
    assert(reason == WH_NATIVE_REASON_NONE);
    /* No record is added and no admitted UUID can be evicted. */
    assert(state.update_record_count == WH_NATIVE_UPDATE_REPLAY_CAPACITY);
    assert(memcmp(saved, state.update_records, sizeof(saved)) == 0);
    /* Unknown UUIDs are never mislabeled as ATTEMPT_CONFLICT. */
    assert(wh_native_update_reject(&state, unknown_attempt, 7,
                                    WH_NATIVE_UPDATE_REPLAY_CAPACITY + 2, 100,
                                    1, 1, 2, 7, &reason)
           == WH_NATIVE_UPDATE_CAPACITY_FULL);
    assert(reason == WH_NATIVE_REASON_NONE);
    assert(memcmp(saved, state.update_records, sizeof(saved)) == 0);
}

typedef struct {
    wh_native_services_state_t *state;
    pthread_mutex_t *lock;
    pthread_barrier_t *barrier;
    gpio_fixture_t *gpio;
    wh_native_control_disposition_t disposition;
    wh_native_control_result_t result;
} worker_t;

static void *duplicate_worker(void *argument)
{
    worker_t *worker = argument;
    (void)pthread_barrier_wait(worker->barrier);
    assert(pthread_mutex_lock(worker->lock) == 0);
    worker->disposition = wh_native_control_request(worker->state, 42, 32000,
                                                    fake_gpio, worker->gpio,
                                                    &worker->result);
    assert(pthread_mutex_unlock(worker->lock) == 0);
    return NULL;
}

static void test_concurrent_duplicate_invariant(void)
{
    enum { THREADS = 16 };
    wh_native_services_state_t state;
    gpio_fixture_t gpio = {0};
    pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
    pthread_barrier_t barrier;
    pthread_t threads[THREADS];
    worker_t workers[THREADS] = {0};
    assert(wh_native_services_state_init(&state, 1));
    assert(pthread_barrier_init(&barrier, NULL, THREADS) == 0);
    for (size_t i = 0; i < THREADS; ++i) {
        workers[i] = (worker_t){.state = &state, .lock = &lock,
                                .barrier = &barrier, .gpio = &gpio};
        assert(pthread_create(&threads[i], NULL, duplicate_worker, &workers[i]) == 0);
    }
    unsigned new_count = 0, replay_count = 0;
    for (size_t i = 0; i < THREADS; ++i) {
        assert(pthread_join(threads[i], NULL) == 0);
        if (workers[i].disposition == WH_NATIVE_CONTROL_NEW) new_count++;
        else if (workers[i].disposition == WH_NATIVE_CONTROL_REPLAY) replay_count++;
        else assert(false);
        assert(workers[i].result.requested_gpio == 1);
        assert(workers[i].result.observed_gpio == 1);
        assert(workers[i].result.status == WH_NATIVE_CONTROL_APPLIED);
    }
    assert(new_count == 1 && replay_count == THREADS - 1);
    assert(gpio.calls == 1);
    assert(pthread_barrier_destroy(&barrier) == 0);
    assert(pthread_mutex_destroy(&lock) == 0);
}

int main(int argc, char **argv)
{
    assert(argc == 2);
    if (!strcmp(argv[1], "hysteresis")) test_hysteresis();
    else if (!strcmp(argv[1], "dedup")) test_control_dedup();
    else if (!strcmp(argv[1], "errors")) test_control_errors_and_codec();
    else if (!strcmp(argv[1], "telemetry")) test_telemetry_body();
    else if (!strcmp(argv[1], "update")) test_update_rejection();
    else if (!strcmp(argv[1], "update-capacity")) test_update_replay_capacity();
    else if (!strcmp(argv[1], "concurrent")) test_concurrent_duplicate_invariant();
    else return 2;
    puts(argv[1]);
    return 0;
}
'''


class NativeServicesHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
        if compiler is None:
            raise unittest.SkipTest("A host C compiler is required for native service tests")
        cls.temporary = tempfile.TemporaryDirectory(prefix="wasmhome-native-services-")
        folder = pathlib.Path(cls.temporary.name)
        harness = folder / "native_services_host.c"
        executable = folder / "native_services_host"
        harness.write_text(HARNESS, encoding="utf-8")
        command = [
            compiler, "-std=c11", "-D_POSIX_C_SOURCE=200809L",
            "-DWASMHOME_NATIVE_SERVICES_HOST=1", "-Wall", "-Wextra", "-Werror",
            "-pedantic", "-pthread", f"-I{ROOT / 'main'}", str(harness),
            str(ROOT / "main/native_services.c"), str(ROOT / "main/observation_wire.c"),
            "-o", str(executable),
        ]
        build = subprocess.run(command, capture_output=True, text=True)
        if build.returncode != 0:
            cls.temporary.cleanup()
            raise AssertionError(build.stdout + build.stderr)
        cls.executable = executable

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_case(self, name):
        result = subprocess.run([str(self.executable), name], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), name)

    def test_v1_v2_hysteresis_edges_and_distinguishing_vector(self):
        self.run_case("hysteresis")

    def test_control_duplicate_replay_conflict_and_stale_suppression(self):
        self.run_case("dedup")

    def test_gpio_execution_errors_and_udp_v1_codec(self):
        self.run_case("errors")

    def test_telemetry_sample_age_status_saturation_and_wire_validation(self):
        self.run_case("telemetry")

    def test_update_requests_are_rejected_without_side_effects_and_deduplicated(self):
        self.run_case("update")

    def test_update_replay_pool_saturates_without_evicting_or_false_conflicts(self):
        self.run_case("update-capacity")

    def test_concurrent_duplicate_requests_execute_gpio_once(self):
        self.run_case("concurrent")


if __name__ == "__main__":
    unittest.main()
