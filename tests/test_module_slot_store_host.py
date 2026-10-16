"""Failure-injection tests for persistent signed-module slot writes."""

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
    ''',
    "esp_err.h": r'''
        #pragma once
        typedef int esp_err_t;
        #define ESP_OK 0
        #define ESP_FAIL -1
        #define ESP_ERR_INVALID_ARG 0x101
        #define ESP_ERR_INVALID_STATE 0x102
        #define ESP_ERR_NOT_FOUND 0x103
        #define ESP_ERR_INVALID_CRC 0x104
    ''',
    "esp_partition.h": r'''
        #pragma once
        #include <stddef.h>
        #include <stdint.h>
        #include "esp_err.h"
        #define ESP_PARTITION_TYPE_DATA 1
        #define ESP_PARTITION_SUBTYPE_ANY 0xff
        typedef struct {
            char label[17];
            uint32_t address;
            uint32_t size;
        } esp_partition_t;
        const esp_partition_t *esp_partition_find_first(int type, int subtype,
                                                        const char *label);
        esp_err_t esp_partition_erase_range(const esp_partition_t *partition,
                                            uint32_t offset, uint32_t size);
        esp_err_t esp_partition_write(const esp_partition_t *partition,
                                      uint32_t offset, const void *data,
                                      size_t size);
        esp_err_t esp_partition_read(const esp_partition_t *partition,
                                     uint32_t offset, void *data, size_t size);
    ''',
    "mbedtls/constant_time.h": r'''
        #pragma once
        #include <stddef.h>
        int mbedtls_ct_memcmp(const void *a, const void *b, size_t size);
    ''',
    "mbedtls/sha256.h": r'''
        #pragma once
        #include <stddef.h>
        #include <stdint.h>
        typedef struct { uint8_t state[32]; uint64_t length; } mbedtls_sha256_context;
        void mbedtls_sha256_init(mbedtls_sha256_context *context);
        void mbedtls_sha256_free(mbedtls_sha256_context *context);
        int mbedtls_sha256_starts(mbedtls_sha256_context *context, int is224);
        int mbedtls_sha256_update(mbedtls_sha256_context *context,
                                  const unsigned char *input, size_t size);
        int mbedtls_sha256_finish(mbedtls_sha256_context *context,
                                  unsigned char output[32]);
    ''',
}


HARNESS = r'''
#include "module_slot_store.h"
#include "esp_partition.h"
#include "mbedtls/constant_time.h"
#include "mbedtls/sha256.h"

#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define SLOT_SIZE 65536u
#define SLOT_COUNT 4u

static esp_partition_t partitions[SLOT_COUNT] = {
    {"interp_slot_a", 0xe20000u, SLOT_SIZE},
    {"interp_slot_b", 0xe30000u, SLOT_SIZE},
    {"aot_slot_a", 0xe40000u, SLOT_SIZE},
    {"aot_slot_b", 0xe50000u, SLOT_SIZE},
};
static uint8_t flash[SLOT_COUNT][SLOT_SIZE];
static unsigned erases[SLOT_COUNT];
static const esp_partition_t *active_partition = &partitions[3];
static bool fail_erase, fail_write, fail_read, missing_last_partition;

static size_t partition_index(const esp_partition_t *partition)
{
    assert(partition >= partitions && partition < partitions + SLOT_COUNT);
    return (size_t)(partition - partitions);
}

const esp_partition_t *esp_partition_find_first(int type, int subtype,
                                                const char *label)
{
    assert(type == ESP_PARTITION_TYPE_DATA && subtype == ESP_PARTITION_SUBTYPE_ANY);
    for (size_t i = 0; i < SLOT_COUNT; ++i) {
        if (missing_last_partition && i == SLOT_COUNT - 1) continue;
        if (strcmp(partitions[i].label, label) == 0) return &partitions[i];
    }
    return NULL;
}

