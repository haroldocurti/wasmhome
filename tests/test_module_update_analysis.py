import json
import pathlib
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
from analyze_module_update import analyze, normalize_boot_id, parse_update_events, write_exclusive


ATTEMPT = "00000000-0000-0000-0000-000000000017"
BOOT = 0x1122334455667788
TELEMETRY_SHA = "a" * 64
MANIFEST_SHA = "f" * 64
CONTROL_V1_SHA = "b" * 64
PAYLOAD_SHA = "d" * 64
CONTROL_V2_SHA = PAYLOAD_SHA


def update_event(phase, elapsed_us, *, boot_id=BOOT, telemetry_generation=7,
                 control_generation=3, control_module_version=1,
                 control_sha256=CONTROL_V1_SHA, status=None,
                 payload_sha256=None, payload_bytes_expected=328,
                 payload_bytes_received=328):
    expected_status = {
        "accepted": "ok",
        "manifest_download_started": "in_progress",
        "payload_download_started": "in_progress",
        "payload_download_progress": "in_progress",
        "activating": "in_progress",
    }.get(phase, "ok")
    return {
        "event": phase,
        "attempt_id": ATTEMPT,
        "boot_id": f"{boot_id:016x}",
        "scenario": 2,
        "component": 1,
        "phase": phase,
        "status": status if status is not None else expected_status,
        "failure": "none",
        "release_version": "v2",
        "target_version": 2,
        "sequence": 2,
        "elapsed_us": elapsed_us,
        "elapsed_scope": "boot",
        "payload_sha256": payload_sha256,
        "payload_bytes_expected": payload_bytes_expected,
        "payload_bytes_received": payload_bytes_received,
        "manifest_sha256": MANIFEST_SHA,
        "heap": {
            "internal_free_bytes": 180_000 - elapsed_us,
            "internal_largest_free_block_bytes": 100_000 - elapsed_us,
            "psram_free_bytes": None,
            "psram_largest_free_block_bytes": None,
        },
        "telemetry_generation": telemetry_generation,
        "telemetry_sha256": TELEMETRY_SHA,
        "control_generation": control_generation,
        "control_module_version": control_module_version,
        "control_sha256": control_sha256,
        "control_artifact_bytes": 328,
        "ledger_readback_sequence": 2 if phase == "floor_committed" else None,
    }


def fixture():
    events = [
        update_event("accepted", 1_000),
        update_event("manifest_download_started", 1_200),
        update_event("manifest_downloaded", 2_000),
        update_event("signature_verified", 3_000),
        update_event("payload_download_started", 4_000),
        update_event("payload_download_progress", 5_000, status="in_progress",
                     payload_bytes_received=128),
        update_event("payload_verified", 7_000, payload_sha256=PAYLOAD_SHA),
        update_event("staged", 7_500),
        update_event("activating", 7_700),
        update_event("candidate_loaded", 8_000),
        update_event("candidate_instantiated", 9_000),
        update_event("candidate_health_check", 9_500),
        update_event("floor_committed", 9_800),
        update_event("control_activated", 10_000, control_generation=4,
                     control_module_version=2, control_sha256=CONTROL_V2_SHA),
        update_event("previous_control_released", 11_000, control_generation=4,
                     control_module_version=2, control_sha256=CONTROL_V2_SHA),
    ]
    serial = "\r\n".join("I (123) update: WASMHOME_UPDATE," + json.dumps(row) for row in events).encode()
    rows = [
        {"kind": "session_start", "host_monotonic_ns": 100, "initial_version": 1,
         "scenario": 2, "target_sequence": 2, "trigger_after": 5},
        {"kind": "send_intent", "host_monotonic_ns": 450,
         "packet": {"attempt_id": ATTEMPT, "message_type": "UPDATE_TRIGGER",
                    "body": {"component": 1, "scenario": 2,
                             "target_version": 2, "target_sequence": 2}}},
        {"kind": "telemetry_valid", "host_monotonic_ns": 200, "boot_id": BOOT,
         "sequence": 1, "sample_id": 1, "age_ms": 50},
        {"kind": "telemetry_valid", "host_monotonic_ns": 300, "boot_id": BOOT,
         "sequence": 2, "sample_id": 1, "age_ms": 60},
        {"kind": "telemetry_valid", "host_monotonic_ns": 400, "boot_id": BOOT,
         "sequence": 3, "sample_id": 2, "age_ms": 70},
        {"kind": "control_valid", "host_monotonic_ns": 300, "boot_id": BOOT,
         "module_version": 1, "input_temperature_mC": 27000,
         "requested_gpio": 1, "observed_gpio": 1},
        {"kind": "update_accepted", "host_monotonic_ns": 500},
        {"kind": "control_valid", "host_monotonic_ns": 600, "boot_id": BOOT,
         "module_version": 1, "input_temperature_mC": 32000,
         "requested_gpio": 1, "observed_gpio": 1},
        {"kind": "telemetry_valid", "host_monotonic_ns": 550, "boot_id": BOOT,
         "sequence": 4, "sample_id": 2, "age_ms": 80},
        {"kind": "v2_observed", "host_monotonic_ns": 700},
        {"kind": "control_valid", "host_monotonic_ns": 700, "boot_id": BOOT,
         "module_version": 2, "input_temperature_mC": 27000,
         "requested_gpio": 0, "observed_gpio": 0},
        {"kind": "telemetry_valid", "host_monotonic_ns": 800, "boot_id": BOOT,
         "sequence": 7, "sample_id": 3, "age_ms": 60},
    ]
    observations = "\n".join(json.dumps(row) for row in rows).encode() + b"\n"
    summary = {
        "attempt_id": ATTEMPT,
        "boot_ids": [BOOT],
        "initial_version": 1,
        "period_ns": 100,
        "accepted_ns": 500,
        "activated_ns": 700,
        "update_state": "activated",
        "update_total_ns": 450,
        "telemetry_max_gap_ns": 400,
        "control_max_gap_ns": 400,
        "lost_controls": 0,
        "unresolved_sequence_gaps": {f"{BOOT}:TELEMETRY": 0},
        "counters": {"telemetry_valid": 2, "control_valid": 3},
    }
    return serial, observations, json.dumps(summary).encode()


