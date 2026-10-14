import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PATCH = ROOT / "runtime-port/esp-idf-aot-dualbus.patch"
HEADER_PATH = "core/shared/platform/esp-idf/espidf_dualbus.h"


class DualBusSyncTests(unittest.TestCase):
    def test_loader_syncs_literal_and_debug_code_ranges_separately(self):
        patch_text = PATCH.read_text(encoding="utf-8")
        loader_patch = patch_text.split(
            "diff --git c/core/iwasm/aot/aot_loader.c ", 1
        )[1].split("diff --git ", 1)[0]
        added_loader = "\n".join(
            line[1:]
            for line in loader_patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        self.assertIn(
            "os_sync_exec_memory(module->literal,\n"
            "                                          module->literal_size)",
            added_loader,
        )
        self.assertIn(
            "os_sync_exec_memory(module->code, module->code_size)",
            added_loader,
        )
        self.assertNotIn("literal_size + module->code_size", added_loader)

    def test_alias_ranges_alignment_overflow_order_and_failures(self):
        cc = shutil.which("cc")
        self.assertIsNotNone(cc, "a host C compiler is required for this test")

        with tempfile.TemporaryDirectory(prefix="wasmhome-dualbus-") as temporary:
            work = pathlib.Path(temporary)
            subprocess.run(["git", "init", "-q", str(work)], check=True)
            subprocess.run(
                ["git", "apply", "--include=" + HEADER_PATH, str(PATCH)],
                cwd=work,
                check=True,
            )
            source = work / "dualbus_probe.c"
            source.write_text(
                r'''#include <assert.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <limits.h>
#include "core/shared/platform/esp-idf/espidf_dualbus.h"

#define ILOW ((uintptr_t)0x42000000u)
#define IHIGH ((uintptr_t)0x44000000u)
#define DLOW ((uintptr_t)0x3c000000u)
#define DHIGH ((uintptr_t)0x3e000000u)
#define LINE ((size_t)32)
#define C2M 0x14
#define M2C 0x28

typedef struct {
    int count;
    uintptr_t addr[2];
    size_t size[2];
    int flags[2];
    int result[2];
} calls_t;

static int record_sync(void *addr, size_t size, int flags, void *context)
{
    calls_t *calls = context;
    int index = calls->count++;
    assert(index < 2);
    calls->addr[index] = (uintptr_t)addr;
    calls->size[index] = size;
    calls->flags[index] = flags;
    return calls->result[index];
}

static void test_alias_and_range_alignment(void)
{
    wh_dualbus_sync_range_t range;
    assert(wh_dualbus_sync_range(ILOW + 0x1003u, 40, ILOW, IHIGH,
                                 DLOW, DHIGH, LINE, &range));
    assert(range.ibus_start == ILOW + 0x1000u);
    assert(range.dbus_start == DLOW + 0x1000u);
    assert(range.size == 64);
    assert((range.ibus_start % LINE) == 0);
    assert((range.dbus_start % LINE) == 0);
    assert((range.size % LINE) == 0);
    assert((range.ibus_start - ILOW) == (range.dbus_start - DLOW));

    assert(wh_dualbus_sync_range(IHIGH - LINE, LINE, ILOW, IHIGH,
                                 DLOW, DHIGH, LINE, &range));
    assert(range.ibus_start == IHIGH - LINE);
    assert(range.dbus_start == DHIGH - LINE);
    assert(range.size == LINE);
}

static void test_allocation_rounding_and_rejection(void)
{
    size_t data_size = 0, allocation_size = 0;
    wh_dualbus_sync_range_t range;
    assert(wh_dualbus_allocation_size(33, LINE, &data_size,
                                      &allocation_size));
    assert(data_size == 64 && allocation_size == 96);
    assert(!wh_dualbus_allocation_size(SIZE_MAX, LINE, &data_size,
                                       &allocation_size));
    assert(!wh_dualbus_allocation_size(1, sizeof(uintptr_t) / 2,
                                       &data_size, &allocation_size));
    assert(!wh_dualbus_allocation_size(1, 3, &data_size,
                                       &allocation_size));
    assert(!wh_dualbus_sync_range(IHIGH - 1, 2, ILOW, IHIGH,
                                  DLOW, DHIGH, LINE, &range));
    assert(!wh_dualbus_sync_range(UINTPTR_MAX - 7, 16, ILOW, IHIGH,
                                  DLOW, DHIGH, LINE, &range));
    assert(!wh_dualbus_sync_range(ILOW, 0, ILOW, IHIGH,
                                  DLOW, DHIGH, LINE, &range));
}

static void test_sync_order_and_error_propagation(void)
{
    calls_t calls = {0};
    int result = wh_dualbus_sync_exec(ILOW + 5, 34, ILOW, IHIGH,
                                      DLOW, DHIGH, LINE, 0, C2M, M2C,
                                      record_sync, &calls);
    assert(result == 0 && calls.count == 2);
    assert(calls.addr[0] == DLOW && calls.flags[0] == C2M);
    assert(calls.addr[1] == ILOW && calls.flags[1] == M2C);
    assert(calls.size[0] == 64 && calls.size[1] == 64);

    calls = (calls_t){ .result = { 7, 0 } };
    result = wh_dualbus_sync_exec(ILOW, LINE, ILOW, IHIGH,
                                  DLOW, DHIGH, LINE, 0, C2M, M2C,
                                  record_sync, &calls);
    assert(result == 7 && calls.count == 1);

    calls = (calls_t){ .result = { 0, 9 } };
    result = wh_dualbus_sync_exec(ILOW, LINE, ILOW, IHIGH,
                                  DLOW, DHIGH, LINE, 0, C2M, M2C,
                                  record_sync, &calls);
    assert(result == 9 && calls.count == 2);

    calls = (calls_t){0};
    result = wh_dualbus_sync_exec(IHIGH, LINE, ILOW, IHIGH,
                                  DLOW, DHIGH, LINE, 0, C2M, M2C,
                                  record_sync, &calls);
    assert(result != 0 && calls.count == 0);
}

int main(void)
{
    test_alias_and_range_alignment();
    test_allocation_rounding_and_rejection();
    test_sync_order_and_error_propagation();
    puts("dual-bus range/cache-sync checks passed");
    return 0;
}
''',
                encoding="utf-8",
            )
            binary = work / "dualbus_probe"
            subprocess.run(
                [cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-I", str(work), str(source), "-o", str(binary)],
                check=True,
                capture_output=True,
                text=True,
            )
            result = subprocess.run([str(binary)], check=True, capture_output=True, text=True)
            self.assertEqual(result.stdout.strip(), "dual-bus range/cache-sync checks passed")


if __name__ == "__main__":
    unittest.main()
