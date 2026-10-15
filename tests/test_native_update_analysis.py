import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
from analyze_native_update import analyze


ATTEMPT = "11111111-2222-3333-4444-555555555555"
OLD_BOOT = 0x1122334455667788
NEW_BOOT = 0x8877665544332211
MANIFEST_SHA = "a" * 64
PAYLOAD_SHA = "b" * 64


def event(phase, elapsed_us, *, post=False, status=None):
    status_by_phase = {
        "manifest_download_started": "in_progress",
        "payload_download_started": "in_progress",
        "activating": "in_progress",
        "reboot_requested": "in_progress",
        "boot_pending": "in_progress",
    }
    boot = NEW_BOOT if post else OLD_BOOT
    value = {
        "event": phase,
        "phase": phase,
        "status": status or status_by_phase.get(phase, "ok"),
        "failure": "none",
        "attempt_id": ATTEMPT,
        "boot_id": f"{boot:016x}",
        "scenario": 1,
        "component": 1,
        "release_version": "v2",
        "target_version": 2,
        "sequence": 2,
        "elapsed_us": elapsed_us,
        "elapsed_scope": "boot",
        "payload_bytes_expected": 1024,
        "payload_bytes_received": 1024,
        "payload_sha256": PAYLOAD_SHA,
        "manifest_sha256": MANIFEST_SHA,
        "accepted_floor": 2 if phase == "floor_committed" else 1,
        "heap": {
            "internal_free_bytes": 180_000 - elapsed_us,
            "internal_largest_free_block_bytes": 100_000 - elapsed_us,
            "psram_free_bytes": None,
            "psram_largest_free_block_bytes": None,
        },
    }
    if post:
        value["origin_boot_id"] = f"{OLD_BOOT:016x}"
    if phase == "health_check_passed":
        value["running_sha256"] = PAYLOAD_SHA
    if phase == "floor_committed":
        value["ledger_readback_sequence"] = 2
    return value


def capture():
    events = [
        event("accepted", 1_000),
        event("manifest_download_started", 2_000),
        event("manifest_downloaded", 3_000),
        event("signature_verified", 4_000),
        event("payload_download_started", 5_000),
        event("payload_verified", 6_000),
        event("staged", 7_000),
        event("activating", 8_000),
        event("reboot_requested", 9_000),
        event("boot_pending", 100, post=True),
        event("health_check_passed", 20_000, post=True),
        event("floor_committed", 30_000, post=True),
    ]
    uart = b"\n".join(b"I update: WASMHOME_UPDATE," + json.dumps(row).encode()
                     for row in events) + b"\n"
    rows = [
        {"kind": "session_start", "host_monotonic_ns": 100,
         "initial_version": 1, "scenario": 1, "target_sequence": 2},
        {"kind": "send_intent", "host_monotonic_ns": 450,
         "packet": {"attempt_id": ATTEMPT, "sequence": 9,
                    "boot_id": OLD_BOOT, "message_type": "UPDATE_TRIGGER",
                    "body": {"scenario": 1, "component": 1,
                             "target_version": 2, "target_sequence": 2}}},
        {"kind": "decoded", "host_monotonic_ns": 500,
         "packet": {"attempt_id": ATTEMPT, "sequence": 1,
                    "boot_id": OLD_BOOT, "message_type": "UPDATE_ACCEPTANCE",
                    "body": {"request_sequence": 9, "target_sequence": 2,
                             "status": 1, "reason": 0}}},
        {"kind": "update_accepted", "host_monotonic_ns": 500},
        {"kind": "telemetry_valid", "host_monotonic_ns": 200,
         "boot_id": OLD_BOOT, "sequence": 1},
        {"kind": "telemetry_valid", "host_monotonic_ns": 1_800,
         "boot_id": NEW_BOOT, "sequence": 1},
        {"kind": "control_valid", "host_monotonic_ns": 400,
         "boot_id": OLD_BOOT, "module_version": 1,
         "input_temperature_mC": 27000, "requested_gpio": 1, "observed_gpio": 1},
        {"kind": "control_valid", "host_monotonic_ns": 1_600,
         "boot_id": NEW_BOOT, "module_version": 2,
         "input_temperature_mC": 27000, "requested_gpio": 0, "observed_gpio": 0},
        {"kind": "v2_observed", "host_monotonic_ns": 1_600},
    ]
    observations = b"\n".join(json.dumps(row).encode() for row in rows) + b"\n"
    summary = {
        "attempt_id": ATTEMPT,
        "boot_ids": [OLD_BOOT, NEW_BOOT],
        "initial_version": 1,
        "period_ns": 100,
        "accepted_ns": 500,
        "activated_ns": 1_600,
        "update_state": "activated",
        "lost_controls": 2,
        "control_max_gap_ns": 1_200,
        "telemetry_max_gap_ns": 1_600,
        "unresolved_sequence_gaps": {},
        "counters": {"control_timeout": 2},
    }
    return uart, observations, json.dumps(summary).encode()


