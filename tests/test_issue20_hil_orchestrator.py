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

    def test_overlay_command_can_only_write_table_and_app(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            table, app = root / "table.bin", root / "app.bin"
            table.write_bytes(table_bytes())
            raw = bytearray(24)
            raw[0:2], raw[12:14] = bytes((0xe9, 1)), (9).to_bytes(2, "little")
            app.write_bytes(raw)
            profile = {kind: {"path": str(path), "sha256": hil.digest(path)}
                       for kind, path in (("partition_table", table), ("app", app))}
            board = hil.Board(argparse.Namespace(), root)
            board.command = Mock()
            board.overlay(profile)
            board.command.assert_called_once_with("write_flash", "0x8000", table,
                                                  "0x20000", app, after="hard_reset")
            profile["app"]["sha256"] = "0" * 64
            board.command.reset_mock()
            with self.assertRaises(hil.HilError):
                board.overlay(profile)
            board.command.assert_not_called()

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
