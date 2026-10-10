"""Local rejection gates; importing the helper never opens UART or network."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("session_lxc_capture", Path(__file__).with_name("capture-lxc-probe.py"))
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)


def event(timestamp, kind, value):
    return {"host_monotonic_ns": timestamp, "kind": kind, "value": value}


def good_records():
    records = [event(1, "host_reset_released", {}),
               event(2, "WASMHOME_EVENT", {"event": "boot_observed", "boot_id": "0000000000000001"})]
    for sequence, timestamp in enumerate((3, 4, 5), 1):
        records.append(event(timestamp, "udp_received", {"peer": [capture.DEVICE, 3333], "message_type": 1,
                             "boot_id": 1, "sequence": sequence}))
    records.append(event(6, "serial_command_sent", {"command": "WH_LXC", "write_started_ns": 6}))
    records.append(event(7, "WASMHOME_LXC", {"result": "ok", "nonce": "a" * 64,
                         "server_ipv4": capture.SERVER, "server_port": 18080, "local_ipv4": capture.DEVICE}))
    for sequence, timestamp in enumerate((8, 9, 10, 11), 4):
        records.append(event(timestamp, "udp_received", {"peer": [capture.DEVICE, 3333], "message_type": 1,
                             "boot_id": 1, "sequence": sequence}))
    return records


class CaptureGates(unittest.TestCase):
    def test_existing_directory_rejected_before_uart_or_socket_and_preserves_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "capture"
            target.mkdir()
            evidence = target / "serial.log"
            evidence.write_bytes(b"prior evidence")
            with mock.patch.object(capture.serial, "Serial") as uart, mock.patch.object(capture.socket, "socket") as network:
                with self.assertRaises(FileExistsError):
                    capture.capture(target, "success")
                uart.assert_not_called()
                network.assert_not_called()
            self.assertEqual(evidence.read_bytes(), b"prior evidence")

    def test_positive_transcript_remains_pending_server_pairing(self):
        summary = capture.summarize(good_records(), "success")
        self.assertTrue(summary["local_capture_checked"])
        self.assertEqual(summary["classification"], "positive_transcript_pending_server_pairing")
        self.assertEqual(summary["udp_after_result"], 4)

    def test_stale_or_duplicate_serial_results_are_rejected(self):
        for duplicate in (False, True):
            records = good_records()
            reply = next(r for r in records if r["kind"] == "WASMHOME_LXC")
            if duplicate:
                records.append(event(12, "WASMHOME_LXC", reply["value"]))
            else:
                reply["host_monotonic_ns"] = 5
            self.assertFalse(capture.summarize(records, "success")["local_capture_checked"])

    def test_other_serial_command_cannot_correlate_as_lxc_request(self):
        records = good_records()
        next(r for r in records if r["kind"] == "serial_command_sent")["value"]["command"] = "WH_WDT"
        self.assertFalse(capture.summarize(records, "success")["local_capture_checked"])

    def test_equal_frame_command_timestamp_remains_inconclusive(self):
        records = good_records()
        next(r for r in records if r["kind"] == "serial_command_sent")["host_monotonic_ns"] = 5
        summary = capture.summarize(records, "success")
        self.assertEqual(summary["udp_before_command"], 2)
        self.assertFalse(summary["local_capture_checked"])
        self.assertIn("perf_counter_ns", summary["host_clock"])

    def test_boot_absent_repeated_or_changed_cannot_claim_continuity(self):
        for case in ("absent", "repeated", "changed"):
            records = good_records()
            if case == "absent":
                records = [r for r in records if r["kind"] != "WASMHOME_EVENT"]
            elif case == "repeated":
                records.append(event(12, "WASMHOME_EVENT", {"event": "boot_observed", "boot_id": "2"}))
            else:
                records[-1]["value"]["boot_id"] = 2
            with self.subTest(case=case):
                self.assertFalse(capture.summarize(records, "success")["local_capture_checked"])

    def test_malformed_nonce_wrong_ips_or_wrong_port_cannot_pass(self):
        for field, value in (("nonce", "z" * 64), ("nonce", "a" * 63), ("local_ipv4", ""),
                             ("local_ipv4", "192.0.2.1"), ("server_ipv4", "192.0.2.2"), ("server_port", 80)):
            records = good_records()
            next(r for r in records if r["kind"] == "WASMHOME_LXC")["value"][field] = value
            with self.subTest(field=field, value=value):
                self.assertFalse(capture.summarize(records, "success")["local_capture_checked"])

    def test_only_pre_result_udp_or_duplicate_post_frames_cannot_pass(self):
        for duplicate in (False, True):
            records = good_records()
            for record in records:
                if record["kind"] == "udp_received" and record["host_monotonic_ns"] > 7:
                    if duplicate:
                        record["value"]["sequence"] = 4
                    else:
                        record["host_monotonic_ns"] = 5
            self.assertFalse(capture.summarize(records, "success")["local_capture_checked"])

    def test_negative_attempt_never_proves_absent_reader(self):
        for status in ("socket_error", "timeout", "disabled_or_invalid_config", "response_mismatch"):
            records = good_records()
            reply = next(r for r in records if r["kind"] == "WASMHOME_LXC")["value"]
            reply.update(result=status, local_ipv4="")
            summary = capture.summarize(records, "failure")
            self.assertFalse(summary["negative_condition_confirmed"])
            self.assertFalse(summary["expected_result_confirmed"])
            self.assertFalse(summary["local_capture_checked"])
            self.assertEqual(summary["classification"], "transport_attempt_failed" if status in ("socket_error", "timeout") else "inconclusive")


if __name__ == "__main__":
    unittest.main()
