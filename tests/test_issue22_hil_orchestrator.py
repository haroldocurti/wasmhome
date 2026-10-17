import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import tempfile
import unittest
from unittest import mock

from tools import issue22_hil_orchestrator as orchestrator
from tools.resilience_harness import resilience_preliminary_plan


class Issue22OrchestratorTests(unittest.TestCase):
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
    def valid_partition_table():
        import csv

        entries = []
        with (orchestrator.ROOT / "partitions.csv").open(newline="", encoding="utf-8") as stream:
            for row in csv.reader(line for line in stream if line.strip() and not line.lstrip().startswith("#")):
                name, _kind, _subtype, offset, size, *_ = (item.strip() for item in row)
                entries.append(struct.pack("<HBBII16sI", 0x50AA, 1, 0, int(offset, 0),
                                           int(size, 0), name.encode().ljust(16, b"\0"), 0))
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
        with self.assertRaisesRegex(orchestrator.OrchestratorError, "SHA-256"):
            orchestrator.validate_accepted_baseline(image, "0" * 64)

    def test_profile_manifest_binds_endpoint_timeout_and_ca_to_app_bytes(self):
        ca = self.root / "ca.pem"
        ca_bytes = b"-----BEGIN CERTIFICATE-----\nsynthetic test CA\n-----END CERTIFICATE-----\n"
        ca.write_bytes(ca_bytes)
        ca_hash = hashlib.sha256(ca_bytes).hexdigest()

        def write_manifest(native_port=18445, native_rollback=True, module_rollback=True,
                           native_update_port=18445):
            profiles = {}
            for name in ("native", "interp", "aot"):
                port = native_port if name == "native" else 18445
                config = self.root / f"{name}-sdkconfig"
                rollback = native_rollback if name == "native" else module_rollback
                config.write_text(('CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y\n' if rollback else
                                   '# CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE is not set\n') +
                                  'CONFIG_IDF_TARGET="esp32s3"\n'
                                  'CONFIG_ESPTOOLPY_FLASHSIZE_16MB=y\n'
                                  'CONFIG_PARTITION_TABLE_OFFSET=0x8000\n')
                config_hash = hashlib.sha256(config.read_bytes()).hexdigest()
                update_port = native_update_port if name == "native" else 18445
                marker = ("WASMHOME_ISSUE22_BUILD_V2\n"
                    f"profile={name}\nfunctional_timeout_ms=450\nserver_ipv4=192.0.2.8\n"
                    f"server_port={port}\nserver_name=wasmhome-issue22.test\nca_sha256={ca_hash}\n"
                    f"update_server_ipv4=192.0.2.8\nupdate_server_port={update_port}\n"
                    "update_server_name=wasmhome-issue22.test\n"
                    f"sdkconfig_sha256={config_hash}\napp_rollback_enabled={1 if rollback else 0}\n"
                    "END_WASMHOME_ISSUE22_BUILD_V2")
                header = bytearray(24)
                header[0] = 0xE9
                header[1] = 1
                header[12:14] = (9).to_bytes(2, "little")
                app = self.root / f"{name}.bin"
                app_bytes = bytes(header) + marker.encode() + ca_bytes
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
                "profiles": profiles,
                "source_commit": "a" * 40,
            }))
            return manifest

        manifest = write_manifest()
        value = orchestrator.validate_profile_manifest(manifest, 450, "192.0.2.8",
            18445, "wasmhome-issue22.test", ca)
        self.assertEqual(value["profiles"]["native"]["build_provenance"]["server_port"], 18445)

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

    def test_overlay_installs_bootloader_before_apps_and_verifies_before_reset(self):
        app = self.root / "app.bin"
        bootloader = self.root / "bootloader.bin"
        app.write_bytes(b"A" * 24)
        bootloader.write_bytes(self.valid_bootloader_bytes())
        board = orchestrator.Board(object(), self.root)

        def readback(_offset, _size, path, **_kwargs):
            path.write_bytes(bootloader.read_bytes())

        with mock.patch.object(board, "command") as command, \
             mock.patch.object(board, "read", side_effect=readback) as read:
            board.overlay_profile(app, bootloader)
        self.assertEqual(command.call_args.args, ("write_flash", "0x0", str(bootloader),
            hex(orchestrator.OTA_OFFSETS[0]), str(app), hex(orchestrator.OTA_OFFSETS[1]), str(app)))
        self.assertEqual(read.call_args.args[:2], (0, len(self.valid_bootloader_bytes())))

        def wrong_readback(_offset, _size, path, **_kwargs):
            path.write_bytes(b"C" * 24)

        with mock.patch.object(board, "command") as command, \
             mock.patch.object(board, "read", side_effect=wrong_readback):
            with self.assertRaisesRegex(orchestrator.OrchestratorError, "refusing reset"):
                board.overlay_profile(app, bootloader)
        self.assertEqual(command.call_count, 1)

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

    def test_remote_server_scripts_are_syntax_checked_and_start_uses_process_receipt(self):
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
            toolchain="wamr-test")
        server.start()
        self.assertTrue(server.ready)
        server.stop()
        self.assertFalse(server.ready)
        shell_commands = []
        for command in ssh.commands:
            if command[:2] == ["python3", "-c"]:
                compile(command[2], "remote-python-embedded", "exec")
            if command[:2] == ["sh", "-c"]:
                shell_commands.append(command[2])
                checked = orchestrator.subprocess.run(["sh", "-n", "-c", command[2]],
                    capture_output=True, check=False)
                self.assertEqual(checked.returncode, 0, checked.stderr.decode(errors="replace"))
        self.assertTrue(any("--pid-file" in command and "nohup" in command
                            for command in shell_commands))
        self.assertTrue(any("process identity differs" in command
                            for command in shell_commands))

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

    def test_parser_has_recovery_actions_and_high_unprivileged_port(self):
        options = orchestrator.build_parser().parse_args([])
        self.assertEqual(orchestrator.PORT, 18445)
        self.assertEqual(options.https_port, 18445)
        self.assertEqual(options.ct_target, "wasmhome@192.168.0.138")
        self.assertEqual(options.ct_host_key_alias, "192.168.0.138")

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


if __name__ == "__main__":
    unittest.main()
