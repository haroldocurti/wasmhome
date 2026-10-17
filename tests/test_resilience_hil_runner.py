import contextlib
from collections import Counter
import io
import json
import os
import pty
import select
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import termios

from tools.resilience_harness import _read_jsonl, resilience_plan
from tools.resilience_hil_runner import (
    CAMPAIGN_ID, PREFLIGHT_SCHEMA, HILInputError, _capture_serial,
    EXPECTED_FIRMWARE_SHA256, ExclusiveSerialPort, _dispatch,
    correlate_http_rows, correlate_http_snapshots,
    _gate_report_classifications, _observation_coverage, _post_execution_coverage,
    execute_trial, load_plan, main,
    validate_preflight,
)


def row_for(case, scenario):
    return next(row for row in resilience_plan("hil-runner-test")
                if row["case_id"] == case and row["scenario"] == scenario)


def preflight(item, *, floor=2, active_version=2, active_sequence=2):
    common = {
        "schema": PREFLIGHT_SCHEMA,
        "campaign_id": CAMPAIGN_ID,
        "profile": {"native": "native", "interp": "module-interp", "aot": "module-aot"}[item["scenario"]],
        "boot_id": "0123456789abcdef",
        "firmware_sha256": EXPECTED_FIRMWARE_SHA256[
            {"native": "native", "interp": "module-interp", "aot": "module-aot"}[item["scenario"]]],
        "native_state": None,
        "module_state": None,
    }
    if item["scenario"] == "native":
        common["native_state"] = {"ledger_present": True,
                                  "accepted_floor": floor, "pending": False}
    else:
        common["module_state"] = {
            "ledger_present": True, "accepted_floor": floor,
            "active_version": active_version, "active_sequence": active_sequence,
            "pending": False,
        }
    return common


class FakeSerial:
    def __init__(self, data=b"raw serial\r\n"):
        self.data = data

    @property
    def in_waiting(self):
        return len(self.data)

    def read(self, count):
        result, self.data = self.data[:count], self.data[count:]
        return result


class SelectableFakeSerial:
    def __init__(self):
        self.reader, self.writer = socket.socketpair()
        self.port = "fake-no-hardware"
        self.baudrate = 115200
        self.write_calls = []
        self.closed = False

    def fileno(self):
        return self.reader.fileno()

    @property
    def in_waiting(self):
        return 0

    def read(self, _count):
        return b""

    def write(self, data):
        self.write_calls.append(data)
        return len(data)

    def flush(self):
        pass

    def close(self):
        self.closed = True
        self.reader.close()
        self.writer.close()


class SparseObserver:
    """A deterministic observer double for coverage-gate tests."""
    def __init__(self, _log, _peer, _period_ns, _timeout_ns, *,
                 initial_version, attempt_id, max_sample_age_ms):
        self.boot_id = int("0123456789abcdef", 16)
        self.retired_boots = set()
        self.success_by_boot = Counter({self.boot_id: self.initial_controls})
        self.success_times = [1] * self.initial_controls
        self.telemetry_times = [1] * self.initial_telemetry
        self.requests = {
            index: {"sent_ns": 1, "success": True}
            for index in range(self.initial_controls)
        }
        self._next_request_id = self.initial_controls

    # Set by each test before the double is installed.
    initial_controls = 1
    initial_telemetry = 1

    def record(self, *_args, **_kwargs):
        pass

    def event(self, *_args, **_kwargs):
        pass

    def expire(self, _now):
        pass

    def send_control(self, _udp, now, _planned_ns):
        self._next_request_id += 1
        self.requests[self._next_request_id] = {"sent_ns": now, "success": False}

    def receive(self, _raw, _peer, _now):
        pass

    def summary(self):
        return {"valid_controls_by_boot": dict(self.success_by_boot)}


