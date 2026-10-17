import hashlib
import json
import os
import sys
import tempfile
import unittest
import uuid
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch
from pathlib import Path

from tools.update_campaign import (
    CampaignError,
    DEFAULT_SERVER_LOG,
    SCHEMA_VERSION,
    _load_artifacts,
    _load_slot_records,
    _start_serial_capture,
    make_schedule,
    _observer_attempt_id,
    _read_jsonl,
    build_parser,
    plan,
    run_slot,
)


class UpdateCampaignTests(unittest.TestCase):
    def test_schedule_is_deterministic_balanced_and_complete(self):
        first = make_schedule(7, "pilot-seed")
        second = make_schedule(7, "pilot-seed")
        self.assertEqual(first, second)
        self.assertEqual(len(first["slots"]), 21)
        self.assertEqual(max(first["permutation_counts"].values())
                         - min(first["permutation_counts"].values()), 1)
        for block in range(1, 8):
            rows = [row for row in first["slots"] if row["block_id"] == f"B{block:04d}"]
            self.assertEqual({row["scenario"] for row in rows}, {"native", "interp", "aot"})
            self.assertEqual(sorted(row["order_in_block"] for row in rows), [1, 2, 3])

    def test_artifact_manifest_checks_all_scenarios_hashes_and_sizes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            rows = []
            for scenario in ("native", "interp", "aot"):
                artifact = root / f"{scenario}.bin"
                artifact.write_bytes(scenario.encode())
                rows.append({"artifact_id": scenario, "scenario": scenario,
                             "path": str(artifact),
                             "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()})
            manifest = root / "artifacts.json"
            manifest.write_text(json.dumps({"schema_version": SCHEMA_VERSION,
                                            "artifacts": rows}))
            loaded, _ = _load_artifacts(manifest)
            self.assertEqual(len(loaded["artifacts"]), 3)
            self.assertEqual(loaded["artifacts"][0]["size_bytes"], len(b"native"))

            rows[1]["sha256"] = "0" * 64
            manifest.write_text(json.dumps({"schema_version": SCHEMA_VERSION,
                                            "artifacts": rows}))
            with self.assertRaisesRegex(CampaignError, "SHA-256 mismatch"):
                _load_artifacts(manifest)

    def test_analyze_rejects_unresolved_instrument_status(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            schedule = make_schedule(1, "slot-test")
            campaign = {"campaign_id": str(uuid.uuid4())}
            for slot in schedule["slots"]:
                self._write_slot(root, campaign, slot, instrument_status="unresolved")
            with self.assertRaisesRegex(CampaignError, "requires a resolved instrument_status"):
                _load_slot_records(root, schedule, campaign)

    def test_analyze_rejects_accepted_attempt_without_terminal_status(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            schedule = make_schedule(1, "slot-test")
            campaign = {"campaign_id": str(uuid.uuid4())}
            for slot in schedule["slots"]:
                self._write_slot(root, campaign, slot, accepted_status=None)
            with self.assertRaisesRegex(CampaignError, "requires a terminal accepted_attempt_status"):
                _load_slot_records(root, schedule, campaign)

    def test_analyze_rejects_inconsistent_slot_success(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            schedule = make_schedule(1, "slot-test")
            campaign = {"campaign_id": str(uuid.uuid4())}
            for slot in schedule["slots"]:
                self._write_slot(root, campaign, slot, slot_success=False)
            with self.assertRaisesRegex(CampaignError, "slot_success disagrees"):
                _load_slot_records(root, schedule, campaign)

    def test_jsonl_inspection_preserves_observer_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "observations.jsonl"
            raw = (b'{ "kind" : "send_intent", "packet" : {"message_type":"UPDATE_TRIGGER", '
                   b'"attempt_id":"00000000-0000-0000-0000-000000000001"} }\r\n')
            path.write_bytes(raw)
            rows = _read_jsonl(path, "observations.jsonl")
            self.assertEqual(rows[0]["kind"], "send_intent")
            self.assertEqual(_observer_attempt_id(path, {}), "00000000-0000-0000-0000-000000000001")
            self.assertEqual(path.read_bytes(), raw)

    def test_jsonl_reader_rejects_malformed_duplicate_and_nonfinite_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "observations.jsonl"
            for raw in (b'{"kind":"x"}\nnot-json\n',
                        b'{"kind":"x","kind":"y"}\n',
                        b'{"value":NaN}\n'):
                path.write_bytes(raw)
                with self.assertRaises(CampaignError):
                    _read_jsonl(path, "observations.jsonl")

    def test_serial_capture_sets_dtr_and_rts_inactive_before_open(self):
        instances = []

        class FakeSerial:
            def __init__(self, *, port, baudrate, timeout):
                self.port = port
                self.baudrate = baudrate
                self.timeout = timeout
                self._dtr = True
                self._rts = True
                self.calls = []
                self.closed = False
                instances.append(self)

            @property
            def dtr(self):
                return self._dtr

            @dtr.setter
            def dtr(self, value):
                self._dtr = value
                self.calls.append(("dtr", value))

            @property
            def rts(self):
                return self._rts

            @rts.setter
            def rts(self, value):
                self._rts = value
                self.calls.append(("rts", value))

            def open(self):
                self.calls.append(("open", self.port))
                self.assert_inactive_before_open()

            def assert_inactive_before_open(self):
                self_outer.assertEqual(self._dtr, False)
                self_outer.assertEqual(self._rts, False)
                self_outer.assertEqual(self.port, "fake-device")

            @property
            def in_waiting(self):
                return 0

            def read(self, _size):
                time.sleep(0.005)
                return b""

            def close(self):
                self.closed = True

        self_outer = self
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "device.jsonl"
            with patch.dict(sys.modules, {"serial": SimpleNamespace(Serial=FakeSerial)}):
                stop, worker = _start_serial_capture("fake-device", 115200, output)
                stop.set()
                worker.join(timeout=2)

            self.assertFalse(worker.is_alive())
            self.assertEqual(len(instances), 1)
            self.assertEqual(instances[0].calls[:3], [
                ("dtr", False), ("rts", False), ("open", "fake-device")])
            self.assertTrue(instances[0].closed)
            capture = output.read_text()
            self.assertIn('"kind": "serial_open"', capture)
            self.assertIn('"kind": "serial_closed"', capture)

    def test_cli_server_log_default_matches_observer_filename(self):
        args = build_parser().parse_args([
            "run-slot", "campaign", "B0001-native", "--device-ip", "192.0.2.1",
            "--serial-port", "fake", "--seconds", "150", "--trigger-after", "60",
            "--target-sequence", "2",
        ])
        self.assertEqual(args.server_log, DEFAULT_SERVER_LOG)
        self.assertEqual(args.server_log.as_posix(), "server/http-events.jsonl")

    def test_run_slot_records_missing_server_log_after_no_trigger(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package, slot = self._make_test_campaign(root, "pre-window-server-log")
            summary = {"update_state": "pre_window_failed", "lost_controls": 1,
                       "counters": {"pre_window_failed": 1}}

            def fake_observer(**kwargs):
                (kwargs["output"] / "observations.jsonl").write_bytes(b'{"kind":"awaiting_boot"}\n')
                (kwargs["output"] / "summary.json").write_text(json.dumps(summary))
                (kwargs["output"] / "server.jsonl").write_bytes(b"preserve existing evidence\n")
                return summary

            def fake_serial(_port, _baud, path):
                path.write_bytes(b'{"kind":"serial_open"}\n{"kind":"serial_closed"}\n')
                worker = Mock()
                worker.is_alive.return_value = False
                return threading.Event(), worker

            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
            import external_observer
            with patch("tools.update_campaign._start_serial_capture", side_effect=fake_serial), \
                    patch.object(external_observer, "run", side_effect=fake_observer):
                result = run_slot(
                    package, slot["slot_id"], device_ip="192.0.2.1", serial_port="fake",
                    seconds=2, trigger_after=0.5, target_sequence=2,
                    server_log=Path("server/measurement-http-events.jsonl"),
                )

            output = Path(result["output"])
            metadata = json.loads((output / "slot.json").read_text())
            self.assertEqual(result["dispatch_status"], "not_sent")
            self.assertEqual(result["server_log_status"], "absent_no_update_trigger")
            self.assertEqual(metadata["server_log"]["http_requests_expected"], False)
            self.assertEqual(metadata["server_log"]["reason"],
                             "observer_did_not_send_update_trigger")
            self.assertTrue((output / "summary.json").is_file())
            self.assertTrue((output / "observations.jsonl").is_file())
            self.assertEqual((output / "server.jsonl").read_bytes(),
                             b"preserve existing evidence\n")

    def test_run_slot_archives_only_appended_server_log_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package, slot = self._make_test_campaign(root, "server-log-append")
            server_log = package / "server" / "http-events.jsonl"
            server_log.parent.mkdir()
            previous = b'{"event":"older-slot"}\n'
            current = b'{"event":"this-slot"}\n'
            server_log.write_bytes(previous)
            attempt = "00000000-0000-0000-0000-000000000003"
            packet = {"message_type": "UPDATE_TRIGGER", "attempt_id": attempt}
            raw = (json.dumps({"kind": "send_intent", "packet": packet, "hex": "aa"}) + "\n"
                   + json.dumps({"kind": "send", "hex": "aa", "peer": ["192.0.2.1", 3333],
                                "planned_ns": None}) + "\n").encode()
            summary = {"update_state": "accepted_without_v2", "lost_controls": 0,
                       "counters": {"update_accepted": 1}}

            def fake_observer(**kwargs):
                (kwargs["output"] / "observations.jsonl").write_bytes(raw)
                (kwargs["output"] / "summary.json").write_text(json.dumps(summary))
                with server_log.open("ab") as stream:
                    stream.write(current)
                return summary

            result = self._run_fake_slot(package, slot, fake_observer, Path("server/http-events.jsonl"))
            output = Path(result["output"])
            archived = (output / "server.jsonl").read_bytes()
            record = json.loads((output / "slot.json").read_text())["server_log"]

            self.assertEqual(archived, current)
            self.assertNotIn(previous, archived)
            self.assertEqual(record["initial_offset_bytes"], len(previous))
            self.assertEqual(record["archive_start_offset_bytes"], len(previous))
            self.assertEqual(record["archive_end_offset_bytes"], len(previous) + len(current))
            self.assertEqual(record["bytes_archived"], len(current))
            self.assertEqual(record["initial_snapshot"]["state"], "present")
            self.assertEqual(record["initial_snapshot"]["device"], server_log.stat().st_dev)
            self.assertEqual(record["initial_snapshot"]["inode"], server_log.stat().st_ino)
            self.assertEqual(record["initial_snapshot"]["prefix_sha256"],
                             hashlib.sha256(previous).hexdigest())
            self.assertEqual(record["initial_snapshot"]["prefix_bytes_hashed"], len(previous))
            self.assertTrue(record["initial_snapshot"]["prefix_complete"])
            self.assertFalse(record["rotation_detected"])
            self.assertFalse(record["truncation_detected"])
            self.assertEqual(record["status"], "captured")
            self.assertTrue(record["accepted"])
            self.assertEqual(record["segments"][0]["sha256"], hashlib.sha256(current).hexdigest())

    def test_run_slot_requires_review_for_new_server_events_without_trigger(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package, slot = self._make_test_campaign(root, "server-log-no-trigger")
            server_log = package / "server" / "http-events.jsonl"
            server_log.parent.mkdir()
            previous = b'{"event":"older-slot"}\n'
            current = b'{"event":"unexpected-new-event"}\n'
            server_log.write_bytes(previous)
            summary = {"update_state": "pre_window_failed", "lost_controls": 1,
                       "counters": {"pre_window_failed": 1}}

            def fake_observer(**kwargs):
                (kwargs["output"] / "observations.jsonl").write_bytes(b'{"kind":"awaiting_boot"}\n')
                (kwargs["output"] / "summary.json").write_text(json.dumps(summary))
                with server_log.open("ab") as stream:
                    stream.write(current)
                return summary

            result = self._run_fake_slot(package, slot, fake_observer, Path("server/http-events.jsonl"))
            output = Path(result["output"])
            record = json.loads((output / "slot.json").read_text())["server_log"]

            self.assertEqual((output / "server.jsonl").read_bytes(), current)
            self.assertEqual(record["initial_offset_bytes"], len(previous))
            self.assertEqual(record["bytes_archived"], len(current))
            self.assertEqual(record["status"], "unexpected_events_without_update_trigger")
            self.assertFalse(record["accepted"])
            self.assertTrue(record["review_required"])

    def test_run_slot_records_server_log_rotation_and_truncation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            summary = {"update_state": "pre_window_failed", "lost_controls": 1,
                       "counters": {"pre_window_failed": 1}}
            cases = (
                ("rotation", b'{"event":"old"}\n' * 4,
                 b'{"event":"after-rotation"}\n', b'{"event":"old-inode-tail"}\n', True, False),
                ("truncation", b'{"event":"old"}\n' * 8,
                 b'{"event":"after-truncation"}\n', b"", False, True),
            )
            for name, previous, current, old_suffix, expect_rotation, expect_truncation in cases:
                with self.subTest(change=name):
                    package, slot = self._make_test_campaign(root, f"server-log-{name}")
                    server_log = package / "server" / "http-events.jsonl"
                    server_log.parent.mkdir()
                    server_log.write_bytes(previous)

                    def fake_observer(**kwargs):
                        (kwargs["output"] / "observations.jsonl").write_bytes(
                            b'{"kind":"awaiting_boot"}\n')
                        (kwargs["output"] / "summary.json").write_text(json.dumps(summary))
                        if name == "rotation":
                            old_path = server_log.with_suffix(".old")
                            server_log.rename(old_path)
                            with old_path.open("ab") as stream:
                                stream.write(old_suffix)
                        server_log.write_bytes(current)
                        return summary

                    result = self._run_fake_slot(
                        package, slot, fake_observer, Path("server/http-events.jsonl"))
                    output = Path(result["output"])
                    record = json.loads((output / "slot.json").read_text())["server_log"]

                    if expect_rotation:
                        self.assertEqual((output / "server.jsonl").read_bytes(), old_suffix)
                        self.assertEqual((output / "server-rotated.jsonl").read_bytes(), current)
                        self.assertEqual([segment["role"] for segment in record["segments"]], [
                            "original_inode_delta", "current_rotated_inode"])
                        self.assertEqual(
                            record["segments"][0]["sha256"], hashlib.sha256(old_suffix).hexdigest())
                        self.assertEqual(
                            record["segments"][1]["sha256"], hashlib.sha256(current).hexdigest())
                        self.assertEqual(record["initial_snapshot"]["prefix_sha256"],
                                         hashlib.sha256(previous).hexdigest())
                        self.assertEqual(record["segments"][0]["device"],
                                         record["initial_snapshot"]["device"])
                        self.assertEqual(record["segments"][0]["inode"],
                                         record["initial_snapshot"]["inode"])
                        self.assertEqual(record["segments"][1]["device"], server_log.stat().st_dev)
                        self.assertEqual(record["segments"][1]["inode"], server_log.stat().st_ino)
                        raw_files = json.loads((output / "slot.json").read_text())["raw_files"]
                        self.assertIn("server.jsonl", raw_files)
                        self.assertIn("server-rotated.jsonl", raw_files)
                    else:
                        self.assertEqual((output / "server.jsonl").read_bytes(), current)
                    self.assertEqual(record["rotation_detected"], expect_rotation)
                    self.assertEqual(record["truncation_detected"], expect_truncation)
                    self.assertEqual(record["status"], "unexpected_events_without_update_trigger")
                    self.assertFalse(record["accepted"])
                    self.assertTrue(record["review_required"])

    def test_run_slot_reopens_and_archives_path_rotated_during_capture(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package, slot = self._make_test_campaign(root, "server-log-capture-race")
            server_log = package / "server" / "http-events.jsonl"
            server_log.parent.mkdir()
            previous = b'{"event":"before-slot"}\n'
            old_tail = b'{"event":"old-inode-tail"}\n'
            first_current = b'{"event":"first-rotated-inode"}\n'
            second_current = b'{"event":"path-after-capture-rotation"}\n'
            server_log.write_bytes(previous)
            summary = {"update_state": "pre_window_failed", "lost_controls": 1,
                       "counters": {"pre_window_failed": 1}}

            def fake_observer(**kwargs):
                (kwargs["output"] / "observations.jsonl").write_bytes(b'{"kind":"awaiting_boot"}\n')
                (kwargs["output"] / "summary.json").write_text(json.dumps(summary))
                renamed_old = server_log.with_suffix(".old")
                server_log.rename(renamed_old)
                with renamed_old.open("ab") as stream:
                    stream.write(old_tail)
                server_log.write_bytes(first_current)
                return summary

            import tools.update_campaign as campaign_runner
            original_archive = campaign_runner._archive_server_log_segment

            def rotate_path_after_archiving(stream, destination, **kwargs):
                segment = original_archive(stream, destination, **kwargs)
                if (kwargs["role"] == "current_rotated_inode"
                        and destination.name == "server-rotated.jsonl"):
                    during_capture_old = server_log.with_suffix(".during-capture")
                    server_log.rename(during_capture_old)
                    server_log.write_bytes(second_current)
                return segment

            with patch("tools.update_campaign._archive_server_log_segment",
                       side_effect=rotate_path_after_archiving):
                result = self._run_fake_slot(
                    package, slot, fake_observer, Path("server/http-events.jsonl"))

            output = Path(result["output"])
            record = json.loads((output / "slot.json").read_text())["server_log"]
            self.assertEqual((output / "server.jsonl").read_bytes(), old_tail)
            self.assertEqual((output / "server-rotated.jsonl").read_bytes(), first_current)
            self.assertEqual((output / "server-rotated-2.jsonl").read_bytes(), second_current)
            self.assertTrue(record["rotation_detected"])
            self.assertTrue(record["path_changed_during_capture"])
            self.assertTrue(record["rotation_stabilized"])
            self.assertFalse(record["rotation_unstable"])
            self.assertGreaterEqual(record["rotation_passes"], 2)
            self.assertEqual(record["status"], "unexpected_events_without_update_trigger")
            self.assertTrue(record["review_required"])

    def test_run_slot_rejects_continuous_same_inode_growth_after_four_passes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package, slot = self._make_test_campaign(root, "server-log-continuous-growth")
            server_log = package / "server" / "http-events.jsonl"
            server_log.parent.mkdir()
            server_log.write_bytes(b'{"event":"before-slot"}\n')
            attempt = "00000000-0000-0000-0000-000000000020"
            packet = {"message_type": "UPDATE_TRIGGER", "attempt_id": attempt}
            raw = (json.dumps({"kind": "send_intent", "packet": packet, "hex": "aa"}) + "\n"
                   + json.dumps({"kind": "send", "hex": "aa", "peer": ["192.0.2.1", 3333],
                                 "planned_ns": None}) + "\n").encode()
            summary = {"update_state": "accepted_without_v2", "lost_controls": 0,
                       "counters": {"update_accepted": 1}}
            armed = {"value": False, "appends": 0}

            def fake_observer(**kwargs):
                (kwargs["output"] / "observations.jsonl").write_bytes(raw)
                (kwargs["output"] / "summary.json").write_text(json.dumps(summary))
                armed["value"] = True
                return summary

            real_fstat = os.fstat
            inode = server_log.stat().st_ino

            def append_after_fstat(fd):
                info = real_fstat(fd)
                if armed["value"] and info.st_ino == inode and armed["appends"] < 5:
                    with server_log.open("ab") as stream:
                        stream.write(b'{"event":"continuous-growth"}\n')
                    armed["appends"] += 1
                return info

            with patch("tools.update_campaign.os.fstat", side_effect=append_after_fstat):
                result = self._run_fake_slot(
                    package, slot, fake_observer, Path("server/http-events.jsonl"))

            output = Path(result["output"])
            record = json.loads((output / "slot.json").read_text())["server_log"]
            self.assertEqual(armed["appends"], 5)
            self.assertTrue(record["rotation_unstable"])
            self.assertEqual(record["rotation_passes"], 4)
            self.assertEqual(record["capture_mode"], "rotation_unstable")
            self.assertFalse(record["rotation_detected"])
            self.assertTrue(record["update_trigger_sent"])
            self.assertEqual(record["status"], "log_replaced_truncated_or_changed")
            self.assertFalse(record["accepted"])
            self.assertTrue(record["review_required"])

    def test_run_slot_best_effort_archives_path_swapped_on_last_rotation_check(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package, slot = self._make_test_campaign(root, "server-log-last-check-rotation")
            server_log = package / "server" / "http-events.jsonl"
            server_log.parent.mkdir()
            server_log.write_bytes(b'{"event":"before-slot"}\n')
            attempt = "00000000-0000-0000-0000-000000000021"
            packet = {"message_type": "UPDATE_TRIGGER", "attempt_id": attempt}
            raw = (json.dumps({"kind": "send_intent", "packet": packet, "hex": "aa"}) + "\n"
                   + json.dumps({"kind": "send", "hex": "aa", "peer": ["192.0.2.1", 3333],
                                 "planned_ns": None}) + "\n").encode()
            summary = {"update_state": "accepted_without_v2", "lost_controls": 0,
                       "counters": {"update_accepted": 1}}
            generations = [f'{{"event":"generation-{index}"}}\n'.encode()
                           for index in range(5)]

            def fake_observer(**kwargs):
                (kwargs["output"] / "observations.jsonl").write_bytes(raw)
                (kwargs["output"] / "summary.json").write_text(json.dumps(summary))
                old_path = server_log.with_suffix(".original")
                server_log.rename(old_path)
                server_log.write_bytes(generations[0])
                return summary

            import tools.update_campaign as campaign_runner
            original_archive = campaign_runner._archive_server_log_segment
            replacements = {"count": 0}

            def rotate_after_each_archive(stream, destination, **kwargs):
                segment = original_archive(stream, destination, **kwargs)
                if kwargs["role"] == "current_rotated_inode":
                    index = replacements["count"]
                    replacements["count"] += 1
                    archived_path = server_log.with_suffix(f".captured-{index}")
                    server_log.rename(archived_path)
                    server_log.write_bytes(generations[index + 1])
                return segment

            with patch("tools.update_campaign._archive_server_log_segment",
                       side_effect=rotate_after_each_archive):
                result = self._run_fake_slot(
                    package, slot, fake_observer, Path("server/http-events.jsonl"))

            output = Path(result["output"])
            record = json.loads((output / "slot.json").read_text())["server_log"]
            best_effort_path = output / "server-rotated-best-effort.jsonl"
            self.assertEqual(replacements["count"], 4)
            self.assertEqual(record["rotation_passes"], 4)
            self.assertTrue(record["rotation_unstable"])
            self.assertEqual(record["capture_mode"], "rotation_unstable")
            self.assertEqual(best_effort_path.read_bytes(), generations[4])
            self.assertEqual(record["best_effort_capture"]["status"], "written")
            self.assertEqual(record["best_effort_capture"]["bytes_archived"], len(generations[4]))
            self.assertEqual(record["best_effort_capture"]["sha256"],
                             hashlib.sha256(generations[4]).hexdigest())
            self.assertTrue(record["best_effort_capture"]["path_still_matches_open_inode"])
            self.assertTrue(record["rotation_detected"])
            self.assertEqual(record["status"], "log_replaced_truncated_or_changed")
            self.assertFalse(record["accepted"])
            self.assertTrue(record["review_required"])
            raw_file = json.loads((output / "slot.json").read_text())["raw_files"]
            self.assertEqual(raw_file["server-rotated-best-effort.jsonl"]["sha256"],
                             hashlib.sha256(generations[4]).hexdigest())

    def test_empty_server_log_is_accepted_only_without_update_trigger(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package, slot = self._make_test_campaign(root, "empty-server-log")
            server_log = package / "server" / "http-events.jsonl"
            server_log.parent.mkdir()
            server_log.write_bytes(b'{"event":"older-slot"}\n')
            no_trigger_summary = {"update_state": "pre_window_failed", "lost_controls": 1,
                                  "counters": {"pre_window_failed": 1}}

            def fake_observer(**kwargs):
                (kwargs["output"] / "observations.jsonl").write_bytes(b'{"kind":"awaiting_boot"}\n')
                (kwargs["output"] / "summary.json").write_text(json.dumps(no_trigger_summary))
                return no_trigger_summary

            def fake_serial(_port, _baud, path):
                path.write_bytes(b'{"kind":"serial_open"}\n{"kind":"serial_closed"}\n')
                worker = Mock()
                worker.is_alive.return_value = False
                return threading.Event(), worker

            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
            import external_observer
            with patch("tools.update_campaign._start_serial_capture", side_effect=fake_serial), \
                    patch.object(external_observer, "run", side_effect=fake_observer):
                no_trigger_result = run_slot(
                    package, slot["slot_id"], device_ip="192.0.2.1", serial_port="fake",
                    seconds=2, trigger_after=0.5, target_sequence=2,
                    server_log=Path("server/http-events.jsonl"),
                )

            # Each scheduled output is single-use; create a second package to
            # exercise the unexpected-empty case without overwriting evidence.
            second_package, second_slot = self._make_test_campaign(root, "empty-server-log-trigger")
            second_server_log = second_package / "server" / "http-events.jsonl"
            second_server_log.parent.mkdir()
            second_server_log.write_bytes(b"")
            attempt = "00000000-0000-0000-0000-000000000002"
            packet = {"message_type": "UPDATE_TRIGGER", "attempt_id": attempt}
            raw = (json.dumps({"kind": "send_intent", "packet": packet, "hex": "aa"}) + "\n"
                   + json.dumps({"kind": "send", "hex": "aa", "peer": ["192.0.2.1", 3333],
                                "planned_ns": None}) + "\n").encode()
            sent_summary = {"update_state": "acceptance_timeout", "lost_controls": 1,
                            "counters": {}}

            def fake_sent_observer(**kwargs):
                (kwargs["output"] / "observations.jsonl").write_bytes(raw)
                (kwargs["output"] / "summary.json").write_text(json.dumps(sent_summary))
                return sent_summary

            with patch("tools.update_campaign._start_serial_capture", side_effect=fake_serial), \
                    patch.object(external_observer, "run", side_effect=fake_sent_observer):
                sent_result = run_slot(
                    second_package, second_slot["slot_id"], device_ip="192.0.2.1", serial_port="fake",
                    seconds=2, trigger_after=0.5, target_sequence=2,
                    server_log=Path("server/http-events.jsonl"),
                )

            no_trigger_metadata = json.loads(
                (Path(no_trigger_result["output"]) / "slot.json").read_text())
            sent_metadata = json.loads((Path(sent_result["output"]) / "slot.json").read_text())
            self.assertEqual(no_trigger_result["server_log_status"], "empty_no_update_trigger")
            self.assertFalse(no_trigger_metadata["server_log"].get("review_required", False))
            self.assertTrue(no_trigger_metadata["server_log"]["accepted"])
            self.assertEqual(no_trigger_metadata["raw_files"]["server.jsonl"]["size_bytes"], 0)
            self.assertEqual(sent_result["dispatch_status"], "acceptance_timeout")
            self.assertEqual(sent_result["server_log_status"],
                             "unexpected_empty_after_update_trigger")
            self.assertFalse(sent_metadata["server_log"]["accepted"])
            self.assertTrue(sent_metadata["server_log"]["review_required"])
            self.assertTrue((Path(sent_result["output"]) / "slot.json").is_file())

    def test_archived_jsonl_is_parsed_after_hash_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            schedule = make_schedule(1, "archive-test")
            campaign = {"campaign_id": str(uuid.uuid4())}
            for slot in schedule["slots"]:
                self._write_slot(root, campaign, slot)
            first = schedule["slots"][0]
            slot_dir = root / "raw" / "slots" / first["slot_id"]
            observation = slot_dir / "observations.jsonl"
            observation.write_bytes(b'{"kind":"ok"}\n{"kind":NaN}\n')
            record_path = slot_dir / "slot.json"
            record = json.loads(record_path.read_text())
            record["raw_files"]["observations.jsonl"] = {
                "sha256": hashlib.sha256(observation.read_bytes()).hexdigest(),
                "size_bytes": observation.stat().st_size,
            }
            record_path.write_text(json.dumps(record))
            with self.assertRaisesRegex(CampaignError, "non-finite JSON number"):
                _load_slot_records(root, schedule, campaign)

    def test_run_slot_keeps_observer_jsonl_byte_identical(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            artifacts = []
            for scenario in ("native", "interp", "aot"):
                artifact = root / f"{scenario}.bin"
                artifact.write_bytes(scenario.encode())
                artifacts.append({"artifact_id": scenario, "scenario": scenario,
                                  "path": str(artifact),
                                  "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()})
            manifest = root / "artifacts.json"
            manifest.write_text(json.dumps({"schema_version": 1, "artifacts": artifacts}))
            metadata = root / "metadata.json"
            metadata.write_text('{"protocol_revision":"test"}')
            package = root / "campaign"
            plan(package, metadata, manifest, 1, "raw-preservation")
            slot = json.loads((package / "schedule.json").read_text())["slots"][0]
            attempt = "00000000-0000-0000-0000-000000000001"
            packet = {"message_type": "UPDATE_TRIGGER", "attempt_id": attempt}
            raw = (json.dumps({"kind": "send_intent", "packet": packet, "hex": "aa"}) + "\r\n"
                   + json.dumps({"kind": "send", "hex": "aa", "peer": ["127.0.0.1", 3333]}) + "\r\n").encode()

            def fake_observer(**kwargs):
                (kwargs["output"] / "observations.jsonl").write_bytes(raw)
                summary = {"update_state": "accepted_without_v2", "lost_controls": 1,
                           "counters": {"update_accepted": 1}}
                (kwargs["output"] / "summary.json").write_text(json.dumps(summary))
                return summary

            def fake_serial(_port, _baud, path):
                path.write_bytes(b"serial bytes\n")
                worker = Mock()
                worker.is_alive.return_value = False
                return threading.Event(), worker

            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
            import external_observer
            with patch("tools.update_campaign._start_serial_capture", side_effect=fake_serial), \
                    patch.object(external_observer, "run", side_effect=fake_observer):
                result = run_slot(package, slot["slot_id"], device_ip="192.0.2.1", serial_port="fake",
                                  seconds=2, trigger_after=0.5, target_sequence=2)
            self.assertEqual((Path(result["output"]) / "observations.jsonl").read_bytes(), raw)

    def test_issue20_run_slot_rejects_nonprotocol_capture_timing(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            artifacts = []
            for scenario in ("native", "interp", "aot"):
                artifact = root / f"{scenario}.bin"
                artifact.write_bytes(scenario.encode())
                artifacts.append({"artifact_id": scenario, "scenario": scenario,
                                  "path": str(artifact),
                                  "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()})
            manifest = root / "artifacts.json"
            manifest.write_text(json.dumps({"schema_version": 1, "artifacts": artifacts}))
            metadata = root / "metadata.json"
            metadata.write_text(json.dumps({"protocol_revision": "issue20-v1.0",
                                           "campaign_type": "pilot"}))
            package = root / "campaign"
            plan(package, metadata, manifest, 6, "issue20-runner-check")
            slot = json.loads((package / "schedule.json").read_text())["slots"][0]
            with self.assertRaisesRegex(CampaignError, "requires --seconds 150"):
                run_slot(package, slot["slot_id"], device_ip="192.0.2.1",
                         serial_port="fake", seconds=149, trigger_after=60,
                         target_sequence=2)

    def test_issue20_run_slot_requires_protocol_block_count(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            artifacts = []
            for scenario in ("native", "interp", "aot"):
                artifact = root / f"{scenario}.bin"
                artifact.write_bytes(scenario.encode())
                artifacts.append({"artifact_id": scenario, "scenario": scenario,
                                  "path": str(artifact),
                                  "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()})
            manifest = root / "artifacts.json"
            manifest.write_text(json.dumps({"schema_version": 1, "artifacts": artifacts}))
            metadata = root / "metadata.json"
            metadata.write_text(json.dumps({"protocol_revision": "issue20-v1.0",
                                           "campaign_type": "normal"}))
            package = root / "campaign"
            plan(package, metadata, manifest, 6, "issue20-invalid-normal-count")
            slot = json.loads((package / "schedule.json").read_text())["slots"][0]
            with self.assertRaisesRegex(CampaignError, "requires N=30 blocks"):
                run_slot(package, slot["slot_id"], device_ip="192.0.2.1",
                         serial_port="fake", seconds=150, trigger_after=60,
                         target_sequence=2)

    @staticmethod
    def _make_test_campaign(root, seed):
        artifacts = []
        for scenario in ("native", "interp", "aot"):
            artifact = root / f"{seed}-{scenario}.bin"
            artifact.write_bytes(scenario.encode())
            artifacts.append({"artifact_id": f"{seed}-{scenario}", "scenario": scenario,
                              "path": str(artifact),
                              "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()})
        manifest = root / f"{seed}-artifacts.json"
        manifest.write_text(json.dumps({"schema_version": SCHEMA_VERSION,
                                        "artifacts": artifacts}))
        metadata = root / f"{seed}-metadata.json"
        metadata.write_text('{"protocol_revision":"test"}')
        package = root / f"{seed}-campaign"
        plan(package, metadata, manifest, 1, seed)
        slot = json.loads((package / "schedule.json").read_text())["slots"][0]
        return package, slot

    def _run_fake_slot(self, package, slot, fake_observer, server_log):
        def fake_serial(_port, _baud, path):
            path.write_bytes(b'{"kind":"serial_open"}\n{"kind":"serial_closed"}\n')
            worker = Mock()
            worker.is_alive.return_value = False
            return threading.Event(), worker

        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
        import external_observer
        with patch("tools.update_campaign._start_serial_capture", side_effect=fake_serial), \
                patch.object(external_observer, "run", side_effect=fake_observer):
            return run_slot(
                package, slot["slot_id"], device_ip="192.0.2.1", serial_port="fake",
                seconds=2, trigger_after=0.5, target_sequence=2, server_log=server_log,
            )

    @staticmethod
    def _write_slot(root, campaign, scheduled, *, instrument_status="valid",
                    accepted_status="success", slot_success=True):
        slot_dir = root / "raw" / "slots" / scheduled["slot_id"]
        slot_dir.mkdir(parents=True)
        raw_files = {}
        for filename, content in (("observations.jsonl", b"{}\n"),
                                  ("summary.json", b"{}\n"),
                                  ("device.jsonl", b"{}\n")):
            path = slot_dir / filename
            path.write_bytes(content)
            raw_files[filename] = {"sha256": hashlib.sha256(content).hexdigest(),
                                   "size_bytes": len(content)}
        attempt_id = str(uuid.uuid4())
        row = {
            "schema_version": SCHEMA_VERSION,
            "campaign_id": campaign["campaign_id"],
            "slot_id": scheduled["slot_id"],
            "block_id": scheduled["block_id"],
            "scenario": scheduled["scenario"],
            "order_in_block": scheduled["order_in_block"],
            "raw_files": raw_files,
            "dispatches": [{"attempt_id": attempt_id, "dispatch_status": "accepted",
                             "instrument_status": instrument_status,
                             "observed_update_state": "activated"}],
            "accepted_attempt_ids": [attempt_id],
            "accepted_attempt_status": accepted_status,
            "accepted_attempt_status_null_reason": None,
            "slot_success": slot_success,
        }
        (slot_dir / "slot.json").write_text(json.dumps(row))


if __name__ == "__main__":
    unittest.main()
