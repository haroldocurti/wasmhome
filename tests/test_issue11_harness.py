import pathlib
import sys
import tempfile
import time
import uuid
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tools"))
import issue11_harness as harness
from observation_protocol_v1 import (
    AcceptanceStatus, Component, ControlStatus, MessageType, Packet,
    RejectionReason, Scenario, SensorStatus, decode, encode,
)


class MemoryEvents:
    def __init__(self):
        self.rows = []

    def write(self, kind, **values):
        self.rows.append({"kind": kind, **values})


class FakeDevice:
    def __init__(self, version=1, *, wrong_boot_response=False, telemetry_age=0):
        self.version = version
        self.boot_id = 0x1122334455667788
        self.peer = ("192.0.2.44", 3333)
        self.queue = []
        self.highest_sample = 0
        self.gpio = 0
        self.control_cache = {}
        self.control_sequence = 0
        self.telemetry_sequence = 0
        self.telemetry_sample = 0
        self.acceptance_sequence = 0
        self.wrong_boot_response = wrong_boot_response
        self.telemetry_age = telemetry_age
        self.closed = False

    def _enqueue_telemetry(self):
        self.telemetry_sequence += 1
        self.telemetry_sample += 1
        packet = Packet(
            MessageType.TELEMETRY, uuid.UUID(int=0), self.telemetry_sequence,
            self.boot_id, time.monotonic_ns() // 1000,
            {"sample_id": self.telemetry_sample, "temperature_mC": 25_000,
             "age_ms": self.telemetry_age, "sensor_status": SensorStatus.VALID},
        )
        self.queue.append((encode(packet), self.peer))

    def send(self, datagram):
        request = decode(datagram)
        self._enqueue_telemetry()
        if request.message_type == MessageType.CONTROL_REQUEST:
            if request.boot_id != self.boot_id and not self.wrong_boot_response:
                return
            sample_id = request.body["sample_id"]
            temperature = request.body["input_temperature_mC"]
            cached = self.control_cache.get(sample_id)
            if cached:
                if cached[0] != temperature:
                    return
                body = cached[1]
            else:
                if sample_id <= self.highest_sample:
                    return
                threshold = harness.VERSION_THRESHOLD_MC[self.version]
                if temperature >= threshold + 500:
                    self.gpio = 1
                elif temperature <= threshold - 500:
                    self.gpio = 0
                body = {"sample_id": sample_id, "module_version": self.version,
                        "requested_gpio": self.gpio, "observed_gpio": self.gpio,
                        "status": ControlStatus.APPLIED}
                self.control_cache[sample_id] = (temperature, body)
                self.highest_sample = sample_id
            self.control_sequence += 1
            response = Packet(MessageType.CONTROL_RESULT, request.attempt_id,
                              self.control_sequence, self.boot_id,
                              time.monotonic_ns() // 1000, body)
            self.queue.append((encode(response), self.peer))
            return
        if request.message_type == MessageType.UPDATE_TRIGGER:
            self.acceptance_sequence += 1
            body = {"request_sequence": request.sequence,
                    "target_sequence": request.body["target_sequence"],
                    "status": AcceptanceStatus.REJECTED,
                    "reason": RejectionReason.BUSY}
            response = Packet(MessageType.UPDATE_ACCEPTANCE, request.attempt_id,
                              self.acceptance_sequence, self.boot_id,
                              time.monotonic_ns() // 1000, body)
            self.queue.append((encode(response), self.peer))

    def receive(self, timeout_seconds):
        if self.queue:
            return self.queue.pop(0)
        time.sleep(min(max(timeout_seconds, 0), 0.001))
        if self.queue:
            return self.queue.pop(0)
        return None

    def close(self):
        self.closed = True


class Issue11HarnessTests(unittest.TestCase):
    def make_harness(self, device, version=1):
        return harness.Issue11Harness(
            device, MemoryEvents(), control_version=version,
            boot_id=device.boot_id, device_peer=device.peer,
            silence_ms=3, response_timeout_ms=20,
            telemetry_wait_seconds=0.05,
        )

    def test_full_v1_matrix_replay_wrong_boot_conflict_busy_and_fresh_sensor(self):
        device = FakeDevice(version=1)
        runner = self.make_harness(device, version=1)
        result = runner.run()
        self.assertEqual(result["outcome"], "passed")
        self.assertGreaterEqual(result["distinct_valid_ds18b20_samples"], 5)
        self.assertIn("replay.new_device_sequence", [item["name"] for item in result["checks"]])
        self.assertFalse(device.closed)

    def test_full_v2_matrix_uses_30000_millicelsius_threshold(self):
        device = FakeDevice(version=2)
        runner = self.make_harness(device, version=2)
        result = runner.run()
        self.assertEqual(result["outcome"], "passed")
        self.assertEqual(result["threshold_mC"], 30_000)

    def test_plan_only_does_not_open_socket_or_create_output(self):
        with tempfile.TemporaryDirectory(prefix="wasmhome-issue11-plan-") as temp_name:
            output = pathlib.Path(temp_name) / "not-created"
            stdout = StringIO()
            with mock.patch.object(harness.socket, "socket", side_effect=AssertionError("socket opened")):
                with redirect_stdout(stdout):
                    code = harness.main([
                        "--control-version", "1", "--source-revision", "fixture-revision",
                        "--elf", "unused.elf", "--bin", "unused.bin", "--output", str(output),
                    ])
            self.assertEqual(code, 0)
            self.assertFalse(output.exists())
            self.assertIn('"would_send_udp": false', stdout.getvalue())

    def test_execute_rejects_nonfinite_telemetry_wait_before_output_or_socket(self):
        for invalid_wait in ("nan", "inf", "-inf", "121"):
            with self.subTest(telemetry_wait_seconds=invalid_wait), \
                    tempfile.TemporaryDirectory(prefix="wasmhome-issue11-timeout-") as temp_name:
                output = pathlib.Path(temp_name) / "must-not-exist"
                argv = [
                    "--execute", "--device-ip", "192.0.2.44", "--bind-ip", "192.0.2.10",
                    "--control-version", "1", "--boot-id", "0x1122334455667788",
                    "--boot-evidence", "unused-startup.log", "--source-revision", "fixture",
                    "--elf", "unused.elf", "--bin", "unused.bin", "--output", str(output),
                    f"--telemetry-wait-seconds={invalid_wait}",
                ]
                with mock.patch.object(harness.socket, "socket",
                                       side_effect=AssertionError("socket opened")) as create_socket:
                    stderr = StringIO()
                    with redirect_stderr(stderr):
                        with self.assertRaises(SystemExit) as raised:
                            harness.main(argv)
                self.assertEqual(raised.exception.code, 2)
                self.assertIn("finite and at most 120 seconds", stderr.getvalue())
                create_socket.assert_not_called()
                self.assertFalse(output.exists())

    def test_serial_boot_evidence_must_match_boot_observed_event(self):
        with tempfile.TemporaryDirectory(prefix="wasmhome-issue11-boot-evidence-") as temp_name:
            log = pathlib.Path(temp_name) / "startup.log"
            log.write_text(
                'I (42) wasmhome: WASMHOME_EVENT,{"event":"boot_observed",'
                '"boot_id":"1122334455667788"}\n', encoding="utf-8")
            harness.verify_boot_evidence(log, 0x1122334455667788)
            with self.assertRaises(ValueError):
                harness.verify_boot_evidence(log, 0x8877665544332211)

    def test_wrong_boot_response_is_a_failure(self):
        device = FakeDevice(version=1, wrong_boot_response=True)
        runner = self.make_harness(device, version=1)
        with self.assertRaises(harness.CheckFailure):
            runner.run()

    def test_stale_ds18b20_samples_do_not_satisfy_freshness_gate(self):
        device = FakeDevice(version=2, telemetry_age=501)
        runner = self.make_harness(device, version=2)
        with self.assertRaises(harness.CheckFailure):
            runner.run()


if __name__ == "__main__":
    unittest.main()