esp_err_t esp_partition_erase_range(const esp_partition_t *partition,
                                    uint32_t offset, uint32_t size)
{
    size_t index = partition_index(partition);
    assert(offset % 4096u == 0 && size % 4096u == 0);
    assert(partition->size == SLOT_SIZE && offset + size <= SLOT_SIZE);
    if (partition == active_partition) return ESP_ERR_INVALID_STATE;
    erases[index]++;
    if (fail_erase) return ESP_FAIL;
    memset(flash[index] + offset, 0xff, size);
    return ESP_OK;
}

esp_err_t esp_partition_write(const esp_partition_t *partition, uint32_t offset,
                              const void *data, size_t size)
{
    size_t index = partition_index(partition);
    if (partition == active_partition) return ESP_ERR_INVALID_STATE;
    if (fail_write) return ESP_FAIL;
    if (offset > SLOT_SIZE || size > SLOT_SIZE - offset) return ESP_ERR_INVALID_ARG;
    const uint8_t *source = data;
    for (size_t i = 0; i < size; ++i) {
        if ((flash[index][offset + i] & source[i]) != source[i])
            return ESP_ERR_INVALID_STATE;
    }
    for (size_t i = 0; i < size; ++i) flash[index][offset + i] &= source[i];
    return ESP_OK;
}

esp_err_t esp_partition_read(const esp_partition_t *partition, uint32_t offset,
                             void *data, size_t size)
{
    size_t index = partition_index(partition);
    if (fail_read) return ESP_FAIL;
    if (offset > SLOT_SIZE || size > SLOT_SIZE - offset) return ESP_ERR_INVALID_ARG;
    memcpy(data, flash[index] + offset, size);
    return ESP_OK;
}

void mbedtls_sha256_init(mbedtls_sha256_context *context) { memset(context, 0, sizeof(*context)); }
void mbedtls_sha256_free(mbedtls_sha256_context *context) { (void)context; }
int mbedtls_sha256_starts(mbedtls_sha256_context *context, int is224)
{
    (void)is224;
    for (size_t i = 0; i < sizeof(context->state); ++i)
        context->state[i] = (uint8_t)(i * 17u + 3u);
    context->length = 0;
    return 0;
}
int mbedtls_sha256_update(mbedtls_sha256_context *context,
                          const unsigned char *input, size_t size)
{
    for (size_t i = 0; i < size; ++i) {
        size_t index = (size_t)(context->length++ % sizeof(context->state));
        context->state[index] = (uint8_t)((context->state[index] * 33u) ^ input[i]);
    }
    return 0;
}
int mbedtls_sha256_finish(mbedtls_sha256_context *context, unsigned char output[32])
{
    memcpy(output, context->state, sizeof(context->state));
    return 0;
}
int mbedtls_ct_memcmp(const void *a, const void *b, size_t size)
{
    const uint8_t *left = a, *right = b;
    uint8_t different = 0;
    for (size_t i = 0; i < size; ++i) different |= left[i] ^ right[i];
    return different;
}

static void sha(const uint8_t *bytes, size_t size, uint8_t output[32])
{
    mbedtls_sha256_context context;
    mbedtls_sha256_init(&context);
    assert(mbedtls_sha256_starts(&context, 0) == 0);
    assert(mbedtls_sha256_update(&context, bytes, size) == 0);
    assert(mbedtls_sha256_finish(&context, output) == 0);
    mbedtls_sha256_free(&context);
}