class NativeUpdateAnalysisTests(unittest.TestCase):
    def test_complete_ota_reconciles_across_two_boots(self):
        report = analyze(*capture())
        self.assertEqual(report["result"], "passed")
        self.assertTrue(all(check["passed"] for check in report["checks"].values()))
        self.assertEqual(report["old_boot_id"], f"{OLD_BOOT:016x}")
        self.assertEqual(report["new_boot_id"], f"{NEW_BOOT:016x}")
        self.assertEqual(report["payload"]["sha256"], PAYLOAD_SHA)
        self.assertEqual(report["service"]["last_old_control_to_first_v2_host_gap_ns"], 1_200)
        self.assertEqual(report["service"]["lost_controls"], 2)
        self.assertNotIn("reboot_requested_to_boot_pending_us",
                         report["phase_durations_us_within_boot_only"])

    def test_post_reboot_events_must_preserve_origin_boot_and_attempt(self):
        uart, observations, summary = capture()
        rows = [json.loads(line.split(b"WASMHOME_UPDATE,", 1)[1])
                for line in uart.splitlines()]
        next(row for row in rows if row["phase"] == "health_check_passed")[
            "origin_boot_id"] = f"{NEW_BOOT:016x}"
        uart = b"\n".join(b"WASMHOME_UPDATE," + json.dumps(row).encode() for row in rows)
        report = analyze(uart, observations, summary)
        self.assertFalse(report["checks"]["reboot_correlates_old_and_new_boot"]["passed"])

    def test_running_image_hash_must_match_verified_payload(self):
        uart, observations, summary = capture()
        rows = [json.loads(line.split(b"WASMHOME_UPDATE,", 1)[1])
                for line in uart.splitlines()]
        next(row for row in rows if row["phase"] == "health_check_passed")[
            "running_sha256"] = "c" * 64
        uart = b"\n".join(b"WASMHOME_UPDATE," + json.dumps(row).encode() for row in rows)
        report = analyze(uart, observations, summary)
        self.assertFalse(report["checks"]["verified_payload_is_running_image"]["passed"])

    def test_floor_must_advance_and_read_back_target_sequence(self):
        uart, observations, summary = capture()
        rows = [json.loads(line.split(b"WASMHOME_UPDATE,", 1)[1])
                for line in uart.splitlines()]
        next(row for row in rows if row["phase"] == "floor_committed")[
            "accepted_floor"] = 1
        uart = b"\n".join(b"WASMHOME_UPDATE," + json.dumps(row).encode() for row in rows)
        report = analyze(uart, observations, summary)
        self.assertFalse(report["checks"]["anti_rollback_floor_readback_matches_target"]["passed"])

        uart, observations, summary = capture()
        rows = [json.loads(line.split(b"WASMHOME_UPDATE,", 1)[1])
                for line in uart.splitlines()]
        next(row for row in rows if row["phase"] == "floor_committed").pop(
            "ledger_readback_sequence")
        uart = b"\n".join(b"WASMHOME_UPDATE," + json.dumps(row).encode() for row in rows)
        report = analyze(uart, observations, summary)
        self.assertFalse(report["checks"]["anti_rollback_floor_readback_matches_target"]["passed"])

    def test_wrong_discriminating_control_result_or_v1_regression_fails(self):
        uart, observations, summary = capture()
        rows = [json.loads(line) for line in observations.splitlines()]
        rows[-1]["requested_gpio"] = 1
        rows.append({"kind": "control_valid", "host_monotonic_ns": 1_700,
                     "boot_id": NEW_BOOT, "module_version": 1,
                     "input_temperature_mC": 27000,
                     "requested_gpio": 1, "observed_gpio": 1})
        observations = b"\n".join(json.dumps(row).encode() for row in rows) + b"\n"
        report = analyze(uart, observations, summary)
        self.assertFalse(report["checks"]["functional_v1_to_v2_is_discriminating"]["passed"])

    def test_activation_summary_must_match_unique_first_v2_observation(self):
        uart, observations, summary = capture()
        summary_value = json.loads(summary)
        summary_value["activated_ns"] = 1_500
        report = analyze(uart, observations, json.dumps(summary_value).encode())
        self.assertFalse(report["checks"]["observer_activation_matches_first_v2_control"]["passed"])

        rows = [json.loads(line) for line in observations.splitlines()]
        rows.append({"kind": "v2_observed", "host_monotonic_ns": 1_600})
        report = analyze(uart, b"\n".join(json.dumps(row).encode() for row in rows), summary)
        self.assertFalse(report["checks"]["observer_activation_matches_first_v2_control"]["passed"])

    def test_late_control_is_reported_as_service_loss_not_ota_failure(self):
        uart, observations, summary = capture()
        rows = [json.loads(line) for line in observations.splitlines()]
        rows.append({"kind": "late_control", "host_monotonic_ns": 1_200,
                     "boot_id": OLD_BOOT, "module_version": 1, "correct": True})
        rows.append({"kind": "control_valid", "host_monotonic_ns": 1_400,
                     "boot_id": OLD_BOOT, "module_version": 1,
                     "input_temperature_mC": 32000,
                     "requested_gpio": 1, "observed_gpio": 1})
        report = analyze(uart, b"\n".join(json.dumps(row).encode() for row in rows), summary)
        self.assertTrue(report["checks"]["no_failure_or_rollback"]["passed"])
        self.assertEqual(report["service"]["last_old_control_to_first_v2_host_gap_ns"], 200)

    def test_rollback_is_reported_as_recovery_required(self):
        uart, observations, summary = capture()
        rollback = event("rollback_detected", 31_000, post=True, status="error")
        uart += b"WASMHOME_UPDATE," + json.dumps(rollback).encode() + b"\n"
        report = analyze(uart, observations, summary)
        self.assertTrue(report["recovery"]["recovery_required"])
        self.assertTrue(report["recovery"]["recovery_observed"])
        self.assertEqual(report["recovery"]["rollback_or_failure_events"][0]["phase"],
                         "rollback_detected")

    def test_incomplete_boot_identifiers_fail_without_crashing(self):
        uart, observations, summary = capture()
        rows = [json.loads(line.split(b"WASMHOME_UPDATE,", 1)[1])
                for line in uart.splitlines()]
        next(row for row in rows if row["phase"] == "boot_pending")["boot_id"] = "invalid"
        uart = b"\n".join(b"WASMHOME_UPDATE," + json.dumps(row).encode() for row in rows)
        report = analyze(uart, observations, summary)
        self.assertEqual(report["result"], "incomplete_or_failed")
        self.assertFalse(report["checks"]["reboot_correlates_old_and_new_boot"]["passed"])

    def test_bad_signature_phase_and_nonmonotonic_uart_order_fail(self):
        uart, observations, summary = capture()
        rows = [json.loads(line.split(b"WASMHOME_UPDATE,", 1)[1])
                for line in uart.splitlines()]
        next(row for row in rows if row["phase"] == "signature_verified")[
            "manifest_sha256"] = "0" * 64
        rows[3], rows[4] = rows[4], rows[3]
        uart = b"\n".join(b"WASMHOME_UPDATE," + json.dumps(row).encode() for row in rows)
        report = analyze(uart, observations, summary)
        self.assertFalse(report["checks"]["signature_verifies_downloaded_manifest"]["passed"])
        self.assertFalse(report["checks"]["device_timeline_is_ordered_per_boot"]["passed"])


if __name__ == "__main__":
    unittest.main()
