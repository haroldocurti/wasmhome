import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from tools.generate_resilience_fixtures import generate
from tools.signed_artifacts import sign_set, verify_envelope


@unittest.skipUnless(shutil.which("openssl"), "OpenSSL CLI is required")
class ResilienceFixtureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.trusted_private = self.make_key("trusted")
        self.trusted_public = self.make_public("trusted", self.trusted_private)
        self.untrusted_private = self.make_key("untrusted")
        self.untrusted_public = self.make_public("untrusted", self.untrusted_private)
        self.package = self.make_package()

    def tearDown(self):
        self.temporary.cleanup()

    def make_key(self, name):
        path = self.root / f"{name}.key"
        subprocess.run([
            "openssl", "genpkey", "-algorithm", "EC", "-pkeyopt",
            "ec_paramgen_curve:prime256v1", "-out", str(path),
        ], check=True, capture_output=True)
        path.chmod(0o600)
        return path

    def make_public(self, name, private):
        path = self.root / f"{name}.pem"
        with path.open("wb") as output:
            subprocess.run(["openssl", "pkey", "-in", str(private), "-pubout"],
                           check=True, stdout=output, stderr=subprocess.PIPE)
        return path

    def make_package(self):
        artifacts = []
        for scenario, magic in (
            (1, b"\xe9"),
            (2, b"\x00asm\x01\x00\x00\x00"),
            (3, b"\x00aot"),
        ):
            for label, sequence in (("v1", 1), ("v2", 2)):
                name = f"payload-{scenario}-{label}.bin"
                payload = magic + f"scenario-{scenario}-{label}".encode()
                (self.root / name).write_bytes(payload)
                artifacts.append({"scenario": scenario, "label": label,
                                  "sequence": sequence, "payload": name})
        config = self.root / "package-input.json"
        config.write_text(json.dumps({
            "schema": "wasmhome-issue15-artifacts-v1",
            "campaign_id": "issue15-fixture",
            "aot_toolchain_id": "wamr-2.4.5-wasmhome.3",
            "artifacts": artifacts,
        }), encoding="utf-8")
        package = self.root / "package"
        sign_set(config, package, self.trusted_private)
        return package

    def test_fixture_matrix_has_well_formed_signatures_and_correct_failure_modes(self):
        output = self.root / "resilience-fixtures"
        index = generate(self.package, self.trusted_public, self.trusted_private,
                         self.untrusted_private, self.untrusted_public, output,
                         old_sequence=1, next_sequence=2,
                         expected_campaign_id="issue15-fixture",
                         expected_aot_toolchain_id="wamr-2.4.5-wasmhome.3")
        self.assertEqual(len(index["artifacts"]), 9)
        self.assertEqual(index["status"], "host_generated_not_device_validated")
        self.assertEqual(index["sequence_setup"]["next_sequences_by_scenario"], {
            "scenario-1": 2, "scenario-2": 3, "scenario-3": 3,
        })
        self.assertFalse(list(output.rglob("*.key")))
        for record in index["artifacts"]:
            envelope = (output / record["manifest_path"]).read_bytes()
            payload = (output / record["payload_path"]).read_bytes()
            if record["case_id"] in ("ENV-SIG", "ENV-HASH"):
                self.assertEqual(record["sequence"],
                                 2 if record["scenario"] == 1 else 3)
            if record["case_id"] == "ENV-SIG":
                verify_envelope(envelope, payload, self.untrusted_public)
                with self.assertRaisesRegex(ValueError, "signature|kid"):
                    verify_envelope(envelope, payload, self.trusted_public)
            elif record["case_id"] == "ENV-HASH":
                with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                    verify_envelope(envelope, payload, self.trusted_public)
            else:
                manifest = verify_envelope(envelope, payload, self.trusted_public)
                self.assertEqual(manifest[7], 1)

    def test_fixture_generator_refuses_existing_output(self):
        output = self.root / "existing"
        output.mkdir()
        with self.assertRaisesRegex(ValueError, "already exists"):
            generate(self.package, self.trusted_public, self.trusted_private,
                     self.untrusted_private, self.untrusted_public, output)

    def test_old_sequence_cannot_be_newer_than_requested_next(self):
        with self.assertRaisesRegex(ValueError, "must be greater"):
            generate(self.package, self.trusted_public, self.trusted_private,
                     self.untrusted_private, self.untrusted_public,
                     self.root / "bad-sequences", old_sequence=2, next_sequence=2)


if __name__ == "__main__":
    unittest.main()
