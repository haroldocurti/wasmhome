import hashlib
import json
import sys
import tempfile
import unittest
import uuid
import threading
from unittest.mock import Mock, patch
from pathlib import Path

from tools.update_campaign import (
    CampaignError,
    SCHEMA_VERSION,
    _load_artifacts,
    _load_slot_records,
    make_schedule,
    _observer_attempt_id,
    _read_jsonl,
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
