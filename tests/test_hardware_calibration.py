import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
from calibrate_observer_hardware import evaluate


def events(*values):
    return [{"kind": "firmware_event", "tag": "WASMHOME_EVENT", "value": value}
            for value in values]


class HardwareCalibrationTests(unittest.TestCase):
    def summary(self):
        return {"period_ns": 100_000_000, "control_unavailability_estimate_ns": 1_010_000_000,
                "boot_ids": [1], "valid_controls_by_boot": {1: 20}, "lost_controls": 10,
                "counters": {"control_valid": 20, "telemetry_valid": 20, "update_rejected": 1}}

    def test_known_gap_requires_same_boot_and_expected_resolution(self):
        fixture = events({"event": "observer_fixture_v1"},
                         {"event": "observer_fixture_gap_start", "boot_id": "1", "monotonic_us": 1_000_000},
                         {"event": "observer_fixture_gap_end", "boot_id": "1", "monotonic_us": 2_000_000})
        summary = self.summary()
        self.assertTrue(evaluate(summary, fixture, "gap")["passed"])
        summary["control_unavailability_estimate_ns"] = 1_200_000_000
        self.assertFalse(evaluate(summary, fixture, "gap")["passed"])
        fixture[-1]["value"]["boot_id"] = "2"
        self.assertIsNone(evaluate(summary, fixture, "gap")["known_silence_ns"])

    def test_telemetry_after_reboot_does_not_prove_control_recovery(self):
        fixture = events({"event": "observer_fixture_v1"},
                         {"event": "observer_fixture_reboot"}, {"event": "boot_observed"})
        summary = self.summary()
        summary["boot_ids"] = [1, 2]
        self.assertFalse(evaluate(summary, fixture, "reboot")["passed"])
        summary["valid_controls_by_boot"][2] = 10
        self.assertTrue(evaluate(summary, fixture, "reboot")["passed"])
        summary["valid_controls_by_boot"] = {str(boot): count for boot, count in summary["valid_controls_by_boot"].items()}
        self.assertTrue(evaluate(summary, fixture, "reboot")["passed"])


if __name__ == "__main__":
    unittest.main()
