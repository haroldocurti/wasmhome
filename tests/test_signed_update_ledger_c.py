"""Compile the firmware ledger operations with heap/NVS host shims."""

import pathlib
import shutil
import subprocess
import tempfile
import textwrap
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


PRELUDE = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

typedef int esp_err_t;
typedef void *nvs_handle_t;
typedef void *SemaphoreHandle_t;
typedef int BaseType_t;

#define ESP_OK 0
#define ESP_FAIL 0x101
#define ESP_ERR_INVALID_ARG 0x102
#define ESP_ERR_NO_MEM 0x103
#define ESP_ERR_INVALID_STATE 0x104
#define ESP_ERR_INVALID_VERSION 0x105
#define ESP_ERR_INVALID_CRC 0x106
#define ESP_ERR_NVS_NOT_FOUND 0x107
#define ESP_ERR_IO 0x108
#define MALLOC_CAP_8BIT 1
#define NVS_READWRITE 0
#define NVS_READONLY 1
#define WH_SIGNED_UPDATE_NO_ACTIVE_SLOT UINT32_MAX
#define WH_SIGNED_UPDATE_MAX_COMPONENT_BYTES 32u
#define WH_SIGNED_UPDATE_MAX_RELEASE_BYTES 32u
#define UPDATE_LEDGER_MAGIC 0x57485550u
#define UPDATE_LEDGER_VERSION 2u
#define PENDING_NONE 0u
#define PENDING_ACTIVATING 3u
#define portMAX_DELAY UINT32_MAX

typedef struct {
    uint8_t used;
    uint8_t scenario;
    uint8_t campaign_sha256[32];
    uint8_t component_sha256[32];
    uint64_t accepted_floor;
    uint32_t active_slot;
    uint64_t active_sequence;
    uint32_t active_payload_size;
    uint16_t active_target_version;
    uint16_t reserved;
    uint8_t active_payload_sha256[32];
    uint8_t active_manifest_sha256[32];
    uint8_t active_attempt_id[16];
    uint64_t active_origin_boot_id;
} update_policy_record_t;

typedef struct {
    uint32_t magic;
    uint16_t version;
    uint16_t reserved;
    uint64_t generation;
    update_policy_record_t records[8];
    uint8_t pending;
    uint8_t pending_phase;
    uint8_t pending_scenario;
    uint8_t pending_component_sha256[32];
    uint8_t pending_campaign_sha256[32];
    uint64_t pending_sequence;
    uint16_t pending_target_version;
    uint16_t pending_reserved;
    uint32_t pending_payload_size;
    uint32_t pending_slot;
    uint8_t pending_attempt_id[16];
    uint64_t origin_boot_id;
    char pending_release_version[WH_SIGNED_UPDATE_MAX_RELEASE_BYTES];
    uint8_t pending_payload_sha256[32];
    uint8_t pending_manifest_sha256[32];
    uint32_t crc32;
} update_ledger_t;

typedef struct {
    uint8_t scenario;
    char campaign_id[64];
    char component[WH_SIGNED_UPDATE_MAX_COMPONENT_BYTES];
    char release_version[WH_SIGNED_UPDATE_MAX_RELEASE_BYTES];
    uint64_t sequence;
    uint32_t payload_size;
    uint8_t payload_sha256[32];
    uint8_t manifest_sha256[32];
} wh_signed_update_manifest_t;

typedef struct {
    bool pending;
    uint8_t scenario;
    char component[WH_SIGNED_UPDATE_MAX_COMPONENT_BYTES];
    uint64_t accepted_floor;
    uint64_t active_sequence;
    uint64_t pending_sequence;
    uint32_t active_slot;
    uint32_t pending_slot;
    uint32_t active_payload_size;
    uint16_t active_target_version;
    uint16_t pending_target_version;
    uint32_t pending_payload_size;
    uint8_t pending_attempt_id[16];
    uint64_t origin_boot_id;
    char pending_release_version[WH_SIGNED_UPDATE_MAX_RELEASE_BYTES];
    uint8_t active_payload_sha256[32];
    uint8_t active_manifest_sha256[32];
    char active_release_version[WH_SIGNED_UPDATE_MAX_RELEASE_BYTES];
    uint8_t active_attempt_id[16];
    uint64_t active_origin_boot_id;
    uint8_t pending_payload_sha256[32];
    uint8_t pending_manifest_sha256[32];
    uint8_t pending_phase;
} wh_signed_update_state_t;

