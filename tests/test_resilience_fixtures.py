import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import tools.resilience_fixture_https_server as fixture_server
from tools.generate_resilience_fixtures import generate
from tools.signed_artifacts import public_der_from_key, sign_set, verify_envelope
from tools.resilience_fixture_https_server import (
    load_allowlist, validate_fixture_port, write_server_pid_receipt,
)


@unittest.skipUnless(shutil.which("openssl"), "OpenSSL CLI is required")
class ResilienceFixtureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.trusted_private = self.make_key("trusted")
        self.trusted_public = self.make_public("trusted", self.trusted_private)
        self.fixture_private = self.make_key("fixture")
        self.fixture_public = self.make_public("fixture", self.fixture_private)
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

    def test_fixture_server_rejects_existing_campaign_ports_and_allows_smoke_port(self):
        for port in (8443, 8444):
            with self.subTest(port=port):
                with self.assertRaisesRegex(ValueError, "reserved for existing campaigns"):
                    validate_fixture_port(port)
        self.assertEqual(validate_fixture_port(18445), 18445)

    def test_fixture_server_cli_rejects_existing_campaign_ports(self):
        server_script = Path(__file__).resolve().parents[1] / "tools" / "resilience_fixture_https_server.py"
        for port in (8443, 8444):
            with self.subTest(port=port):
                result = subprocess.run([
                    sys.executable, str(server_script),
                    "--fixture-root", str(self.root / "fixtures"),
                    "--trusted-public-key", str(self.trusted_public),
                    "--untrusted-public-key", str(self.untrusted_public),
                    "--certificate", str(self.root / "server.crt"),
                    "--private-key", str(self.root / "server.key"),
                    "--log", str(self.root / "http.log"),
                    "--server-name", "wasmhome-issue22.test",
                    "--server-ip", "192.0.2.22",
                    "--campaign-id", "issues16-18-fixture",
                    "--aot-toolchain-id", "wamr-2.4.5-wasmhome.3",
                    "--port", str(port), "--check",
                ], check=False, capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)
                self.assertIn("ports 8443 and 8444 are reserved", result.stderr)

    def test_server_process_receipt_is_atomic_exclusive_and_private(self):
        directory = self.root / "private-server"
        directory.mkdir(mode=0o700)
        directory.chmod(0o700)
        receipt = directory / "server.pid"
        pid, starttime = write_server_pid_receipt(receipt)
        self.assertEqual(pid, os.getpid())
        self.assertTrue(starttime.isdigit())
        self.assertEqual(receipt.read_text(), f"{pid} {starttime}\n")
        self.assertEqual(receipt.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(directory.glob(".server.pid.*")), [])
        with self.assertRaisesRegex(ValueError, "already exists"):
            write_server_pid_receipt(receipt)

    def test_startup_interruption_after_receipt_leaves_recoverable_identity_before_bind(self):
        directory = self.root / "private-startup"
        directory.mkdir(mode=0o700)
        directory.chmod(0o700)
        receipt = directory / "server.pid"
        arguments = ["resilience_fixture_https_server.py",
            "--fixture-root", str(self.root / "fixtures"),
            "--trusted-public-key", str(self.trusted_public),
            "--untrusted-public-key", str(self.untrusted_public),
            "--certificate", str(self.root / "server.crt"),
            "--private-key", str(self.root / "server.key"),
            "--log", str(directory / "http.jsonl"),
            "--bind", "127.0.0.1", "--port", "18445",
            "--server-name", "wasmhome-issue22.test",
            "--server-ip", "192.0.2.22",
            "--campaign-id", "issues16-18-fixture",
            "--aot-toolchain-id", "wamr-2.4.5-wasmhome.3",
            "--pid-file", str(receipt)]
        with mock.patch.object(sys, "argv", arguments), \
             mock.patch.object(fixture_server, "FixtureHTTPServer",
                               side_effect=RuntimeError("simulated interruption before bind")) as bind:
            with self.assertRaisesRegex(RuntimeError, "before bind"):
                fixture_server.main()
        self.assertTrue(bind.called)
        pid, starttime = map(int, receipt.read_text().split())
        self.assertEqual(pid, os.getpid())
        self.assertGreater(starttime, 0)

    def test_fixture_matrix_has_well_formed_signatures_and_correct_failure_modes(self):
        output = self.root / "resilience-fixtures"
        index = generate(self.package, self.trusted_public, self.fixture_private,
                         self.untrusted_private, self.untrusted_public, output,
                         old_sequence=1, next_sequence=2, module_next_sequence=3,
                         expected_campaign_id="issue15-fixture",
                         expected_aot_toolchain_id="wamr-2.4.5-wasmhome.3",
                         trusted_public_key=self.fixture_public,
                         fixture_campaign_id="issues16-18-fixture")
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
                verify_envelope(envelope, payload, self.untrusted_public,
                                expected_campaign_id="issues16-18-fixture")
                with self.assertRaisesRegex(ValueError, "signature|kid"):
                    verify_envelope(envelope, payload, self.fixture_public,
                                    expected_campaign_id="issues16-18-fixture")
            elif record["case_id"] == "ENV-HASH":
                with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                    verify_envelope(envelope, payload, self.fixture_public,
                                    expected_campaign_id="issues16-18-fixture")
            else:
                manifest = verify_envelope(envelope, payload, self.fixture_public,
                                           expected_campaign_id="issues16-18-fixture")
                self.assertEqual(manifest[7], 1)

    def test_fixture_generator_refuses_existing_output(self):
        output = self.root / "existing"
        output.mkdir()
        with self.assertRaisesRegex(ValueError, "already exists"):
            generate(self.package, self.trusted_public, self.fixture_private,
                     self.untrusted_private, self.untrusted_public, output,
                     old_sequence=1, next_sequence=3, module_next_sequence=3,
                     expected_campaign_id="issue15-fixture",
                     expected_aot_toolchain_id="wamr-2.4.5-wasmhome.3",
                     trusted_public_key=self.fixture_public,
                     fixture_campaign_id="issues16-18-fixture")

    def test_old_sequence_cannot_be_newer_than_requested_next(self):
        with self.assertRaisesRegex(ValueError, "must be greater"):
            generate(self.package, self.trusted_public, self.fixture_private,
                     self.untrusted_private, self.untrusted_public,
                     self.root / "bad-sequences", old_sequence=2, next_sequence=2,
                     module_next_sequence=3,
                     expected_campaign_id="issue15-fixture",
                     expected_aot_toolchain_id="wamr-2.4.5-wasmhome.3",
                     trusted_public_key=self.fixture_public,
                     fixture_campaign_id="issues16-18-fixture")

    def test_fixture_key_and_campaign_can_be_isolated_from_source_package(self):
        output = self.root / "isolated-resilience-fixtures"
        index = generate(
            self.package, self.trusted_public, self.fixture_private,
            self.untrusted_private, self.untrusted_public, output,
            old_sequence=1, next_sequence=3, module_next_sequence=3,
            expected_campaign_id="issue15-fixture",
            expected_aot_toolchain_id="wamr-2.4.5-wasmhome.3",
            trusted_public_key=self.fixture_public,
            fixture_campaign_id="issues16-18-fixture",
        )

        self.assertEqual(index["source_campaign_id"], "issue15-fixture")
        self.assertEqual(index["campaign_id"], "issues16-18-fixture")
        self.assertNotEqual(index["campaign_id"], index["source_package_verified"]["campaign_id"])
        self.assertEqual(index["trusted_fixture_public_key_sha256"],
                         hashlib.sha256(public_der_from_key(self.fixture_public)).hexdigest())
        self.assertNotEqual(index["trusted_fixture_public_key_sha256"],
                            index["source_package_verified"]["signing_public_key_sha256"])
        self.assertEqual(index["expected_aot_toolchain_id"], "wamr-2.4.5-wasmhome.3")
        self.assertEqual((output / "verification" / "trusted-public.der").read_bytes(),
                         public_der_from_key(self.fixture_public))
        self.assertEqual((output / "verification" / "untrusted-public.der").read_bytes(),
                         public_der_from_key(self.untrusted_public))
        package_root, campaign, allowed, server_index = load_allowlist(
            output, output / "verification" / "trusted-public.der",
            output / "verification" / "untrusted-public.der",
            "issues16-18-fixture", "wamr-2.4.5-wasmhome.3")
        self.assertEqual(package_root, output.resolve())
        self.assertEqual(campaign, "issues16-18-fixture")
        self.assertEqual(len(allowed), 18)
        self.assertEqual(server_index["expected_aot_toolchain_id"],
                         "wamr-2.4.5-wasmhome.3")
        self.assertEqual(index["sequence_setup"]["next_sequences_by_scenario"], {
            "scenario-1": 3, "scenario-2": 3, "scenario-3": 3,
        })
        for record in index["artifacts"]:
            envelope = (output / record["manifest_path"]).read_bytes()
            payload = (output / record["payload_path"]).read_bytes()
            verification_payload = (output / record["verification_payload_path"]).read_bytes()
            self.assertEqual(record["payload_bytes"], len(payload))
            self.assertEqual(record["payload_sha256"], hashlib.sha256(payload).hexdigest())
            self.assertEqual(record["verification_payload_sha256"],
                             hashlib.sha256(verification_payload).hexdigest())
            if record["case_id"] == "ENV-SIG":
                verify_envelope(envelope, payload, self.untrusted_public,
                                expected_campaign_id="issues16-18-fixture")
            elif record["case_id"] == "ENV-HASH":
                with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                    verify_envelope(envelope, payload, self.fixture_public,
                                    expected_campaign_id="issues16-18-fixture")
            else:
                manifest = verify_envelope(envelope, payload, self.fixture_public,
                                           expected_campaign_id="issues16-18-fixture")
                self.assertEqual(manifest[7], 1)

    def test_fixture_identity_must_not_reuse_source_campaign_or_key(self):
        with self.assertRaisesRegex(ValueError, "campaign must differ"):
            generate(
                self.package, self.trusted_public, self.fixture_private,
                self.untrusted_private, self.untrusted_public,
                self.root / "same-campaign",
                old_sequence=1, next_sequence=3, module_next_sequence=3,
                expected_campaign_id="issue15-fixture",
                expected_aot_toolchain_id="wamr-2.4.5-wasmhome.3",
                trusted_public_key=self.fixture_public,
                fixture_campaign_id="issue15-fixture",
            )
        with self.assertRaisesRegex(ValueError, "trust key must differ"):
            generate(
                self.package, self.trusted_public, self.trusted_private,
                self.untrusted_private, self.untrusted_public,
                self.root / "same-key",
                old_sequence=1, next_sequence=3, module_next_sequence=3,
                expected_campaign_id="issue15-fixture",
                expected_aot_toolchain_id="wamr-2.4.5-wasmhome.3",
                trusted_public_key=self.trusted_public,
                fixture_campaign_id="issues16-18-fixture",
            )

    def test_combined_allowlist_exposes_only_fixtures_and_signed_sequence_two(self):
        fixtures = self.root / "combined-fixtures"
        generate(self.package, self.trusted_public, self.fixture_private,
                 self.untrusted_private, self.untrusted_public, fixtures,
                 old_sequence=1, next_sequence=3, module_next_sequence=3,
                 expected_campaign_id="issue15-fixture",
                 expected_aot_toolchain_id="wamr-2.4.5-wasmhome.3",
                 trusted_public_key=self.fixture_public,
                 fixture_campaign_id="issues16-18-fixture")
        config = json.loads((self.root / "package-input.json").read_text(encoding="utf-8"))
        config["campaign_id"] = "issues16-18-fixture"
        ota_config = self.root / "issue22-package-input.json"
        ota_config.write_text(json.dumps(config), encoding="utf-8")
        ota_package = self.root / "issue22-package"
        sign_set(ota_config, ota_package, self.trusted_private)

        _, campaign, allowlist, _ = load_allowlist(
            fixtures, fixtures / "verification/trusted-public.der",
            fixtures / "verification/untrusted-public.der", "issues16-18-fixture",
            "wamr-2.4.5-wasmhome.3", ota_package_root=ota_package,
            ota_public_key=self.trusted_public)
        self.assertEqual(campaign, "issues16-18-fixture")
        self.assertEqual(len(allowlist), 24)
        ota_paths = {path for path in allowlist if path.startswith("scenario-")}
        self.assertEqual(len(ota_paths), 6)
        self.assertTrue(all("/v2/" in path for path in ota_paths))
        self.assertEqual({"resilience/ENV-SIG/scenario-1/firmware.bin",
                          "resilience/ENV-SIG/scenario-1/manifest.cose"} & set(allowlist),
                         {"resilience/ENV-SIG/scenario-1/firmware.bin",
                          "resilience/ENV-SIG/scenario-1/manifest.cose"})


if __name__ == "__main__":
    unittest.main()
