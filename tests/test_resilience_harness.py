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
    resilience_preliminary_plan,
    _read_serial_events,
    _observer_gap_series,
    _observer_packet_times,
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


def observer_packet(message_type, host_ns, *, boot="0123456789abcdef",
                    sensor_status=0, age_ms=100, control_status=0,
                    module_version=1, requested_gpio=0, observed_gpio=0,
                    sample_id=1, sequence=None):
    if message_type == "TELEMETRY":
        body = {"sample_id": sample_id, "temperature_mC": 23000,
                "age_ms": age_ms, "sensor_status": sensor_status}
    else:
        body = {
        "sample_id": sample_id, "module_version": module_version,
        "requested_gpio": requested_gpio, "observed_gpio": observed_gpio,
        "status": control_status,
        }
    return {
        "kind": "decoded",
        "host_monotonic_ns": host_ns,
        "packet": {
            "attempt_id": ATTEMPT,
            "boot_id": int(boot, 16),
            "sequence": sequence if sequence is not None else int(host_ns % (2**32 - 1)) + 1,
            "message_type": message_type,
            "body": body,
        },
    }


def observer_session(*, initial_version=1, max_sample_age_ms=1000,
                     attempt_id=ATTEMPT):
    return {"kind": "session_start", "initial_version": initial_version,
            "max_sample_age_ms": max_sample_age_ms, "attempt_id": attempt_id,
            "period_ns": 100_000_000, "timeout_ns": 300_000_000}


