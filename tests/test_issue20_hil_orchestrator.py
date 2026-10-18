import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from contextlib import nullcontext
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


def config_bytes(*, rollback=True, server_port=None):
    text = ('#define CONFIG_IDF_TARGET "esp32s3"\n'
            '#define CONFIG_IDF_TARGET_ESP32S3 1\n'
            '#define CONFIG_ESPTOOLPY_FLASHSIZE "16MB"\n'
            '#define CONFIG_ESPTOOLPY_FLASHSIZE_16MB 1\n'
            '#define CONFIG_PARTITION_TABLE_OFFSET 0x8000\n')
    if rollback:
        text += '#define CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE 1\n'
    if server_port is not None:
        text += f'#define CONFIG_WASMHOME_UPDATE_SERVER_PORT {server_port}\n'
    return text.encode()


def profile_files(root, *, app_rollback=True, bootloader_rollback=True, server_port=8446):
    result = {}
    for kind, data in (("bootloader", bootloader_bytes()), ("partition_table", table_bytes()),
                       ("app", image_bytes()),
                       ("app_config", config_bytes(rollback=app_rollback, server_port=server_port)),
                       ("bootloader_config", config_bytes(rollback=bootloader_rollback))):
        path = root / (kind + (".h" if kind.endswith("_config") else ".bin"))
        path.write_bytes(data)
        result[kind] = {"path": str(path), "sha256": hil.digest(path)}
    return result


