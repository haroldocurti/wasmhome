import json
import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from tools.compare_update_metrics import (
    DEFAULT_INPUTS,
    _enrich_native,
    _native,
    _raw_control_metrics,
    compare,
    main,
)


CAPTURE_SCENARIOS = ("native", "interp", "aot")
CAPTURE_FILES = ("uart", "observations", "summary")


def complete_capture_groups():
    return {
        scenario: {name: Path(f"/unused/{scenario}-{name}") for name in CAPTURE_FILES}
        for scenario in CAPTURE_SCENARIOS
    }


class CompareUpdateMetricsTests(unittest.TestCase):
    def compare_with_stubbed_captures(self, inputs=DEFAULT_INPUTS):
        captures = complete_capture_groups()

        def raw_capture(_paths, scenario):
            return {}, {name: b"" for name in CAPTURE_FILES}, {}

        with (patch("tools.compare_update_metrics._raw_capture", side_effect=raw_capture),
              patch("tools.compare_update_metrics._check_public_private_hashes"),
              patch("tools.compare_update_metrics._enrich_native"),
              patch("tools.compare_update_metrics._enrich_module")):
            return compare(inputs, captures)

    def test_normalizes_three_scenarios_and_preserves_non_applicable_values(self):
        report = self.compare_with_stubbed_captures()
        self.assertEqual(report["result"], "passed")
        self.assertEqual(set(report["scenarios"]), {"native", "interp", "aot"})
        self.assertEqual(report["functional_equivalence"]["microbenchmark_checksum"], "0xDBB2F0D4")
        self.assertTrue(report["functional_equivalence"]["status"] == "passed")
        self.assertIsNone(report["processing_costs"]["native"]["runtime_module_load_us"])
        self.assertIsNone(report["scenarios"]["interp"]["timing"]["device_phases_us"])
        self.assertNotEqual(report["scenarios"]["native"]["payload"]["artifact_bytes"],
                            report["scenarios"]["interp"]["payload"]["artifact_bytes"])

    def test_rejects_incomplete_signed_payload_transfer(self):
        with tempfile.TemporaryDirectory() as folder:
            bad_interp = Path(folder) / "interp.json"
            value = json.loads(DEFAULT_INPUTS["interp"].read_text())
            value["signed_control_module"]["bytes_received"] -= 1
            bad_interp.write_text(json.dumps(value))
            inputs = dict(DEFAULT_INPUTS)
            inputs["interp"] = bad_interp
            with self.assertRaisesRegex(ValueError, "payload transfer is incomplete"):
                self.compare_with_stubbed_captures(inputs)

    def test_rejects_non_equivalent_microbenchmark_checksum(self):
        with tempfile.TemporaryDirectory() as folder:
            bad_benchmark = Path(folder) / "benchmark.json"
            value = json.loads(DEFAULT_INPUTS["benchmark"].read_text())
            value["scenarios"][1]["checksum"] = "0x00000000"
            bad_benchmark.write_text(json.dumps(value))
            inputs = dict(DEFAULT_INPUTS)
            inputs["benchmark"] = bad_benchmark
            with self.assertRaisesRegex(ValueError, "workload checksums do not match"):
                self.compare_with_stubbed_captures(inputs)

    def test_reports_unknown_transport_and_gap_metrics_with_scope(self):
        report = self.compare_with_stubbed_captures()
        native = report["scenarios"]["native"]
        interp = report["scenarios"]["interp"]
        self.assertIsNone(native["payload"]["transport_bytes_received"])
        self.assertIn("not_published", native["payload"]["transport_bytes_received_null_reason"])
        self.assertEqual(native["service"]["telemetry_gap_window"], "full observer capture")
        self.assertEqual(interp["service"]["telemetry_gap_window"], "update interval")
        self.assertIsNone(interp["service"]["control_interval_max_gap_ns"])
        self.assertIn("not reported", interp["service"]["control_interval_max_gap_null_reason"])

    def test_rejects_nonfinite_and_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory() as folder:
            bad_native = Path(folder) / "native.json"
            bad_native.write_text('{"schema":"one","schema":"two"}')
            inputs = dict(DEFAULT_INPUTS)
            inputs["native"] = bad_native
            with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
                self.compare_with_stubbed_captures(inputs)

            bad_native.write_text(DEFAULT_INPUTS["native"].read_text().replace(
                '"trigger_to_first_v2": 16303452369', '"trigger_to_first_v2": NaN'))
            with self.assertRaisesRegex(ValueError, "invalid JSON constant"):
                self.compare_with_stubbed_captures(inputs)

    def test_rejects_inverted_host_clock_boundaries(self):
        with tempfile.TemporaryDirectory() as folder:
            bad_native = Path(folder) / "native.json"
            value = json.loads(DEFAULT_INPUTS["native"].read_text())
            value["successful_hil"]["host_monotonic_times_ns"]["acceptance_to_first_v2"] = 20_000_000_000
            bad_native.write_text(json.dumps(value))
            inputs = dict(DEFAULT_INPUTS)
            inputs["native"] = bad_native
            with self.assertRaisesRegex(ValueError, "acceptance-to-v2 exceeds trigger-to-v2"):
                self.compare_with_stubbed_captures(inputs)

    def test_rejects_aot_active_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as folder:
            bad_aot = Path(folder) / "aot.json"
            value = json.loads(DEFAULT_INPUTS["aot"].read_text())
            value["main_update"]["control_after"]["sha256"] = "0" * 64
            bad_aot.write_text(json.dumps(value))
            inputs = dict(DEFAULT_INPUTS)
            inputs["aot"] = bad_aot
            with self.assertRaisesRegex(ValueError, "active control hash differs"):
                self.compare_with_stubbed_captures(inputs)

    def test_requires_complete_raw_capture_groups_in_api_and_cli(self):
        with self.assertRaisesRegex(ValueError, "complete private raw capture groups"):
            compare(DEFAULT_INPUTS)

        incomplete = complete_capture_groups()
        del incomplete["aot"]
        with self.assertRaisesRegex(ValueError, "complete private raw capture groups"):
            compare(DEFAULT_INPUTS, incomplete)

        incomplete = complete_capture_groups()
        del incomplete["native"]["summary"]
        with self.assertRaisesRegex(ValueError, "native: complete private raw capture requires"):
            compare(DEFAULT_INPUTS, incomplete)

        with tempfile.TemporaryDirectory() as folder:
            stderr = io.StringIO()
            with patch("sys.argv", ["compare_update_metrics", "--output", str(Path(folder) / "report.json")]), \
                    redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
                main()
            self.assertEqual(raised.exception.code, 2)
            self.assertIn("complete private raw capture groups are required", stderr.getvalue())

    def test_native_enrichment_removes_stale_gap_null_reason(self):
        source = json.loads(DEFAULT_INPUTS["native"].read_text())
        evidence = _native(source)
        internal_min = evidence["memory"]["internal_free_min_bytes"]
        psram_min = evidence["memory"]["psram_free_min_bytes"]
        raw_report = {
            "payload": {
                "bytes_expected": evidence["payload"]["artifact_bytes"],
                "bytes_received": evidence["payload"]["artifact_bytes"],
                "sha256": evidence["payload"]["sha256"],
                "running_sha256": evidence["payload"]["sha256"],
            },
            "service": {
                "lost_controls": 0,
                "control_max_gap_ns": 123,
                "accepted_ns": 100,
                "activated_ns": 200,
                "trigger_to_first_v2_host_ns": 300,
                "telemetry_max_gap_ns": 400,
                "observer_counters": {"telemetry_unavailable": 0},
            },
            "phase_durations_us_within_boot_only": {"stage_us": 50},
            "memory": {"heap_snapshots": [{
                "internal_free_bytes": internal_min,
                "psram_free_bytes": psram_min,
            }]},
        }
        observations = [
            {"kind": "send_intent", "packet": {"message_type": "CONTROL_REQUEST",
                                                   "body": {"sample_id": 1}}},
            {"kind": "control_valid", "sample_id": 1},
            {"kind": "update_accepted", "host_monotonic_ns": 100},
            {"kind": "v2_observed", "host_monotonic_ns": 200},
        ]
        summary = {
            "sent_controls": 1,
            "control_max_gap_ns": 123,
            "observation_start_ns": 0,
            "observation_end_ns": 500,
            "counters": {"control_valid": 1, "control_timeout": 0,
                         "late_control": 0, "telemetry_unavailable": 0},
        }
        raw_bytes = {
            "observations": "\n".join(json.dumps(row) for row in observations).encode(),
            "summary": json.dumps(summary).encode(),
        }

        _enrich_native(evidence, raw_report, raw_bytes, {"analysis": "a" * 64})

        self.assertEqual(evidence["service"]["control_interval_max_gap_ns"], 123)
        self.assertNotIn("control_interval_max_gap_null_reason", evidence["service"])

    def test_reconciles_private_control_ids_denominators_and_host_boundaries(self):
        rows = [
            {"kind": "send_intent", "packet": {"message_type": "CONTROL_REQUEST",
                                                     "body": {"sample_id": 1}}},
            {"kind": "send_intent", "packet": {"message_type": "CONTROL_REQUEST",
                                                     "body": {"sample_id": 2}}},
            {"kind": "control_valid", "sample_id": 1},
            {"kind": "control_timeout", "sample_id": 2},
            {"kind": "late_control", "sample_id": 2},
            {"kind": "update_accepted", "host_monotonic_ns": 100},
            {"kind": "v2_observed", "host_monotonic_ns": 250},
        ]
        observations = "\n".join(json.dumps(row) for row in rows).encode()
        summary = json.dumps({"sent_controls": 2, "control_max_gap_ns": 700,
                              "control_unavailability_estimate_ns": 600,
                              "control_tail_censored_ns": 50,
                              "observation_start_ns": 0, "observation_end_ns": 1000,
                              "counters": {"control_valid": 1, "control_timeout": 1,
                                           "late_control": 1}}).encode()
        analysis = {"service": {"lost_controls": 1, "control_max_gap_ns": 700,
                                 "accepted_ns": 100, "activated_ns": 250}}
        metrics = _raw_control_metrics(observations, summary, analysis, "interp")
        self.assertEqual(metrics["control_requests"], 2)
        self.assertEqual(metrics["control_timeout_sample_ids"], [2])
        self.assertEqual(metrics["late_control_sample_ids"], [2])
        self.assertEqual(metrics["acceptance_to_first_v2_ns"], 150)
        self.assertEqual(metrics["control_timeout_rate"], 0.5)

    def test_rejects_mismatched_private_observer_denominator(self):
        observations = json.dumps({"kind": "send_intent", "packet": {
            "message_type": "CONTROL_REQUEST", "body": {"sample_id": 1}}}).encode()
        summary = json.dumps({"sent_controls": 2, "counters": {
            "control_valid": 0, "control_timeout": 0, "late_control": 0}}).encode()
        analysis = {"service": {"lost_controls": 0, "control_max_gap_ns": None}}
        with self.assertRaisesRegex(ValueError, "raw control IDs differ"):
            _raw_control_metrics(observations, summary, analysis, "aot")


if __name__ == "__main__":
    unittest.main()