def observer_control_request(host_ns=1_050_000_000, *, boot="0123456789abcdef",
                             temperature=23000, sample_id=1, sent=True):
    raw = f"request-{boot}-{sample_id}"
    intent = {
        "kind": "send_intent", "host_monotonic_ns": host_ns, "hex": raw,
        "packet": {
            "attempt_id": ATTEMPT, "boot_id": int(boot, 16),
            "sequence": sample_id,
            "message_type": "CONTROL_REQUEST",
            "body": {"sample_id": sample_id,
                     "input_temperature_mC": temperature},
        },
    }
    records = [intent]
    if sent:
        records.append({"kind": "send", "host_monotonic_ns": host_ns + 1,
                        "hex": raw})
    return records


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

    def test_issue22_preliminary_plan_is_exactly_the_closed_16_attempt_proof(self):
        plan = resilience_preliminary_plan("issue22-seed")
        expected = [
            (case, scenario, 1, "failure")
            for case in ("ENV-SIG", "ENV-HASH", "ENV-OLD")
            for scenario in ("native", "interp", "aot")
        ] + [
            ("WASM-ZERO", scenario, 1, "control") for scenario in ("interp", "aot")
        ] + [
            (case, scenario, 1, "failure")
            for case in ("WASM-OOB", "WASM-DIV0")
            for scenario in ("interp", "aot")
        ] + [("NAT-ABORT", "native", 1, "failure")]
        self.assertEqual(len(plan), 16)
        self.assertEqual(
            [(row["case_id"], row["scenario"], row["repetition"], row["purpose"])
             for row in plan], expected)
        self.assertEqual({row["plan_index"] for row in plan}, set(range(1, 17)))
        self.assertEqual({row["event"] for row in plan}, {"fault_request"})
        self.assertTrue(all(row["status"] == "planned" for row in plan))
        self.assertTrue(all(set(row) == {
            "event", "attempt_id", "case_id", "scenario", "repetition",
            "purpose", "plan_index", "status",
        } for row in plan))
        self.assertEqual(len({row["attempt_id"] for row in plan}), 16)
        self.assertEqual(plan, resilience_preliminary_plan("issue22-seed"))
        self.assertNotEqual(
            [row["attempt_id"] for row in plan],
            [row["attempt_id"] for row in resilience_preliminary_plan("another-seed")],
        )

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
        accepted = serial_event("fault_accepted", case="ENV-OLD",
                                functional_timeout_ms_candidate=300,
                                host_monotonic_ns=1_000_000_000)
        rejection = serial_event("update_rejected", reason="SEQUENCE_NOT_NEWER",
                                 case="ENV-OLD",
                                 stage="envelope_sequence", real_envelope_validator=True,
                                 nvs_floor_checked=True, active_slot_unchanged=True,
                                 active_version_unchanged=True,
                                 accepted_sequence_unchanged=True,
                                 host_monotonic_ns=1_100_000_000)
        restored = serial_event("control_slot_restored", case="ENV-OLD",
                                control_slot_recovered=True,
                                control_health_check_passed=True,
                                host_monotonic_ns=1_200_000_000)
        recovery = serial_event("control_recovery_completed",
                                case="ENV-OLD", host_monotonic_ns=1_500_000_000)
        udp = [observer_session(), *observer_control_request(host_ns=1_200_000_000),
               observer_packet("TELEMETRY", 900_000_000),
               observer_packet("CONTROL_RESULT", 950_000_000),
               observer_packet("TELEMETRY", 1_300_000_000),
               observer_packet("CONTROL_RESULT", 1_400_000_000)]
        result = classify_attempt([accepted, rejection, restored, recovery],
                                  expected_case="ENV-OLD", udp_records=udp)
        self.assertEqual(result["status"], "candidate_passed")
        self.assertEqual(result["functional_timeout_status"], "candidate_only")
        self.assertEqual(result["stage"], "envelope_sequence")
        accepted["functional_timeout_frozen"] = True
        self.assertEqual(classify_attempt([accepted, rejection, restored, recovery],
                                          expected_case="ENV-OLD",
                                          udp_records=udp)["status"], "passed")
        rejection["stage"] = "udp_trigger"
        self.assertEqual(classify_attempt([accepted, rejection, restored, recovery],
                                          udp_records=udp)["status"], "observed_failure")

    def test_env_rejection_without_observer_receipts_is_incomplete(self):
        accepted = serial_event("fault_accepted", case="ENV-SIG",
                                host_monotonic_ns=1_000_000_000)
        rejection = serial_event("update_rejected", reason="SIGNATURE_INVALID",
                                 case="ENV-SIG", stage="envelope_signature",
                                 real_envelope_validator=True,
                                 active_slot_unchanged=True,
                                 active_version_unchanged=True,
                                 accepted_sequence_unchanged=True,
                                 payload_written=False,
                                 host_monotonic_ns=1_100_000_000)
        recovery = serial_event("control_recovery_completed",
                                case="ENV-SIG", host_monotonic_ns=1_200_000_000)
        self.assertEqual(classify_attempt([accepted, rejection, recovery],
                                          expected_case="ENV-SIG")["status"],
                         "accepted_incomplete")

    def test_wasm_trap_requires_observed_udp_receipts_and_recovery_timing(self):
        accepted = serial_event("fault_accepted", case="WASM-OOB",
                                host_monotonic_ns=1_000_000_000)
        trap = serial_event("wasm_trap", case="WASM-OOB", call_ok=False,
                            timed_out=False,
                            exception_raw="out of bounds memory access",
                            functional_timeout_ms_candidate=300,
                            host_monotonic_ns=1_100_000_000)
        restored = serial_event("control_slot_restored", case="WASM-OOB",
                                control_slot_recovered=True,
                                control_health_check_passed=True,
                                host_monotonic_ns=1_200_000_000)
        recovery = serial_event("control_recovery_completed", case="WASM-OOB",
                                control_slot_recovered=True,
                                telemetry_within_timeout=True,
                                control_recovery_within_30s=True,
                                host_monotonic_ns=1_350_000_000)
        udp = [observer_session(), *observer_control_request(host_ns=1_200_000_000),
               observer_packet("TELEMETRY", 1_250_000_000),
               observer_packet("CONTROL_RESULT", 1_300_000_000)]
        result = classify_attempt([accepted, trap, restored, recovery],
                                  expected_case="WASM-OOB", udp_records=udp)
        self.assertEqual(result["status"], "candidate_passed")
        self.assertTrue(result["telemetry_udp_received"])
        self.assertTrue(result["control_udp_received"])
        self.assertEqual(classify_attempt([accepted, trap, restored, recovery],
                                          expected_case="WASM-OOB")["status"],
                         "accepted_incomplete")

    def test_wasm_zero_control_requires_received_udp_evidence(self):
        accepted = serial_event("fault_accepted", case="WASM-ZERO",
                                host_monotonic_ns=1_000_000_000)
        control = serial_event("wasm_control", case="WASM-ZERO", call_ok=True,
                               return_value=0x12345678,
                               host_monotonic_ns=1_050_000_000)
        restored = serial_event("control_slot_restored", case="WASM-ZERO",
                                control_slot_recovered=True,
                                host_monotonic_ns=1_100_000_000)
        recovery = serial_event("control_recovery_completed", case="WASM-ZERO",
                                control_slot_recovered=True,
                                host_monotonic_ns=1_500_000_000)
        self.assertEqual(classify_attempt([accepted, control, restored, recovery],
                                          expected_case="WASM-ZERO")["status"],
                         "accepted_incomplete")
        udp = [observer_session(), *observer_control_request(host_ns=1_200_000_000),
               observer_packet("TELEMETRY", 1_300_000_000),
               observer_packet("CONTROL_RESULT", 1_300_000_000)]
        self.assertEqual(classify_attempt([accepted, control, restored, recovery],
                                          expected_case="WASM-ZERO",
                                          udp_records=udp)["status"], "candidate_passed")

    def test_observer_control_requires_intent_with_successful_send(self):
        request = observer_control_request(sent=False)
        result = observer_packet("CONTROL_RESULT", 1_300_000_000)
        self.assertEqual(_observer_packet_times(
            [observer_session(), *request, result], ATTEMPT,
            "0123456789abcdef", "CONTROL_RESULT"), [])

    def test_late_control_response_is_not_a_valid_recovery_receipt(self):
        request = observer_control_request(host_ns=1_000_000_000)
        response = observer_packet("CONTROL_RESULT", 1_310_000_000)
        self.assertEqual(_observer_packet_times(
            [observer_session(), *request, response], ATTEMPT,
            "0123456789abcdef", "CONTROL_RESULT"), [])

    def test_control_request_in_flight_at_acceptance_uses_overlapping_deadline(self):
        accepted = serial_event("fault_accepted", case="ENV-SIG",
                                functional_timeout_ms_candidate=300,
                                host_monotonic_ns=1_000_000_000)
        rejection = serial_event("update_rejected", case="ENV-SIG",
                                 reason="SIGNATURE_INVALID", stage="envelope_signature",
                                 real_envelope_validator=True, payload_written=False,
                                 active_slot_unchanged=True,
                                 active_version_unchanged=True,
                                 accepted_sequence_unchanged=True,
                                 host_monotonic_ns=1_100_000_000)
        restored = serial_event("control_slot_restored", case="ENV-SIG",
                                control_slot_recovered=True,
                                host_monotonic_ns=1_200_000_000)
        recovery = serial_event("control_recovery_completed", case="ENV-SIG",
                                host_monotonic_ns=1_600_000_000)
        udp = [observer_session(),
               *observer_control_request(host_ns=900_000_000, sample_id=1),
               *observer_control_request(host_ns=1_200_000_000, sample_id=2),
               observer_packet("TELEMETRY", 1_300_000_000, sample_id=2),
               observer_packet("CONTROL_RESULT", 1_150_000_000, sample_id=1),
               observer_packet("CONTROL_RESULT", 1_400_000_000, sample_id=2)]
        result = classify_attempt([accepted, rejection, restored, recovery],
                                  expected_case="ENV-SIG", udp_records=udp)
        self.assertEqual(result["status"], "candidate_passed")
        self.assertEqual(result["control_requests_checked"], 2)
        udp.remove(next(record for record in udp
                        if record.get("kind") == "decoded" and
                        record["packet"]["message_type"] == "CONTROL_RESULT" and
                        record["packet"]["body"]["sample_id"] == 1))
        result = classify_attempt([accepted, rejection, restored, recovery],
                                  expected_case="ENV-SIG", udp_records=udp)
        self.assertEqual(result["status"], "accepted_incomplete")
        self.assertEqual(result["control_requests_overdue"], 1)

    def test_env_recovery_requires_telemetry_gap_and_control_deadline_evidence(self):
        accepted = serial_event("fault_accepted", case="ENV-SIG",
                                functional_timeout_ms_candidate=100,
                                host_monotonic_ns=1_000_000_000)
        rejection = serial_event("update_rejected", case="ENV-SIG",
                                 reason="SIGNATURE_INVALID", stage="envelope_signature",
                                 real_envelope_validator=True, payload_written=False,
                                 active_slot_unchanged=True,
                                 active_version_unchanged=True,
                                 accepted_sequence_unchanged=True,
                                 host_monotonic_ns=1_100_000_000)
        restored = serial_event("control_slot_restored", case="ENV-SIG",
                                control_slot_recovered=True,
                                host_monotonic_ns=1_200_000_000)
        recovery = serial_event("control_recovery_completed", case="ENV-SIG",
                                host_monotonic_ns=1_500_000_000)
        udp = [observer_session(), *observer_control_request(host_ns=1_200_000_000),
               observer_packet("TELEMETRY", 1_300_000_000),
               observer_packet("CONTROL_RESULT", 1_400_000_000)]
        result = classify_attempt([accepted, rejection, restored, recovery],
                                  expected_case="ENV-SIG", udp_records=udp)
        self.assertEqual(result["status"], "accepted_incomplete")
        self.assertFalse(result["telemetry_gap_series_within_candidate"])
        udp[-1] = observer_packet("CONTROL_RESULT", 1_510_000_000)
        result = classify_attempt([accepted, rejection, restored, recovery],
                                  expected_case="ENV-SIG", udp_records=udp)
        self.assertEqual(result["status"], "accepted_incomplete")
        self.assertFalse(result["control_udp_received"])

    def test_duplicate_decoded_telemetry_cannot_shrink_gap_series(self):
        telemetry = observer_packet("TELEMETRY", 1_310_000_000, sequence=27)
        duplicate = observer_packet("TELEMETRY", 1_320_000_000, sequence=27)
        records = [telemetry, duplicate]
        self.assertEqual(_observer_packet_times(
            records, ATTEMPT, "0123456789abcdef", "TELEMETRY"),
            [1_310_000_000])
        evidence = _observer_gap_series(
            records, ATTEMPT, "0123456789abcdef", 1_000_000_000,
            1_350_000_000, 300_000_000)
        self.assertFalse(evidence["telemetry_gap_series_within_candidate"])

    def test_wasm_wrong_exception_timeout_and_execution_failure_fail_closed(self):
        accepted = serial_event("fault_accepted", case="WASM-DIV0",
                                host_monotonic_ns=1_000_000_000)
        trap = serial_event("wasm_trap", case="WASM-DIV0", call_ok=False,
                            timed_out=False,
                            exception_raw="integer divide by zero",
                            host_monotonic_ns=1_100_000_000)
        unexpected = dict(trap, exception_raw="out of bounds memory access")
        timeout = dict(trap, timed_out=True)
        recovery = serial_event("control_recovery_completed", case="WASM-DIV0",
                                host_monotonic_ns=1_200_000_000)
        self.assertEqual(classify_attempt([accepted, unexpected, recovery])["status"],
                         "observed_failure")
        self.assertEqual(classify_attempt([accepted, timeout, recovery])["status"],
                         "observed_failure")
        failed = serial_event("fault_execution_failed", case="WASM-DIV0")
        self.assertEqual(classify_attempt([accepted, trap, recovery, failed])["status"],
                         "observed_failure")

    def test_wasm_recovery_uses_observer_control_after_serial_completion(self):
        accepted = serial_event("fault_accepted", case="WASM-OOB",
                                host_monotonic_ns=1_000_000_000)
        trap = serial_event("wasm_trap", case="WASM-OOB", call_ok=False,
                            timed_out=False,
                            exception_raw="out of bounds memory access",
                            functional_timeout_ms_candidate=300,
                            host_monotonic_ns=1_100_000_000)
        restored = serial_event("control_slot_restored", case="WASM-OOB",
                                control_slot_recovered=True,
                                host_monotonic_ns=1_200_000_000)
        recovery = serial_event("control_recovery_completed", case="WASM-OOB",
                                control_slot_recovered=True,
                                telemetry_within_timeout=True,
                                control_recovery_within_30s=True,
                                host_monotonic_ns=1_300_000_000)
        udp = [observer_session(), *observer_control_request(host_ns=1_200_000_000),
               observer_packet("TELEMETRY", 1_250_000_000),
               observer_packet("CONTROL_RESULT", 1_300_000_000)]
        result = classify_attempt([accepted, trap, restored, recovery],
                                  expected_case="WASM-OOB", udp_records=udp)
        self.assertEqual(result["status"], "candidate_passed")
        self.assertEqual(result["recovery_endpoint_host_monotonic_ns"],
                         1_300_000_000)

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

    def test_session_metadata_is_scoped_to_attempt_and_not_unmatched_udp(self):
        other_attempt = "00000000-0000-0000-0000-000000000001"
        session = observer_session(max_sample_age_ms=0, attempt_id=other_attempt)
        report = correlate([serial_event("fault_accepted")], [session])
        attempt = report["attempts"][0]
        self.assertEqual(attempt["udp_record_count"], 0)
        self.assertEqual(report["unmatched_udp_records"], [])

    def test_retired_zero_attempt_udp_is_not_attached_to_previous_trial(self):
        accepted = serial_event("fault_accepted", case="ENV-OLD",
                                host_monotonic_ns=1_000_000_000)
        rejected = serial_event("update_rejected", case="ENV-OLD",
                                host_monotonic_ns=1_100_000_000)
        recovery = serial_event("control_recovery_completed", case="ENV-OLD",
                                host_monotonic_ns=1_300_000_000)
        retired_packet = {
            "kind": "decoded", "host_monotonic_ns": 1_500_000_000,
            "packet": {"attempt_id": "00000000-0000-0000-0000-000000000000",
                       "boot_id": int(accepted["boot_id"], 16),
                       "message_type": "TELEMETRY"},
        }
        report = correlate([accepted, rejected, recovery], [retired_packet])
        attempt = report["attempts"][0]
        self.assertEqual(attempt["udp_record_count"], 0)
        self.assertEqual(report["unmatched_udp_records"], [retired_packet])

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

    def test_partial_host_write_is_preserved_without_claiming_acceptance(self):
        partial = make_fault_request_record(ATTEMPT, "NAT-ABORT", "native", 1234)
        partial["dispatch_status"] = "partial_write"
        partial["bytes_written"] = 7
        report = correlate([], [], [partial])
        attempt = report["attempts"][0]
        self.assertEqual(attempt["status"], "unconfirmed")
        self.assertEqual(attempt["fault_requests"], [partial])
        self.assertEqual(attempt["dispatch_statuses"], ["partial_write"])

    def test_nat_abort_requires_raw_marker_panic_reset_and_recovery(self):
        accepted = serial_event("fault_accepted", case="NAT-ABORT",
                                host_monotonic_ns=1_000_000_000)
        marker = parse_serial_event(
            "resilience:controlled_abort attempt_id="
            "b78edc42-e6c3-4598-8609-55a061b1c8d6 case_id=NAT-ABORT "
            "boot_id=0123456789abcdef monotonic_us=20\r\n")
        marker["host_monotonic_ns"] = 1_200_000_000
        boot = serial_event("boot_observed", case="NAT-ABORT",
                            boot="fedcba9876543210", reset_reason="ESP_RST_PANIC",
                            host_monotonic_ns=2_000_000_000)
        recovery = serial_event("control_recovery_completed", case="NAT-ABORT",
                                boot="fedcba9876543210",
                                telemetry_udp_tx=True, control_udp_tx=True,
                                host_monotonic_ns=2_500_000_000)
        udp = [observer_session(),
               *observer_control_request(boot="0123456789abcdef", host_ns=1_050_000_000,
                                         sample_id=1),
               *observer_control_request(boot="fedcba9876543210", host_ns=2_100_000_000,
                                         sample_id=2),
               observer_packet("TELEMETRY", 1_100_000_000,
                               boot="0123456789abcdef", sample_id=1),
               observer_packet("CONTROL_RESULT", 1_150_000_000,
                               boot="0123456789abcdef", sample_id=1),
               observer_packet("TELEMETRY", 2_300_000_000,
                               boot="fedcba9876543210", sample_id=2),
               observer_packet("CONTROL_RESULT", 2_400_000_000,
                               boot="fedcba9876543210", sample_id=2)]
        self.assertEqual(marker["event"], "abort_marker")
        self.assertEqual(classify_attempt(
            [accepted, marker, boot, recovery], expected_case="NAT-ABORT",
            udp_records=udp)["status"],
            "candidate_passed")
        self.assertEqual(classify_attempt(
            [accepted, boot, recovery], expected_case="NAT-ABORT",
            udp_records=udp)["status"],
            "accepted_incomplete")
        interrupted = [record for record in udp if not (
            record.get("packet", {}).get("boot_id") == int("0123456789abcdef", 16))]
        self.assertEqual(classify_attempt(
            [accepted, marker, boot, recovery], expected_case="NAT-ABORT",
            udp_records=interrupted)["status"],
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