static SemaphoreHandle_t get_ledger_lock(void);
static BaseType_t xSemaphoreTake(SemaphoreHandle_t, uint32_t);
static BaseType_t xSemaphoreGive(SemaphoreHandle_t);
static void *heap_caps_malloc(size_t, unsigned);
static void heap_caps_free(void *);
static esp_err_t nvs_open(const char *, int, nvs_handle_t *);
static esp_err_t nvs_get_blob(nvs_handle_t, const char *, void *, size_t *);
static esp_err_t nvs_set_blob(nvs_handle_t, const char *, const void *, size_t);
static esp_err_t nvs_commit(nvs_handle_t);
static void nvs_close(nvs_handle_t);
static uint32_t ledger_crc(const update_ledger_t *);
static esp_err_t ledger_load(update_ledger_t *);
static bool namespace_hashes(const char *, uint8_t, const char *, uint8_t[32], uint8_t[32]);
static int policy_index(update_ledger_t *, uint8_t, const uint8_t[32], const uint8_t[32], bool);
static bool pending_matches(const update_ledger_t *, const wh_signed_update_manifest_t *);
static bool bytes_equal(const uint8_t *, const uint8_t *, size_t);

static update_ledger_t persisted;
static update_ledger_t staged;
static bool has_persisted;
static bool has_staged;
static bool commit_returns_error;
static bool commit_is_durable;
static int fail_next_alloc;
static esp_err_t forced_load_error;
static unsigned allocations;
static unsigned frees;
static unsigned lock_takes;
static unsigned lock_gives;
static unsigned open_handles;
static unsigned nvs_open_calls;
static unsigned nvs_set_calls;
static unsigned nvs_commit_calls;
static unsigned nvs_read_calls;
static int dummy_lock;

static uint32_t ledger_crc(const update_ledger_t *ledger)
{
    (void)ledger;
    return 0x12345678u;
}