class ResilienceHILRunnerTests(unittest.TestCase):
    def test_plan_loader_requires_full_matrix(self):
        with tempfile.TemporaryDirectory() as temp_name:
            path = Path(temp_name) / "plan.jsonl"
            rows = resilience_plan("fixed-seed")
            path.write_text("".join(json.dumps(item) + "\n" for item in rows))
            self.assertEqual(len(load_plan(path)), 72)
            path.write_text(json.dumps(rows[0]) + "\n")
            with self.assertRaises(HILInputError):
                load_plan(path)

    def test_preflight_rejects_zero_and_wrong_native_floors(self):
        old = row_for("ENV-OLD", "native")
        with self.assertRaisesRegex(HILInputError, "floor 0"):
            validate_preflight(preflight(old, floor=0), old)
        self.assertEqual(validate_preflight(preflight(old, floor=1), old)["native_state"]["accepted_floor"], 1)
        hashed = row_for("ENV-HASH", "native")
        with self.assertRaisesRegex(HILInputError, "exactly 2"):
            validate_preflight(preflight(hashed, floor=3), hashed)
        self.assertEqual(validate_preflight(preflight(hashed, floor=2), hashed)["campaign_id"], CAMPAIGN_ID)
        wrong_build = preflight(hashed)
        wrong_build["firmware_sha256"] = "f" * 64
        with self.assertRaisesRegex(HILInputError, "registered issue #22 profile build"):
            validate_preflight(wrong_build, hashed)

    def test_modular_preflight_requires_floor_two_and_active_v2(self):
        item = row_for("WASM-OOB", "interp")
        with self.assertRaisesRegex(HILInputError, "active module version/sequence=2"):
            validate_preflight(preflight(item, active_version=1), item)
        with self.assertRaisesRegex(HILInputError, "active module version/sequence=2"):
            validate_preflight(preflight(item, floor=0), item)
        self.assertEqual(validate_preflight(preflight(item), item)["profile"], "module-interp")

    def test_preflight_rejects_extra_fields_that_could_carry_private_data(self):
        item = row_for("ENV-SIG", "native")
        value = preflight(item)
        value["wifi_password"] = "not allowed"
        with self.assertRaisesRegex(HILInputError, "privacy-minimized"):
            validate_preflight(value, item)

    def test_raw_serial_capture_uses_base64_and_monotonic_timestamp(self):
        with tempfile.TemporaryDirectory() as temp_name:
            path = Path(temp_name) / "serial.jsonl"
            with path.open("w") as stream:
                self.assertEqual(_capture_serial(FakeSerial(b"bytes\x00\xff"), stream), 7)
            row = json.loads(path.read_text())
            self.assertEqual(row["kind"], "serial_bytes")
            self.assertEqual(row["byte_count"], 7)
            self.assertEqual(__import__("base64").b64decode(row["raw_base64"]), b"bytes\x00\xff")
            self.assertIsInstance(row["host_monotonic_ns"], int)

    def test_no_boot_observed_means_no_fault_request_is_written(self):
        item = row_for("ENV-SIG", "native")
        with tempfile.TemporaryDirectory() as temp_name:
            output = Path(temp_name) / "attempt"
            output.mkdir(mode=0o700)
            serial_port = SelectableFakeSerial()
            udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            result = execute_trial(
                item=item, preflight=preflight(item), serial_port=serial_port,
                udp=udp, output=output, bind_ip="127.0.0.1", device_ip="127.0.0.1",
                udp_port=0, device_port=3333, pre_seconds=0.005, post_seconds=0.005,
                boot_wait_seconds=0.005, period_ms=0.1, timeout_ms=1,
                max_sample_age_ms=1000)
            self.assertEqual(result["status"], "no_boot_observed")
            self.assertEqual(result["dispatch"]["dispatch_status"], "not_sent_no_boot")
            self.assertEqual(serial_port.write_calls, [])
            self.assertEqual(_read_jsonl(output / "requests.jsonl"), [])
            self.assertFalse(result["fault_accepted_inferred"])
            self.assertTrue(serial_port.closed)

    def test_observation_coverage_requires_30_valid_samples_of_each_kind(self):
        self.assertFalse(_observation_coverage(1, 1)["passed"])
        self.assertFalse(_observation_coverage(30, 29)["passed"])
        self.assertFalse(_observation_coverage(29, 30)["passed"])
        self.assertTrue(_observation_coverage(30, 30)["passed"])

    def test_post_coverage_starts_at_matching_execution_terminal_and_gates_classification(self):
        item = row_for("ENV-SIG", "native")
        events = [
            {"event": "fault_accepted", "attempt_id": item["attempt_id"],
             "case_id": item["case_id"], "host_monotonic_ns": 100},
            {"event": "update_rejected", "attempt_id": item["attempt_id"],
             "case_id": item["case_id"], "host_monotonic_ns": 200},
        ]
        controls = [150] * 31 + list(range(200, 230))
        telemetry = [150] * 31 + list(range(200, 230))
        result = _post_execution_coverage(events, item, controls, telemetry)
        self.assertEqual(result["valid_control_count"], 30)
        self.assertEqual(result["valid_telemetry_count"], 30)
        self.assertTrue(result["passed"])

        report = {"attempts": [{"classification": {"status": "candidate_passed"}}]}
        _gate_report_classifications(report, _observation_coverage(1, 1))
        self.assertEqual(report["attempts"][0]["classification"]["status"],
                         "insufficient_observation_coverage")

    def test_fault_request_is_not_sent_without_preflight_coverage(self):
        item = row_for("ENV-SIG", "native")
        SparseObserver.initial_controls = 1
        SparseObserver.initial_telemetry = 1
        with tempfile.TemporaryDirectory() as temp_name:
            output = Path(temp_name) / "attempt"
            output.mkdir(mode=0o700)
            serial_port = SelectableFakeSerial()
            udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            with mock.patch("tools.resilience_hil_runner.Observer", SparseObserver):
                result = execute_trial(
                    item=item, preflight=preflight(item), serial_port=serial_port,
                    udp=udp, output=output, bind_ip="127.0.0.1", device_ip="127.0.0.1",
                    udp_port=0, device_port=3333, pre_seconds=0.001, post_seconds=0.04,
                    boot_wait_seconds=0.04, period_ms=1, timeout_ms=1,
                    max_sample_age_ms=1000)
            self.assertEqual(result["status"], "insufficient_preflight_observation_coverage")
            self.assertEqual(result["dispatch"]["dispatch_status"],
                             "not_sent_insufficient_preflight_coverage")
            self.assertEqual(serial_port.write_calls, [])
            self.assertEqual(_read_jsonl(output / "requests.jsonl"), [])
            self.assertFalse(result["coverage_gate"]["passed"])

    def test_short_post_observation_cannot_be_classified(self):
        item = row_for("ENV-SIG", "native")
        SparseObserver.initial_controls = 30
        SparseObserver.initial_telemetry = 30
        with tempfile.TemporaryDirectory() as temp_name:
            output = Path(temp_name) / "attempt"
            output.mkdir(mode=0o700)
            serial_port = SelectableFakeSerial()
            udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            with mock.patch("tools.resilience_hil_runner.Observer", SparseObserver):
                result = execute_trial(
                    item=item, preflight=preflight(item), serial_port=serial_port,
                    udp=udp, output=output, bind_ip="127.0.0.1", device_ip="127.0.0.1",
                    udp_port=0, device_port=3333, pre_seconds=0.001, post_seconds=0.04,
                    boot_wait_seconds=0.04, period_ms=1, timeout_ms=1,
                    max_sample_age_ms=1000)
            self.assertEqual(result["status"], "insufficient_post_execution_observation_coverage")
            self.assertTrue(result["coverage_gate"]["before_fault_request"]["passed"])
            self.assertFalse(result["coverage_gate"]["after_fault_request"]["passed"])
            report = json.loads((output / "correlation.json").read_text())
            self.assertFalse(report["coverage_gate"]["passed"])
            self.assertEqual(serial_port.write_calls, [
                __import__("tools.resilience_harness", fromlist=["make_fault_request"])
                .make_fault_request(item["attempt_id"], item["case_id"])])

    def test_timing_that_rounds_to_zero_is_rejected_before_io(self):
        item = row_for("ENV-SIG", "native")
        with tempfile.TemporaryDirectory() as temp_name:
            output = Path(temp_name) / "attempt"
            for timing in ({"period_ms": 1e-10, "timeout_ms": 1},
                           {"period_ms": 1, "timeout_ms": 1e-10}):
                serial_port = mock.Mock()
                udp = mock.Mock()
                with self.subTest(timing=timing), self.assertRaisesRegex(HILInputError, "rounds to zero"):
                    execute_trial(
                        item=item, preflight=preflight(item), serial_port=serial_port,
                        udp=udp, output=output, bind_ip="127.0.0.1", device_ip="127.0.0.1",
                        udp_port=3333, device_port=3333, pre_seconds=1, post_seconds=1,
                        boot_wait_seconds=1, max_sample_age_ms=1000, **timing)
                serial_port.assert_not_called()
                udp.getsockname.assert_not_called()
                self.assertFalse(output.exists())

    def test_fault_dispatch_writes_exact_closed_json_line(self):
        item = row_for("WASM-OOB", "interp")
        fake = SelectableFakeSerial()
        with tempfile.TemporaryDirectory() as temp_name:
            path = Path(temp_name) / "requests.jsonl"
            with path.open("w") as stream:
                result = _dispatch(fake, stream, item)
            from tools.resilience_harness import make_fault_request
            self.assertEqual(fake.write_calls, [make_fault_request(item["attempt_id"], item["case_id"])])
            self.assertEqual(result["dispatch_status"], "sent")
            self.assertEqual(result["raw_request"], make_fault_request(item["attempt_id"], item["case_id"]).decode())
        fake.close()

    def test_posix_serial_adapter_works_with_a_virtual_pty(self):
        master_fd, slave_fd = pty.openpty()
        port = None
        try:
            original_termios = termios.tcgetattr(slave_fd)
            port = ExclusiveSerialPort(os.ttyname(slave_fd), 115200)
            self.assertEqual(port.write(b"request\n"), 8)
            port.flush()
            self.assertEqual(os.read(master_fd, 8), b"request\n")
            os.write(master_fd, b"reply\r\n")
            self.assertTrue(select.select([port], [], [], 0.2)[0])
            self.assertEqual(port.in_waiting, 7)
            self.assertEqual(port.read(7), b"reply\r\n")
            port.close()
            self.assertEqual(termios.tcgetattr(slave_fd), original_termios)
        finally:
            if port is not None:
                port.close()
            os.close(master_fd)
            os.close(slave_fd)

    def test_http_routes_are_separate_transport_evidence(self):
        item = row_for("ENV-HASH", "interp")
        base = "resilience/ENV-HASH/scenario-2"
        rows = [
            {"path": f"{base}/manifest.cose", "method": "GET", "status": 200,
             "bytes_sent": 300, "sha256": "b" * 64, "tls_protocol": "TLSv1.3",
             "time_unix_ns": 10},
            {"path": f"{base}/control.wasm", "method": "GET", "status": 200,
             "bytes_sent": 400, "sha256": "c" * 64, "tls_protocol": "TLSv1.3",
             "time_unix_ns": 11},
        ]
        result = correlate_http_rows(item, rows)
        self.assertTrue(result["transport_routes_complete"])
        self.assertFalse(result["fault_accepted_inferred"])
        self.assertIn("attempt_id", result)
        self.assertEqual(result["unexpected_route_count"], 0)

    def test_http_snapshots_require_append_only_and_write_private_report(self):
        item = row_for("ENV-SIG", "native")
        base = "resilience/ENV-SIG/scenario-1/manifest.cose"
        entry = {
            "schema": "wasmhome-issue22-fixture-http-v1", "time_unix_ns": 1,
            "peer": "192.0.2.1", "host": "wasmhome-issue22.test:8445",
            "method": "GET", "path": base, "status": 200, "bytes_sent": 256,
            "sha256": "d" * 64, "tls_protocol": "TLSv1.3", "tls_cipher": "TLS_AES_128_GCM_SHA256",
            "duration_ns": 12,
        }
        with tempfile.TemporaryDirectory() as temp_name:
            directory = Path(temp_name)
            before = directory / "before.jsonl"
            after = directory / "after.jsonl"
            run_dir = directory / "run"
            run_dir.mkdir()
            (run_dir / "run.json").write_text(json.dumps({"schema": "wasmhome-issue22-hil-run-v1",
                                                            "attempt": item}))
            baseline = dict(entry, path="resilience/ENV-OLD/scenario-1/manifest.cose")
            before_bytes = (json.dumps(baseline) + "\n").encode()
            before.write_bytes(before_bytes)
            after.write_bytes(before_bytes + (json.dumps(entry) + "\n").encode())
            result = correlate_http_snapshots(run_dir, before, after)
            self.assertTrue(result["transport_routes_complete"])
            self.assertFalse(result["fault_accepted_inferred"])
            self.assertEqual((run_dir / "http-delta.jsonl").stat().st_mode & 0o777, 0o600)
            after.write_text("{}")  # Make the second input fail prefix validation.
            with self.assertRaisesRegex(HILInputError, "append-only"):
                correlate_http_snapshots(run_dir, before, after)

    def test_zero_floor_barrier_runs_before_serial_or_output_access(self):
        item = row_for("ENV-HASH", "native")
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            plan_file = root / "plan.jsonl"
            preflight_file = root / "preflight.json"
            output = root / "out"
            rows = resilience_plan("barrier-test")
            plan_file.write_text("".join(json.dumps(row) + "\n" for row in rows))
            chosen = rows.index(next(row for row in rows if row["case_id"] == item["case_id"]
                                     and row["scenario"] == item["scenario"])) + 1
            bad = preflight(item, floor=0)
            preflight_file.write_text(json.dumps(bad))
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
                main(["run", "--plan", str(plan_file), "--slot", str(chosen),
                      "--preflight", str(preflight_file), "--arm-attempt-id",
                      rows[chosen - 1]["attempt_id"], "--serial-port", "/dev/ttyACM0",
                      "--device-ip", "192.0.2.3", "--bind-ip", "192.0.2.4",
                      "--output", str(output)])
            self.assertEqual(raised.exception.code, 2)
            self.assertFalse(output.exists())
            self.assertIn("exactly 2", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
