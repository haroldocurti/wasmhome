import io
import json
import pathlib
import sys
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
from external_observer import Observer, run
from observation_protocol_v1 import MessageType, Packet, encode


class Socket:
    def __init__(self):
        self.sent = []

    def sendto(self, data, peer):
        self.sent.append((data, peer))


class ObserverTests(unittest.TestCase):
    def setUp(self):
        self.log = io.StringIO()
        self.peer = ("127.0.0.1", 3333)
        self.observer = Observer(self.log, self.peer, period_ns=100, timeout_ns=300,
                                 attempt_id=uuid.UUID(int=1))
        self.socket = Socket()
        self.clock = patch("external_observer.time.monotonic_ns", side_effect=self.local_time)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.telemetry(1, 1, 1)

    def local_time(self):
        # Tests use explicit nanosecond values; the last recorded intent sets
        # the synthetic send clock without a real wall-clock dependency.
        rows = self.log.getvalue().splitlines()
        return json.loads(rows[-1])["host_monotonic_ns"] if rows else 0

    def receive(self, kind, seq, boot, body, now, attempt=None):
        packet = Packet(kind, attempt or self.observer.attempt_id, seq, boot, 1, body)
        raw = encode(packet)
        self.observer.receive(raw, self.peer, now)
        return raw

    def telemetry(self, boot, seq, now, sample=1, temperature=25250, age=100, status=0):
        return self.receive(MessageType.TELEMETRY, seq, boot,
                            {"sample_id": sample, "temperature_mC": temperature,
                             "age_ms": age, "sensor_status": status}, now, uuid.UUID(int=0))

    def control(self, sample=1, seq=1, boot=1, now=200, version=1, gpio=0, attempt=None):
        return self.receive(MessageType.CONTROL_RESULT, seq, boot,
                            {"sample_id": sample, "module_version": version,
                             "requested_gpio": gpio, "observed_gpio": gpio, "status": 0}, now, attempt)

    def acceptance(self, now=210, request=1, target=2, status=1, reason=0, attempt=None):
        return self.receive(MessageType.UPDATE_ACCEPTANCE, 1, 1,
                            {"request_sequence": request, "target_sequence": target,
                             "status": status, "reason": reason}, now, attempt)

    def test_raw_invalid_and_foreign_packets_preserved(self):
        self.observer.receive(b"invalid", self.peer, 10)
        self.observer.receive(b"other", ("127.0.0.2", 3333), 11)
        rows = [json.loads(line) for line in self.log.getvalue().splitlines()]
        self.assertEqual([r["hex"] for r in rows if r["kind"] == "receive"][-2:],
                         [b"invalid".hex(), b"other".hex()])
        self.assertEqual(self.observer.counters["malformed"], 1)
        self.assertEqual(self.observer.counters["foreign_peer"], 1)

    def test_timeout_late_duplicate_and_incorrect_remain_losses(self):
        self.observer.send_control(self.socket, 100, 95)
        self.observer.expire(401)
        raw = self.control(now=402)
        self.observer.receive(raw, self.peer, 403)
        self.observer.send_control(self.socket, 500, 500)  # 32 C must produce high.
        self.control(sample=2, seq=2, now=501, gpio=0)
        result = self.observer.summary()
        self.assertEqual(result["lost_controls"], 2)
        for name in ("control_timeout", "late_control", "duplicate", "incorrect_control"):
            self.assertEqual(result["counters"][name], 1)
        self.assertIsNone(result["control_max_gap_ns"])

    def test_wrong_id_attempt_boot_and_version_do_not_count(self):
        self.observer.send_control(self.socket, 100, 100)
        self.control(sample=99)
        self.control(seq=2, attempt=uuid.UUID(int=2))
        self.control(seq=1, boot=2)
        self.control(seq=3, version=2)
        self.assertEqual(self.observer.summary()["lost_controls"], 1)
        for name in ("unknown_control_id", "wrong_attempt", "wrong_control_boot", "incorrect_control"):
            self.assertEqual(self.observer.counters[name], 1)

    def test_reboot_preserves_clock_and_retires_previous_boot(self):
        self.observer.send_control(self.socket, 100, 100)
        self.control(now=110)
        self.telemetry(2, 1, 500)
        self.telemetry(1, 2, 510)
        self.observer.send_control(self.socket, 600, 600)
        self.control(sample=2, boot=2, now=610, gpio=1)
        result = self.observer.summary()
        self.assertEqual(result["boot_ids"], [1, 2])
        self.assertEqual(result["valid_controls_by_boot"], {1: 1, 2: 1})
        self.assertEqual(result["control_unavailability_estimate_ns"], 400)
        self.assertEqual(result["lost_controls"], 0)
        self.assertEqual(self.observer.boot_id, 2)

    def test_correlated_acceptance_allows_v2_and_detects_regression(self):
        self.observer.send_trigger(self.socket, 100, 2, 1)
        self.acceptance(now=110)
        self.observer.send_control(self.socket, 120, 120)
        self.control(now=130, version=2)
        self.observer.send_control(self.socket, 140, 140)
        self.control(sample=2, seq=2, now=150, version=1, gpio=1)
        self.assertEqual(self.observer.summary()["update_total_ns"], 30)
        self.assertEqual(self.observer.counters["incorrect_control"], 1)

    def test_uncorrelated_late_and_rejected_acceptances_do_not_start_attempt(self):
        for args, category in (({"target": 3}, "uncorrelated_acceptance"),
                               ({"now": 500}, "late_acceptance"),
                               ({"status": 2, "reason": 6}, "update_rejected")):
            with self.subTest(category=category):
                self.setUp()
                self.observer.send_trigger(self.socket, 100, 2, 1)
                self.acceptance(**args)
                self.assertIsNone(self.observer.accepted_ns)
                self.assertEqual(self.observer.counters[category], 1)

    def test_reordered_results_can_meet_deadline_and_fill_sequence_gap(self):
        self.observer.send_control(self.socket, 100, 100)
        self.observer.send_control(self.socket, 200, 200)
        self.observer.send_control(self.socket, 300, 300)
        self.control(sample=1, seq=1, now=310)
        self.control(sample=3, seq=3, now=320, gpio=1)
        self.control(sample=2, seq=2, now=330, gpio=1)
        self.assertEqual(self.observer.summary()["lost_controls"], 0)
        self.assertEqual(self.observer.summary()["unresolved_sequence_gaps"]["1:CONTROL_RESULT"], 0)
        self.assertEqual(self.observer.counters["out_of_order"], 1)

    def test_telemetry_freshness_ids_and_conflicting_duplicates(self):
        self.telemetry(1, 2, 100, sample=2)
        self.telemetry(1, 3, 200, sample=1)
        self.telemetry(1, 4, 300, sample=2, age=1001)
        self.telemetry(1, 4, 310, sample=2, temperature=25500)
        self.assertEqual(self.observer.counters["telemetry_id_conflict"], 1)
        self.assertEqual(self.observer.counters["telemetry_unavailable"], 1)
        self.assertEqual(self.observer.counters["sequence_conflict"], 1)
        self.assertEqual(self.observer.telemetry_times, [1, 100])

    def test_invalid_configuration_is_rejected_before_files_or_network(self):
        for value in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                run("127.0.0.1", "unused", seconds=value)


if __name__ == "__main__":
    unittest.main()
