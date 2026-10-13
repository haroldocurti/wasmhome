"""Negative and positive tests for the offline E1 device-session validator."""

import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from collect_results import FIELDS  # noqa: E402
from validate_e1_device import EXPECTED_CHECKSUM, SCENARIOS, validate_session  # noqa: E402


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _memory(scenario, boot_id, phase, status, repetition=None):
    event = {
        "schema": "wasmhome-memory-v1",
        "scenario": scenario,
        "boot_id": boot_id,
        "phase": phase,
        "phase_status": status,
        "units": "bytes",
        "scope": "dynamic_heap",
        "internal": {
            "capability": "MALLOC_CAP_INTERNAL", "status": "measured",
            "total_bytes": 2048, "free_bytes": 1000,
            "largest_free_block_bytes": 800, "phase_peak_free_bytes": None,
            "phase_peak_status": "not_instrumented",
        },
        "psram": {
            "capability": "MALLOC_CAP_SPIRAM", "status": "not_applicable",
            "total_bytes": None, "free_bytes": None,
            "largest_free_block_bytes": None, "phase_peak_free_bytes": None,
            "phase_peak_status": "not_applicable",
        },
        "static_memory": {"status": "external_elf", "source": "ELF", "bytes": None},
        "repetition": repetition,
    }
    return "WASMHOME_MEMORY," + json.dumps(event, separators=(",", ":"))


def _serial_log(scenario, boot_id):
    lines = ["WASMHOME_EVENT," + json.dumps({
        "event": "boot_observed", "scenario": scenario, "boot_id": boot_id,
    }, separators=(",", ":"))]
    statuses = {
        "before_runtime": "measured",
        "after_init": "not_applicable" if scenario == "native" else "ok",
        "after_load": "not_applicable" if scenario == "native" else "ok",
        "after_instantiate": "not_applicable" if scenario == "native" else "ok",
        "after_exec_env_lookup": "not_applicable" if scenario == "native" else "ok",
        "after_warmup": "ok",
    }
    for phase, status in statuses.items():
        lines.append(_memory(scenario, boot_id, phase, status))
    lines.append(f"I (42) app: E1 preliminary calls: scenario={scenario} warmup=5 measured=30; one load, not independent updates")
    lines.append("WASMHOME_CSV," + ",".join(FIELDS))
    for repetition in range(1, 31):
        lines.append(_memory(scenario, boot_id, "before_execution", "measured", repetition))
        lines.append(_memory(scenario, boot_id, "execution", "ok", repetition))
        lines.append(
            f"WASMHOME_CSV,{scenario},{repetition},10,20,1,1000,990,980,900,{EXPECTED_CHECKSUM},ok"
        )
    lines.append(_memory(scenario, boot_id, "after_release", "ok"))
    lines.append(f"I (100) app: E1 scenario={scenario} status=ok heap_before_runtime=1000 heap_after_release=1000")
    return "\n".join(lines) + "\n"


