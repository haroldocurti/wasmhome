import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from signed_artifacts import (  # noqa: E402
    CborTag,
    _json_load,
    _rename_directory_noreplace,
    _manifest_values,
    _signature_structure,
    build_envelope,
    cbor_decode,
    cbor_encode,
    key_id,
    public_der_from_key,
    sign_message,
    sign_set,
    verify_envelope,
    verify_set,
)


@unittest.skipUnless(shutil.which("openssl"), "OpenSSL CLI is required")
class SignedArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temporary.name)
        self.private_key = self.root / "signing.key"
        self.public_key = self.root / "signing.pem"
        subprocess.run([
            "openssl", "genpkey", "-algorithm", "EC", "-pkeyopt",
            "ec_paramgen_curve:prime256v1", "-out", str(self.private_key),
        ], check=True, capture_output=True)
        self.private_key.chmod(0o600)
        with self.public_key.open("wb") as output:
            subprocess.run([
                "openssl", "pkey", "-in", str(self.private_key), "-pubout",
            ], check=True, stdout=output, stderr=subprocess.PIPE)

    def tearDown(self):
        self.temporary.cleanup()

    def manifest(self, scenario=1, payload=b"\xe9test", sequence=1, **overrides):
        values = _manifest_values(
            "issue15-fixture", scenario, "v1" if sequence == 1 else "v2",
            sequence, len(payload), hashlib.sha256(payload).digest(),
            "wamr-2.4.5-wasmhome.3" if scenario == 3 else None,
        )
        values.update(overrides)
        if scenario == 3:
            values[12] = "wamr-2.4.5-wasmhome.3"
        return values

    def raw_envelope(self, manifest, payload):
        public_der = public_der_from_key(self.private_key, private=True)
        protected = cbor_encode({1: -7, 4: key_id(public_der)})
        encoded_manifest = cbor_encode(manifest)
        signature = sign_message(
            _signature_structure(protected, encoded_manifest), self.private_key,
        )
        return cbor_encode(CborTag(18, [protected, {}, encoded_manifest, signature]))

    def test_sign_and_verify_across_all_artifact_kinds(self):
        cases = (
            (1, b"\xe9native-firmware"),
            (2, b"\x00asm\x01\x00\x00\x00wasm"),
            (3, b"\x00aotaot-payload"),
        )
        for scenario, payload in cases:
            with self.subTest(scenario=scenario):
                manifest = self.manifest(scenario, payload)
                envelope = build_envelope(manifest, payload, self.private_key)
                actual = verify_envelope(envelope, payload, self.public_key)
                self.assertEqual(actual, manifest)
                self.assertEqual(len(cbor_decode(envelope).value[3]), 64)

    def test_rejects_tampered_signature_payload_and_old_sequence(self):
        payload = b"\x00asm\x01\x00\x00\x00module"
        manifest = self.manifest(2, payload, sequence=2)
        envelope = build_envelope(manifest, payload, self.private_key)
        tampered = bytearray(envelope)
        tampered[-1] ^= 1
        with self.assertRaisesRegex(ValueError, "signature|P-256"):
            verify_envelope(bytes(tampered), payload, self.public_key)
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            verify_envelope(envelope, payload[:-1] + b"X", self.public_key)
        with self.assertRaisesRegex(ValueError, "not newer"):
            verify_envelope(envelope, payload, self.public_key, accepted_floor=2)
        with self.assertRaisesRegex(ValueError, "expected campaign"):
            verify_envelope(envelope, payload, self.public_key,
                            expected_campaign_id="another-campaign")

    def test_rejects_wrong_aot_toolchain_and_duplicate_tag_map_keys(self):
        payload = b"\x00aotaot-payload"
        envelope = build_envelope(self.manifest(3, payload), payload, self.private_key)
        with self.assertRaisesRegex(ValueError, "expected toolchain"):
            verify_envelope(envelope, payload, self.public_key,
                            expected_aot_toolchain_id="other-toolchain")
        duplicate_tag_keys = b"\xa2\xc1\x00\xc1\x01\xc1\x00\xc1\x02"
        with self.assertRaisesRegex(ValueError, "duplicate CBOR map key"):
            cbor_decode(duplicate_tag_keys)

    def test_rejects_signed_invalid_semantics_and_bad_size(self):
        payload = b"\xe9native-firmware"
        bad_version = self.manifest(1, payload)
        bad_version[1] = 2
        with self.assertRaisesRegex(ValueError, "manifest_version"):
            verify_envelope(self.raw_envelope(bad_version, payload), payload, self.public_key)

        bad_size = self.manifest(1, payload)
        bad_size[8] += 1
        with self.assertRaisesRegex(ValueError, "payload_size mismatch"):
            verify_envelope(self.raw_envelope(bad_size, payload), payload, self.public_key)

        bad_api = self.manifest(1, payload)
        bad_api[11] = 99
        with self.assertRaisesRegex(ValueError, "base_api_version"):
            verify_envelope(self.raw_envelope(bad_api, payload), payload, self.public_key)

    def test_rejects_noncanonical_and_ambiguous_cbor(self):
        for encoded, message in (
            (b"\x18\x01", "non-minimal"),
            (b"\xbf\xff", "indefinite"),
            (b"\xa2\x01\x00\x01\x01", "duplicate"),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                cbor_decode(encoded)

    def test_sign_set_builds_immutable_six_artifact_matrix(self):
        inputs = []
        for scenario, magic in (
            (1, b"\xe9"),
            (2, b"\x00asm\x01\x00\x00\x00"),
            (3, b"\x00aot"),
        ):
            for label, sequence in (("v1", 1), ("v2", 2)):
                name = f"scenario-{scenario}-{label}.bin"
                (self.root / name).write_bytes(magic + f"{scenario}-{label}".encode())
                inputs.append({"scenario": scenario, "label": label, "sequence": sequence, "payload": name})
        config = self.root / "inputs.json"
        config.write_text(json.dumps({
            "schema": "wasmhome-issue15-artifacts-v1",
            "campaign_id": "issue15-fixture",
            "aot_toolchain_id": "wamr-2.4.5-wasmhome.3",
            "artifacts": inputs,
        }), encoding="utf-8")
        package = self.root / "package"
        result = sign_set(config, package, self.private_key)
        verified = verify_set(package, self.public_key,
                              expected_campaign_id="issue15-fixture",
                              expected_aot_toolchain_id="wamr-2.4.5-wasmhome.3")
        self.assertEqual(len(result["artifacts"]), 6)
        self.assertEqual(len(verified["verified"]), 6)
        self.assertEqual(verified["aot_toolchain_id"], "wamr-2.4.5-wasmhome.3")
        self.assertFalse((package / "signing.key").exists())
        self.assertFalse(list(package.rglob("package.json")))
        with self.assertRaisesRegex(ValueError, "expected campaign"):
            verify_set(package, self.public_key, expected_campaign_id="wrong-campaign")
        with self.assertRaisesRegex(ValueError, "expected toolchain"):
            verify_set(package, self.public_key, expected_aot_toolchain_id="other-toolchain")
        with self.assertRaisesRegex(ValueError, "already exists"):
            sign_set(config, package, self.private_key)

    def test_refuses_broken_symlink_output_and_oversized_wasm_before_read(self):
        inputs = []
        for scenario, magic in ((1, b"\xe9"), (2, b"\x00asm\x01\x00\x00\x00"), (3, b"\x00aot")):
            for label, sequence in (("v1", 1), ("v2", 2)):
                name = f"bounded-{scenario}-{label}.bin"
                data = magic + b"fixture"
                if scenario == 2 and label == "v1":
                    data += b"x" * 65536
                (self.root / name).write_bytes(data)
                inputs.append({"scenario": scenario, "label": label, "sequence": sequence, "payload": name})
        config = self.root / "bounded.json"
        config.write_text(json.dumps({
            "schema": "wasmhome-issue15-artifacts-v1", "campaign_id": "issue15-fixture",
            "aot_toolchain_id": "wamr-2.4.5-wasmhome.3", "artifacts": inputs,
        }), encoding="utf-8")
        broken_link = self.root / "broken-output"
        broken_link.symlink_to(self.root / "missing-target")
        with self.assertRaisesRegex(ValueError, "already exists"):
            sign_set(config, broken_link, self.private_key)
        config_link = self.root / "config-link.json"
        config_link.symlink_to(config)
        with self.assertRaisesRegex(ValueError, "symlink"):
            sign_set(config_link, self.root / "symlink-config-output", self.private_key)
        with self.assertRaisesRegex(ValueError, "configured limit"):
            sign_set(config, self.root / "too-large", self.private_key)

    def test_rejects_deep_cbor_and_json_nesting_as_value_errors(self):
        with self.assertRaisesRegex(ValueError, "CBOR nesting depth"):
            cbor_decode(b"\x81" * 2000 + b"\xf6")
        deep_json = self.root / "deep.json"
        deep_json.write_text("[" * 2000 + "null" + "]" * 2000, encoding="ascii")
        with self.assertRaisesRegex(ValueError, "JSON nesting depth"):
            _json_load(deep_json)

    @unittest.skipUnless(sys.platform == "linux", "renameat2 is a Linux publication primitive")
    def test_atomic_package_publication_never_replaces_empty_directory(self):
        stage = self.root / "stage"
        output = self.root / "output"
        stage.mkdir()
        (stage / "payload").write_bytes(b"staged")
        output.mkdir()
        with self.assertRaisesRegex(ValueError, "created during artifact generation"):
            _rename_directory_noreplace(stage, output)
        self.assertTrue((stage / "payload").is_file())
        self.assertEqual(list(output.iterdir()), [])

    def test_private_key_permissions_and_p256_are_required(self):
        self.private_key.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "permissions"):
            build_envelope(self.manifest(), b"\xe9test", self.private_key)

    def test_index_is_bounded_not_symlinked_and_strictly_typed(self):
        inputs = []
        for scenario, magic in ((1, b"\xe9"), (2, b"\x00asm\x01\x00\x00\x00"), (3, b"\x00aot")):
            for label, sequence in (("v1", 1), ("v2", 2)):
                name = f"index-{scenario}-{label}.bin"
                (self.root / name).write_bytes(magic + label.encode())
                inputs.append({"scenario": scenario, "label": label, "sequence": sequence, "payload": name})
        config = self.root / "index-input.json"
        config.write_text(json.dumps({
            "schema": "wasmhome-issue15-artifacts-v1", "campaign_id": "issue15-fixture",
            "aot_toolchain_id": "wamr-2.4.5-wasmhome.3", "artifacts": inputs,
        }), encoding="utf-8")
        package = self.root / "index-package"
        sign_set(config, package, self.private_key)

        symlink_package = self.root / "symlink-package"
        shutil.copytree(package, symlink_package)
        (symlink_package / "index.json").unlink()
        (symlink_package / "index.json").symlink_to(package / "index.json")
        with self.assertRaisesRegex(ValueError, "symlink"):
            verify_set(symlink_package, self.public_key)

        oversized_package = self.root / "oversized-package"
        shutil.copytree(package, oversized_package)
        (oversized_package / "index.json").write_bytes(b" " * 65537)
        with self.assertRaisesRegex(ValueError, "configured limit"):
            verify_set(oversized_package, self.public_key)

        typed_package = self.root / "typed-package"
        shutil.copytree(package, typed_package)
        index = json.loads((typed_package / "index.json").read_text())
        index["base_api_version"] = True
        (typed_package / "index.json").write_text(json.dumps(index), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "target/base API"):
            verify_set(typed_package, self.public_key)

        if hasattr(os, "mkfifo"):
            fifo_package = self.root / "fifo-package"
            shutil.copytree(package, fifo_package)
            first_payload = fifo_package / index["artifacts"][0]["payload_path"]
            first_payload.unlink()
            os.mkfifo(first_payload)
            with self.assertRaisesRegex(ValueError, "regular file"):
                verify_set(fifo_package, self.public_key)


if __name__ == "__main__":
    unittest.main()
