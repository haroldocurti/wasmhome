import base64
import json
import pathlib
import tempfile
import unittest
import uuid

from tools.resilience_harness import (
    ResilienceFormatError,
    classify_attempt,
    correlate,
    make_fault_request,
    make_fault_request_record,
    parse_fault_request,
    parse_serial_event,
    resilience_plan,
    _read_serial_events,
)


ATTEMPT = "b78edc42-e6c3-4598-8609-55a061b1c8d6"


def serial_event(event, *, case="ENV-OLD", boot="0123456789abcdef", **values):
    return {
        "event": event,
        "attempt_id": ATTEMPT,
        "case_id": case,
        "scenario": "native",
        "boot_id": boot,
        "monotonic_us": 10 + len(event),
        **values,
    }


class ResilienceHarnessTests(unittest.TestCase):
    def test_fault_request_is_closed_and_round_trips(self):
        raw = make_fault_request(ATTEMPT, "WASM-OOB")
        self.assertEqual(parse_fault_request(raw), {
            "event": "fault_request", "attempt_id": ATTEMPT, "case_id": "WASM-OOB",
        })
        decoded = json.loads(raw)
        self.assertEqual(set(decoded), {"event", "attempt_id", "case_id"})

    def test_fault_request_rejects_duplicate_unknown_and_unapproved_values(self):
        cases = (
            '{"event":"fault_request","attempt_id":"%s","attempt_id":"%s","case_id":"ENV-SIG"}' % (ATTEMPT, ATTEMPT),
            '{"event":"fault_request","attempt_id":"%s","case_id":"ENV-SIG","url":"https://bad"}' % ATTEMPT,
            '{"event":"fault_request","attempt_id":"%s","case_id":"UPDATE_TRIGGER"}' % ATTEMPT,
            '{"event":"fault_request","attempt_id":"%s","case_id":"ENV-SIG","x":null}' % ATTEMPT,
        )
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(ResilienceFormatError):
                parse_fault_request(raw)

    def test_plan_is_exactly_70_failures_and_two_controls(self):
        plan = resilience_plan("pilot-independent-seed")
        self.assertEqual(len(plan), 72)
        self.assertEqual(len({item["attempt_id"] for item in plan}), 72)
        self.assertEqual(sum(item["purpose"] == "failure" for item in plan), 70)
        self.assertEqual(sum(item["purpose"] == "control" for item in plan), 2)
        counts = {}
        for item in plan:
            key = item["scenario"], item["case_id"]
            counts[key] = counts.get(key, 0) + 1
        self.assertEqual(counts[("native", "NAT-ABORT")], 5)
        self.assertEqual(counts[("interp", "WASM-ZERO")], 1)
        self.assertEqual(counts[("aot", "WASM-ZERO")], 1)
        for scenario in ("native", "interp", "aot"):
            for case in ("ENV-SIG", "ENV-HASH", "ENV-OLD"):
                self.assertEqual(counts[(scenario, case)], 5)
        for scenario in ("interp", "aot"):
            for case in ("WASM-OOB", "WASM-DIV0"):
                self.assertEqual(counts[(scenario, case)], 5)

    def test_serial_parser_requires_correlation_fields_and_prefix(self):
        self.assertIsNone(parse_serial_event(b"normal ESP-IDF log\r\n"))
        event = serial_event("fault_accepted")
        parsed = parse_serial_event("I (100) test: WASMHOME_RESILIENCE," +
                                    json.dumps(event) + "\r\n")
        self.assertEqual(parsed, event)
        event.pop("boot_id")
        with self.assertRaises(ResilienceFormatError):
            parse_serial_event("WASMHOME_RESILIENCE," + json.dumps(event))

    def test_no_acceptance_is_unconfirmed_not_rejection(self):
        events = [serial_event("fault_request")]
        self.assertEqual(classify_attempt(events, expected_case="ENV-OLD"), {
            "status": "unconfirmed", "reason": None, "request_preserved": True,
        })

    def test_explicit_fixture_request_rejection_is_not_envelope_rejection(self):
        rejected = serial_event("fault_request_rejected", case="ENV-HASH",
                                reason="case_not_supported_by_this_firmware")
        self.assertEqual(classify_attempt([rejected], expected_case="ENV-HASH"), {
            "status": "request_rejected",
            "reason": "case_not_supported_by_this_firmware",
        })

    def test_old_envelope_requires_real_sequence_stage_after_acceptance(self):
        accepted = serial_event("fault_accepted")
        rejection = serial_event("update_rejected", reason="SEQUENCE_NOT_NEWER",
                                 stage="envelope_sequence", real_envelope_validator=True,
                                 nvs_floor_checked=True, active_slot_unchanged=True,
                                 active_version_unchanged=True,
                                 accepted_sequence_unchanged=True)
        result = classify_attempt([accepted, rejection], expected_case="ENV-OLD")
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["stage"], "envelope_sequence")
        rejection["stage"] = "udp_trigger"
        self.assertEqual(classify_attempt([accepted, rejection])["status"], "observed_failure")

    def test_serial_events_correlate_udp_separately_on_each_boot(self):
        before = serial_event("fault_accepted")
        after = serial_event("boot_observed", boot="fedcba9876543210",
                             reset_reason="ESP_RST_PANIC")
        udp = [
            {"packet": {"attempt_id": ATTEMPT, "boot_id": int(before["boot_id"], 16)}},
            {"packet": {"attempt_id": ATTEMPT, "boot_id": int(after["boot_id"], 16)}},
        ]
        report = correlate([before, after], udp)
        attempt = report["attempts"][0]
        self.assertEqual(attempt["boots"], [before["boot_id"], after["boot_id"]])
        self.assertEqual(attempt["udp_record_count"], 2)
        self.assertIn("never subtract them", report["clock_rule"])

    def test_request_without_acceptance_is_preserved_as_unconfirmed(self):
        request = make_fault_request_record(ATTEMPT, "NAT-ABORT", "native", 1234)
        report = correlate([], [], [request])
        attempt = report["attempts"][0]
        self.assertEqual(attempt["status"], "unconfirmed")
        self.assertEqual(attempt["fault_requests"], [request])
        self.assertEqual(attempt["serial_events"], [])
        self.assertEqual(attempt["udp_record_count"], 0)

    def test_serial_capture_order_survives_monotonic_reset_after_reboot(self):
        accepted = serial_event("fault_accepted", case="NAT-ABORT", monotonic_us=900_000)
        boot = serial_event("boot_observed", case="NAT-ABORT",
                            boot="fedcba9876543210", monotonic_us=100)
        report = correlate([accepted, boot], [])
        self.assertEqual([item["event"] for item in report["attempts"][0]["serial_events"]],
                         ["fault_accepted", "boot_observed"])

    def test_request_log_requires_actual_dispatch_time_and_matching_wire_bytes(self):
        valid = make_fault_request_record(ATTEMPT, "NAT-ABORT", "native", 1234)
        self.assertEqual(correlate([], [], [valid])["attempts"][0]["status"],
                         "unconfirmed")
        planned = dict(valid)
        del planned["host_monotonic_ns"]
        with self.assertRaises(ResilienceFormatError):
            correlate([], [], [planned])
        mismatched = dict(valid, raw_request=make_fault_request(ATTEMPT, "ENV-OLD").decode("ascii"))
        with self.assertRaises(ResilienceFormatError):
            correlate([], [], [mismatched])

    def test_nat_abort_requires_raw_marker_panic_reset_and_recovery(self):
        accepted = serial_event("fault_accepted", case="NAT-ABORT")
        marker = parse_serial_event(
            "resilience:controlled_abort attempt_id="
            "b78edc42-e6c3-4598-8609-55a061b1c8d6 case_id=NAT-ABORT "
            "boot_id=0123456789abcdef monotonic_us=20\r\n")
        boot = serial_event("boot_observed", case="NAT-ABORT",
                            boot="fedcba9876543210", reset_reason="ESP_RST_PANIC")
        recovery = serial_event("control_recovery_completed", case="NAT-ABORT",
                                boot="fedcba9876543210",
                                telemetry_udp_received=True, control_udp_received=True,
                                recovery_within_30s=True)
        self.assertEqual(marker["event"], "abort_marker")
        self.assertEqual(classify_attempt(
            [accepted, marker, boot, recovery], expected_case="NAT-ABORT")["status"],
            "passed")
        self.assertEqual(classify_attempt(
            [accepted, boot, recovery], expected_case="NAT-ABORT")["status"],
            "accepted_incomplete")

    def test_nat_serial_send_event_alone_does_not_prove_udp_recovery(self):
        accepted = serial_event("fault_accepted", case="NAT-ABORT")
        marker = parse_serial_event(
            "resilience:controlled_abort attempt_id="
            "b78edc42-e6c3-4598-8609-55a061b1c8d6 case_id=NAT-ABORT "
            "boot_id=0123456789abcdef monotonic_us=20")
        boot = serial_event("boot_observed", case="NAT-ABORT",
                            boot="fedcba9876543210", reset_reason="ESP_RST_PANIC")
        recovery = serial_event("control_recovery_completed", case="NAT-ABORT",
                                boot="fedcba9876543210", firmware_udp_tx=True,
                                control_udp_tx=True)
        self.assertEqual(classify_attempt(
            [accepted, marker, boot, recovery], expected_case="NAT-ABORT")["status"],
            "accepted_incomplete")

    def test_abort_marker_rejects_unattributed_or_malformed_raw_lines(self):
        with self.assertRaises(ResilienceFormatError):
            parse_serial_event("resilience:controlled_abort")
        with self.assertRaises(ResilienceFormatError):
            parse_serial_event(
                "resilience:controlled_abort attempt_id=not-a-uuid case_id=NAT-ABORT "
                "boot_id=0123456789abcdef monotonic_us=20")

    def test_update_campaign_serial_capture_is_decoded_with_host_clock(self):
        event = serial_event("fault_accepted", case="NAT-ABORT")
        device_lines = (
            "WASMHOME_RESILIENCE," + json.dumps(event, separators=(",", ":")) + "\r\n" +
            "resilience:controlled_abort attempt_id=" + ATTEMPT +
            " case_id=NAT-ABORT boot_id=0123456789abcdef monotonic_us=20\r\n"
        ).encode("ascii")
        rows = [
            {"kind": "serial_open", "host_monotonic_ns": 10},
            {"kind": "serial_bytes", "host_monotonic_ns": 20,
             "byte_count": len(device_lines),
             "raw_base64": base64.b64encode(device_lines).decode("ascii")},
            {"kind": "serial_closed", "host_monotonic_ns": 30},
        ]
        capture = "\n".join(json.dumps(row, indent=2) for row in rows) + "\n"
        with tempfile.TemporaryDirectory(prefix="wasmhome-serial-capture-") as tmp:
            path = pathlib.Path(tmp) / "serial.jsonl"
            path.write_text(capture, encoding="utf-8")
            events = _read_serial_events(path)
        self.assertEqual([item["event"] for item in events],
                         ["fault_accepted", "abort_marker"])
        self.assertEqual([item["host_monotonic_ns"] for item in events], [20, 20])

    def test_invalid_uuid_and_duplicate_event_fields_are_rejected(self):
        bad = serial_event("fault_accepted", attempt_id="not-a-uuid")
        with self.assertRaises(ResilienceFormatError):
            parse_serial_event("WASMHOME_RESILIENCE," + json.dumps(bad))
        duplicate = ('WASMHOME_RESILIENCE,{"event":"fault_accepted",'
                     '"event":"fault_accepted"}')
        with self.assertRaises(ResilienceFormatError):
            parse_serial_event(duplicate)


if __name__ == "__main__":
    unittest.main()