class ValidateE1DeviceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="wasmhome-e1-device-")
        self.root = pathlib.Path(self.temporary.name)
        self.manifest_path = self.root / "session.json"
        self.manifest = {
            "schema": "wasmhome-e1-device-session-v1",
            "scenario_order": list(SCENARIOS),
            "expected_checksum": EXPECTED_CHECKSUM,
            "artifacts": {},
            "runs": {},
        }
        for index, scenario in enumerate(SCENARIOS, start=1):
            folder = self.root / scenario
            folder.mkdir()
            elf = folder / "app.elf"
            binary = folder / "app.bin"
            log = folder / "serial.log"
            elf.write_bytes(f"elf-{scenario}".encode())
            binary.write_bytes(f"bin-{scenario}".encode())
            boot_id = f"00000000000000{index:02x}"
            log.write_text(_serial_log(scenario, boot_id), encoding="utf-8")
            elf_sha = _digest(elf)
            bin_sha = _digest(binary)
            self.manifest["artifacts"][scenario] = {
                "elf_path": f"{scenario}/app.elf", "elf_sha256": elf_sha,
                "bin_path": f"{scenario}/app.bin", "bin_sha256": bin_sha,
            }
            self.manifest["runs"][scenario] = {
                "boot_id": boot_id, "elf_sha256": elf_sha,
                "flashed_bin_sha256": bin_sha, "flash_verified": True,
                "log_path": f"{scenario}/serial.log", "log_sha256": _digest(log),
            }
        self._save_manifest()

    def tearDown(self):
        self.temporary.cleanup()

    def _save_manifest(self):
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")

    def _refresh_log_hash(self, scenario):
        path = self.root / self.manifest["runs"][scenario]["log_path"]
        self.manifest["runs"][scenario]["log_sha256"] = _digest(path)

    def _edit_log(self, scenario, transform):
        path = self.root / self.manifest["runs"][scenario]["log_path"]
        path.write_text(transform(path.read_text(encoding="utf-8")), encoding="utf-8")
        self._refresh_log_hash(scenario)
        self._save_manifest()

    def test_complete_three_scenario_session_and_memory_schema(self):
        result = validate_session(self.manifest_path)
        self.assertEqual(result["scenario_order"], list(SCENARIOS))
        self.assertEqual(result["status"], "session_logs_and_manifest_consistent")
        self.assertEqual([item["measured_calls"] for item in result["scenarios"]], [30, 30, 30])
        self.assertTrue(all(item["checked_warmups"] == 5 for item in result["scenarios"]))
        self.assertEqual(result["scope"], "offline_consistency_only_not_independent_flash_or_hardware_attestation")
        cli = subprocess.run(
            [sys.executable, str(ROOT / "tools/validate_e1_device.py"), str(self.manifest_path)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(cli.returncode, 0, cli.stdout + cli.stderr)
        self.assertEqual(json.loads(cli.stdout)["status"], "session_logs_and_manifest_consistent")

    def test_rejects_checksum_mismatch_after_log_hash_is_updated(self):
        self._edit_log("aot", lambda content: content.replace("0xDBB2F0D4,ok", "0x00000000,ok", 1))
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            validate_session(self.manifest_path)

    def test_rejects_incomplete_measured_rows(self):
        self._edit_log("interp", lambda content: "\n".join(
            line for line in content.splitlines() if "WASMHOME_CSV,interp,30," not in line
        ) + "\n")
        with self.assertRaisesRegex(ValueError, "exactly 30 measured CSV rows"):
            validate_session(self.manifest_path)

    def test_rejects_final_status_before_after_release(self):
        def move_final_before_release(content):
            lines = content.splitlines()
            final_index = next(i for i, line in enumerate(lines)
                               if "E1 scenario=native status=ok" in line)
            release_index = next(i for i, line in enumerate(lines)
                                 if line.startswith("WASMHOME_MEMORY,")
                                 and '"phase":"after_release"' in line)
            final = lines.pop(final_index)
            release_index -= int(final_index < release_index)
            lines.insert(release_index, final)
            return "\n".join(lines) + "\n"

        self._edit_log("native", move_final_before_release)
        with self.assertRaisesRegex(ValueError, "final E1 status preceded after_release"):
            validate_session(self.manifest_path)

    def _set_csv_field(self, content, repetition, index, value):
        lines = content.splitlines()
        prefix = "WASMHOME_CSV,"
        for line_index, line in enumerate(lines):
            if not line.startswith(prefix):
                continue
            fields = line[len(prefix):].split(",")
            if fields[0] == "scenario" or fields[1] != str(repetition):
                continue
            fields[index] = str(value)
            lines[line_index] = prefix + ",".join(fields)
        return "\n".join(lines) + "\n"

    def test_rejects_heap_min_above_internal_capacity(self):
        self._edit_log("interp", lambda content: self._set_csv_field(
            content, 1, 8, 999999
        ))
        with self.assertRaisesRegex(ValueError, "heap_min exceeds internal heap capacity"):
            validate_session(self.manifest_path)

    def test_rejects_heap_min_above_observed_free_heap(self):
        self._edit_log("interp", lambda content: self._set_csv_field(
            content, 1, 8, 1500
        ))
        with self.assertRaisesRegex(ValueError, "heap_min exceeds observed free heap"):
            validate_session(self.manifest_path)

    def test_rejects_heap_min_increase_across_repetitions(self):
        def raise_second_minimum(content):
            return self._set_csv_field(content, 2, 8, 950)

        self._edit_log("interp", raise_second_minimum)
        with self.assertRaisesRegex(ValueError, "heap_min must be nonincreasing"):
            validate_session(self.manifest_path)

    def test_rejects_duplicate_boot_identity_across_scenarios(self):
        self.manifest["runs"]["aot"]["boot_id"] = self.manifest["runs"]["native"]["boot_id"]
        self._save_manifest()
        with self.assertRaisesRegex(ValueError, "duplicate boot_id"):
            validate_session(self.manifest_path)

    def test_rejects_firmware_identity_that_differs_from_manifest(self):
        self.manifest["runs"]["native"]["flashed_bin_sha256"] = "0" * 64
        self._save_manifest()
        with self.assertRaisesRegex(ValueError, "flashed BIN identity differs"):
            validate_session(self.manifest_path)

    def test_rejects_missing_memory_phase(self):
        self._edit_log("native", lambda content: "\n".join(
            line for line in content.splitlines()
            if not ("WASMHOME_MEMORY," in line and '"phase":"after_instantiate"' in line)
        ) + "\n")
        with self.assertRaisesRegex(ValueError, "missing memory phases"):
            validate_session(self.manifest_path)

    def test_rejects_memory_json_split_by_interleaved_log_line(self):
        def interleave(content):
            lines = content.splitlines()
            index = next(i for i, line in enumerate(lines) if line.startswith("WASMHOME_MEMORY,"))
            line = lines[index]
            split = len(line) // 2
            lines[index:index + 1] = [
                line[:split],
                'WASMHOME_EVENT,{"event":"concurrent_log_probe"}',
                line[split:],
            ]
            return "\n".join(lines) + "\n"

        self._edit_log("native", interleave)
        with self.assertRaisesRegex(ValueError, "invalid JSON event"):
            validate_session(self.manifest_path)

    def test_absent_psram_is_null_not_zero(self):
        self._edit_log("native", lambda content: content.replace(
            '"status":"not_applicable","total_bytes":null',
            '"status":"not_applicable","total_bytes":0', 1,
        ))
        with self.assertRaisesRegex(ValueError, "absent capability values must be null"):
            validate_session(self.manifest_path)

    def test_point_in_time_samples_do_not_claim_phase_peak(self):
        self._edit_log("interp", lambda content: content.replace(
            '"phase_peak_free_bytes":null,"phase_peak_status":"not_instrumented"',
            '"phase_peak_free_bytes":700,"phase_peak_status":"measured"', 1,
        ))
        with self.assertRaisesRegex(ValueError, "cannot be represented as a phase peak"):
            validate_session(self.manifest_path)

    def test_rejects_wrong_session_order_or_unverified_flash(self):
        self.manifest["scenario_order"] = ["native", "aot", "interp"]
        self._save_manifest()
        with self.assertRaisesRegex(ValueError, "scenario_order"):
            validate_session(self.manifest_path)
        self.manifest["scenario_order"] = list(SCENARIOS)
        self.manifest["runs"]["interp"]["flash_verified"] = False
        self._save_manifest()
        with self.assertRaisesRegex(ValueError, "flash verification"):
            validate_session(self.manifest_path)


if __name__ == "__main__":
    unittest.main()
