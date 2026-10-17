"""Verify modular fixture correlation IDs are retired after final recovery."""

import pathlib
import shutil
import subprocess
import tempfile
import textwrap
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


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


PRELUDE = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

typedef int SemaphoreHandle_t;
#define portMAX_DELAY 0xffffffffu
#define pdTRUE 1

static SemaphoreHandle_t manager_lock = 1;
static bool resilience_attempt_valid;
static uint8_t resilience_attempt_id[16];
static bool manager_public_enter(void) { return true; }
static void manager_public_leave(void) { }
static int xSemaphoreTake(SemaphoreHandle_t lock, unsigned wait)
{ (void)lock; (void)wait; return pdTRUE; }
static int xSemaphoreGive(SemaphoreHandle_t lock)
{ (void)lock; return pdTRUE; }
'''


MAIN = r'''
int main(void)
{
    const uint8_t active[16] = {1, 2, 3};
    const uint8_t other[16] = {4, 5, 6};
    memcpy(resilience_attempt_id, active, sizeof(active));
    resilience_attempt_valid = true;
    wh_module_manager_clear_resilience_attempt_id(other);
    assert(resilience_attempt_valid);
    wh_module_manager_clear_resilience_attempt_id(active);
    assert(!resilience_attempt_valid);
    const uint8_t zero[16] = {0};
    assert(memcmp(resilience_attempt_id, zero, sizeof(zero)) == 0);
    return 0;
}
'''


class ModuleResilienceAttemptRetirementTests(unittest.TestCase):
    def test_manager_clears_only_the_matching_attempt_id(self):
        compiler = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
        if compiler is None:
            self.skipTest("A host C compiler is required for the manager retirement shim test")
        source = (ROOT / "main/module_manager.c").read_text(encoding="utf-8")
        helper = extract_function(
            source, "static void\nclear_resilience_attempt_id_internal(")
        function = extract_function(
            source, "void\nwh_module_manager_clear_resilience_attempt_id(")
        harness = "\n".join((PRELUDE, helper, function, MAIN))
        with tempfile.TemporaryDirectory(prefix="wasmhome-module-attempt-retire-") as temp_name:
            temp = pathlib.Path(temp_name)
            source_path = temp / "retire.c"
            executable = temp / "retire"
            source_path.write_text(textwrap.dedent(harness), encoding="utf-8")
            build = subprocess.run(
                [compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-pedantic",
                 str(source_path), "-o", str(executable)],
                capture_output=True, text=True,
            )
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_retirement_follows_persisted_completion_event(self):
        source = (ROOT / "main/module_resilience_test.c").read_text(encoding="utf-8")
        function = extract_function(source, "emit_recovery_if_complete(void)")
        self.assertLess(function.index('emit_event_for_pending("control_recovery_completed"'),
                        function.index("retired = true"))
        self.assertLess(function.index("retired = true"),
                        function.index("wh_module_manager_clear_resilience_attempt_id"))


if __name__ == "__main__":
    unittest.main()
