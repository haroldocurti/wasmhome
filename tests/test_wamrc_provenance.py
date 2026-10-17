import copy
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from evidence_json import read
from wamrc_provenance import RECORD, assert_manifest_provenance, load_record


class WamrcProvenanceTests(unittest.TestCase):
    def test_reviewable_receipt_binds_runtime_and_compiler(self):
        record, digest = load_record(ROOT)
        receipt = record["receipt"]
        self.assertEqual(receipt["runtime_identity"]["version"], "2.4.5-wasmhome.3")
        self.assertEqual(receipt["binary"]["version"], "wamrc 2.4.5")
        self.assertEqual(receipt["binary"]["sha256"],
                         "98ab5acb7bc451cc5bf9d28940d4e252e8c00c7dbc5b0fe6d097901818fe7824")
        self.assertEqual(len(receipt["raw_file_sha256"]), 64)
        self.assertEqual(len(receipt["canonical_json_sha256"]), 64)
        self.assertEqual(len(digest), 64)
        serialized = (ROOT / RECORD).read_text(encoding="utf-8")
        self.assertNotIn("/home/haroldocurti", serialized)
        self.assertNotIn("/tmp/wasmhome", serialized)
        self.assertIn("${TOOLCHAIN_ROOT}", serialized)
        self.assertIn("${WASMHOME_ROOT}", serialized)

    def test_manifest_binding_checks_runtime_receipt_and_record_digest(self):
        record, digest = load_record(ROOT)
        receipt = record["receipt"]
        manifest = {
            "runtime_identity": receipt["runtime_identity"],
            "wamrc_build_receipt_sha256": receipt["canonical_json_sha256"],
            "wamrc_receipt_input_file_sha256": receipt["raw_file_sha256"],
            "wamrc_provenance_record_sha256": digest,
            "toolchain": {
                "wamrc_sha256": receipt["binary"]["sha256"],
                "wamrc_version": receipt["binary"]["version"],
            },
        }
        self.assertEqual(assert_manifest_provenance(manifest, ROOT)[1], digest)
        for key in ("runtime_identity", "wamrc_build_receipt_sha256",
                    "wamrc_receipt_input_file_sha256", "wamrc_provenance_record_sha256"):
            bad = copy.deepcopy(manifest)
            bad[key] = "0" * 64
            with self.subTest(key=key), self.assertRaises(ValueError):
                assert_manifest_provenance(bad, ROOT)

    def test_committed_record_is_json_with_explicit_unsigned_boundary(self):
        path = ROOT / RECORD
        parsed = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(parsed["schema"], "wasmhome-issue22-wamrc-build-provenance-v1")
        self.assertIn("Unsigned local build receipt", parsed["trust_boundary"])
        self.assertEqual(read(path), parsed)


if __name__ == "__main__":
    unittest.main()
