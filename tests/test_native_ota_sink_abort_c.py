"""Exercise the real native OTA abort/invalidation callback with host shims."""

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
#include <string.h>

typedef int esp_err_t;
typedef uint32_t esp_ota_handle_t;
#define ESP_OK 0
#define ESP_FAIL 1
#define ESP_ERR_INVALID_ARG 2
#define ESP_ERR_INVALID_STATE 3
#define ESP_ERR_IO 4
#define NATIVE_OTA_ERASE_SIZE 4096u
#define NATIVE_OTA_HASH_CHUNK_BYTES 1024u

typedef struct {
    uint32_t size;
    uint8_t *bytes;
} esp_partition_t;
typedef struct {
    const esp_partition_t *partition;
    esp_ota_handle_t handle;
    uint32_t expected_size;
    uint32_t written;
    bool begun;
    bool invalidated;
} native_ota_sink_context_t;

static uint8_t active_bytes[8192];
static uint8_t inactive_bytes[8192];
static esp_partition_t active_partition = { sizeof(active_bytes), active_bytes };
static esp_partition_t inactive_partition = { sizeof(inactive_bytes), inactive_bytes };
static bool fail_erase;
static bool fail_read;
static esp_err_t ota_abort_result;
static unsigned erase_calls;

static const esp_partition_t *esp_ota_get_running_partition(void)
{
    return &active_partition;
}
static esp_err_t esp_ota_abort(esp_ota_handle_t handle)
{
    (void)handle;
    return ota_abort_result;
}
static esp_err_t esp_partition_erase_range(const esp_partition_t *partition,
                                            uint32_t offset, uint32_t size)
{
    ++erase_calls;
    assert(partition == &inactive_partition);
    assert(offset == 0 && size == partition->size);
    if (fail_erase) return ESP_FAIL;
    memset(partition->bytes, 0xff, size);
    return ESP_OK;
}
static esp_err_t esp_partition_read(const esp_partition_t *partition,
                                     uint32_t offset, void *out, size_t size)
{
    if (fail_read) return ESP_FAIL;
    if (offset > partition->size || size > partition->size - offset)
        return ESP_ERR_INVALID_ARG;
    memcpy(out, partition->bytes + offset, size);
    return ESP_OK;
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


MAIN = r'''
int main(void)
{
    memset(active_bytes, 0xa5, sizeof(active_bytes));
    memset(inactive_bytes, 0x3c, sizeof(inactive_bytes));
    uint8_t active_before[sizeof(active_bytes)];
    memcpy(active_before, active_bytes, sizeof(active_before));

    native_ota_sink_context_t context = {
        .partition = &inactive_partition, .handle = 9, .written = 17,
        .begun = true,
    };
    assert(sink_abort(&context) == ESP_OK);
    assert(context.invalidated && !context.begun && erase_calls == 1);
    for (size_t i = 0; i < sizeof(inactive_bytes); ++i)
        assert(inactive_bytes[i] == 0xff);
    assert(memcmp(active_bytes, active_before, sizeof(active_bytes)) == 0);

    memset(inactive_bytes, 0x3c, sizeof(inactive_bytes));
    context = (native_ota_sink_context_t){
        .partition = &inactive_partition, .written = 1,
    };
    fail_erase = true;
    assert(sink_abort(&context) == ESP_FAIL);
    assert(!context.invalidated && erase_calls == 2);
    fail_erase = false;

    context = (native_ota_sink_context_t){
        .partition = &inactive_partition, .written = 1,
    };
    fail_read = true;
    assert(sink_abort(&context) == ESP_FAIL);
    assert(!context.invalidated && erase_calls == 3);
    fail_read = false;

    context = (native_ota_sink_context_t){
        .partition = &active_partition, .written = 1,
    };
    assert(sink_abort(&context) == ESP_ERR_INVALID_STATE);
    assert(erase_calls == 3);

    context = (native_ota_sink_context_t){
        .partition = &inactive_partition, .handle = 10, .written = 1,
        .begun = true,
    };
    ota_abort_result = ESP_ERR_IO;
    assert(sink_abort(&context) == ESP_ERR_IO);
    assert(context.invalidated && erase_calls == 4);
    assert(memcmp(active_bytes, active_before, sizeof(active_bytes)) == 0);
    return 0;
}
'''


class NativeOtaSinkAbortHostTests(unittest.TestCase):
    def test_inactive_slot_is_erased_read_back_and_errors_propagate(self):
        compiler = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
        if compiler is None:
            self.skipTest("A host C compiler is required for the OTA sink shim test")
        source = (ROOT / "main/native_ota.c").read_text(encoding="utf-8")
        function = extract_function(source, "static esp_err_t sink_abort(")
        harness = "\n".join((PRELUDE, function, MAIN))
        with tempfile.TemporaryDirectory(prefix="wasmhome-native-ota-abort-") as temp_name:
            temp = pathlib.Path(temp_name)
            source_path = temp / "sink_abort.c"
            executable = temp / "sink_abort"
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