int main(void)
{
    for (size_t i = 0; i < SLOT_COUNT; ++i) memset(flash[i], 0xff, SLOT_SIZE);
    assert(wh_module_slot_store_init() == ESP_OK);
    missing_last_partition = true;
    assert(wh_module_slot_store_init() == ESP_ERR_NOT_FOUND);
    missing_last_partition = false;
    assert(wh_module_slot_store_init() == ESP_OK);

    uint8_t payload[7001], digest[32], copy[7001];
    for (size_t i = 0; i < sizeof(payload); ++i) payload[i] = (uint8_t)(i * 13u + 9u);
    sha(payload, sizeof(payload), digest);
    assert(wh_module_slot_store_begin(3, WH_MODULE_SLOT_ID_A,
                                     sizeof(payload)) == ESP_OK);
    assert(erases[2] == 0 && erases[3] == 0); /* begin cannot erase before pending slot commit */
    assert(wh_module_slot_store_write(payload, 3000) == ESP_OK);
    assert(erases[2] == 1 && erases[3] == 0);
    assert(wh_module_slot_store_write(payload + 3000,
                                      sizeof(payload) - 3000) == ESP_OK);
    assert(wh_module_slot_store_finish(3, WH_MODULE_SLOT_ID_A,
                                       sizeof(payload), digest) == ESP_OK);
    assert(wh_module_slot_store_read(3, WH_MODULE_SLOT_ID_A,
                                     sizeof(copy), copy, sizeof(copy)) == ESP_OK);
    assert(memcmp(payload, copy, sizeof(payload)) == 0);
    assert(erases[3] == 0); /* never erase the selected/active slot */

    active_partition = &partitions[2];
    fail_erase = true;
    assert(wh_module_slot_store_begin(3, WH_MODULE_SLOT_ID_B,
                                     sizeof(payload)) == ESP_OK);
    assert(wh_module_slot_store_write(payload, sizeof(payload)) == ESP_FAIL);
    wh_module_slot_store_abort();
    fail_erase = false;
    assert(erases[3] == 1 && erases[2] == 1);

    fail_write = true;
    assert(wh_module_slot_store_begin(3, WH_MODULE_SLOT_ID_B,
                                     sizeof(payload)) == ESP_OK);
    assert(wh_module_slot_store_write(payload, sizeof(payload)) == ESP_FAIL);
    wh_module_slot_store_abort();
    fail_write = false;

    assert(wh_module_slot_store_begin(3, WH_MODULE_SLOT_ID_B,
                                     sizeof(payload)) == ESP_OK);
    assert(wh_module_slot_store_write(payload, sizeof(payload)) == ESP_OK);
    fail_read = true;
    assert(wh_module_slot_store_finish(3, WH_MODULE_SLOT_ID_B,
                                       sizeof(payload), digest) == ESP_ERR_INVALID_CRC);
    wh_module_slot_store_abort();
    fail_read = false;

    assert(wh_module_slot_store_begin(3, WH_MODULE_SLOT_ID_B,
                                     sizeof(payload)) == ESP_OK);
    assert(wh_module_slot_store_write(payload, sizeof(payload)) == ESP_OK);
    digest[0] ^= 0xff;
    assert(wh_module_slot_store_finish(3, WH_MODULE_SLOT_ID_B,
                                       sizeof(payload), digest) == ESP_ERR_INVALID_CRC);
    digest[0] ^= 0xff;
    wh_module_slot_store_abort();
    assert(erases[2] == 1); /* failed writes and readback never erase active A */

    assert(wh_module_slot_store_begin(3, WH_MODULE_SLOT_ID_A,
                                     SLOT_SIZE + 1u) == ESP_ERR_INVALID_ARG);
    puts("module slot store failure-injection harness: PASS");
    return 0;
}
'''


class ModuleSlotStoreHostTests(unittest.TestCase):
    def test_inactive_slot_storage_failure_boundaries(self):
        compiler = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
        if compiler is None:
            self.skipTest("A host C compiler is required for the slot-store shim test")
        with tempfile.TemporaryDirectory(prefix="wasmhome-module-slot-store-") as temporary:
            folder = pathlib.Path(temporary)
            stub_root = folder / "stubs"
            for relative, contents in STUBS.items():
                target = stub_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(textwrap.dedent(contents).lstrip(), encoding="utf-8")
            harness = folder / "module_slot_store_harness.c"
            executable = folder / "module_slot_store_harness"
            harness.write_text(HARNESS, encoding="utf-8")
            result = subprocess.run(
                [
                    compiler,
                    "-std=c11",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-pedantic",
                    f"-I{stub_root}",
                    f"-I{ROOT / 'main'}",
                    str(harness),
                    str(ROOT / "main/module_slot_store.c"),
                    "-o",
                    str(executable),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
