import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PATCH = ROOT / "runtime-port/esp-idf-aot-alias.patch"

BASE_HARNESS = r'''#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef int8_t int8;
typedef int16_t int16;
typedef int32_t int32;
typedef uint16_t uint16;
typedef uint32_t uint32;
#define BH_KB 1024
'''

BIG_ENDIAN_ACCESS = r'''
static uint32 test_load_be32(const void *addr)
{
    const unsigned char *p = (const unsigned char *)addr;
    return ((uint32)p[0] << 24) | ((uint32)p[1] << 16)
           | ((uint32)p[2] << 8) | (uint32)p[3];
}
static void test_store_be32(void *addr, uint32 value)
{
    unsigned char *p = (unsigned char *)addr;
    p[0] = (unsigned char)(value >> 24);
    p[1] = (unsigned char)(value >> 16);
    p[2] = (unsigned char)(value >> 8);
    p[3] = (unsigned char)value;
}
'''

CHECKS = r'''
int main(void)
{
    static const uint16 values[] = { 0x0000u, 0x1234u, 0xfff6u };
    unsigned char *storage = (unsigned char *)malloc(64u);
    unsigned char before[64];
    size_t value_index;
    int alignment;
    if (storage == NULL) return 90;
    for (value_index = 0; value_index < sizeof(values) / sizeof(values[0]); ++value_index) {
        for (alignment = 0; alignment < 4; ++alignment) {
            size_t i;
            unsigned char *field;
            uint16 bits = values[value_index];
            memset(storage, 0xa5, 64u);
            field = storage + 16u + (size_t)alignment;
            field[0] = 0xff;
            field[1] = 0xff;
#if TEST_BIG_ENDIAN
#define EXPECT_FIRST ((unsigned char)(bits >> 8))
#define EXPECT_SECOND ((unsigned char)bits)
#else
#define EXPECT_FIRST ((unsigned char)bits)
#define EXPECT_SECOND ((unsigned char)(bits >> 8))
            if (alignment == 0) {
                field[2] = 0x88;
                field[3] = 0x08;
            }
#endif
            memcpy(before, storage, 64u);
            put_imm16_to_addr((int16)bits, (int16 *)(void *)field);
            if (field[0] != EXPECT_FIRST || field[1] != EXPECT_SECOND) {
                fprintf(stderr,
                        "value=%04x alignment=%d got=%02x%02x expected=%02x%02x\n",
                        (unsigned)bits, alignment, field[0], field[1],
                        EXPECT_FIRST, EXPECT_SECOND);
                free(storage);
                return 1;
            }
            for (i = 0; i < 64u; ++i) {
                if (i != (size_t)(field - storage)
                    && i != (size_t)(field - storage) + 1u
                    && storage[i] != before[i]) {
                    fprintf(stderr, "value=%04x alignment=%d changed neighbor byte %zu\n",
                            (unsigned)bits, alignment, i);
                    free(storage);
                    return 2;
                }
            }
#if !TEST_BIG_ENDIAN
            if (alignment == 0 && bits == UINT16_C(0xfff6)) {
                uint32 word;
                memcpy(&word, field, sizeof(word));
                if (word != UINT32_C(0x0888fff6)) {
                    fprintf(stderr, "relocation fixture word mismatch %08x\n", word);
                    free(storage);
                    return 3;
                }
            }
#endif
        }
    }
    free(storage);
    puts("3 immediates x 4 alignments passed; neighbors preserved");
    return 0;
}
'''


def patch_added_lines(text):
    marker = "--- a/core/iwasm/aot/arch/aot_reloc_xtensa.c"
    start = text.index(marker)
    lines = []
    for line in text[start:].splitlines():
        if line.startswith("diff --git ") and lines:
            break
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            lines.append(line[1:])
    return "\n".join(lines)


