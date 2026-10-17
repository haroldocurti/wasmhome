import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools import issue20_hil_orchestrator as hil


def table_bytes(rows=hil.PARTITIONS):
    raw = b"".join(struct.pack("<HBBII16sI", 0x50aa, kind, sub, start, size,
                               name.encode().ljust(16, b"\0"), 0)
                   for name, kind, sub, start, size in rows)
    return (raw + b"\xeb\xeb" + b"\xff" * 14 + hashlib.md5(raw).digest()).ljust(0xc00, b"\xff")


def image_bytes():
    image = bytearray(24)
    image[0:2], image[12:14] = bytes((0xe9, 1)), (9).to_bytes(2, "little")
    return image


def bootloader_bytes(*, chip_id=9):
    descriptor = bytearray(80)
    descriptor[0] = 80
    descriptor[4:8] = (1).to_bytes(4, "little")
    descriptor[8:16] = b"v5.2.6\0\0"
    image = bytearray(struct.pack("<BBBBI", 0xe9, 2, 2, 0x4f, 0x40370000))
    image.extend(b"\0" * 16)
    image[12:14] = chip_id.to_bytes(2, "little")
    image[23] = 1
    image.extend(struct.pack("<II", 0x3fce0000, len(descriptor)))
    image.extend(descriptor)
    executable = b"\x00" * 4
    image.extend(struct.pack("<II", 0x40370000, len(executable)))
    image.extend(executable)
    checksum = 0xef
    for byte in descriptor + executable:
        checksum ^= byte
    checksum_offset = (len(image) // 16 + 1) * 16 - 1
    image.extend(b"\0" * (checksum_offset - len(image)))
    image.append(checksum)
    image.extend(hashlib.sha256(image).digest())
    return image


def config_bytes(*, rollback=True):
    text = ('#define CONFIG_IDF_TARGET "esp32s3"\n'
            '#define CONFIG_IDF_TARGET_ESP32S3 1\n'
            '#define CONFIG_ESPTOOLPY_FLASHSIZE "16MB"\n'
            '#define CONFIG_ESPTOOLPY_FLASHSIZE_16MB 1\n'
            '#define CONFIG_PARTITION_TABLE_OFFSET 0x8000\n')
    if rollback:
        text += '#define CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE 1\n'
    return text.encode()


def profile_files(root, *, app_rollback=True, bootloader_rollback=True):
    result = {}
    for kind, data in (("bootloader", bootloader_bytes()), ("partition_table", table_bytes()),
                       ("app", image_bytes()), ("app_config", config_bytes(rollback=app_rollback)),
                       ("bootloader_config", config_bytes(rollback=bootloader_rollback))):
        path = root / (kind + (".h" if kind.endswith("_config") else ".bin"))
        path.write_bytes(data)
        result[kind] = {"path": str(path), "sha256": hil.digest(path)}
    return result


class HilArtifactTests(unittest.TestCase):
    def test_offsets_layout_and_table_sector_are_strict(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "table.bin"
            path.write_bytes(table_bytes())
            hil.validate_table(path)
            rows = list(hil.PARTITIONS)
            rows[3] = ("ota_0", 0, 0x10, 0x10000, hil.APP_LIMIT)
            path.write_bytes(table_bytes(rows))
            with self.assertRaisesRegex(hil.HilError, "layout"):
                hil.validate_table(path)
            path.write_bytes(table_bytes() + b"\xff" * 0x1000)
            with self.assertRaisesRegex(hil.HilError, "sector"):
                hil.validate_table(path)

    def test_app_rejects_wrong_chip_and_partition_overflow(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "app.bin"
            image = bytearray(24)
            image[0:2] = bytes((0xe9, 1))
            image[12:14] = (9).to_bytes(2, "little")
            path.write_bytes(image)
            hil.validate_app(path)
            image[12:14] = (0).to_bytes(2, "little")
            path.write_bytes(image)
            with self.assertRaisesRegex(hil.HilError, "ESP32-S3"):
                hil.validate_app(path)
            with path.open("wb") as stream:
                stream.truncate(hil.APP_LIMIT + 1)
            with self.assertRaisesRegex(hil.HilError, "ota_0"):
                hil.validate_app(path)

    def test_bootloader_rejects_wrong_chip_and_partition_table_overlap(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bootloader.bin"
            path.write_bytes(bootloader_bytes())
            hil.validate_bootloader(path)
            path.write_bytes(bootloader_bytes(chip_id=0))
            with self.assertRaisesRegex(hil.HilError, "ESP32-S3"):
                hil.validate_bootloader(path)
            truncated = bootloader_bytes()[:-1]
            path.write_bytes(truncated)
            with self.assertRaisesRegex(hil.HilError, "digest"):
                hil.validate_bootloader(path)
            bad_checksum = bootloader_bytes()
            bad_checksum[40] ^= 1
            path.write_bytes(bad_checksum)
            with self.assertRaisesRegex(hil.HilError, "checksum mismatch"):
                hil.validate_bootloader(path)
            bad_digest = bootloader_bytes()
            bad_digest[-1] ^= 1
            path.write_bytes(bad_digest)
            with self.assertRaisesRegex(hil.HilError, "SHA-256 digest mismatch"):
                hil.validate_bootloader(path)
            path.write_bytes(b"\xff" * (hil.BOOTLOADER_LIMIT + 1))
            with self.assertRaisesRegex(hil.HilError, "partition table"):
                hil.validate_bootloader(path)

    def test_overlay_only_writes_frozen_segments_and_verifies_before_boot(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            profile = profile_files(root)
            board = hil.Board(argparse.Namespace(), root)
            board.command = Mock()
            board.overlay(profile, scenario="native")
            segments = ("0x0", root / "bootloader.bin", "0x8000", root / "partition_table.bin",
                        "0x20000", root / "app.bin")
            calls = board.command.call_args_list
            self.assertEqual(calls[0].args, ("write_flash", *segments))
            self.assertEqual(calls[1].args, ("verify_flash", *segments))
            self.assertEqual(calls[0].kwargs, {})
            self.assertEqual(calls[1].kwargs, {})
            self.assertEqual(calls[2].args, ("run",))
            self.assertEqual(calls[2].kwargs, {"after": "hard_reset"})
            self.assertEqual(len(calls), 3)
            profile["bootloader"]["sha256"] = "0" * 64
            board.command.reset_mock()
            with self.assertRaises(hil.HilError):
                board.overlay(profile, scenario="native")
            board.command.assert_not_called()

    def test_failed_verification_never_boots_prepared_profile(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            board = hil.Board(argparse.Namespace(), root)
            board.command = Mock(side_effect=["", hil.HilError("verification failed")])
            with self.assertRaisesRegex(hil.HilError, "verification failed"):
                board.overlay(profile_files(root), scenario="native")
            self.assertEqual([call.args[0] for call in board.command.call_args_list],
                             ["write_flash", "verify_flash"])

    def test_profiles_require_v2_and_bootloader_for_every_scenario(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            profile = profile_files(root)
            value = {"schema_version": 2,
                     "profiles": {scenario: profile.copy() for scenario in hil.campaign.SCENARIOS}}
            path = root / "profiles.json"
            path.write_text(json.dumps(value))
            self.assertEqual(hil.load_profiles(path)["schema_version"], 2)
            value["profiles"]["native"].pop("bootloader")
            path.write_text(json.dumps(value))
            with self.assertRaisesRegex(hil.HilError, "bootloader"):
                hil.load_profiles(path)
            value["schema_version"] = 1
            path.write_text(json.dumps(value))
            with self.assertRaisesRegex(hil.HilError, "schema"):
                hil.load_profiles(path)

    def test_generated_configs_reject_wrong_target_flash_and_rollback_mismatch(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for kind in ("app_config", "bootloader_config"):
                for old, new, expected in ((b'"esp32s3"', b'"esp32"', "ESP32-S3"),
                                           (b'"16MB"', b'"8MB"', "16 MiB"),
                                           (b'0x8000', b'0x9000', "partition table")):
                    with self.subTest(kind=kind, invalid=expected):
                        profile = profile_files(root)
                        path = Path(profile[kind]["path"])
                        path.write_bytes(path.read_bytes().replace(old, new))
                        profile[kind]["sha256"] = hil.digest(path)
                        with self.assertRaisesRegex(hil.HilError, expected):
                            hil.validate_profile(profile, scenario="native")
            for app_rollback, bootloader_rollback in ((True, False), (False, True)):
                profile = profile_files(root, app_rollback=app_rollback,
                                        bootloader_rollback=bootloader_rollback)
                with self.assertRaisesRegex(hil.HilError, "rollback configuration mismatch"):
                    hil.validate_profile(profile, scenario="native")
            profile = profile_files(root, app_rollback=False, bootloader_rollback=False)
            with self.assertRaisesRegex(hil.HilError, "native profile requires rollback"):
                hil.validate_profile(profile, scenario="native")
            for scenario in ("interp", "aot"):
                hil.validate_profile(profile, scenario=scenario)

    def test_generated_configs_are_mandatory_and_hash_checked_before_flash(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for kind in ("app_config", "bootloader_config"):
                with self.subTest(kind=kind):
                    profile = profile_files(root)
                    profile.pop(kind)
                    with self.assertRaisesRegex(hil.HilError, "invalid profile fields"):
                        hil.validate_profile(profile, scenario="native")
                    profile = profile_files(root)
                    Path(profile[kind]["path"]).write_bytes(b"changed generated config")
                    board = hil.Board(argparse.Namespace(), root)
                    board.command = Mock()
                    with self.assertRaisesRegex(hil.HilError, "SHA-256 mismatch"):
                        board.overlay(profile, scenario="native")
                    board.command.assert_not_called()

    def test_generated_config_rejects_ambiguous_or_invalid_rollback_definition(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sdkconfig.h"
            for definition in (b"#define CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE 0\n",
                               b"#undef CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE\n"):
                path.write_bytes(config_bytes(rollback=False) + definition)
                self.assertFalse(hil.validate_generated_config(path))
            for definition in (b"#define CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE\n",
                               b"#define CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE 2\n"):
                path.write_bytes(config_bytes() + definition)
                with self.assertRaises(hil.HilError):
                    hil.validate_generated_config(path)
            for inactive in (b"#if 0\n#define CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE 1\n#endif\n",
                             b"/*\n#define CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE 1\n*/\n"):
                path.write_bytes(config_bytes(rollback=False) + inactive)
                if inactive.startswith(b"#if"):
                    with self.assertRaisesRegex(hil.HilError, "conditional"):
                        hil.validate_generated_config(path)
                else:
                    self.assertFalse(hil.validate_generated_config(path))
            path.write_bytes(config_bytes().replace(b"ROLLBACK_ENABLE 1", b"ROLLBACK_ENABLE 2"))
            with self.assertRaisesRegex(hil.HilError, "invalid rollback"):
                hil.validate_generated_config(path)

    def test_new_journal_snapshots_bootloader_as_well_as_table_and_app(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            profile = profile_files(root)
            profiles = root / "profiles.json"
            profiles.write_text(json.dumps({"schema_version": 2,
                "profiles": {scenario: profile for scenario in hil.campaign.SCENARIOS}}))
            baseline = root / "baseline.bin"
            baseline.write_bytes(b"approved baseline fixture")
            journal_root = root / "journal"
            journal_root.mkdir(mode=0o700)
            options = argparse.Namespace(campaign_root=str(root), baseline=str(baseline), profiles=str(profiles))
            value = {"protocol_revision": hil.campaign.ISSUE20_PROTOCOL_REVISION, "N": 6,
                     "campaign_id": "new-campaign", "git_commit": "commit",
                     "metadata": {"campaign_type": "pilot", "hil_profiles_sha256": hil.digest(profiles)}}
            with patch.object(hil, "BASELINE_SHA256", hil.digest(baseline)), \
                 patch.object(hil, "FLASH_BYTES", baseline.stat().st_size), \
                 patch.object(hil.campaign, "_validate_repository"), \
                 patch.object(hil.campaign, "_load_campaign", return_value=(value, {"slots": []}, {})):
                journal = hil._new_journal(options, journal_root)
            self.assertEqual(journal["profiles_schema_version"], 2)
            for scenario in hil.campaign.SCENARIOS:
                for kind in ("bootloader", "partition_table", "app", "app_config", "bootloader_config"):
                    snapshot = Path(journal["profiles"][scenario][kind]["path"])
                    self.assertEqual(snapshot.parent, journal_root / "inputs")
                    self.assertEqual(hil.digest(snapshot), profile[kind]["sha256"])
                    self.assertEqual(snapshot.stat().st_mode & 0o777, 0o600)
                    self.assertEqual(snapshot.suffix, ".h" if kind.endswith("_config") else ".bin")
            for kind in ("bootloader", "app_config", "bootloader_config"):
                source = Path(profile[kind]["path"])
                source.write_bytes(b"changed source")
                self.assertNotEqual(hil.digest(source),
                                    hil.digest(Path(journal["profiles"]["native"][kind]["path"])))

    def test_identity_rejects_other_board_before_read_or_write(self):
        board = hil.Board(argparse.Namespace(expected_mac="dc:b4:d9:0b:22:e4"), Path("/unused"))
        board.command = Mock(return_value="Chip is ESP32-S3\nMAC: 00:00:00:00:00:00\nDetected flash size: 16MB")
        with self.assertRaisesRegex(hil.HilError, "mismatch"):
            board.identify()
        board.command.assert_called_once_with("flash_id")

    def test_full_read_has_conservative_budget_without_changing_write_budget(self):
        self.assertEqual(hil.parser().get_default("flash_baud"), 921600)
        self.assertEqual(hil.parser().get_default("read_timeout_seconds"), 600)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            baseline, readback = root / "baseline.bin", root / "readback.bin"
            with baseline.open("wb") as stream:
                stream.truncate(hil.FLASH_BYTES)
            board = hil.Board(argparse.Namespace(read_timeout_seconds=600), root)

            def emulate_command(*args, **kwargs):
                if args[0] == "read_flash":
                    with Path(args[-1]).open("wb") as stream:
                        stream.truncate(hil.FLASH_BYTES)
                return ""

            board.command = Mock(side_effect=emulate_command)
            with patch.object(hil, "digest", return_value=hil.BASELINE_SHA256):
                board.restore(baseline, readback)
            calls = board.command.call_args_list
            self.assertEqual(calls[0].args, ("write_flash", "0x0", baseline))
            self.assertEqual(calls[0].kwargs["timeout"], 240)
            self.assertEqual(calls[1].args, ("read_flash", "0x0", "0x1000000", readback))
            self.assertEqual(calls[1].kwargs["timeout"], 600)


class HilOrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.options = argparse.Namespace(action="run", campaign_root=str(self.root), profiles="/profiles",
            baseline="/baseline", expected_mac="dc:b4:d9:0b:22:e4", serial_port="/dev/ttyACM0",
            device_ip="192.168.0.145", python="/python", esptool="/esptool.py", flash_baud=921600,
            read_timeout_seconds=600,
            serial_baud=115200, bind_ip="192.168.0.138", remote_observer="wasmhome@192.168.0.138",
            remote_known_hosts="/known_hosts", remote_identity="/identity", remote_root=".local/share/hil",
            remote_ssh_port=22, remote_server_log="/home/wasmhome/http-events.jsonl", max_slots=2)
        self.slots = [{"slot_id": "B0001-interp", "scenario": "interp", "order_in_block": 1},
                      {"slot_id": "B0001-native", "scenario": "native", "order_in_block": 2}]
        self.journal = {"schema_version": 1, "campaign_id": "campaign", "git_commit": "commit",
                        "state": "ready", "restore_required": False, "next_index": 0,
                        "slots": self.slots, "baseline": "/baseline-snapshot",
                        "profiles_schema_version": 2,
                        "profiles": {name: {"selected": name} for name in hil.campaign.SCENARIOS},
                        "options": vars(self.options).copy(), "events": []}
        self.board = Mock()
        self.board.read_hash.return_value = hil.BASELINE_SHA256
        self.invoke = Mock()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        original_lock = hil.hardware_lock
        self.stack.enter_context(patch.object(hil, "hardware_lock", lambda: original_lock(self.root / "lock")))
        self.stack.enter_context(patch.object(hil.os, "geteuid", return_value=1000))
        self.stack.enter_context(patch.object(hil, "_new_journal", return_value=self.journal))
        self.stack.enter_context(patch.object(hil.campaign, "_load_campaign", return_value=(
            {"campaign_id": "campaign"}, {"slots": self.slots}, {})))
        self.stack.enter_context(patch.object(hil.campaign, "_validate_repository"))
        self.stack.enter_context(patch.object(hil, "validate_profile"))
        self.stack.enter_context(patch("tools.remote_observer.validate_options"))

    def run_hil(self):
        return hil.execute(self.options, board_factory=lambda *_: self.board, invoke_slot=self.invoke)

    def stored(self):
        path = self.root / "hil-orchestrator/journal.json"
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        return json.loads(path.read_text())

    def test_ordered_slots_restore_before_and_after_every_capture(self):
        result = self.run_hil()
        self.assertEqual([row.args[1]["slot_id"] for row in self.invoke.call_args_list],
                         [row["slot_id"] for row in self.slots])
        self.assertEqual([row.args[0]["selected"] for row in self.board.overlay.call_args_list], ["interp", "native"])
        self.assertEqual(self.board.restore.call_count, 4)
        self.assertEqual([row.kwargs.get("boot", True) for row in self.board.restore.call_args_list],
                         [False, False, False, False])
        self.assertEqual(result["completed_slots"], 2)
        self.assertFalse(self.stored()["restore_required"])
        # A repeated invocation cannot dispatch the same slots.
        self.run_hil()
        self.assertEqual(self.invoke.call_count, 2)

    def test_live_baseline_mismatch_refuses_before_flash(self):
        self.board.read_hash.return_value = "0" * 64
        with self.assertRaisesRegex(hil.HilError, "refusing writes"):
            self.run_hil()
        self.board.overlay.assert_not_called()
        self.board.restore.assert_not_called()
        self.invoke.assert_not_called()

    def test_legacy_journal_cannot_prepare_another_slot(self):
        self.journal.pop("profiles_schema_version")
        with self.assertRaisesRegex(hil.HilError, "legacy preparation profiles"):
            self.run_hil()
        self.board.identify.assert_not_called()
        self.board.overlay.assert_not_called()
        self.invoke.assert_not_called()

    def test_existing_raw_slot_refuses_before_board_access(self):
        (self.root / "raw/slots/B0001-interp").mkdir(parents=True)
        with self.assertRaisesRegex(hil.HilError, "no retry"):
            self.run_hil()
        self.board.identify.assert_not_called()
        self.invoke.assert_not_called()

    def test_capture_exception_always_restores_and_blocks_retry(self):
        self.invoke.side_effect = RuntimeError("simulated capture failure")
        with self.assertRaisesRegex(hil.HilError, "baseline restored"):
            self.run_hil()
        self.assertEqual(self.board.restore.call_count, 2)
        self.assertEqual(self.invoke.call_count, 1)
        self.assertEqual(self.stored()["state"], "slot_failed")
        with self.assertRaisesRegex(hil.HilError, "never repeat"):
            self.run_hil()
        self.assertEqual(self.invoke.call_count, 1)

    def test_overlay_exception_restores_without_dispatch(self):
        self.board.overlay.side_effect = OSError("simulated overlay failure")
        with self.assertRaises(hil.HilError):
            self.run_hil()
        self.assertEqual(self.board.restore.call_count, 2)
        self.invoke.assert_not_called()

    def test_restore_failure_blocks_batch_and_recovery_cannot_capture(self):
        self.board.restore.side_effect = [None, OSError("simulated failed restore")]
        with self.assertRaisesRegex(hil.HilError, "batch blocked"):
            self.run_hil()
        self.assertEqual(self.invoke.call_count, 1)
        self.assertTrue(self.stored()["restore_required"])
        self.assertEqual(self.stored()["state"], "restore_failed")
        self.board.restore.side_effect = None
        self.options.action = "recover"
        result = self.run_hil()
        self.assertEqual(result["state"], "recovered_interruption")
        self.assertEqual(self.invoke.call_count, 1)
        self.assertFalse(self.stored()["restore_required"])

    def test_keyboard_interrupt_restores_and_preserves_claim(self):
        self.invoke.side_effect = KeyboardInterrupt()
        with self.assertRaises(hil.HilError):
            self.run_hil()
        self.assertEqual(self.board.restore.call_count, 2)
        self.assertTrue((self.root / "hil-orchestrator/claims/B0001-interp.json").exists())

    def test_journal_write_error_in_finally_does_not_skip_restoration(self):
        original = hil.write_private

        def fail_restoring(path, value, **kwargs):
            if value.get("state") == "restoring":
                raise OSError("simulated full disk")
            return original(path, value, **kwargs)

        with patch.object(hil, "write_private", side_effect=fail_restoring):
            with self.assertRaisesRegex(hil.HilError, "baseline restored"):
                self.run_hil()
        self.assertEqual(self.board.restore.call_count, 2)
        self.assertEqual(self.invoke.call_count, 1)

    def test_negative_journal_index_is_rejected_before_board_access(self):
        self.journal["next_index"] = -1
        with self.assertRaisesRegex(hil.HilError, "index"):
            self.run_hil()
        self.board.identify.assert_not_called()

    def test_config_change_between_operations_is_refused(self):
        self.options.device_ip = "192.168.0.146"
        with self.assertRaisesRegex(hil.HilError, "configuration changed"):
            self.run_hil()
        self.board.identify.assert_not_called()

    def test_read_budget_validation_occurs_before_board_access(self):
        for invalid in (240, 3601, True):
            self.options.read_timeout_seconds = invalid
            with self.assertRaisesRegex(hil.HilError, "read-timeout-seconds"):
                self.run_hil()
        self.board.identify.assert_not_called()

    def test_legacy_ready_journal_can_use_longer_read_budget(self):
        self.journal["options"].pop("read_timeout_seconds")
        self.options.read_timeout_seconds = 900
        self.run_hil()
        self.assertEqual(self.invoke.call_count, 2)


class HilLockTests(unittest.TestCase):
    def test_competing_lock_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "lock"
            with hil.hardware_lock(root):
                with self.assertRaisesRegex(hil.HilError, "another HIL"):
                    with hil.hardware_lock(root):
                        self.fail("second owner acquired lock")
            with hil.hardware_lock(root):
                pass


if __name__ == "__main__":
    unittest.main()