static SemaphoreHandle_t get_ledger_lock(void) { return &dummy_lock; }
static BaseType_t xSemaphoreTake(SemaphoreHandle_t lock, uint32_t ticks)
{
    assert(lock == &dummy_lock); (void)ticks; ++lock_takes; return 1;
}
static BaseType_t xSemaphoreGive(SemaphoreHandle_t lock)
{
    assert(lock == &dummy_lock); ++lock_gives; return 1;
}
static void *heap_caps_malloc(size_t size, unsigned caps)
{
    assert(caps == MALLOC_CAP_8BIT);
    if (fail_next_alloc) { --fail_next_alloc; return NULL; }
    void *result = malloc(size);
    if (result) ++allocations;
    return result;
}
static void heap_caps_free(void *value)
{
    if (value) { ++frees; free(value); }
}
static esp_err_t nvs_open(const char *name, int mode, nvs_handle_t *handle)
{
    assert(strcmp(name, "wh_update") == 0);
    assert(mode == NVS_READWRITE || mode == NVS_READONLY);
    ++nvs_open_calls; ++open_handles; *handle = &persisted; return ESP_OK;
}
static esp_err_t nvs_get_blob(nvs_handle_t handle, const char *key, void *out, size_t *size)
{
    assert(handle == &persisted); assert(strcmp(key, "ledger") == 0); ++nvs_read_calls;
    if (!has_persisted) return ESP_ERR_NVS_NOT_FOUND;
    if (*size < sizeof(persisted)) return ESP_ERR_INVALID_ARG;
    memcpy(out, &persisted, sizeof(persisted)); *size = sizeof(persisted); return ESP_OK;
}
static esp_err_t nvs_set_blob(nvs_handle_t handle, const char *key, const void *data, size_t size)
{
    assert(handle == &persisted); assert(strcmp(key, "ledger") == 0);
    assert(size == sizeof(staged)); ++nvs_set_calls;
    memcpy(&staged, data, size); has_staged = true; return ESP_OK;
}
static esp_err_t nvs_commit(nvs_handle_t handle)
{
    assert(handle == &persisted); ++nvs_commit_calls;
    assert(has_staged);
    if (commit_is_durable) { memcpy(&persisted, &staged, sizeof(persisted)); has_persisted = true; }
    return commit_returns_error ? ESP_ERR_IO : ESP_OK;
}
static void nvs_close(nvs_handle_t handle)
{
    assert(handle == &persisted); assert(open_handles); --open_handles;
}
static esp_err_t ledger_load(update_ledger_t *ledger)
{
    if (forced_load_error != ESP_OK) return forced_load_error;
    if (has_persisted) memcpy(ledger, &persisted, sizeof(*ledger));
    else {
        memset(ledger, 0, sizeof(*ledger));
        ledger->magic = UPDATE_LEDGER_MAGIC;
        ledger->version = UPDATE_LEDGER_VERSION;
    }
    return ESP_OK;
}
static bool namespace_hashes(const char *campaign, uint8_t scenario, const char *component,
                             uint8_t campaign_hash[32], uint8_t component_hash[32])
{
    if (!campaign || !component || !scenario) return false;
    memset(campaign_hash, 0x11, 32); memset(component_hash, 0x22, 32); return true;
}
static int policy_index(update_ledger_t *ledger, uint8_t scenario,
                        const uint8_t campaign_hash[32], const uint8_t component_hash[32],
                        bool create)
{
    (void)scenario; (void)campaign_hash; (void)component_hash; (void)create;
    return ledger->records[0].used ? 0 : -1;
}
static bool pending_matches(const update_ledger_t *ledger,
                            const wh_signed_update_manifest_t *manifest)
{
    return ledger->pending && ledger->pending_sequence == manifest->sequence &&
        ledger->pending_payload_size == manifest->payload_size;
}
static bool bytes_equal(const uint8_t *a, const uint8_t *b, size_t size)
{
    return a && b && memcmp(a, b, size) == 0;
}
'''


MAIN = r'''
static void reset_counters(void)
{
    allocations = frees = lock_takes = lock_gives = 0;
    open_handles = nvs_open_calls = nvs_set_calls = nvs_commit_calls = nvs_read_calls = 0;
    fail_next_alloc = 0; forced_load_error = ESP_OK;
    commit_returns_error = false; commit_is_durable = true;
    has_staged = false;
}

static wh_signed_update_manifest_t valid_manifest(void)
{
    wh_signed_update_manifest_t m = {0};
    m.scenario = 1; strcpy(m.campaign_id, "bench-campaign");
    strcpy(m.component, "firmware"); strcpy(m.release_version, "v2");
    m.sequence = 2; m.payload_size = 4096; memset(m.payload_sha256, 0x33, 32);
    memset(m.manifest_sha256, 0x44, 32); return m;
}

static void seed_active_state(void)
{
    memset(&persisted, 0, sizeof(persisted)); has_persisted = true;
    persisted.records[0].used = 1; persisted.records[0].scenario = 1;
    memset(persisted.records[0].campaign_sha256, 0x11, 32);
    memset(persisted.records[0].component_sha256, 0x22, 32);
    persisted.records[0].accepted_floor = 7; persisted.records[0].active_sequence = 7;
    persisted.records[0].active_slot = 0x20000; persisted.records[0].active_payload_size = 2048;
    persisted.records[0].active_target_version = 2;
}

static void seed_pending_state(void)
{
    seed_active_state();
    persisted.records[0].accepted_floor = 1;
    persisted.records[0].active_sequence = 1;
    persisted.pending = 1; persisted.pending_phase = PENDING_ACTIVATING;
    persisted.pending_scenario = 1; persisted.pending_sequence = 2;
    persisted.pending_target_version = 2; persisted.pending_payload_size = 4096;
    persisted.pending_slot = 0x720000; strcpy(persisted.pending_release_version, "v2");
    memset(persisted.pending_campaign_sha256, 0x11, 32);
    memset(persisted.pending_component_sha256, 0x22, 32);
    memset(persisted.pending_payload_sha256, 0x33, 32);
    memset(persisted.pending_manifest_sha256, 0x44, 32);
}