class ModuleUpdateAnalysisTests(unittest.TestCase):
    def test_complete_interpreter_update_is_reconciled(self):
        report = analyze(*fixture(), expected_scenario=2)
        self.assertEqual(report["result"], "passed")
        self.assertTrue(all(check["passed"] for check in report["checks"].values()))
        self.assertEqual(report["payload"]["bytes_received"], 328)
        self.assertTrue(report["checks"]["verified_payload_is_active_module"]["passed"])
        self.assertEqual(report["memory"]["minimum_internal_free_bytes"], 169_000)
        self.assertEqual(report["phase_durations_us"][
            "payload_download_started_to_payload_verified_us"], 3_000)
        self.assertEqual(report["service"]["control_v1_to_first_v2_gap_ns"], 400)
        self.assertEqual(report["service"]["last_valid_control_to_first_v2_gap_ns"], 100)
        self.assertEqual(report["service"]["control_unavailability_estimate_ns"], 0)
        self.assertEqual(report["checks"]["telemetry_covered_update_interval"]["observed"][
            "valid_telemetry_during_interval"], 1)

    def test_new_boot_does_not_prove_modular_same_boot_update(self):
        serial, observations, summary = fixture()
        serial = serial.replace(b"1122334455667788", b"8877665544332211")
        report = analyze(serial, observations, summary, expected_scenario=2)
        self.assertFalse(report["checks"]["same_boot"]["passed"])
        self.assertEqual(report["result"], "incomplete_or_failed")

    def test_changed_telemetry_generation_or_digest_fails_continuity(self):
        serial, observations, summary = fixture()
        rows = [json.loads(line.split(b"WASMHOME_UPDATE,", 1)[1])
                for line in serial.splitlines()]
        target = next(row for row in rows if row["phase"] == "control_activated")
        target["telemetry_generation"] = 8
        serial = b"\n".join(b"WASMHOME_UPDATE," + json.dumps(row).encode() for row in rows)
        report = analyze(serial, observations, summary, expected_scenario=2)
        self.assertFalse(report["checks"]["telemetry_instance_unchanged"]["passed"])

    def test_any_reported_intermediate_telemetry_snapshot_must_remain_stable(self):
        serial, observations, summary = fixture()
        rows = [json.loads(line.split(b"WASMHOME_UPDATE,", 1)[1])
                for line in serial.splitlines()]
        next(row for row in rows if row["phase"] == "candidate_health_check")[
            "telemetry_sha256"] = "e" * 64
        serial = b"\n".join(b"WASMHOME_UPDATE," + json.dumps(row).encode() for row in rows)
        report = analyze(serial, observations, summary, expected_scenario=2)
        self.assertFalse(report["checks"]["reported_intermediate_telemetry_snapshots_stable"]["passed"])

    def test_invalid_update_boot_id_is_not_ignored(self):
        serial, observations, summary = fixture()
        rows = [json.loads(line.split(b"WASMHOME_UPDATE,", 1)[1])
                for line in serial.splitlines()]
        rows[0]["boot_id"] = "unknown"
        serial = b"\n".join(b"WASMHOME_UPDATE," + json.dumps(row).encode() for row in rows)
        report = analyze(serial, observations, summary, expected_scenario=2)
        self.assertFalse(report["checks"]["same_boot"]["passed"])

    def test_mixed_or_unknown_device_clock_scope_is_rejected(self):
        serial, observations, summary = fixture()
        rows = [json.loads(line.split(b"WASMHOME_UPDATE,", 1)[1])
                for line in serial.splitlines()]
        next(row for row in rows if row["phase"] == "candidate_loaded")["elapsed_scope"] = "attempt"
        serial = b"\n".join(b"WASMHOME_UPDATE," + json.dumps(row).encode() for row in rows)
        report = analyze(serial, observations, summary, expected_scenario=2)
        self.assertFalse(report["checks"]["single_boot_elapsed_clock"]["passed"])

    def test_no_telemetry_for_multiple_baseline_periods_fails_coverage(self):
        serial, observations, summary = fixture()
        rows = [json.loads(line) for line in observations.splitlines()]
        rows = [row for row in rows if not (
            row.get("kind") == "telemetry_valid" and row.get("host_monotonic_ns") == 550)]
        observations = b"\n".join(json.dumps(row).encode() for row in rows) + b"\n"
        summary_value = json.loads(summary)
        summary_value["activated_ns"] = 850
        rows.append({"kind": "telemetry_valid", "host_monotonic_ns": 900,
                     "boot_id": BOOT, "sequence": 7, "sample_id": 3, "age_ms": 60})
        observations = b"\n".join(json.dumps(row).encode() for row in rows) + b"\n"
        report = analyze(serial, observations, json.dumps(summary_value).encode(), expected_scenario=2)
        self.assertFalse(report["checks"]["telemetry_covered_update_interval"]["passed"])

    def test_sub_cadence_swap_does_not_require_an_in_window_telemetry_packet(self):
        serial, observations, summary = fixture()
        rows = [json.loads(line) for line in observations.splitlines()]
        rows = [row for row in rows if not (
            row.get("kind") == "telemetry_valid" and row.get("host_monotonic_ns") == 550)]
        observations = b"\n".join(json.dumps(row).encode() for row in rows) + b"\n"
        summary_value = json.loads(summary)
        summary_value["activated_ns"] = 550
        report = analyze(serial, observations, json.dumps(summary_value).encode(), expected_scenario=2)
        coverage = report["checks"]["telemetry_covered_update_interval"]
        self.assertTrue(coverage["passed"])
        self.assertEqual(coverage["observed"]["minimum_expected_from_cadence"], 0)
        self.assertEqual(coverage["observed"]["valid_telemetry_during_interval"], 0)

    def test_bunched_telemetry_at_one_edge_does_not_cover_a_long_update(self):
        serial, observations, summary = fixture()
        rows = [json.loads(line) for line in observations.splitlines()]
        rows.extend({"kind": "telemetry_valid", "host_monotonic_ns": 501 + offset,
                     "boot_id": BOOT, "sequence": 20 + offset,
                     "sample_id": 10 + offset, "age_ms": 50}
                    for offset in range(8))
        observations = b"\n".join(json.dumps(row).encode() for row in rows) + b"\n"
        summary_value = json.loads(summary)
        summary_value["activated_ns"] = 1_400
        report = analyze(serial, observations, json.dumps(summary_value).encode(), expected_scenario=2)
        self.assertFalse(report["checks"]["telemetry_covered_update_interval"]["passed"])

    def test_memory_snapshots_are_required_for_a_pass(self):
        serial, observations, summary = fixture()
        rows = [json.loads(line.split(b"WASMHOME_UPDATE,", 1)[1])
                for line in serial.splitlines()]
        next(row for row in rows if row["phase"] == "candidate_instantiated").pop("heap")
        serial = b"\n".join(b"WASMHOME_UPDATE," + json.dumps(row).encode() for row in rows)
        report = analyze(serial, observations, summary, expected_scenario=2)
        self.assertFalse(report["checks"]["memory_snapshots_recorded"]["passed"])

    def test_v1_result_after_first_v2_is_a_version_regression(self):
        serial, observations, summary = fixture()
        rows = [json.loads(line) for line in observations.splitlines()]
        rows.append({"kind": "control_valid", "host_monotonic_ns": 750, "boot_id": BOOT,
                     "module_version": 1, "input_temperature_mC": 27000,
                     "requested_gpio": 1, "observed_gpio": 1})
        observations = b"\n".join(json.dumps(row).encode() for row in rows) + b"\n"
        report = analyze(serial, observations, summary, expected_scenario=2)
        self.assertFalse(report["checks"]["no_failure_rollback_or_version_regression"]["passed"])

    def test_wrong_payload_size_or_missing_signature_phase_fails_closed(self):
        serial, observations, summary = fixture()
        rows = [json.loads(line.split(b"WASMHOME_UPDATE,", 1)[1])
                for line in serial.splitlines()]
        verified = next(row for row in rows if row["phase"] == "payload_verified")
        verified["payload_bytes_received"] = 327
        signature = next(row for row in rows if row["phase"] == "signature_verified")
        signature["phase"] = "signature_rejected"
        serial = b"\n".join(b"WASMHOME_UPDATE," + json.dumps(row).encode() for row in rows)
        report = analyze(serial, observations, summary, expected_scenario=2)
        self.assertFalse(report["checks"]["payload_complete_and_hashed"]["passed"])
        self.assertFalse(report["checks"]["verified_payload_is_active_module"]["passed"])
        self.assertFalse(report["checks"]["required_stages_succeeded"]["passed"])
        self.assertEqual(report["result"], "incomplete_or_failed")

    def test_verified_payload_must_match_active_control_hash_and_size(self):
        serial, observations, summary = fixture()
        rows = [json.loads(line.split(b"WASMHOME_UPDATE,", 1)[1])
                for line in serial.splitlines()]
        for row in rows:
            if row["phase"] in ("control_activated", "previous_control_released"):
                row["control_sha256"] = "e" * 64
                row["control_artifact_bytes"] = 329
        serial = b"\n".join(b"WASMHOME_UPDATE," + json.dumps(row).encode() for row in rows)
        report = analyze(serial, observations, summary, expected_scenario=2)
        self.assertFalse(report["checks"]["verified_payload_is_active_module"]["passed"])

    def test_out_of_order_event_stream_or_rollback_is_rejected(self):
        serial, observations, summary = fixture()
        rows = [json.loads(line.split(b"WASMHOME_UPDATE,", 1)[1])
                for line in serial.splitlines()]
        rows[2], rows[3] = rows[3], rows[2]
        rows.append(update_event("rollback_detected", 12_000, status="error"))
        serial = b"\n".join(b"WASMHOME_UPDATE," + json.dumps(row).encode() for row in rows)
        report = analyze(serial, observations, summary, expected_scenario=2)
        self.assertFalse(report["checks"]["device_timeline_monotonic"]["passed"])
        self.assertFalse(report["checks"]["no_failure_rollback_or_version_regression"]["passed"])

    def test_event_parser_rejects_malformed_json(self):
        with self.assertRaisesRegex(ValueError, "invalid WASMHOME_UPDATE JSON"):
            parse_update_events(b"WASMHOME_UPDATE,{broken}\n")

    def test_boot_id_must_fit_wire_uint64(self):
        self.assertIsNone(normalize_boot_id(2**64))
        self.assertIsNone(normalize_boot_id("18446744073709551616"))

    def test_phase_statuses_follow_started_and_completed_semantics(self):
        serial, observations, summary = fixture()
        rows = [json.loads(line.split(b"WASMHOME_UPDATE,", 1)[1])
                for line in serial.splitlines()]
        next(row for row in rows if row["phase"] == "accepted")["status"] = "in_progress"
        next(row for row in rows if row["phase"] == "payload_download_started")["status"] = "ok"
        serial = b"\n".join(b"WASMHOME_UPDATE," + json.dumps(row).encode() for row in rows)
        report = analyze(serial, observations, summary, expected_scenario=2)
        self.assertFalse(report["checks"]["required_stages_succeeded"]["passed"])

    def test_manifest_hash_and_floor_readback_are_required(self):
        serial, observations, summary = fixture()
        rows = [json.loads(line.split(b"WASMHOME_UPDATE,", 1)[1])
                for line in serial.splitlines()]
        next(row for row in rows if row["phase"] == "signature_verified")[
            "manifest_sha256"] = "0" * 64
        next(row for row in rows if row["phase"] == "floor_committed")[
            "ledger_readback_sequence"] = 1
        serial = b"\n".join(b"WASMHOME_UPDATE," + json.dumps(row).encode() for row in rows)
        report = analyze(serial, observations, summary, expected_scenario=2)
        self.assertFalse(report["checks"]["manifest_signature_bound_to_download"]["passed"])
        self.assertFalse(report["checks"]["rollback_floor_readback_matches_target"]["passed"])

    def test_analysis_output_refuses_to_overwrite_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory) / "analysis.json"
            output.write_text('{"previous":"kept"}\n', encoding="utf-8")
            with self.assertRaises(FileExistsError):
                write_exclusive(output, {"result": "passed"})
            self.assertEqual(output.read_text(encoding="utf-8"), '{"previous":"kept"}\n')


if __name__ == "__main__":
    unittest.main()
