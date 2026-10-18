import hashlib
import json
import os
from argparse import Namespace
from contextlib import contextmanager
from pathlib import Path
import stat
import subprocess
import struct
import tempfile
import unittest
from unittest import mock

from tools import issue22_hil_orchestrator as orchestrator
from tools.resilience_harness import resilience_preliminary_plan


class Issue22OrchestratorTests(unittest.TestCase):
    # Public P-256 generator point encoded as SubjectPublicKeyInfo (no private key).
    SIGNING_DER = bytes.fromhex(
        "3059301306072a8648ce3d020106082a8648ce3d03010703420004"
        "6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296"
        "4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def valid_bootloader_bytes(*, description_magic=0x50, extra_segments=()):
        header = bytearray(24)
        header[:4] = bytes((0xE9, 2 + len(extra_segments), 2, 0x4F))
        header[4:8] = (0x403C9700).to_bytes(4, "little")
        header[12:14] = (9).to_bytes(2, "little")
        header[23] = 1
        description = struct.pack("<B3sI32s24s16s", description_magic, b"\0" * 3, 1,
            b"v5.2.6".ljust(32, b"\0"), b"\0" * 24, b"\0" * 16)
        body = bytes(header)
        checksum = 0xEF
        for address, payload in ((0x3FCE3700, description), (0x403C9700, b"\0" * 16),
                                 *extra_segments):
            body += struct.pack("<II", address, len(payload)) + payload
            for value in payload:
                checksum ^= value
        checksum_offset = len(body) | 15
        body += b"\0" * (checksum_offset - len(body)) + bytes((checksum,))
        return body + hashlib.sha256(body).digest()

    @staticmethod
    def valid_partition_table(names=None, overrides=None):
        import csv

        entries = []
        with (orchestrator.ROOT / "partitions.csv").open(newline="", encoding="utf-8") as stream:
            for row in csv.reader(line for line in stream if line.strip() and not line.lstrip().startswith("#")):
                name, kind_name, subtype_name, offset, size, flags = (item.strip() for item in row)
                if names is not None and name not in names:
                    continue
                start, length = (overrides or {}).get(name, (int(offset, 0), int(size, 0)))
                type_codes = {"app": 0, "data": 1}
                subtype_codes = {
                    ("data", "nvs"): 2, ("data", "ota"): 0, ("data", "phy"): 1,
                    ("app", "ota_0"): 0x10, ("app", "ota_1"): 0x11,
                }
                kind = type_codes[kind_name]
                subtype = (subtype_codes[(kind_name, subtype_name)]
                           if (kind_name, subtype_name) in subtype_codes
                           else int(subtype_name, 0) if subtype_name.startswith("0x") else None)
                if subtype is None or flags:
                    raise ValueError(f"unsupported fixture partition declaration: {row!r}")
                entries.append(struct.pack("<HBBII16sI", 0x50AA, kind, subtype, start,
                                           length, name.encode().ljust(16, b"\0"), 0))
        body = b"".join(entries)
        md5 = b"\xeb\xeb" + b"\xff" * 14 + hashlib.md5(body).digest()
        return body + md5 + b"\xff" * (orchestrator.TABLE_BYTES - len(body) - len(md5))

    def test_journal_updates_are_atomic_private_and_replaceable(self):
        directory = self.root / "private"
        orchestrator.private_directory(directory)
        journal = directory / "journal.json"
        orchestrator.write_json(journal, {"state": "starting"}, exclusive=True)
        orchestrator.write_json(journal, {"state": "restoring"})
        self.assertEqual(json.loads(journal.read_text()), {"state": "restoring"})
        self.assertEqual(stat.S_IMODE(journal.stat().st_mode), 0o600)
        self.assertEqual(list(directory.glob("*.tmp")), [])
        self.assertEqual(list(directory.glob(".journal.json.*.tmp")), [])

    def test_accepted_baseline_requires_exact_image_hash_and_partition_map(self):
        image = self.root / "accepted.bin"
        data = bytearray(b"\xff" * orchestrator.FLASH_BYTES)
        data[orchestrator.TABLE_OFFSET:orchestrator.TABLE_OFFSET + orchestrator.TABLE_BYTES] = self.valid_partition_table()
        image.write_bytes(data)
        image.chmod(0o600)
        digest = hashlib.sha256(data).hexdigest()
        result = orchestrator.validate_accepted_baseline(image, digest)
        self.assertEqual(result["sha256"], digest)
        self.assertEqual(result["missing_profile_partitions"], [])
        with self.assertRaisesRegex(orchestrator.OrchestratorError, "SHA-256"):
            orchestrator.validate_accepted_baseline(image, "0" * 64)

    def test_partition_table_requires_md5_to_terminate_the_table(self):
        valid = self.valid_partition_table()
        md5_offset = next(offset for offset in range(0, len(valid), 32)
                          if valid[offset:offset + 2] == b"\xeb\xeb")
        extra_entry = valid[:32]
        malformed = (valid[:md5_offset + 32] + extra_entry
                     + b"\xff" * (len(valid) - md5_offset - 64))
        with self.assertRaisesRegex(orchestrator.OrchestratorError, "data after MD5"):
            orchestrator.validate_partition_table(malformed)

    def test_partition_table_checks_type_subtype_and_flags(self):
        valid = self.valid_partition_table()
        md5_offset = next(offset for offset in range(0, len(valid), 32)
                          if valid[offset:offset + 2] == b"\xeb\xeb")
        for field_offset, encoded in ((2, b"\x00"), (3, b"\x00"), (28, b"\x01\x00\x00\x00")):
            with self.subTest(field_offset=field_offset):
                malformed = bytearray(valid)
                malformed[field_offset:field_offset + len(encoded)] = encoded
                malformed[md5_offset + 16:md5_offset + 32] = hashlib.md5(
                    malformed[:md5_offset]).digest()
                with self.assertRaisesRegex(orchestrator.OrchestratorError,
                                            "live flash partition table differs"):
                    orchestrator.validate_partition_table(bytes(malformed))

    def test_resume_accepts_private_baseline_snapshot_but_pins_its_hash_and_options(self):
        saved = {"action": "run", "accepted_baseline": "/accepted/original.bin",
                 "accepted_baseline_sha256": "a" * 64, "device_ip": "192.0.2.5",
                 "timeout_ms": 300}
        options = Namespace(action="run", accepted_baseline=self.root / "inputs" / "accepted-full-flash.bin",
                            accepted_baseline_sha256="a" * 64, device_ip="192.0.2.5",
                            timeout_ms=300)
        orchestrator._validate_resume_options(saved, options)
        options.accepted_baseline_sha256 = "b" * 64
        with self.assertRaisesRegex(orchestrator.OrchestratorError,
                                    "resume configuration changed for accepted_baseline_sha256"):
            orchestrator._validate_resume_options(saved, options)
        options.accepted_baseline_sha256 = "a" * 64
        options.device_ip = "192.0.2.6"
        with self.assertRaisesRegex(orchestrator.OrchestratorError,
                                    "resume configuration changed for device_ip"):
            orchestrator._validate_resume_options(saved, options)

    def test_manager_fingerprint_refresh_requires_verified_zero_slot_recovery(self):
        baseline_hash = "a" * 64
        journal = {
            "server_manager": {"tool_sha256": {"orchestrator": "old"}},
            "state": "interrupted_recovered", "restore_required": False,
            "next_index": 0, "slots": [], "current_attempt": None,
            "current_plan_index": None,
            "recoveries": [{"baseline_sha256": baseline_hash,
                            "claimed_through": 0, "next_index": 0,
                            "flash_rewritten": False}],
        }
        current = {"tool_sha256": {"orchestrator": "new"}}
        self.assertTrue(orchestrator._refresh_manager_fingerprint_after_recovery(
            journal, current, 0, baseline_hash))
        self.assertEqual(journal["server_manager"], current)
        self.assertEqual(len(journal["tooling_rebindings"]), 1)

        for update in ({"restore_required": True}, {"next_index": 1},
                       {"slots": [{"slot_id": "B0001"}]},
                       {"state": "ready"}):
            with self.subTest(update=update):
                rejected = json.loads(json.dumps({
                    "server_manager": {"tool_sha256": {"orchestrator": "old"}},
                    "state": "interrupted_recovered", "restore_required": False,
                    "next_index": 0, "slots": [], "current_attempt": None,
                    "current_plan_index": None,
                    "recoveries": [{"baseline_sha256": baseline_hash,
                                    "claimed_through": 0, "next_index": 0,
                                    "flash_rewritten": False}],
                }))
                rejected.update(update)
                with self.assertRaisesRegex(orchestrator.OrchestratorError,
                                            "inputs changed after issue #22 slots began"):
                    orchestrator._refresh_manager_fingerprint_after_recovery(
                        rejected, current, 0, baseline_hash)

    def test_accepted_legacy_baseline_allows_only_nonoverlapping_module_slots(self):
        image = self.root / "accepted-legacy.bin"
        legacy_names = {"nvs", "otadata", "phy_init", "ota_0", "ota_1"}
        data = bytearray(b"\xff" * orchestrator.FLASH_BYTES)
        table = self.valid_partition_table(names=legacy_names)
        data[orchestrator.TABLE_OFFSET:orchestrator.TABLE_OFFSET + orchestrator.TABLE_BYTES] = table
        image.write_bytes(data)
        image.chmod(0o600)
        result = orchestrator.validate_accepted_baseline(image, hashlib.sha256(data).hexdigest())
        self.assertEqual(result["missing_profile_partitions"], [
            "aot_slot_a", "aot_slot_b", "interp_slot_a", "interp_slot_b"])

        changed = bytearray(data)
        changed[orchestrator.TABLE_OFFSET:orchestrator.TABLE_OFFSET + orchestrator.TABLE_BYTES] = self.valid_partition_table(
            names=legacy_names, overrides={"nvs": (0x9000, 0x5000)})
        image.write_bytes(changed)
        with self.assertRaisesRegex(orchestrator.OrchestratorError, "changed an existing"):
            orchestrator.validate_accepted_baseline(image, hashlib.sha256(changed).hexdigest())

    def test_profile_manifest_binds_endpoint_timeout_and_ca_to_app_bytes(self):
        ca = self.root / "ca.pem"
        ca_bytes = b"-----BEGIN CERTIFICATE-----\nsynthetic test CA\n-----END CERTIFICATE-----\n"
        ca.write_bytes(ca_bytes)
        ca_hash = hashlib.sha256(ca_bytes).hexdigest()
        signing_key = self.root / "signing-public.der"
        signing_key.write_bytes(self.SIGNING_DER)

        def write_manifest(native_port=18445, native_rollback=True, module_rollback=True,
                           native_update_port=18445, campaign=None, embed_signing_key=True):
            profiles = {}
            partition_table = self.root / "partition-table.bin"
            table_bytes = self.valid_partition_table()
            last_non_erased = max(index for index, value in enumerate(table_bytes) if value != 0xFF)
            partition_table.write_bytes(table_bytes[:last_non_erased + 1])
            for name in ("native", "interp", "aot"):
                port = native_port if name == "native" else 18445
                config = self.root / f"{name}-sdkconfig"
                rollback = native_rollback if name == "native" else module_rollback
                config.write_text(('CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y\n' if rollback else
                                   '# CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE is not set\n') +
                                  'CONFIG_IDF_TARGET="esp32s3"\n'
                                  'CONFIG_ESPTOOLPY_FLASHSIZE_16MB=y\n'
                                  'CONFIG_PARTITION_TABLE_OFFSET=0x8000\n' +
                                  f'CONFIG_WASMHOME_UPDATE_CAMPAIGN_ID={json.dumps(campaign or orchestrator.hil_runner.CAMPAIGN_ID)}\n' +
                                  f'CONFIG_WASMHOME_UPDATE_SIGNING_PUBLIC_KEY_PATH={json.dumps(str(signing_key))}\n')
                config_hash = hashlib.sha256(config.read_bytes()).hexdigest()
                update_port = native_update_port if name == "native" else 18445
                marker = ("WASMHOME_ISSUE22_BUILD_V2\n"
                    f"profile={name}\nsource_commit={'a' * 40}\nsource_tree={'c' * 40}\n"
                    "functional_timeout_ms=450\nserver_ipv4=192.0.2.8\n"
                    f"server_port={port}\nserver_name=wasmhome-issue22.test\nca_sha256={ca_hash}\n"
                    f"update_server_ipv4=192.0.2.8\nupdate_server_port={update_port}\n"
                    "update_server_name=wasmhome-issue22.test\n"
                    f"sdkconfig_sha256={config_hash}\napp_rollback_enabled={1 if rollback else 0}\n"
                    "END_WASMHOME_ISSUE22_BUILD_V2\n\0")
                header = bytearray(24)
                header[0] = 0xE9
                header[1] = 1
                header[12:14] = (9).to_bytes(2, "little")
                app = self.root / f"{name}.bin"
                app_bytes = (bytes(header) + marker.encode() + ca_bytes +
                    (campaign or orchestrator.hil_runner.CAMPAIGN_ID).encode() + b"\0" +
                    (self.SIGNING_DER if embed_signing_key else b""))
                app.write_bytes(app_bytes)
                bootloader = self.root / f"{name}-bootloader.bin"
                bootloader.write_bytes(self.valid_bootloader_bytes())
                boot_config = self.root / f"{name}-bootloader-sdkconfig.h"
                boot_config.write_text(("#define CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE 1\n" if rollback else "") +
                    "#define CONFIG_IDF_TARGET_ESP32S3 1\n"
                    "#define CONFIG_ESPTOOLPY_FLASHSIZE_16MB 1\n"
                    "#define CONFIG_PARTITION_TABLE_OFFSET 0x8000\n")
                profiles[name] = {
                    "v1_app": {"path": str(app), "sha256": hashlib.sha256(app_bytes).hexdigest()},
                    "bootloader": {"path": str(bootloader),
                        "sha256": hashlib.sha256(bootloader.read_bytes()).hexdigest()},
                    "sdkconfig": {"path": str(config), "sha256": config_hash},
                    "bootloader_config": {"path": str(boot_config),
                        "sha256": hashlib.sha256(boot_config.read_bytes()).hexdigest()}}
            manifest = self.root / "profiles.json"
            manifest.write_text(json.dumps({
                "schema": orchestrator.PROFILE_SCHEMA,
                "resilience_test_mode": True,
                "functional_timeout_ms": 450,
                "endpoint": {"ipv4": "192.0.2.8", "port": 18445,
                    "server_name": "wasmhome-issue22.test", "ca_sha256": ca_hash},
                "update_endpoint": {"ipv4": "192.0.2.8", "port": 18445,
                    "server_name": "wasmhome-issue22.test", "ca_sha256": ca_hash},
                "partition_table": {"path": str(partition_table),
                    "sha256": hashlib.sha256(partition_table.read_bytes()).hexdigest()},
                "profiles": profiles,
                "source_commit": "a" * 40,
                "source_tree": "c" * 40,
            }))
            return manifest

        manifest = write_manifest()
        value = orchestrator.validate_profile_manifest(manifest, 450, "192.0.2.8",
            18445, "wasmhome-issue22.test", ca)
        self.assertEqual(value["profiles"]["native"]["build_provenance"]["server_port"], 18445)
        self.assertEqual(value["partition_table"]["bytes"],
                         (self.root / "partition-table.bin").stat().st_size)
        self.assertEqual(value["profiles"]["native"]["signing_identity"], {
            "campaign_id": orchestrator.hil_runner.CAMPAIGN_ID,
            "signing_public_key_sha256": hashlib.sha256(self.SIGNING_DER).hexdigest()})

        native_app = Path(json.loads(manifest.read_text())["profiles"]["native"]["v1_app"]["path"])
        with self.assertRaisesRegex(orchestrator.OrchestratorError, "exactly one"):
            orchestrator._embedded_issue22_build_marker(native_app.read_bytes() * 2)

        for key in ("source_commit", "source_tree"):
            with self.subTest(forged_source_field=key):
                original = json.loads(manifest.read_text())
                forged = dict(original, **{key: "b" * 40})
                manifest.write_text(json.dumps(forged))
                with self.assertRaisesRegex(orchestrator.OrchestratorError, "BIN-embedded"):
                    orchestrator.validate_profile_manifest(manifest, 450, "192.0.2.8",
                        18445, "wasmhome-issue22.test", ca)
                manifest.write_text(json.dumps(original))

        mismatched_campaign = write_manifest(campaign="different-campaign")
        with self.assertRaisesRegex(orchestrator.OrchestratorError, "compiled campaign differs"):
            orchestrator.validate_profile_manifest(mismatched_campaign, 450, "192.0.2.8",
                18445, "wasmhome-issue22.test", ca)

        missing_embedded_key = write_manifest(embed_signing_key=False)
        with self.assertRaisesRegex(orchestrator.OrchestratorError, "BIN does not contain.*DER bytes"):
            orchestrator.validate_profile_manifest(missing_embedded_key, 450, "192.0.2.8",
                18445, "wasmhome-issue22.test", ca)

        manifest = write_manifest()
        # A replaced file at the configured path cannot relabel an existing BIN.
        signing_key.write_bytes(self.SIGNING_DER[:-1] + bytes((self.SIGNING_DER[-1] ^ 1,)))
        with self.assertRaises(orchestrator.OrchestratorError):
            orchestrator.validate_profile_manifest(manifest, 450, "192.0.2.8",
                18445, "wasmhome-issue22.test", ca)
        signing_key.write_bytes(self.SIGNING_DER)

        module_without_app_rollback = write_manifest(module_rollback=False)
        compatible = orchestrator.validate_profile_manifest(module_without_app_rollback, 450,
            "192.0.2.8", 18445, "wasmhome-issue22.test", ca)
        self.assertEqual(compatible["profiles"]["interp"]["build_provenance"]["app_rollback_enabled"], "0")

        stale = write_manifest(native_port=8445)
        with self.assertRaisesRegex(orchestrator.OrchestratorError, "BIN-embedded"):
            orchestrator.validate_profile_manifest(stale, 450, "192.0.2.8",
                18445, "wasmhome-issue22.test", ca)

        stale_normal_update = write_manifest(native_update_port=8445)
        with self.assertRaisesRegex(orchestrator.OrchestratorError, "BIN-embedded"):
            orchestrator.validate_profile_manifest(stale_normal_update, 450, "192.0.2.8",
                18445, "wasmhome-issue22.test", ca)

        manifest = write_manifest()
        data = json.loads(manifest.read_text())
        data["update_endpoint"]["port"] = 8445
        manifest.write_text(json.dumps(data))
        with self.assertRaisesRegex(orchestrator.OrchestratorError, "manifest endpoints must match"):
            orchestrator.validate_profile_manifest(manifest, 450, "192.0.2.8",
                18445, "wasmhome-issue22.test", ca)

        rollback_disabled = write_manifest(native_rollback=False)
        with self.assertRaisesRegex(orchestrator.OrchestratorError, "must enable rollback"):
            orchestrator.validate_profile_manifest(rollback_disabled, 450, "192.0.2.8",
                18445, "wasmhome-issue22.test", ca)

        manifest = write_manifest()
        data = json.loads(manifest.read_text())
        record = data["profiles"]["native"]["bootloader_config"]
        Path(record["path"]).write_text("#define CONFIG_IDF_TARGET_ESP32S3 1\n")
        record["sha256"] = hashlib.sha256(Path(record["path"]).read_bytes()).hexdigest()
        manifest.write_text(json.dumps(data))
        with self.assertRaisesRegex(orchestrator.OrchestratorError, "bootloader config rollback mode differs"):
            orchestrator.validate_profile_manifest(manifest, 450, "192.0.2.8",
                18445, "wasmhome-issue22.test", ca)

        manifest = write_manifest()
        data = json.loads(manifest.read_text())
        data["profiles"]["native"].pop("bootloader")
        manifest.write_text(json.dumps(data))
        with self.assertRaisesRegex(orchestrator.OrchestratorError, "fields differ"):
            orchestrator.validate_profile_manifest(manifest, 450, "192.0.2.8",
                18445, "wasmhome-issue22.test", ca)

    def test_profile_rejects_signing_key_different_from_provisioning(self):
        configured = self.root / "configured.der"
        configured.write_bytes(self.SIGNING_DER)
        expected = self.root / "expected.der"
        expected.write_bytes(bytes.fromhex(
            "3059301306072a8648ce3d020106082a8648ce3d03010703420004"
            "7cf27b188d034f7e8a52380304b51ac3c08969e277f21b35a60b48fc47669978"
            "07775510db8ed040293d9ac69f7430dbba7dade63ce982299e04b79d227873d1"))
        config = [f'CONFIG_WASMHOME_UPDATE_CAMPAIGN_ID="{orchestrator.hil_runner.CAMPAIGN_ID}"',
                  f'CONFIG_WASMHOME_UPDATE_SIGNING_PUBLIC_KEY_PATH={json.dumps(str(configured))}']
        app = orchestrator.hil_runner.CAMPAIGN_ID.encode() + b"\0" + self.SIGNING_DER
        with self.assertRaisesRegex(orchestrator.OrchestratorError, "compiled signing key differs"):
            orchestrator._profile_signing_identity(config, app, expected)

    def test_packages_require_one_trusted_key_for_provisioning_and_fixtures(self):
        package = {"signing_public_key_sha256": "a" * 64, "verified": [
            {"scenario": scenario, "version": "v2", "sequence": 2,
             "payload_sha256": orchestrator.hil_runner.EXPECTED_RELEASE_ARTIFACTS[profile][0]}
            for scenario, profile in ((1, "native"), (2, "module-interp"), (3, "module-aot"))]}
        fixture_index = {"trusted_fixture_public_key_sha256": "b" * 64}
        with mock.patch.object(orchestrator.signed_artifacts, "verify_set", return_value=package), \
             mock.patch.object(orchestrator.fixture_server, "load_allowlist", return_value=(
                 self.root, orchestrator.hil_runner.CAMPAIGN_ID, list(range(24)), fixture_index)), \
             mock.patch.object(orchestrator, "sha256_file", return_value="c" * 64):
            with self.assertRaisesRegex(orchestrator.OrchestratorError, "provisioning key differs"):
                orchestrator.validate_packages(*(self.root for _ in range(5)), "toolchain")
            fixture_index["trusted_fixture_public_key_sha256"] = "a" * 64
            result = orchestrator.validate_packages(*(self.root for _ in range(5)), "toolchain")
            self.assertEqual(result["package_signing_key_sha256"], result["fixture_trusted_key_sha256"])
            self.assertEqual(result["ota_v2"], [
                [1, orchestrator.hil_runner.EXPECTED_RELEASE_ARTIFACTS["native"][0]],
                [2, orchestrator.hil_runner.EXPECTED_RELEASE_ARTIFACTS["module-interp"][0]],
                [3, orchestrator.hil_runner.EXPECTED_RELEASE_ARTIFACTS["module-aot"][0]],
            ])
            self.assertEqual(json.loads(json.dumps(result)), result)

    def test_overlay_installs_bootloader_before_apps_and_verifies_before_reset(self):
        app = self.root / "app.bin"
        bootloader = self.root / "bootloader.bin"
        partition_table = self.root / "partition-table.bin"
        app.write_bytes(b"A" * 24)
        bootloader.write_bytes(self.valid_bootloader_bytes())
        partition_table.write_bytes(self.valid_partition_table())
        board = orchestrator.Board(object(), self.root)

        def readback(offset, _size, path, **_kwargs):
            path.write_bytes(partition_table.read_bytes() if offset == orchestrator.TABLE_OFFSET
                             else bootloader.read_bytes())

        with mock.patch.object(board, "command", return_value="Hash of data verified.\n" * 4) as command, \
             mock.patch.object(board, "read", side_effect=readback) as read:
            board.overlay_profile(app, bootloader, partition_table)
        self.assertEqual(command.call_args.args, ("write_flash", "0x0", str(bootloader),
            hex(orchestrator.TABLE_OFFSET), str(partition_table),
            hex(orchestrator.OTA_OFFSETS[0]), str(app), hex(orchestrator.OTA_OFFSETS[1]), str(app)))
        self.assertEqual(read.call_args_list[0].args[:2], (orchestrator.TABLE_OFFSET,
            orchestrator.TABLE_BYTES))
        self.assertEqual(read.call_args_list[1].args[:2], (0, len(self.valid_bootloader_bytes())))

        def wrong_readback(_offset, _size, path, **_kwargs):
            path.write_bytes(b"C" * 24)

        with mock.patch.object(board, "command", return_value="Hash of data verified.\n" * 4) as command, \
             mock.patch.object(board, "read", side_effect=wrong_readback):
            with self.assertRaisesRegex(orchestrator.OrchestratorError, "refusing reset"):
                board.overlay_profile(app, bootloader, partition_table)
        self.assertEqual(command.call_count, 1)

    def test_overlay_refuses_reset_without_on_chip_md5_for_every_segment(self):
        app = self.root / "app.bin"
        bootloader = self.root / "bootloader.bin"
        partition_table = self.root / "partition-table.bin"
        app.write_bytes(b"A" * 24)
        bootloader.write_bytes(self.valid_bootloader_bytes())
        partition_table.write_bytes(self.valid_partition_table())
        board = orchestrator.Board(object(), self.root)
        with mock.patch.object(board, "command", return_value="Hash of data verified.\n" * 3), \
             mock.patch.object(board, "read") as read:
            with self.assertRaisesRegex(orchestrator.OrchestratorError, "on-chip MD5"):
                board.overlay_profile(app, bootloader, partition_table)
        read.assert_not_called()

    def test_row_restore_uses_write_md5_and_recovery_keeps_full_readback(self):
        baseline = self.root / "baseline.bin"
        baseline.write_bytes(b"\xff" * orchestrator.FLASH_BYTES)
        baseline_hash = orchestrator.sha256_file(baseline)
        board = orchestrator.Board(object(), self.root)
        with mock.patch.object(board, "command", return_value="Hash of data verified.\n"), \
             mock.patch.object(board, "read") as read:
            board.restore(baseline, baseline_hash, self.root / "flash-restored.bin")
        read.assert_not_called()
        receipt = json.loads((self.root / "flash-restored.json").read_text())
        self.assertEqual(receipt["method"], "write_flash_on_chip_md5")
        self.assertEqual(receipt["baseline_sha256"], baseline_hash)

        def readback(_offset, _size, path, **_kwargs):
            path.write_bytes(baseline.read_bytes())

        with mock.patch.object(board, "command", return_value="Hash of data verified.\n"), \
             mock.patch.object(board, "read", side_effect=readback) as read:
            board.restore(baseline, baseline_hash, self.root / "recovered.bin", full_readback=True)
        read.assert_called_once()
        with mock.patch.object(board, "command", return_value="Leaving...\n"):
            with self.assertRaisesRegex(orchestrator.OrchestratorError, "on-chip MD5"):
                board.restore(baseline, baseline_hash, self.root / "unverified.bin")

    def test_verify_image_requires_esptool_digest_match(self):
        baseline = self.root / "baseline.bin"
        baseline.write_bytes(b"\xff" * orchestrator.FLASH_BYTES)
        baseline_hash = orchestrator.sha256_file(baseline)
        board = orchestrator.Board(object(), self.root)
        with mock.patch.object(board, "command",
                               return_value="-- verify OK (digest matched)\n") as command:
            board.verify_image(baseline, baseline_hash)
        self.assertEqual(command.call_args.args, ("verify_flash", "0x0", str(baseline)))
        with mock.patch.object(board, "command", return_value="Verifying...\n"):
            with self.assertRaisesRegex(orchestrator.OrchestratorError, "MD5 differs"):
                board.verify_image(baseline, baseline_hash)

    def bench_profiles(self, **overrides):
        settings = {"CONFIG_WASMHOME_WIFI_SSID": '"bench"',
                    "CONFIG_WASMHOME_WIFI_PASSWORD": '"secret-value"',
                    "CONFIG_WASMHOME_OBSERVER_IPV4": '"127.0.0.1"',
                    "CONFIG_WASMHOME_UDP_PORT": "3333", **overrides}
        config = self.root / "bench-sdkconfig"
        config.write_text("".join(f"{key}={value}\n" for key, value in settings.items()))
        return {"profiles": {"native": {"sdkconfig": {"path": str(config)}}}}

    def test_bench_network_gate_rejects_empty_password_and_wrong_observer(self):
        orchestrator.validate_bench_network(self.bench_profiles(), "127.0.0.1")
        for overrides, message in (({"CONFIG_WASMHOME_WIFI_PASSWORD": '""'}, "empty Wi-Fi password"),
                                   ({"CONFIG_WASMHOME_WIFI_SSID": '""'}, "no Wi-Fi SSID"),
                                   ({"CONFIG_WASMHOME_OBSERVER_IPV4": '"192.0.2.9"'}, "different observer"),
                                   ({"CONFIG_WASMHOME_UDP_PORT": "4444"}, "UDP port")):
            with self.subTest(message=message):
                with self.assertRaisesRegex(orchestrator.OrchestratorError, message) as raised:
                    orchestrator.validate_bench_network(self.bench_profiles(**overrides), "127.0.0.1")
                self.assertNotIn("secret-value", str(raised.exception))

    def test_observer_host_gate_requires_ufw_rule_for_device_telemetry(self):
        conf, defaults, rules = (self.root / name for name in ("ufw.conf", "ufw-defaults", "user.rules"))
        conf.write_text("ENABLED=yes\n")
        defaults.write_text('DEFAULT_INPUT_POLICY="DROP"\n')
        rules.write_text("*filter\n-A ufw-user-input -p tcp --dport 22 -j ACCEPT\nCOMMIT\n")
        paths = {"ufw_conf": conf, "ufw_defaults": defaults, "ufw_rules": rules}
        with self.assertRaisesRegex(orchestrator.OrchestratorError, "sudo ufw allow proto udp"):
            orchestrator.validate_observer_host("127.0.0.1", "192.0.2.20", **paths)
        rules.write_text("-A ufw-user-input -p udp -d 127.0.0.1 --dport 3333 "
                         "-s 192.0.2.21 -j ACCEPT\n")
        with self.assertRaisesRegex(orchestrator.OrchestratorError, "drops inbound UDP 3333"):
            orchestrator.validate_observer_host("127.0.0.1", "192.0.2.20", **paths)
        rules.write_text("-A ufw-user-input -p udp -d 127.0.0.1 --dport 3333 "
                         "-s 192.0.2.20 -j ACCEPT\n")
        self.assertEqual(orchestrator.validate_observer_host("127.0.0.1", "192.0.2.20", **paths),
                         {"firewall": "ufw_rule_admits_device"})
        for constraint in ("-i lo", "--sport 9", "-m conntrack --ctstate ESTABLISHED"):
            with self.subTest(constraint=constraint):
                rules.write_text(f"-A ufw-user-input -p udp --dport 3333 {constraint} -j ACCEPT\n")
                with mock.patch.object(orchestrator.subprocess, "run", return_value=
                        subprocess.CompletedProcess([], 0, stdout='[{"dev":"eth0"}]')):
                    with self.assertRaisesRegex(orchestrator.OrchestratorError, "drops inbound UDP"):
                        orchestrator.validate_observer_host("127.0.0.1", "192.0.2.20", **paths)
        rules.write_text("-A ufw-user-input -i eth0 -p udp --dport 3333 -j ACCEPT\n")
        with mock.patch.object(orchestrator.subprocess, "run", return_value=
                subprocess.CompletedProcess([], 0, stdout='[{"dev":"eth0"}]')):
            self.assertEqual(orchestrator.validate_observer_host("127.0.0.1", "192.0.2.20", **paths),
                             {"firewall": "ufw_rule_admits_device"})
        rules.write_text("-A ufw-user-input -p udp --dport 3333 -j DROP\n"
                         "-A ufw-user-input -p udp --dport 3333 -j ACCEPT\n")
        for default_policy in ("DROP", "ACCEPT"):
            defaults.write_text(f'DEFAULT_INPUT_POLICY="{default_policy}"\n')
            with self.assertRaisesRegex(orchestrator.OrchestratorError, "earlier UFW deny rule"):
                orchestrator.validate_observer_host("127.0.0.1", "192.0.2.20", **paths)
        conf.write_text("ENABLED=no\n")
        self.assertEqual(orchestrator.validate_observer_host("127.0.0.1", "192.0.2.20", **paths),
                         {"firewall": "ufw_disabled"})
        with self.assertRaisesRegex(orchestrator.OrchestratorError, "not assigned to this host"):
            orchestrator.validate_observer_host("192.0.2.10", "192.0.2.20", **paths)

    def test_error_chain_keeps_the_root_cause(self):
        try:
            try:
                raise ValueError("boot_id must be an integer")
            except ValueError as cause:
                raise orchestrator.OrchestratorError("row 1 failed") from cause
        except orchestrator.OrchestratorError as error:
            self.assertEqual(orchestrator.error_chain(error),
                             "OrchestratorError: row 1 failed <- ValueError: boot_id must be an integer")

    def test_staging_multiplexes_only_the_pinned_transport(self):
        args = ["/usr/bin/ssh", "-o", "ControlMaster=no", "-o", "ConnectTimeout=5",
                "--", "wasmhome@192.0.2.1"]
        self.assertIs(orchestrator._multiplexed_args(args, None), args)
        self.assertEqual(orchestrator._multiplexed_args(args, "/tmp/wh-ssh-x/m"),
                         ["/usr/bin/ssh", "-o", "ControlMaster=no", "-o",
                          "ControlPath=/tmp/wh-ssh-x/m",
                          "-o", "ConnectTimeout=5", "--", "wasmhome@192.0.2.1"])
        with self.assertRaisesRegex(orchestrator.OrchestratorError, "refusing to multiplex"):
            orchestrator._multiplexed_args(["/usr/bin/ssh", "--", "wasmhome@192.0.2.1"], "/tmp/m")
        with self.assertRaisesRegex(orchestrator.OrchestratorError, "refusing to multiplex"):
            orchestrator._multiplexed_args(args, "/tmp/%h")

    def test_ssh_cleanup_timeout_removes_socket_directory_and_preserves_staging_error(self):
        args = ["/usr/bin/ssh", "-o", "ControlMaster=no", "--", "wasmhome@192.0.2.1"]
        for staging_fails in (False, True):
            with self.subTest(staging_fails=staging_fails):
                directory = self.root / f"socket-{staging_fails}"
                directory.mkdir()
                session = object.__new__(orchestrator.SSHSession)
                session.control_path = None
                with mock.patch.object(orchestrator.tempfile, "mkdtemp", return_value=str(directory)), \
                     mock.patch.object(orchestrator.user_server_manager, "ssh_args", return_value=args), \
                     mock.patch.object(orchestrator.subprocess, "run", side_effect=[
                         subprocess.CompletedProcess(args, 0), subprocess.TimeoutExpired(args, 10)]):
                    expected = RuntimeError if staging_fails else subprocess.TimeoutExpired
                    with self.assertRaises(expected):
                        with session.multiplexed({"transport": "fixture"}):
                            self.assertIsNotNone(session.control_path)
                            if staging_fails:
                                raise RuntimeError("staging failed")
                self.assertFalse(directory.exists())
                self.assertIsNone(session.control_path)

    def test_smoke_uart_failure_is_reported_to_caller(self):
        stop = orchestrator.threading.Event()
        errors = []
        port = mock.Mock()
        port.read.side_effect = OSError("USB disconnected")
        orchestrator._capture_uart(port, self.root / "uart.log", stop, errors)
        self.assertTrue(stop.is_set())
        self.assertEqual(str(errors[0]), "USB disconnected")

    def test_failed_smoke_restores_board_and_returns_error_with_diagnostic(self):
        options = Namespace(smoke_profile="native", smoke_seconds=5, bind_ip="127.0.0.1",
                            device_ip="192.0.2.20", output_root=self.root / "smoke",
                            serial_port="fake", serial_baud=115200, timeout_ms=300,
                            profiles=self.root / "profiles.json", server_ip="192.0.2.8",
                            https_port=18445, server_name="fixture.test", tls_ca=self.root / "ca",
                            ota_signing_public=self.root / "key", accepted_baseline=self.root / "baseline",
                            accepted_baseline_sha256="a" * 64)
        profile = {"profiles": {"native": {"path": "app", "bootloader": {"path": "bootloader"}}},
                   "partition_table": {"path": "table"}}

        def observer(**kwargs):
            output = kwargs["output"]
            output.mkdir()
            (output / "observations.jsonl").write_text("")
            return {"counters": {}, "valid_controls_by_boot": {}}

        board = mock.Mock()
        with mock.patch.object(orchestrator, "validate_cli"), \
             mock.patch.object(orchestrator, "validate_profile_manifest", return_value=profile), \
             mock.patch.object(orchestrator, "validate_observer_host", return_value={}), \
             mock.patch.object(orchestrator, "validate_accepted_baseline", return_value={
                 "path": options.accepted_baseline, "sha256": "a" * 64}), \
             mock.patch.object(orchestrator, "hardware_lock"), \
             mock.patch.object(orchestrator, "Board", return_value=board), \
             mock.patch.object(orchestrator.hil_runner, "ExclusiveSerialPort"), \
             mock.patch.object(orchestrator.threading, "Thread") as thread, \
             mock.patch.object(orchestrator.external_observer, "run", side_effect=observer), \
             mock.patch.object(orchestrator, "_verify_live_baseline") as verify:
            thread.return_value.is_alive.return_value = False
            with self.assertRaisesRegex(orchestrator.OrchestratorError, "bench smoke stopped"):
                orchestrator.smoke(options)
        board.restore.assert_called_once()
        verify.assert_called_once()
        summaries = list(options.output_root.glob("*/smoke-summary.json"))
        self.assertEqual(len(summaries), 1)
        saved = json.loads(summaries[0].read_text())
        self.assertEqual(saved["diagnosis"], "no_serial_output_board_not_running")
        self.assertIn("bench smoke failed", saved["error"])
        self.assertEqual(saved["restored_sha256"], "a" * 64)

    def test_smoke_diagnosis_separates_wifi_firewall_and_crash_failures(self):
        ok = {"counters": {"telemetry_valid": 3}, "valid_controls_by_boot": {"7": 2}}
        silent = {"counters": {}, "valid_controls_by_boot": {}}
        receive = [{"kind": "receive"}]
        cases = (
            (b"wifi_ip_acquired udp_bound", receive, ok, "pass"),
            (b"wifi_ip_acquired udp_bound abort()", receive, ok, "firmware_crash_or_reboot_loop"),
            (b"wifi_ip_acquired udp_bound", receive, {**ok, "boot_ids": ["7", "8"]},
             "firmware_crash_or_reboot_loop"),
            (b"", receive, ok, "no_serial_output_board_not_running"),
            (b"", [], silent, "no_serial_output_board_not_running"),
            (b'WASMHOME_WIFI,{"reason":210} wifi_disconnected', [], silent, "wifi_not_connected"),
            (b"wifi_ip_acquired", [], silent, "udp_socket_not_bound"),
            (b"wifi_ip_acquired udp_bound", [], silent,
             "no_datagram_reached_observer_check_firewall_and_observer_ip"),
            (b"rst:0x1 rst:0xc Guru Meditation", [], silent, "firmware_crash_or_reboot_loop"),
        )
        for uart, observations, summary, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(orchestrator._smoke_diagnosis(uart, observations, summary), expected)

    def test_remote_staging_directory_creation_uses_private_umask(self):
        ssh = mock.Mock()
        server = orchestrator.RemoteFixtureServer(
            ssh, fixture_root=self.root / "fixtures", package_root=self.root / "package",
            fixture_trusted=self.root / "trusted.der", fixture_untrusted=self.root / "untrusted.der",
            ota_public=self.root / "ota.der", certificate=self.root / "server.crt",
            private_key=self.root / "server.key", campaign_root=self.root / "campaign",
            server_ip="192.0.2.8", port=18445, server_name="wasmhome-issue22.test",
            toolchain="wamr-test")
        target = self.root / "campaign" / "fixtures" / "resilience" / "scenario-1"
        server._stage_mkdir_private(None, target)
        command = ssh.run.call_args.args[0]
        self.assertEqual(command[:2], ["python3", "-c"])
        self.assertIn("os.umask(0o077)", command[2])
        self.assertIn("mkdir(mode=0o700, parents=True, exist_ok=True)", command[2])
        self.assertEqual(command[-1], str(target))

    def test_bootloader_requires_complete_segments_checksum_digest_and_no_tail(self):
        data = self.valid_bootloader_bytes()
        validation = orchestrator._validate_bootloader_bytes(data, "native")
        self.assertEqual(validation["segments"], 2)
        self.assertEqual(validation["bytes"], len(data))
        corrupt_checksum = bytearray(data)
        corrupt_checksum[-33] ^= 1
        corrupt_digest = bytearray(data)
        corrupt_digest[-1] ^= 1
        corrupt_segment = bytearray(data)
        corrupt_segment[28:32] = (0xFFFFFFFF).to_bytes(4, "little")
        for invalid in (data[:24], data[:-1], data + b"tail", bytes(corrupt_checksum),
                        bytes(corrupt_digest), bytes(corrupt_segment),
                        self.valid_bootloader_bytes(description_magic=0x51)):
            with self.subTest(bytes=len(invalid)):
                with self.assertRaises(orchestrator.OrchestratorError):
                    orchestrator._validate_bootloader_bytes(invalid, "native")

    def test_bootloader_rejects_invalid_or_overlapping_segment_load_ranges(self):
        # These retain complete segments, valid checksum and valid SHA-256 so
        # address validation, rather than incidental corruption, rejects them.
        cases = (
            (0, b"\0" * 4, "zero, unaligned or overflowing"),
            (0xFFFFFFFC, b"\0" * 8, "zero, unaligned or overflowing"),
            (0x403C9711, b"\0" * 4, "zero, unaligned or overflowing"),
            (0x42000000, b"\0" * 4, "outside the pinned linker windows"),
            (0x60000000, b"\0" * 4, "outside the pinned linker windows"),
            (0x3FC88000, b"\0" * 4, "outside the pinned linker windows"),
            (0x3FCE76FC, b"\0" * 8, "outside the pinned linker windows"),
            (0x403D36FC, b"\0" * 8, "outside the pinned linker windows"),
            (0x403C9708, b"\0" * 8, "address ranges overlap"),
            (0x3FCE3700, b"\0" * 4, "address ranges overlap"),
            # The alias of an IRAM allocation is outside the DRAM linker window.
            (0x3FCD9700, b"\0" * 4, "outside the pinned linker windows"),
            (0x403C9710, b"", "truncated or malformed segment"),
        )
        for address, payload, reason in cases:
            with self.subTest(address=hex(address), length=len(payload)):
                image = self.valid_bootloader_bytes(extra_segments=((address, payload),))
                with self.assertRaisesRegex(orchestrator.OrchestratorError, reason):
                    orchestrator._validate_bootloader_bytes(image, "native")
        adjacent = self.valid_bootloader_bytes(extra_segments=((0x403C9710, b"\0" * 4),))
        self.assertEqual(orchestrator._validate_bootloader_bytes(adjacent, "native")["segments"], 3)
        # elf2image may merge across the contiguous IRAM/loader logical split.
        merged_iram = self.valid_bootloader_bytes(extra_segments=((0x403CC6FC, b"\0" * 8),))
        self.assertEqual(orchestrator._validate_bootloader_bytes(merged_iram, "native")["segments"], 3)

    def test_generated_config_does_not_accept_comment_or_inactive_rollback(self):
        parser = orchestrator.bootstrap_validation.generated_config_defines
        self.assertEqual(parser("/*\n#define CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE 1\n*/\n"), {})
        self.assertEqual(parser('#pragma once\n#define CONFIG_LABEL "//literal/*text*/"\n'),
                         {"CONFIG_LABEL": '"//literal/*text*/"'})
        for invalid in ("#if 0\n#define CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE 1\n#endif\n",
                        "#undef CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE\n",
                        '#include "other.h"\n',
                        "#define CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE 1\n" * 2,
                        "/* unterminated\n#define CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE 1\n"):
            with self.subTest(config=invalid):
                with self.assertRaises(orchestrator.bootstrap_validation.BootstrapFormatError):
                    parser(invalid)

    def test_scp_translates_ssh_port_to_uppercase_p(self):
        identity = self.root / "identity"
        known_hosts = self.root / "known_hosts"
        source = self.root / "file.bin"
        for path in (identity, known_hosts, source):
            path.write_bytes(b"private" if path == identity else b"x")
        identity.chmod(0o600)
        session = orchestrator.SSHSession("wasmhome@192.0.2.8", identity,
                                          known_hosts, 2222, "192.0.2.8")
        with mock.patch.object(orchestrator.subprocess, "run") as run:
            run.return_value.returncode = 0
            session.scp(source, "/home/wasmhome/file.bin")
        args = run.call_args.args[0]
        self.assertIn("-P", args)
        self.assertEqual(args[args.index("-P") + 1], "2222")
        self.assertNotIn("-p", args)

    def test_remote_server_scripts_are_syntax_checked_and_legacy_lifecycle_is_disabled(self):
        fixture_root = self.root / "fixtures"
        package_root = self.root / "package"
        fixture_root.mkdir()
        package_root.mkdir()
        (fixture_root / "fixture.bin").write_bytes(b"fixture")
        (package_root / "signed.bin").write_bytes(b"package")
        sources = {}
        for name in ("trusted.pub", "untrusted.pub", "ota.pub", "ca.pem", "server.crt", "server.key"):
            path = self.root / name
            path.write_bytes(b"material")
            path.chmod(0o600 if name == "server.key" else 0o644)
            sources[name] = path

        class FakeSSH:
            def __init__(self):
                self.commands = []

            def run(self, command, **_kwargs):
                self.commands.append(command)
                return orchestrator.subprocess.CompletedProcess(command, 0, b"", b"")

            def scp(self, _source, _destination, **_kwargs):
                return None

        ssh = FakeSSH()
        server = orchestrator.RemoteFixtureServer(ssh, fixture_root=fixture_root,
            package_root=package_root, fixture_trusted=sources["trusted.pub"],
            fixture_untrusted=sources["untrusted.pub"], ota_public=sources["ota.pub"],
            certificate=sources["server.crt"], private_key=sources["server.key"],
            campaign_root=Path("/home/wasmhome/.local/share/wasmhome-hil/issue22/test-campaign"),
            server_ip="192.0.2.8", port=18445, server_name="wasmhome-issue22.test",
            toolchain="wamr-test", manager_spec={"server_kind": "issue22-fixture"})

        @contextmanager
        def reservation(_spec):
            class LiveReservation:
                @staticmethod
                def poll():
                    return None

            yield LiveReservation()

        with mock.patch.object(server, "preflight", return_value={
                "manager_receipt_present": False, "server_pid_receipt_present": False,
                "port_owners": [], "campaign_root_exists": False}), \
             mock.patch.object(orchestrator.user_server_manager, "remote_reservation",
                               side_effect=reservation):
            argv = server.stage()
        self.assertEqual(argv[1], str(server.root / "tools/resilience_fixture_https_server.py"))
        self.assertFalse(server.ready)
        with self.assertRaisesRegex(orchestrator.OrchestratorError,
                                    "direct fixture-server start is disabled"):
            server.start()
        with self.assertRaisesRegex(orchestrator.OrchestratorError,
                                    "direct fixture-server stop is disabled"):
            server.stop()
        for command in ssh.commands:
            if command[:2] == ["python3", "-c"]:
                compile(command[2], "remote-python-embedded", "exec")
        self.assertTrue(any(command[-1] == "--check" for command in ssh.commands))
        self.assertFalse(any(command[:2] == ["sh", "-c"] for command in ssh.commands))

    def test_board_operation_receipts_are_reused_after_resume(self):
        operation = self.root / "board-operations"
        operation.mkdir(mode=0o700)
        (operation / "esptool-0007.log").write_text("prior receipt")
        board = orchestrator.Board(object(), operation)
        self.assertEqual(board.index, 7)

    def test_claim_history_is_durable_one_shot_prefix(self):
        campaign = self.root / "campaign"
        orchestrator.private_directory(campaign)
        rows = resilience_preliminary_plan("orchestrator-test-seed")
        path = orchestrator._claim(campaign, rows[0])
        self.assertTrue(path.is_file())
        self.assertEqual(orchestrator._verify_claims_match_plan(campaign, rows), 1)
        self.assertFalse(json.loads(path.read_text())["retry_permitted"])

    def test_preliminary_matrix_is_exactly_sixteen_rows(self):
        plan_path = self.root / "plan.jsonl"
        rows = orchestrator.write_plan(plan_path, "issue22-scope-test")
        self.assertEqual(len(rows), 16)
        self.assertEqual([row["plan_index"] for row in rows], list(range(1, 17)))

    def test_observer_uses_v1_before_ota_and_profile_version_after_boot(self):
        options = type("Options", (), {
            "device_ip": "192.0.2.20", "bind_ip": "192.0.2.10",
            "timeout_ms": 300,
        })()
        summary = {"boot_ids": [22], "valid_controls_by_boot": {"22": 1},
                   "counters": {"telemetry_valid": 1}, "initial_version": 1}
        with mock.patch.object(orchestrator.external_observer, "run", return_value=summary) as run:
            orchestrator._observer_once(options, self.root, seconds=60,
                profile="native", attempt_id="00000000-0000-0000-0000-000000000001", trigger=True)
        self.assertEqual(run.call_args.kwargs["initial_version"], 1)
        self.assertEqual(run.call_args.kwargs["target_sequence"], 2)
        self.assertEqual(orchestrator._validate_observer_boot(summary, expected_version=1),
                         "0000000000000016")
        with self.assertRaisesRegex(orchestrator.OrchestratorError, "expected firmware profile"):
            orchestrator._validate_observer_boot(summary, expected_version=2)

    def test_native_provisioning_requires_acceptance_without_policy_version_change(self):
        valid = {"accepted_ns": 100, "activated_ns": None,
                 "update_state": "accepted_without_v2"}
        orchestrator._validate_provisioning_acceptance(valid, profile="native")
        for invalid in ({**valid, "accepted_ns": None},
                        {**valid, "accepted_ns": True},
                        {**valid, "accepted_ns": -1},
                        {**valid, "update_state": "no_timely_acceptance"},
                        {**valid, "update_state": "activated", "activated_ns": 200},
                        {**valid, "activated_ns": 200}):
            with self.subTest(summary=invalid):
                with self.assertRaises(orchestrator.OrchestratorError):
                    orchestrator._validate_provisioning_acceptance(invalid, profile="native")

    def test_module_provisioning_requires_timely_acceptance_and_activation(self):
        valid = {"accepted_ns": 100, "activated_ns": 200, "update_state": "activated"}
        for profile in ("interp", "aot"):
            orchestrator._validate_provisioning_acceptance(valid, profile=profile)
            for invalid in ({**valid, "accepted_ns": None},
                            {**valid, "activated_ns": None},
                            {**valid, "activated_ns": 99},
                            {**valid, "activated_ns": True},
                            {"accepted_ns": 100, "activated_ns": None,
                             "update_state": "accepted_without_v2"}):
                with self.subTest(profile=profile, summary=invalid):
                    with self.assertRaises(orchestrator.OrchestratorError):
                        orchestrator._validate_provisioning_acceptance(invalid, profile=profile)

    def test_native_acceptance_still_requires_post_boot_nvs_preflight_validation(self):
        row = resilience_preliminary_plan("native-provisioning-ledger-gate")[0]
        self.assertEqual(row["scenario"], "native")
        operation = self.root / "operation"
        operation.mkdir()
        board = mock.Mock()
        nvs = b"\0" * orchestrator.NVS_BYTES
        flash_size = 0x100000
        flash = bytearray(b"\xff" * flash_size)
        flash[orchestrator.NVS_OFFSET:orchestrator.NVS_OFFSET + len(nvs)] = nvs
        flash[orchestrator.TABLE_OFFSET:orchestrator.TABLE_OFFSET + orchestrator.TABLE_BYTES] = self.valid_partition_table()

        def read(offset, _size, destination, **_kwargs):
            destination.write_bytes(nvs if offset == orchestrator.NVS_OFFSET else flash)

        board.read.side_effect = read
        accepted = {"accepted_ns": 100, "activated_ns": None,
                    "update_state": "accepted_without_v2", "boot_ids": [11, 12]}
        post_boot = {"boot_ids": [13], "valid_controls_by_boot": {"13": 1},
                     "counters": {"telemetry_valid": 1}, "initial_version": 1}
        preflight = {"sentinel": "real ledger validation remains mandatory"}
        partition_table = self.root / "partition-table.bin"
        partition_table.write_bytes(self.valid_partition_table())
        profiles = {"partition_table": {"path": str(partition_table)},
                    "profiles": {"native": {"path": "/unused/app.bin",
                    "bootloader": {"path": "/unused/bootloader.bin"}}}}
        with mock.patch.object(orchestrator, "FLASH_BYTES", flash_size), \
             mock.patch.object(orchestrator, "_observer_once", side_effect=[accepted, post_boot]), \
             mock.patch.object(orchestrator.nvs_preflight, "build_preflight", return_value=preflight) as build, \
             mock.patch.object(orchestrator.hil_runner, "validate_preflight",
                side_effect=orchestrator.hil_runner.HILInputError("pending ledger rejected")) as validate:
            with self.assertRaisesRegex(orchestrator.hil_runner.HILInputError, "pending ledger rejected"):
                orchestrator._prepare_profile(object(), board, row, profiles, object(), operation,
                    self.root / "baseline.bin", "a" * 64)
        build.assert_called_once_with(nvs, profile="native", boot_id="000000000000000d")
        validate.assert_called_once_with(preflight, row)
        self.assertEqual(board.run.call_count, 2)

    def test_post_esptool_boot_requires_new_identity_and_fresh_both_services(self):
        valid = {"boot_ids": [22], "valid_controls_by_boot": {"22": 1},
                 "counters": {"telemetry_valid": 1}, "initial_version": 1}
        self.assertEqual(orchestrator._validate_observer_boot(valid,
            expected_version=1, previous_boot_ids=[20, 21]), "0000000000000016")
        for previous in ([22], [], [True]):
            with self.subTest(previous_boots=previous):
                with self.assertRaises(orchestrator.OrchestratorError):
                    orchestrator._validate_observer_boot(valid,
                        expected_version=1, previous_boot_ids=previous)
        for invalid in ({**valid, "valid_controls_by_boot": {}},
                        {**valid, "counters": {"telemetry_valid": 0}}):
            with self.assertRaises(orchestrator.OrchestratorError):
                orchestrator._validate_observer_boot(invalid,
                    expected_version=1, previous_boot_ids=[21])

    def test_parser_has_recovery_actions_and_high_unprivileged_port(self):
        options = orchestrator.build_parser().parse_args([])
        self.assertEqual(orchestrator.PORT, 18445)
        self.assertEqual(options.https_port, 18445)
        self.assertEqual(options.ct_target, "wasmhome@192.168.0.138")
        self.assertEqual(options.ct_host_key_alias, "192.168.0.138")
        self.assertIsNone(options.ct_jump_target)
        self.assertEqual(options.ct_jump_ssh_port, 22)

    def test_restore_runs_even_when_restoring_journal_write_fails(self):
        campaign = self.root / "campaign"
        orchestrator.private_directory(campaign)
        baseline = self.root / "baseline.bin"
        baseline.write_bytes(b"baseline")
        baseline_hash = hashlib.sha256(b"baseline").hexdigest()
        journal_path = campaign / "journal.json"
        orchestrator.write_json(journal_path, {
            "schema": orchestrator.JOURNAL_SCHEMA, "state": "ready",
            "restore_required": False, "next_index": 0, "slots": [],
        }, exclusive=True)
        row = resilience_preliminary_plan("restore-journal-test")[0]

        class FakeBoard:
            def __init__(self):
                self.restore_count = 0

            def identify(self):
                pass

            def read(self, offset, size, destination, **_kwargs):
                destination.write_bytes(b"baseline" if offset == 0 else b"nvs")

            def verify_image(self, *_args):
                pass

            def restore(self, *_args, **_kwargs):
                self.restore_count += 1

        board = FakeBoard()
        original_write = orchestrator.write_json

        def failing_journal(path, value, *, exclusive=False):
            if Path(path) == journal_path and value.get("state") == "restoring":
                raise OSError("simulated journal storage failure")
            return original_write(path, value, exclusive=exclusive)

        options = type("Options", (), {"serial_port": "/dev/null", "serial_baud": 115200,
            "device_ip": "192.0.2.20", "bind_ip": "192.0.2.10", "timeout_ms": 300,
            "trial_timeout_seconds": 60})()
        with mock.patch.object(orchestrator, "_verify_nvs_not_already_updated"), \
             mock.patch.object(orchestrator, "_prepare_profile",
                               side_effect=RuntimeError("stop after write phase starts")), \
             mock.patch.object(orchestrator, "write_json", side_effect=failing_journal):
            with self.assertRaisesRegex(orchestrator.OrchestratorError, "one-shot claim"):
                orchestrator._run_row(options, row, self.root / "plan.jsonl", campaign,
                    board, {"profiles": {}}, object(), baseline, baseline_hash)
        self.assertEqual(board.restore_count, 1)
        saved = json.loads(journal_path.read_text())
        self.assertFalse(saved["restore_required"])
        self.assertEqual(saved["next_index"], 1)

    def test_rehearsal_restores_records_failure_and_never_claims(self):
        self._check_rehearsal_restore(False)

    def test_rehearsal_restore_failure_keeps_recovery_required_and_never_claims(self):
        self._check_rehearsal_restore(True)

    def _check_rehearsal_restore(self, restore_fails):
        campaign = self.root / "rehearsal"
        orchestrator.private_directory(campaign)
        baseline = self.root / "baseline.bin"
        baseline.write_bytes(b"baseline")
        baseline_hash = hashlib.sha256(b"baseline").hexdigest()
        journal_path = campaign / "journal.json"
        orchestrator.write_json(journal_path, {
            "schema": orchestrator.JOURNAL_SCHEMA, "state": "ready", "rehearsal": True,
            "restore_required": False, "next_index": 0, "slots": [],
        }, exclusive=True)
        row = resilience_preliminary_plan("rehearsal-test")[0]

        class FakeBoard:
            restore_count = 0

            def identify(self):
                pass

            def verify_image(self, *_args):
                pass

            def read(self, _offset, _size, destination, **_kwargs):
                destination.write_bytes(b"nvs")

            def restore(self, *_args, **_kwargs):
                self.restore_count += 1
                if restore_fails:
                    raise RuntimeError("restore disconnected")

        board = FakeBoard()
        with mock.patch.object(orchestrator, "_verify_nvs_not_already_updated"), \
             mock.patch.object(orchestrator, "_prepare_profile",
                               side_effect=RuntimeError("ledger still pending")):
            if restore_fails:
                with self.assertRaisesRegex(orchestrator.OrchestratorError,
                                            "rehearsal restoration failed"):
                    orchestrator._rehearse_row(object(), row, campaign, board,
                                              {"profiles": {}}, object(), baseline, baseline_hash)
                saved = json.loads(journal_path.read_text())
                self.assertTrue(saved["restore_required"])
                self.assertEqual(saved["state"], "restore_failed")
                self.assertIn("restore disconnected", saved["last_error_message"])
                self.assertEqual(saved["rehearsals"][0]["state"], "setup_failed")
                self.assertEqual(board.restore_count, 1)
                self.assertEqual(saved["next_index"], 0)
                self.assertFalse((campaign / "claims").exists())
                return
            result = orchestrator._rehearse_row(object(), row, campaign, board,
                                                {"profiles": {}}, object(), baseline, baseline_hash)
        self.assertEqual(result["state"], "setup_failed")
        self.assertIn("ledger still pending", result["error_message"])
        self.assertEqual(board.restore_count, 1)
        saved = json.loads(journal_path.read_text())
        self.assertFalse(saved["restore_required"])
        self.assertEqual(saved["next_index"], 0)
        self.assertEqual(saved["rehearsals"][0]["state"], "setup_failed")
        self.assertFalse((campaign / "claims").exists())

    def test_read_only_baseline_mismatch_does_not_flash_overwrite(self):
        campaign = self.root / "campaign"
        orchestrator.private_directory(campaign)
        baseline = self.root / "baseline.bin"
        baseline.write_bytes(b"accepted")
        baseline_hash = hashlib.sha256(b"accepted").hexdigest()
        journal_path = campaign / "journal.json"
        orchestrator.write_json(journal_path, {
            "schema": orchestrator.JOURNAL_SCHEMA, "state": "ready",
            "restore_required": False, "next_index": 0, "slots": [],
        }, exclusive=True)
        row = resilience_preliminary_plan("baseline-mismatch-test")[0]

        class FakeBoard:
            restore_count = 0

            def identify(self):
                pass

            def read(self, _offset, _size, destination, **_kwargs):
                destination.write_bytes(b"not-accepted")

            def verify_image(self, *_args):
                raise orchestrator.OrchestratorError(
                    "board full-flash MD5 differs from the #20 accepted baseline")

            def restore(self, *_args, **_kwargs):
                self.restore_count += 1

        board = FakeBoard()
        options = type("Options", (), {"serial_port": "/dev/null", "serial_baud": 115200,
            "device_ip": "192.0.2.20", "bind_ip": "192.0.2.10", "timeout_ms": 300,
            "trial_timeout_seconds": 60})()
        with self.assertRaisesRegex(orchestrator.OrchestratorError, "one-shot claim"):
            orchestrator._run_row(options, row, self.root / "plan.jsonl", campaign,
                board, {"profiles": {}}, object(), baseline, baseline_hash)
        self.assertEqual(board.restore_count, 0)
        saved = json.loads(journal_path.read_text())
        self.assertFalse(saved["restore_required"])
        self.assertEqual(saved["next_index"], 1)
        self.assertIn("MD5 differs", saved["slots"][0]["error_message"])
        self.assertTrue(Path(saved["slots"][0]["traceback"]).is_file())


if __name__ == "__main__":
    unittest.main()
