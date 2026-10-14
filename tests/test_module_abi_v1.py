"""Host tests for the pure C helpers in the frozen module ABI v1."""

import pathlib
import shutil
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


HARNESS = r'''
#include "module_abi_v1.h"

#include <assert.h>
#include <limits.h>
#include <stdint.h>
#include <string.h>

static void test_module_versions(void)
{
    assert(wh_abi_module_version_valid(1));
    assert(wh_abi_module_version_valid(2));
    assert(!wh_abi_module_version_valid(0));
    assert(!wh_abi_module_version_valid(3));
    assert(!wh_abi_module_version_valid(UINT8_MAX));
}

static void test_ranges(void)
{
    assert(wh_abi_range_valid(16, 0, 16));
    assert(wh_abi_range_valid(16, 15, 1));
    assert(wh_abi_range_valid(16, 16, 0));
    assert(wh_abi_range_valid(0, 0, 0));
    assert(!wh_abi_range_valid(16, 16, 1));
    assert(!wh_abi_range_valid(16, 17, 0));
    assert(!wh_abi_range_valid(UINT32_MAX, UINT32_MAX, 1));
    assert(!wh_abi_range_valid(UINT32_MAX, UINT32_MAX - 3, 4));
}

static void test_snapshot_encoding(void)
{
    const wh_abi_snapshot_v1_t sample = {
        .sample_id = UINT64_C(0x8877665544332211),
        .temperature_mC = -55000,
        .age_ms = UINT16_C(0x1234),
        .sensor_status = 4,
    };
    const uint8_t expected[WH_ABI_V1_SNAPSHOT_SIZE] = {
        0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88,
        0x28, 0x29, 0xff, 0xff, 0x34, 0x12, 0x04, 0x00,
    };
    uint8_t encoded[WH_ABI_V1_SNAPSHOT_SIZE];

    memset(encoded, 0xa5, sizeof(encoded));
    assert(wh_abi_snapshot_encode(encoded, &sample));
    assert(memcmp(encoded, expected, sizeof(expected)) == 0);

    const wh_abi_snapshot_v1_t max_temp = {
        .sample_id = UINT64_MAX,
        .temperature_mC = 125000,
        .age_ms = UINT16_MAX,
        .sensor_status = 0,
    };
    assert(wh_abi_snapshot_encode(encoded, &max_temp));
    assert(encoded[0] == 0xff && encoded[7] == 0xff);
    assert(encoded[8] == (uint8_t)125000);
    assert(encoded[9] == (uint8_t)(125000u >> 8));
    assert(encoded[10] == (uint8_t)(125000u >> 16));
    assert(encoded[11] == 0);
    assert(encoded[12] == 0xff && encoded[13] == 0xff);
    assert(encoded[14] == 0 && encoded[15] == 0);

    const wh_abi_snapshot_v1_t bad_status = {
        .sample_id = 1,
        .temperature_mC = 0,
        .age_ms = 0,
        .sensor_status = 5,
    };
    assert(!wh_abi_snapshot_encode(encoded, &bad_status));
    assert(!wh_abi_snapshot_encode(NULL, &sample));
    assert(!wh_abi_snapshot_encode(encoded, NULL));
}

int main(void)
{
    test_module_versions();
    test_ranges();
    test_snapshot_encoding();
    return 0;
}
'''


class ModuleAbiV1HostTests(unittest.TestCase):
    def test_pure_abi_helpers(self):
        compiler = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
        if compiler is None:
            self.skipTest("A host C compiler is required for module ABI tests")

        with tempfile.TemporaryDirectory(prefix="wasmhome-module-abi-v1-") as temporary:
            folder = pathlib.Path(temporary)
            harness = folder / "module_abi_v1_harness.c"
            executable = folder / "module_abi_v1_harness"
            harness.write_text(HARNESS, encoding="utf-8")
            build = subprocess.run(
                [
                    compiler,
                    "-std=c11",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-pedantic",
                    f"-I{ROOT / 'main'}",
                    str(harness),
                    str(ROOT / "main/module_abi_v1.c"),
                    "-o",
                    str(executable),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)

            result = subprocess.run([str(executable)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
