import json
import tempfile
import unittest
from pathlib import Path

from tools.verify_module_allocator_abi import check_compile_commands


class ModuleAllocatorAbiTests(unittest.TestCase):
    def _commands(self, manager_flags, runtime_flags):
        return [
            {
                "file": "/src/main/module_manager.c",
                "command": "xtensa-esp32s3-elf-gcc " + manager_flags + " -c /src/main/module_manager.c",
            },
            {
                "file": "/src/components/wasm-micro-runtime/core/iwasm/common/wasm_memory.c",
                "command": "xtensa-esp32s3-elf-gcc " + runtime_flags + " -include /src/main/wamr_allocator_abi_guard.h -c wasm_memory.c",
            },
        ]

    def _write_and_check(self, commands):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "compile_commands.json"
            path.write_text(json.dumps(commands), encoding="utf-8")
            return check_compile_commands(path)

    def test_accepts_usage_abi_on_both_sides(self):
        result = self._write_and_check(
            self._commands("-DWASM_MEM_ALLOC_WITH_USAGE=1", "-DWASM_MEM_ALLOC_WITH_USAGE=1")
        )
        self.assertEqual(len(result), 2)

    def test_rejects_missing_runtime_definition(self):
        with self.assertRaisesRegex(ValueError, "runtime must have exactly one"):
            self._write_and_check(
                self._commands("-DWASM_MEM_ALLOC_WITH_USAGE=1", "-DWASM_ENABLE_BULK_MEMORY=1")
            )

    def test_rejects_runtime_without_compile_time_guard(self):
        commands = self._commands("-DWASM_MEM_ALLOC_WITH_USAGE=1", "-DWASM_MEM_ALLOC_WITH_USAGE=1")
        commands[1]["command"] = commands[1]["command"].replace(
            "-include /src/main/wamr_allocator_abi_guard.h", ""
        )
        with self.assertRaisesRegex(ValueError, "force-include"):
            self._write_and_check(commands)

    def test_rejects_conflicting_or_duplicate_definitions(self):
        for flags in (
            "-DWASM_MEM_ALLOC_WITH_USAGE=0",
            "-DWASM_MEM_ALLOC_WITH_USAGE=1 -DWASM_MEM_ALLOC_WITH_USAGE=0",
            "-DWASM_MEM_ALLOC_WITH_USAGE=1 -DWASM_MEM_ALLOC_WITH_USAGE=1",
        ):
            with self.subTest(flags=flags), self.assertRaisesRegex(ValueError, "manager must have exactly one"):
                self._write_and_check(self._commands(flags, "-DWASM_MEM_ALLOC_WITH_USAGE=1"))


if __name__ == "__main__":
    unittest.main()