class HilArtifactTests(unittest.TestCase):
    def test_frozen_https_port_comes_from_sha_pinned_app_profiles(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            profiles = {}
            for scenario in hil.campaign.SCENARIOS:
                scenario_root = root / scenario
                scenario_root.mkdir()
                profiles[scenario] = profile_files(scenario_root, server_port=8446)
            profiles_path = root / "profiles.json"
            profiles_path.write_text(json.dumps({"schema_version": 2, "profiles": profiles}))
            campaign_value = {"metadata": {"hil_profiles_sha256": hil.digest(profiles_path)}}
            self.assertEqual(hil.frozen_https_port(campaign_value, profiles_path), 8446)

            profiles["aot"] = profile_files(root / "aot", server_port=8447)
            profiles_path.write_text(json.dumps({"schema_version": 2, "profiles": profiles}))
            campaign_value["metadata"]["hil_profiles_sha256"] = hil.digest(profiles_path)
            with self.assertRaisesRegex(hil.HilError, "disagree on the HTTPS server port"):
                hil.frozen_https_port(campaign_value, profiles_path)

    def test_frozen_https_port_rejects_profiles_not_pinned_by_campaign(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            profile = profile_files(root)
            profiles_path = root / "profiles.json"
            profiles_path.write_text(json.dumps({"schema_version": 2,
                "profiles": {scenario: profile for scenario in hil.campaign.SCENARIOS}}))
            campaign_value = {"metadata": {"hil_profiles_sha256": "0" * 64}}
            with self.assertRaisesRegex(hil.HilError, "not frozen in campaign metadata"):
                hil.frozen_https_port(campaign_value, profiles_path)

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
            {"campaign_id": "campaign", "metadata": {"artifact_campaign_id": "signed-campaign"}},
            {"slots": self.slots}, {})))
        self.stack.enter_context(patch.object(hil.campaign, "_validate_repository"))
        self.stack.enter_context(patch.object(hil, "validate_profile"))
        self.stack.enter_context(patch.object(hil, "frozen_https_port", return_value=8446))
        self.stack.enter_context(patch("tools.remote_observer.validate_options"))
        self.preflight = self.stack.enter_context(patch("tools.ct107_user_preflight.verify_server"))

    def run_hil(self):
        return hil.execute(self.options, board_factory=lambda *_: self.board, invoke_slot=self.invoke)

    def stored(self):
        path = self.root / "hil-orchestrator/journal.json"
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        return json.loads(path.read_text())

    def test_preflight_uses_artifact_id_and_runs_before_journal_or_serial(self):
        self.assertNotEqual(self.journal["campaign_id"], "signed-campaign")
        self.preflight.side_effect = ValueError("CT107 preflight refused")
        with self.assertRaisesRegex(ValueError, "CT107 preflight refused"):
            self.run_hil()
        self.assertEqual(self.preflight.call_args.kwargs["campaign_id"], "signed-campaign")
        self.assertEqual(self.preflight.call_args.kwargs["expected_port"], 8446)
        self.assertFalse((self.root / "hil-orchestrator").exists())
        self.board.identify.assert_not_called()
        self.board.read_hash.assert_not_called()
        self.invoke.assert_not_called()

    def test_recover_does_not_need_ct107_preflight(self):
        journal_root = hil.private_directory(self.root / "hil-orchestrator")
        self.journal.update(state="preparing", restore_required=True)
        hil.write_private(journal_root / "journal.json", self.journal)
        self.options.action = "recover"
        self.preflight.side_effect = ValueError("CT107 offline")
        result = self.run_hil()
        self.assertEqual(result["state"], "recovered_interruption")
        self.preflight.assert_not_called()
        self.board.restore.assert_called_once()

    def test_tooling_snapshot_covers_ct107_preflight(self):
        self.assertIn("ct107_user_preflight.py", hil.tooling_snapshot())

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
        self.assertFalse(self.board.restore.call_args.kwargs["boot"])

    def recovered_claim(self, index=0):
        self.journal.update(state="recovered_interruption", restore_required=False, next_index=index,
                            current_slot=self.slots[index]["slot_id"])
        claims = hil.private_directory(self.root / "hil-orchestrator" / "claims")
        path = claims / (self.slots[index]["slot_id"] + ".json")
        hil.write_private(path, {"slot": self.slots[index], "retry_permitted": False})
        return path

    def test_recovered_claim_is_retired_once_and_only_next_slot_runs(self):
        claim = self.recovered_claim()
        self.run_hil()
        self.assertEqual([call.args[1]["slot_id"] for call in self.invoke.call_args_list],
                         [self.slots[1]["slot_id"]])
        event = self.stored()["events"][0]
        self.assertEqual(event["state"], "interrupted_not_accepted")
        self.assertIs(event["accepted"], False)
        self.assertIs(event["capture_started"], False)
        self.assertIs(event["retry_permitted"], False)
        self.assertEqual(event["claim_sha256"], hil.digest(claim))
        self.assertEqual(event["tooling_sha256"], hil.tooling_snapshot())
        self.assertEqual(self.stored()["next_index"], 2)
        self.run_hil()
        self.assertEqual(self.invoke.call_count, 1)
        self.assertEqual(len(self.stored()["events"]), 2)

    def test_recovered_last_claim_checks_live_flash_without_starting_capture(self):
        self.recovered_claim(index=1)
        def read_baseline(_path):
            self.assertTrue(self.stored()["restore_required"])
            self.assertEqual(self.stored()["next_index"], 1)
            self.assertEqual(self.stored()["events"], [])
            return hil.BASELINE_SHA256
        self.board.read_hash.side_effect = read_baseline
        self.run_hil()
        self.assertEqual(self.stored()["next_index"], 2)
        self.board.identify.assert_called_once()
        self.board.read_hash.assert_called_once()
        self.board.restore.assert_not_called()
        self.invoke.assert_not_called()

    def test_recovered_drift_restores_and_verifies_baseline_before_retiring_claim(self):
        self.recovered_claim(index=1)
        self.board.read_hash.return_value = "0" * 64
        def restore_verified(*args, **kwargs):
            self.assertTrue(self.stored()["restore_required"])
            self.assertEqual(self.stored()["next_index"], 1)
            self.assertEqual(self.stored()["events"], [])
            self.assertIs(kwargs["boot"], False)
        self.board.restore.side_effect = restore_verified
        self.run_hil()
        self.board.restore.assert_called_once()
        proof = self.stored()["events"][0]["baseline_verification"]
        self.assertTrue(proof["restored"])
        self.assertEqual(proof["live_sha256"], "0" * 64)
        self.assertEqual(proof["verified_sha256"], hil.BASELINE_SHA256)
        self.assertIs(proof["boot"], False)

    def test_recovered_live_read_or_restore_failure_keeps_claim_recoverable(self):
        self.recovered_claim(index=1)
        for method in ("identify", "read_hash", "restore"):
            with self.subTest(method=method):
                self.journal.update(state="recovered_interruption", restore_required=False,
                                    next_index=1, current_slot=self.slots[1]["slot_id"], events=[])
                hil.write_private(self.root / "hil-orchestrator/journal.json", self.journal)
                self.board.reset_mock(side_effect=True)
                self.board.read_hash.return_value = "0" * 64
                getattr(self.board, method).side_effect = OSError("simulated live proof failure")
                with self.assertRaisesRegex(hil.HilError, "claim not retired"):
                    self.run_hil()
                self.assertEqual(self.stored()["state"], "restore_failed")
                self.assertTrue(self.stored()["restore_required"])
                self.assertEqual(self.stored()["next_index"], 1)
                self.assertEqual(self.stored()["events"], [])
        self.invoke.assert_not_called()

    def test_recovered_claim_with_partial_raw_is_never_skipped_or_retried(self):
        self.recovered_claim()
        (self.root / "raw" / "slots" / self.slots[0]["slot_id"]).mkdir(parents=True)
        with self.assertRaisesRegex(hil.HilError, "partial raw evidence"):
            self.run_hil()
        self.assertEqual(self.stored()["next_index"], 0)
        self.board.identify.assert_not_called()
        self.invoke.assert_not_called()

    def test_recovered_current_slot_must_match_schedule_index(self):
        self.recovered_claim()
        self.journal["current_slot"] = self.slots[1]["slot_id"]
        with self.assertRaisesRegex(hil.HilError, "does not match"):
            self.run_hil()
        self.board.identify.assert_not_called()

    def test_recovered_claim_requires_exact_slot_and_false_retry_flag(self):
        claim = self.recovered_claim()
        for value in ({"slot": self.slots[1], "retry_permitted": False},
                      {"slot": self.slots[0], "retry_permitted": True},
                      {"slot": self.slots[0], "retry_permitted": 0}):
            with self.subTest(value=value):
                hil.write_private(claim, value)
                with self.assertRaisesRegex(hil.HilError, "differs from its slot"):
                    self.run_hil()
                self.assertEqual(self.stored()["next_index"], 0)
        self.board.identify.assert_not_called()

    def test_recovered_claim_must_remain_private(self):
        claim = self.recovered_claim()
        claim.chmod(0o644)
        with self.assertRaisesRegex(hil.HilError, "private regular"):
            self.run_hil()
        self.board.identify.assert_not_called()

    def test_recovered_claim_missing_or_already_adjudicated_cannot_advance(self):
        claim = self.recovered_claim()
        claim.unlink()
        with self.assertRaisesRegex(hil.HilError, "private regular"):
            self.run_hil()
        hil.write_private(claim, {"slot": self.slots[0], "retry_permitted": False})
        value = self.stored()
        value["events"].append({"slot_id": self.slots[0]["slot_id"], "state": "interrupted_not_accepted"})
        hil.write_private(self.root / "hil-orchestrator/journal.json", value)
        with self.assertRaisesRegex(hil.HilError, "terminal event"):
            self.run_hil()
        self.assertEqual(self.stored()["next_index"], 0)
        self.board.identify.assert_not_called()

    def test_failed_skip_audit_write_never_starts_next_slot(self):
        self.recovered_claim()
        original = hil.write_private
        def fail_audit(path, value, **kwargs):
            if value.get("events") and value["events"][0].get("state") == "interrupted_not_accepted":
                raise OSError("audit write failed")
            return original(path, value, **kwargs)
        with patch.object(hil, "write_private", side_effect=fail_audit):
            with self.assertRaisesRegex(hil.HilError, "claim not retired"):
                self.run_hil()
        self.assertEqual(self.stored()["state"], "restore_failed")
        self.assertTrue(self.stored()["restore_required"])
        self.assertEqual(self.stored()["next_index"], 0)
        self.invoke.assert_not_called()

    def test_existing_claim_refuses_before_any_hardware_access(self):
        claims = hil.private_directory(self.root / "hil-orchestrator" / "claims")
        hil.write_private(claims / (self.slots[0]["slot_id"] + ".json"),
                          {"slot": self.slots[0], "retry_permitted": False})
        with self.assertRaisesRegex(hil.HilError, "already has a claim"):
            self.run_hil()
        self.assertEqual(self.stored()["state"], "claiming")
        self.assertTrue(self.stored()["restore_required"])
        self.board.identify.assert_not_called()
        self.invoke.assert_not_called()

    def test_crash_before_claim_file_can_recover_intent_without_retry(self):
        original = hil.write_private
        def interrupt_claim(path, value, **kwargs):
            if Path(path).parent.name == "claims":
                self.assertEqual(self.stored()["state"], "claiming")
                self.assertTrue(self.stored()["restore_required"])
                raise OSError("claim file was not created")
            return original(path, value, **kwargs)
        with patch.object(hil, "write_private", side_effect=interrupt_claim):
            with self.assertRaisesRegex(OSError, "claim file was not created"):
                self.run_hil()
        claim = self.root / "hil-orchestrator/claims" / (self.slots[0]["slot_id"] + ".json")
        self.assertFalse(claim.exists())
        self.assertEqual(self.stored()["next_index"], 0)
        self.board.restore.assert_not_called()
        self.options.action = "recover"
        self.run_hil()
        self.assertEqual(json.loads(claim.read_text()), {"slot": self.slots[0], "retry_permitted": False})
        self.assertEqual(self.stored()["state"], "recovered_interruption")
        self.options.action = "run"
        self.run_hil()
        self.assertEqual(self.invoke.call_count, 1)
        self.assertEqual(self.invoke.call_args.args[1]["slot_id"], self.slots[1]["slot_id"])

    def test_legacy_orphan_claim_is_adopted_and_recovers_without_overwriting_claim(self):
        claims = hil.private_directory(self.root / "hil-orchestrator/claims")
        claim = claims / (self.slots[0]["slot_id"] + ".json")
        hil.write_private(claim, {"slot": self.slots[0], "retry_permitted": False})
        before = hil.digest(claim)
        with self.assertRaisesRegex(hil.HilError, "recovery required"):
            self.run_hil()
        self.board.identify.assert_not_called()
        self.options.action = "recover"
        self.run_hil()
        self.assertEqual(hil.digest(claim), before)
        self.options.action = "run"
        self.run_hil()
        self.assertEqual(hil.digest(claim), before)
        self.assertEqual(self.invoke.call_count, 1)

    def test_failed_claim_intent_write_cannot_create_orphan_claim(self):
        original = hil.write_private
        def fail_intent(path, value, **kwargs):
            if value.get("state") == "claiming":
                raise OSError("intent write failed")
            return original(path, value, **kwargs)
        with patch.object(hil, "write_private", side_effect=fail_intent):
            with self.assertRaisesRegex(OSError, "intent write failed"):
                self.run_hil()
        self.assertEqual(self.stored()["state"], "ready")
        self.assertFalse(list((self.root / "hil-orchestrator/claims").glob("*.json")))
        self.board.restore.assert_not_called()
        self.invoke.assert_not_called()

    def test_recovery_rejects_mismatched_claim_intent_before_hardware_or_claim_creation(self):
        self.journal.update(state="claiming", restore_required=True,
                            current_slot=self.slots[0]["slot_id"],
                            claim_intent={"slot": self.slots[1], "retry_permitted": False})
        root = hil.private_directory(self.root / "hil-orchestrator")
        hil.write_private(root / "journal.json", self.journal)
        self.options.action = "recover"
        with self.assertRaisesRegex(hil.HilError, "claim intent does not match"):
            self.run_hil()
        self.assertTrue(self.stored()["restore_required"])
        self.assertEqual(self.stored()["next_index"], 0)
        self.board.identify.assert_not_called()
        self.invoke.assert_not_called()

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


class TerminalFailureAdjudicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.hil_root = self.root / "hil-orchestrator"
        self.hil_root.mkdir(mode=0o700)
        self.inputs = self.hil_root / "inputs"
        self.inputs.mkdir(mode=0o700)
        self.baseline = self.inputs / "baseline.bin"
        self._sparse_flash(self.baseline)
        self.slots = [{"slot_id": "B0004-native", "scenario": "native", "order_in_block": 1},
                      {"slot_id": "B0005-interp", "scenario": "interp", "order_in_block": 2}]
        self.journal = {"schema_version": 1, "campaign_id": "fixture-campaign", "git_commit": "fixture-commit",
                        "state": "slot_failed", "restore_required": False, "next_index": 1,
                        "current_slot": self.slots[0]["slot_id"], "slots": self.slots,
                        "baseline": str(self.baseline),
                        "claim_intent": {"slot": self.slots[0], "retry_permitted": False},
                        "events": [{"slot_id": self.slots[0]["slot_id"], "state": "slot_failed",
                                    "error": "RemoteObserverError"}]}
        self.journal_path = self.hil_root / "journal.json"
        self._write_json(self.journal_path, self.journal)
        claims = self.hil_root / "claims"
        claims.mkdir(mode=0o700)
        self.claim_path = claims / (self.slots[0]["slot_id"] + ".json")
        self._write_json(self.claim_path, {"slot": self.slots[0], "retry_permitted": False})
        operation = self.hil_root / ("operation-" + "1" * 32)
        operation.mkdir(mode=0o700)
        self.readback = operation / (self.slots[0]["slot_id"] + "-after.bin")
        self._sparse_flash(self.readback)
        self.raw_dir = self.root / "raw" / "slots" / self.slots[0]["slot_id"]
        self.raw_dir.mkdir(mode=0o700, parents=True)
        self.session_id = "12345678-1234-4234-9234-123456789abc"
        self._write_json(self.raw_dir / "remote-session.json", {
            "schema": "wasmhome-remote-observer-v1", "session_id": self.session_id,
            "config": {"context": {"campaign_id": self.journal["campaign_id"],
                                     "slot_id": self.slots[0]["slot_id"],
                                     "git_commit": self.journal["git_commit"]}}})
        self._write_json(self.raw_dir / "remote-start-claim.json", {"session_id": self.session_id})
        self._write_json(self.raw_dir / "remote-status.json", {
            "schema": "wasmhome-remote-observer-v1", "session_id": self.session_id, "state": "running"})
        self._write_json(self.raw_dir / "remote-transport-error.json", {
            "session_id": self.session_id, "reason": "SSH status timed out", "retry_permitted": False})
        self._write_file(self.raw_dir / "device.jsonl", b"serial capture preserved\n")
        raw_files = {}
        for path in sorted(self.raw_dir.iterdir()):
            raw_files[path.name] = {"sha256": hil.campaign._sha256_file(path),
                                    "size_bytes": path.stat().st_size}
        self.terminal_error = {
            "kind": "capture_failed",
            "error": "RemoteObserverError: SSH status did not complete: Command timed out after 5 seconds",
            "automatic_retry_permitted": False, "raw_files": raw_files}
        self._write_json(self.raw_dir / "terminal-error.json", self.terminal_error)
        old_tooling = {name: "a" * 64 for name in hil.tooling_snapshot()}
        self.batch_path = self.hil_root / "batch.json"
        self._write_json(self.batch_path, {
            "state": "blocked", "restore_required": False, "next_index": 1,
            "tooling_sha256": old_tooling,
            "events": [{"before_index": 0, "after_index": 1, "exit_code": 1}]})
        self.real_digest = hil.digest
        self.stack = []
        self.stack.append(patch.object(hil.os, "geteuid", return_value=1000))
        self.stack.append(patch.object(hil, "hardware_lock", lambda: nullcontext()))
        self.stack.append(patch.object(hil, "digest", side_effect=self._fixture_digest))
        for item in self.stack:
            item.start()
        self.addCleanup(self._stop_patches)

    def _stop_patches(self):
        for item in self.stack:
            item.stop()

    def _fixture_digest(self, path):
        if Path(path) in {self.baseline, self.readback}:
            return hil.BASELINE_SHA256
        return self.real_digest(path)

    def _sparse_flash(self, path):
        with path.open("wb") as stream:
            stream.truncate(hil.FLASH_BYTES)
        path.chmod(0o600)

    def _write_file(self, path, data):
        with path.open("xb") as stream:
            stream.write(data)
        path.chmod(0o600)

    def _write_json(self, path, value):
        hil.campaign._write_json(path, value)
        path.chmod(0o600)

    def _stored(self):
        return json.loads(self.journal_path.read_text())

    def test_rejects_wrong_baseline_and_wrong_full_flash_hash(self):
        with patch.object(hil, "digest", side_effect=lambda path: "0" * 64
                          if Path(path) == self.baseline else self._fixture_digest(path)):
            with self.assertRaisesRegex(hil.HilError, "baseline snapshot hash/size"):
                hil.adjudicate_terminal_failure(self.root)
        self.assertEqual(self._stored()["state"], "slot_failed")
        with patch.object(hil, "digest", side_effect=lambda path: "0" * 64
                          if Path(path) == self.readback else self._fixture_digest(path)):
            with self.assertRaisesRegex(hil.HilError, "readback does not match"):
                hil.adjudicate_terminal_failure(self.root)
        self.assertFalse((self.hil_root / "adjudications").exists())

    def test_rejects_slot_claim_mismatch(self):
        self._write_json(self.claim_path, {"slot": self.slots[1], "retry_permitted": False})
        with self.assertRaisesRegex(hil.HilError, "claim mismatch"):
            hil.adjudicate_terminal_failure(self.root)
        self.assertEqual(self._stored()["next_index"], 1)
        self.assertFalse((self.hil_root / "adjudications").exists())

    def test_rejects_capture_or_restore_still_pending(self):
        self.journal["restore_required"] = True
        self._write_json(self.journal_path, self.journal)
        with self.assertRaisesRegex(hil.HilError, "restoration complete"):
            hil.adjudicate_terminal_failure(self.root)
        self.journal["restore_required"] = False
        self._write_json(self.journal_path, self.journal)
        (self.raw_dir / "terminal-error.json").unlink()
        with self.assertRaisesRegex(hil.HilError, "terminal error"):
            hil.adjudicate_terminal_failure(self.root)
        self._write_json(self.raw_dir / "terminal-error.json", self.terminal_error)
        self.readback.unlink()
        with self.assertRaisesRegex(hil.HilError, "readback is missing"):
            hil.adjudicate_terminal_failure(self.root)
        self.assertEqual(self._stored()["state"], "slot_failed")

    def test_rejects_tampered_raw_remote_status(self):
        self._write_json(self.raw_dir / "remote-status.json", {
            "schema": "wasmhome-remote-observer-v1", "session_id": self.session_id, "state": "failed"})
        with self.assertRaisesRegex(hil.HilError, "raw evidence hash/size mismatch"):
            hil.adjudicate_terminal_failure(self.root)
        self.assertEqual(self._stored()["state"], "slot_failed")

    def test_accepts_verified_failure_without_repeating_or_advancing_index(self):
        before_claim = self.real_digest(self.claim_path)
        before_raw = {path.name: self.real_digest(path) for path in self.raw_dir.iterdir()}
        result = hil.adjudicate_terminal_failure(self.root)
        stored = self._stored()
        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["next_index"], 1)
        self.assertEqual(stored["state"], "ready")
        self.assertEqual(stored["next_index"], 1)
        self.assertEqual(stored["events"][-1]["state"], "terminal_failure_adjudicated")
        self.assertIs(stored["events"][-1]["retry_permitted"], False)
        receipt_path = Path(result["receipt"])
        self.assertEqual(receipt_path.stat().st_mode & 0o777, 0o600)
        receipt = json.loads(receipt_path.read_text())
        self.assertEqual(receipt["next_index_before"], receipt["next_index_after"])
        self.assertIs(receipt["post_restore_readback"]["matches_baseline"], True)
        self.assertEqual(receipt["tooling_sha256_before"], {name: "a" * 64 for name in hil.tooling_snapshot()})
        self.assertEqual(receipt["tooling_sha256_after"], hil.tooling_snapshot())
        self.assertEqual(self.real_digest(self.claim_path), before_claim)
        self.assertEqual({path.name: self.real_digest(path) for path in self.raw_dir.iterdir()}, before_raw)

    def test_reuses_matching_private_receipt_if_journal_write_needs_retry(self):
        original = hil.write_private

        def fail_ready_journal(path, value, **kwargs):
            if Path(path) == self.journal_path and value.get("state") == "ready":
                raise OSError("simulated journal write failure")
            return original(path, value, **kwargs)

        with patch.object(hil, "write_private", side_effect=fail_ready_journal):
            with self.assertRaisesRegex(OSError, "journal write failure"):
                hil.adjudicate_terminal_failure(self.root)
        receipt = self.hil_root / "adjudications" / (self.slots[0]["slot_id"] + ".json")
        self.assertTrue(receipt.is_file())
        self.assertEqual(self._stored()["state"], "slot_failed")
        result = hil.adjudicate_terminal_failure(self.root)
        self.assertEqual(result["state"], "ready")
        self.assertEqual(self._stored()["next_index"], 1)


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