def extract_helper(text):
    start = text.index("static void\nput_imm16_to_addr(int16 imm16, int16 *addr)\n{")
    brace = text.index("{", start)
    depth = 0
    for pos in range(brace, len(text)):
        if text[pos] == "{":
            depth += 1
        elif text[pos] == "}":
            depth -= 1
            if depth == 0:
                return text[start:pos + 1]
    raise AssertionError("unterminated put_imm16_to_addr helper in patch")


def as_big_endian_adapter(function):
    replacements = (
        ("uint32 value1 = *word1;",
         "uint32 value1 = test_load_be32((const void *)word1);"),
        ("uint32 value2 = *word2;",
         "uint32 value2 = test_load_be32((const void *)word2);"),
        ("*word1 = (value1 & ~mask) | ((uint32)bits << shift);",
         "test_store_be32((void *)word1, (value1 & ~mask) | ((uint32)bits << shift));"),
        ("*word1 = value1;", "test_store_be32((void *)word1, value1);"),
        ("*word2 = value2;", "test_store_be32((void *)word2, value2);"),
    )
    for old, new in replacements:
        if old not in function:
            raise AssertionError(f"expected dword operation absent from helper: {old}")
        function = function.replace(old, new)
    if any(line.lstrip().startswith(("*word1 =", "*word2 ="))
           for line in function.splitlines()):
        raise AssertionError("unadapted dword store in big-endian simulation")
    if any("= *word1;" in line or "= *word2;" in line
           for line in function.splitlines()):
        raise AssertionError("unadapted dword load in big-endian simulation")
    return function


class XtensaImm16AliasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cc = shutil.which("cc")
        if cls.cc is None:
            raise RuntimeError("a host C compiler is required for this regression test")
        added = patch_added_lines(PATCH.read_text(encoding="utf-8"))
        cls.helper = extract_helper(added)
        cls.big_helper = as_big_endian_adapter(cls.helper)

    def compile_and_run(self, endian, flags):
        with tempfile.TemporaryDirectory(prefix="wasmhome-xtensa-imm16-") as temp:
            work = pathlib.Path(temp)
            if endian == "big":
                mode = "#define is_little_endian() (0)\n#define TEST_BIG_ENDIAN 1\n"
                access = BIG_ENDIAN_ACCESS
                helper = self.big_helper
            else:
                mode = "#define is_little_endian() (1)\n#define TEST_BIG_ENDIAN 0\n"
                access = ""
                helper = self.helper
            source = work / "probe.c"
            source.write_text(BASE_HARNESS + mode + access + helper + CHECKS,
                              encoding="utf-8")
            binary = work / "probe"
            result = subprocess.run(
                [self.cc, *flags, "-std=c11", "-Wall", "-Wextra",
                 "-Wstrict-aliasing=2", "-Werror", str(source), "-o", str(binary)],
                text=True, capture_output=True, check=False, timeout=30,
            )
            self.assertEqual(result.returncode, 0,
                             f"compile failed: {result.stdout}{result.stderr}")
            run = subprocess.run([str(binary)], text=True, capture_output=True,
                                 check=False, timeout=10)
            self.assertEqual(run.returncode, 0, f"run failed: {run.stdout}{run.stderr}")
            self.assertEqual(run.stdout.strip(),
                             "3 immediates x 4 alignments passed; neighbors preserved")

    def test_little_endian_o2_o3_and_ubsan(self):
        for flags in (
            ["-O2", "-fstrict-aliasing"],
            ["-O3", "-O2", "-fstrict-aliasing"],
            ["-O3", "-fstrict-aliasing", "-fsanitize=undefined"],
        ):
            with self.subTest(flags=flags):
                self.compile_and_run("little", flags)

    def test_big_endian_lane_math_and_crossing_word(self):
        for flags in (
            ["-O2", "-fstrict-aliasing"],
            ["-O3", "-fstrict-aliasing"],
        ):
            with self.subTest(flags=flags):
                self.compile_and_run("big", flags)


if __name__ == "__main__":
    unittest.main()