static void test_get_state_oom_and_cleanup(void)
{
    seed_active_state(); reset_counters();
    wh_signed_update_state_t state; memset(&state, 0xa5, sizeof(state));
    fail_next_alloc = 1;
    assert(wh_signed_update_get_state(1, "firmware", "bench-campaign", &state) == ESP_ERR_NO_MEM);
    const wh_signed_update_state_t zero = {0};
    assert(memcmp(&state, &zero, sizeof(state)) == 0);
    assert(lock_takes == 1 && lock_gives == 1 && open_handles == 0);
    assert(nvs_open_calls == 0 && allocations == 0 && frees == 0);

    reset_counters(); forced_load_error = ESP_ERR_IO; memset(&state, 0xa5, sizeof(state));
    assert(wh_signed_update_get_state(1, "firmware", "bench-campaign", &state) == ESP_ERR_IO);
    assert(memcmp(&state, &zero, sizeof(state)) == 0);
    assert(lock_takes == 1 && lock_gives == 1 && allocations == 1 && frees == 1);

    reset_counters();
    assert(wh_signed_update_get_state(1, "firmware", "bench-campaign", &state) == ESP_OK);
    assert(state.accepted_floor == 7 && state.active_sequence == 7 && state.active_slot == 0x20000);
    assert(lock_takes == 1 && lock_gives == 1 && allocations == 1 && frees == 1);
}

static void test_commit_oom_and_ambiguous_readback(void)
{
    wh_signed_update_manifest_t manifest = valid_manifest();
    seed_pending_state(); reset_counters();
    update_ledger_t before = persisted;
    fail_next_alloc = 1;
    assert(wh_signed_update_commit(&manifest, persisted.pending_slot) == ESP_ERR_NO_MEM);
    assert(memcmp(&persisted, &before, sizeof(before)) == 0);
    assert(lock_takes == 1 && lock_gives == 1 && nvs_open_calls == 0 && open_handles == 0);

    seed_pending_state(); reset_counters();
    commit_returns_error = true; commit_is_durable = true;
    assert(wh_signed_update_commit(&manifest, persisted.pending_slot) == ESP_OK);
    assert(persisted.records[0].accepted_floor == 2 && persisted.records[0].active_sequence == 2);
    assert(!persisted.pending && nvs_read_calls == 1 && open_handles == 0);
    assert(lock_takes == 1 && lock_gives == 1 && allocations == frees);

    seed_pending_state(); reset_counters(); before = persisted;
    commit_returns_error = true; commit_is_durable = false;
    assert(wh_signed_update_commit(&manifest, persisted.pending_slot) == ESP_ERR_IO);
    assert(memcmp(&persisted, &before, sizeof(before)) == 0);
    assert(persisted.records[0].accepted_floor == 1 && persisted.pending);
    assert(nvs_read_calls == 1 && open_handles == 0 && allocations == frees);
    assert(lock_takes == 1 && lock_gives == 1);
}

int main(void)
{
    test_get_state_oom_and_cleanup();
    test_commit_oom_and_ambiguous_readback();
    return 0;
}
'''


def extract_function(source: str, signature: str) -> str:
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"unterminated function: {signature}")


class SignedUpdateLedgerHostTests(unittest.TestCase):
    def test_real_ledger_state_and_commit_paths_with_heap_and_nvs_shims(self):
        compiler = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
        if compiler is None:
            self.skipTest("A host C compiler is required for the ledger shim test")

        source = (ROOT / "main/signed_update.c").read_text(encoding="utf-8")
        functions = [
            extract_function(source, "static esp_err_t ledger_store("),
            extract_function(source, "esp_err_t wh_signed_update_get_state("),
            extract_function(source, "esp_err_t wh_signed_update_commit("),
        ]
        harness = "\n".join((PRELUDE, *functions, MAIN))
        with tempfile.TemporaryDirectory(prefix="wasmhome-signed-ledger-") as temp_name:
            temp = pathlib.Path(temp_name)
            source_path = temp / "ledger_harness.c"
            executable = temp / "ledger_harness"
            source_path.write_text(textwrap.dedent(harness), encoding="utf-8")
            build = subprocess.run(
                [compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-pedantic",
                 str(source_path), "-o", str(executable)],
                capture_output=True, text=True,
            )
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
