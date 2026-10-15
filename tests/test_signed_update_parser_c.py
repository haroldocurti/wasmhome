"""Compile the firmware envelope parser with host stubs for early rejects."""

import pathlib
import shutil
import subprocess
import tempfile
import textwrap
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


PRELUDE = r'''
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#define WH_SIGNED_UPDATE_MAX_ENVELOPE_BYTES 4096
typedef enum {
    WH_SIGNED_UPDATE_FAILURE_NONE = 0,
    WH_SIGNED_UPDATE_FAILURE_ENVELOPE = 1,
    WH_SIGNED_UPDATE_FAILURE_SIGNATURE = 2,
} wh_signed_update_failure_t;
typedef struct {
    uint8_t key_id[8];
    uint8_t envelope_sha256[32];
    uint8_t manifest_sha256[32];
} wh_signed_update_manifest_t;
typedef struct {
    const uint8_t *protected_bytes;
    size_t protected_size;
    const uint8_t *manifest_bytes;
    size_t manifest_size;
    const uint8_t *signature;
    wh_signed_update_manifest_t manifest;
    uint8_t envelope_sha256[32];
} parsed_envelope_t;
typedef struct {
    const uint8_t *data;
    size_t size;
    size_t offset;
} cbor_cursor_t;

static bool cbor_read_head(cbor_cursor_t *, uint8_t, uint64_t *);
static bool cbor_read_bytes(cbor_cursor_t *, uint8_t, const uint8_t **, size_t *);
static bool cbor_read_manifest(const uint8_t *, size_t,
                               wh_signed_update_manifest_t *);
static bool hash_sha256(const uint8_t *, size_t, uint8_t[32]);
static bool verify_cose_signature(const parsed_envelope_t *);
'''

STUBS = r'''
static bool cbor_read_head(cbor_cursor_t *cursor, uint8_t major, uint64_t *value)
{
    (void)cursor; (void)major; (void)value; return false;
}
static bool cbor_read_bytes(cbor_cursor_t *cursor, uint8_t major,
                            const uint8_t **bytes, size_t *size)
{
    (void)cursor; (void)major; (void)bytes; (void)size; return false;
}
static bool cbor_read_manifest(const uint8_t *data, size_t size,
                               wh_signed_update_manifest_t *manifest)
{
    (void)data; (void)size; (void)manifest; return false;
}
static bool hash_sha256(const uint8_t *data, size_t size, uint8_t digest[32])
{
    (void)data; (void)size; (void)digest; return false;
}
static bool verify_cose_signature(const parsed_envelope_t *parsed)
{
    (void)parsed; return false;
}
'''

MAIN = r'''
#include <assert.h>

int main(void)
{
    const uint8_t bad_tag[] = {0xd3, 0x84, 0x42, 0x00};
    parsed_envelope_t parsed;
    memset(&parsed, 0xa5, sizeof(parsed));
    wh_signed_update_failure_t failure = WH_SIGNED_UPDATE_FAILURE_NONE;
    assert(!parse_envelope(bad_tag, sizeof(bad_tag), &parsed, &failure));
    assert(failure == WH_SIGNED_UPDATE_FAILURE_ENVELOPE);
    const parsed_envelope_t zero = {0};
    assert(memcmp(&parsed, &zero, sizeof(parsed)) == 0);
    return 0;
}
'''


def extract_function(source, signature):
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


class SignedUpdateParserHostTests(unittest.TestCase):
    def test_bad_tag_clears_parser_output_before_rejecting(self):
        compiler = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
        if compiler is None:
            self.skipTest("A host C compiler is required for the parser shim test")

        source = (ROOT / "main/signed_update.c").read_text(encoding="utf-8")
        parser = extract_function(source, "static bool parse_envelope(")
        harness = "\n".join((PRELUDE, parser, STUBS, MAIN))
        with tempfile.TemporaryDirectory(prefix="wasmhome-signed-parser-") as temp_name:
            temp = pathlib.Path(temp_name)
            source_path = temp / "parser_harness.c"
            executable = temp / "parser_harness"
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
