#!/usr/bin/env python3
"""Normalize the reviewed physical evidence for the three update scenarios.

This is an offline evidence adapter. It never opens hardware or network
connections and does not infer missing measurements from zero or from another
clock domain.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys

try:
    from evidence_json import loads
except ModuleNotFoundError:
    from tools.evidence_json import loads


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUTS = {
    "native": ROOT / "docs/evidence/issue16-native-ota-2026-10-16.json",
    "interp": ROOT / "docs/evidence/issue17-interp-2026-10-16/summary.json",
    "aot": ROOT / "docs/evidence/issue18-aot-https-2026-10-16.json",
    "benchmark": ROOT / "docs/evidence/e1-physical-2026-10-14/physical/physical-abc-benchmark-metrics.json",
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _read(path: Path, name: str) -> tuple[dict, str]:
    raw = path.read_bytes()
    try:
        value = loads(raw.decode("utf-8-sig"))
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as error:
        raise ValueError(f"{name}: invalid JSON: {error}") from error
    _require(isinstance(value, dict), f"{name}: expected a JSON object")
    return value, hashlib.sha256(raw).hexdigest()


def _nonnegative(value, label: str, *, allow_float: bool = False):
    valid = (isinstance(value, (int, float)) and not isinstance(value, bool)
             and (not isinstance(value, float) or math.isfinite(value)) and value >= 0
             and (allow_float or isinstance(value, int)))
    _require(valid, f"{label}: expected a non-negative number")
    return value


def _native(data: dict) -> dict:
    _require(data.get("schema") == "wasmhome-issue16-native-ota-evidence-v1",
             "native: unexpected evidence schema")
    _require(data.get("result") == "passed", "native: HIL evidence did not pass")
    run = data.get("successful_hil")
    _require(isinstance(run, dict) and run.get("analyzer_result") == "passed",
             "native: successful HIL analyzer result is missing")
    _require(run.get("native_v1_to_v2") is True, "native: expected v1-to-v2 OTA proof")
    payload_bytes = _nonnegative(run.get("payload_bytes"), "native payload bytes")
    payload_sha = run.get("payload_sha256")
    _require(isinstance(payload_sha, str) and SHA256_RE.fullmatch(payload_sha),
             "native: payload SHA-256 is invalid")
    _require(run.get("running_image_hash_matches_payload") is True,
             "native: running image does not match verified payload")
    native_artifacts = [item for item in data.get("https_and_signatures", {}).get("artifacts", [])
                        if item.get("scenario") == 1 and item.get("version") == "v2"]
    _require(len(native_artifacts) == 1 and native_artifacts[0].get("bytes") == payload_bytes
             and native_artifacts[0].get("sha256") == payload_sha,
             "native: HIL payload differs from the signed v2 artifact")
    service = run.get("service")
    host = run.get("host_monotonic_times_ns")
    _require(isinstance(service, dict) and isinstance(host, dict),
             "native: service or host-clock metrics are missing")
    phases = run.get("phase_durations_us_within_boot_only")
    _require(isinstance(phases, dict) and phases, "native: device phase timings are missing")
    required_native_phases = {
        "accepted_to_manifest_download_started",
        "manifest_download_started_to_manifest_downloaded",
        "manifest_downloaded_to_signature_verified",
        "signature_verified_to_payload_download_started",
        "payload_download_started_to_payload_verified",
        "payload_verified_to_staged", "staged_to_activating",
        "activating_to_reboot_requested", "boot_pending_to_health_check_passed",
        "health_check_passed_to_floor_committed",
    }
    _require(required_native_phases <= set(phases),
             f"native: missing device stage timings {sorted(required_native_phases - set(phases))}")
    for phase, value in phases.items():
        _nonnegative(value, f"native phase {phase}")
    memory = run.get("phase_heap_snapshots")
    _require(isinstance(memory, list) and memory, "native: heap snapshots are missing")
    total = _nonnegative(host.get("trigger_to_first_v2"), "native host total")
    accepted_total = _nonnegative(host.get("acceptance_to_first_v2"), "native accepted total")
    _require(accepted_total <= total, "native: acceptance-to-v2 exceeds trigger-to-v2")
    return {
        "payload": {"artifact_bytes": payload_bytes, "transport_bytes_received": None,
                    "transport_bytes_received_null_reason": "not_published; final running image hash matches the verified signed artifact",
                    "sha256": payload_sha, "verified_artifact_running": True},
        "timing": {"trigger_to_first_v2_ns": total,
                   "acceptance_to_first_v2_ns": accepted_total,
                   "device_phase_clock": "mcu_monotonic_us_within_each_boot",
                   "host_clock": "observer_monotonic_ns",
                   "device_phases_us": phases},
        "memory": {"source": "phase_snapshots", "phase_snapshots": memory,
                   "internal_free_min_bytes": min(_nonnegative(x.get("internal_free_bytes"), "native internal heap") for x in memory),
                   "psram_free_min_bytes": min(_nonnegative(x.get("psram_free_bytes"), "native PSRAM") for x in memory)},
        "service": {"control_requests": _nonnegative(service.get("control_requests"), "native control requests"),
                    "valid_controls": _nonnegative(service.get("valid_controls"), "native valid controls"),
                    "control_timeouts": _nonnegative(service.get("control_timeouts"), "native control timeouts"),
                    "late_controls": None,
                    "late_controls_null_reason": "not reported separately in the published native summary",
                    "valid_telemetry": _nonnegative(service.get("valid_telemetry"), "native telemetry"),
                    "telemetry_unavailable": _nonnegative(service.get("telemetry_unavailable"), "native unavailable telemetry"),
                    "transition_v1_to_v2_gap_ns": _nonnegative(host.get("last_v1_to_first_v2_gap"), "native control transition gap"),
                    "control_interval_max_gap_ns": None,
                    "control_interval_max_gap_null_reason": "the published native summary provides the v1-to-v2 transition gap, not a separate capture-wide maximum",
                    "telemetry_max_gap_ns": _nonnegative(host.get("telemetry_max_gap"), "native telemetry gap"),
                    "telemetry_gap_window": "full observer capture",
                    "unresolved_sequence_gaps": _nonnegative(service.get("unresolved_sequence_gaps"), "native sequence gaps")},
        "functional_oracle": {"temperature_c": 27, "v1_gpio": "high", "v2_gpio": "low",
                              "v1_responses": run.get("v1_27c_valid_responses_before_acceptance"),
                              "v2_responses": run.get("v2_27c_valid_responses_after_activation")},
    }


def _interp(data: dict) -> dict:
    _require(data.get("schema") == "wasmhome-issue17-interp-evidence-v1",
             "interp: unexpected evidence schema")
    _require(data.get("result") == "passed", "interp: HIL evidence did not pass")
    run = data.get("main_capture")
    payload = data.get("signed_control_module")
    _require(isinstance(run, dict) and isinstance(payload, dict),
             "interp: main capture or payload evidence is missing")
    _require(run.get("analyzer_checks_passed") == run.get("analyzer_checks_total")
             and isinstance(run.get("analyzer_checks_total"), int),
             "interp: analyzer checks did not all pass")
    _require(run.get("observed_update_boots") == 1
             and run.get("reset_or_serial_reopen_after_observer_started") is False,
             "interp: update must stay within the observed boot")
    expected = _nonnegative(payload.get("bytes_expected"), "interp expected payload")
    received = _nonnegative(payload.get("bytes_received"), "interp received payload")
    digest = payload.get("sha256")
    _require(expected == received, "interp: payload transfer is incomplete")
    _require(isinstance(digest, str) and SHA256_RE.fullmatch(digest), "interp: payload SHA-256 is invalid")
    _require(isinstance(payload.get("manifest_sha256"), str)
             and payload.get("manifest_sha256") == payload.get("signature_verified_manifest_sha256"),
             "interp: downloaded and signature-verified manifest digests differ")
    _require(digest == payload.get("sha256"), "interp: verified payload digest differs")
    control, telemetry, memory = run.get("control"), run.get("telemetry"), run.get("memory")
    _require(all(isinstance(x, dict) for x in (control, telemetry, memory)),
             "interp: control, telemetry, or memory metrics are missing")
    _require(isinstance(run.get("local_analysis_sha256"), str)
             and run.get("local_analysis_sha256") == run.get("independent_root_replay_sha256"),
             "interp: independent analyzer replay differs")
    duration_ms = _nonnegative(control.get("trigger_to_first_v2_ms"), "interp host total", allow_float=True)
    accepted_duration_ms = _nonnegative(telemetry.get("update_duration_ms"), "interp accepted total", allow_float=True)
    _require(accepted_duration_ms <= duration_ms,
             "interp: acceptance-to-v2 exceeds trigger-to-v2")
    return {
        "payload": {"artifact_bytes": expected, "transport_bytes_received": received,
                    "sha256": digest, "verified_artifact_running": True},
        "timing": {"trigger_to_first_v2_ns": round(duration_ms * 1_000_000),
                   "acceptance_to_first_v2_ns": round(accepted_duration_ms * 1_000_000),
                   "device_phase_clock": "mcu_monotonic_us_within_one_boot; per-phase public values unavailable",
                   "host_clock": "observer_monotonic_ns",
                   "device_phases_us": None,
                   "device_phases_null_reason": "per-phase MCU durations are absent from the published summary"},
        "memory": {"source": "phase snapshots summarized by analyzer",
                   "phase_snapshots": None,
                   "phase_snapshots_null_reason": "published summary includes lifecycle names and aggregate minima only" ,
                   "internal_free_min_bytes": _nonnegative(memory.get("minimum_internal_free_bytes"), "interp internal heap"),
                   "internal_largest_block_min_bytes": _nonnegative(memory.get("minimum_internal_largest_block_bytes"), "interp largest internal block"),
                   "psram_free_min_bytes": _nonnegative(memory.get("minimum_psram_free_bytes"), "interp PSRAM"),
                   "wamr_allocator_failures": _nonnegative(memory.get("wamr_allocator_failures"), "interp allocator failures")},
        "service": {"control_requests": None,
                    "control_requests_null_reason": "request denominator is not published by the modular summary",
                    "valid_controls": _nonnegative(control.get("valid_results"), "interp valid controls"),
                    "control_timeouts": _nonnegative(control.get("timeouts"), "interp control timeouts"),
                    "late_controls": _nonnegative(control.get("late_results"), "interp late controls"),
                    "valid_telemetry": _nonnegative(telemetry.get("valid_samples_total"), "interp telemetry"),
                    "telemetry_unavailable": None,
                    "telemetry_unavailable_null_reason": "not reported by the published modular capture summary",
                    "transition_v1_to_v2_gap_ns": _nonnegative(control.get("last_v1_to_first_v2_gap_ms"), "interp control transition gap", allow_float=True) * 1_000_000,
                    "control_interval_max_gap_ns": None,
                    "control_interval_max_gap_null_reason": "not reported by the published modular capture summary",
                    "telemetry_max_gap_ns": _nonnegative(telemetry.get("max_gap_during_update_ms"), "interp telemetry gap", allow_float=True) * 1_000_000,
                    "telemetry_gap_window": "update interval",
                    "unresolved_sequence_gaps": _nonnegative(telemetry.get("unresolved_sequence_gaps"), "interp sequence gaps")},
        "functional_oracle": {"temperature_c": 27, "v1_gpio": "high", "v2_gpio": "low",
                              "v1_responses": control.get("direct_27c_v1_gpio_high"),
                              "v2_responses": control.get("direct_27c_v2_gpio_low")},
    }


def _aot(data: dict) -> dict:
    _require(data.get("schema") == "wasmhome-issue18-aot-https-evidence-v2",
             "aot: unexpected evidence schema")
    _require(data.get("result") == "passed", "aot: HIL evidence did not pass")
    update, service, memory = data.get("main_update"), data.get("main_service_observation"), data.get("memory")
    phases = data.get("device_phase_durations_us")
    _require(all(isinstance(x, dict) for x in (update, service, memory, phases)),
             "aot: update, service, memory, or device timings are missing")
    _require(update.get("analyzer_checks_passed") == update.get("analyzer_checks_total")
             and update.get("same_boot") is True,
             "aot: analyzer checks or same-boot gate failed")
    expected = _nonnegative(update.get("payload_bytes_expected"), "aot expected payload")
    received = _nonnegative(update.get("payload_bytes_received"), "aot received payload")
    digest = update.get("payload_sha256")
    _require(expected == received, "aot: payload transfer is incomplete")
    _require(isinstance(digest, str) and SHA256_RE.fullmatch(digest), "aot: payload SHA-256 is invalid")
    _require(update.get("control_after", {}).get("sha256") == digest,
             "aot: active control hash differs from verified payload")
    control_behavior = update.get("control_behavior_at_27c")
    _require(isinstance(control_behavior, dict), "aot: functional control oracle is missing")
    required_phases = {"manifest_download_started_to_manifest_downloaded_us",
                       "manifest_downloaded_to_signature_verified_us",
                       "payload_download_started_to_payload_verified_us",
                       "activating_to_candidate_loaded_us",
                       "candidate_loaded_to_candidate_instantiated_us",
                       "candidate_instantiated_to_candidate_health_check_us",
                       "candidate_health_check_to_floor_committed_us",
                       "floor_committed_to_control_activated_us",
                       "control_activated_to_previous_control_released_us"}
    _require(required_phases <= set(phases),
             f"aot: missing device stage timings {sorted(required_phases - set(phases))}")
    for phase, value in phases.items():
        _nonnegative(value, f"aot phase {phase}")
    return {
        "payload": {"artifact_bytes": expected, "transport_bytes_received": received,
                    "sha256": digest, "verified_artifact_running": True},
        "timing": {"trigger_to_first_v2_ns": round(_nonnegative(service.get("trigger_to_first_v2_response_ms"), "aot host total", allow_float=True) * 1_000_000),
                   "acceptance_to_first_v2_ns": None,
                   "acceptance_to_first_v2_null_reason": "not present in the published AOT summary",
                   "device_phase_clock": "mcu_monotonic_us_within_one_boot",
                   "host_clock": "observer_monotonic_ns",
                   "device_phases_us": phases},
        "memory": {"source": "phase snapshots summarized by analyzer",
                   "phase_snapshots": None,
                   "phase_snapshots_null_reason": "published summary contains aggregate minima; raw phase values were not published",
                   "internal_free_min_bytes": _nonnegative(memory.get("minimum_internal_heap_free_bytes"), "aot internal heap"),
                   "internal_largest_block_min_bytes": _nonnegative(memory.get("minimum_internal_largest_free_block_bytes"), "aot largest internal block"),
                   "psram_free_min_bytes": _nonnegative(memory.get("minimum_psram_free_bytes"), "aot PSRAM"),
                   "wamr_allocator_peak_bytes": _nonnegative(memory.get("wamr_allocator_peak_bytes"), "aot WAMR allocator peak"),
                   "wamr_allocator_failures": _nonnegative(memory.get("wamr_allocator_failures"), "aot WAMR allocator failures")},
        "service": {"control_requests": None,
                    "control_requests_null_reason": "request denominator is not published by the modular summary",
                    "valid_controls": _nonnegative(service.get("valid_control_responses"), "aot valid controls"),
                    "control_timeouts": _nonnegative(service.get("control_timeouts"), "aot control timeouts"),
                    "late_controls": _nonnegative(service.get("late_controls"), "aot late controls"),
                    "valid_telemetry": _nonnegative(service.get("valid_telemetry"), "aot telemetry"),
                    "telemetry_unavailable": None,
                    "telemetry_unavailable_null_reason": "not reported by the published modular capture summary",
                    "transition_v1_to_v2_gap_ns": _nonnegative(service.get("last_v1_to_first_v2_gap_ms"), "aot control transition gap", allow_float=True) * 1_000_000,
                    "control_interval_max_gap_ns": None,
                    "control_interval_max_gap_null_reason": "not reported by the published modular capture summary",
                    "telemetry_max_gap_ns": _nonnegative(service.get("telemetry_max_gap_during_update_ms"), "aot telemetry gap", allow_float=True) * 1_000_000,
                    "telemetry_gap_window": "update interval",
                    "unresolved_sequence_gaps": _nonnegative(service.get("unresolved_sequence_gaps"), "aot sequence gaps")},
        "functional_oracle": {"temperature_c": 27, "v1_gpio": "high", "v2_gpio": "low",
                              "v1_responses": control_behavior.get("valid_v1_high_before_acceptance"),
                              "v2_responses": control_behavior.get("valid_v2_low_after_activation")},
    }


def _raw_capture(paths: dict[str, Path], scenario: str) -> tuple[dict, dict, dict]:
    """Re-analyze private UART/observer files without copying them into Git."""
    required = {"uart", "observations", "summary"}
    _require(set(paths) == required, f"{scenario}: raw capture needs {sorted(required)}")
    raw = {name: path.read_bytes() for name, path in paths.items()}
    if scenario == "native":
        try:
            from analyze_native_update import analyze as analyze_capture
        except ModuleNotFoundError:
            from tools.analyze_native_update import analyze as analyze_capture
        report = analyze_capture(raw["uart"], raw["observations"], raw["summary"], 1, 2)
        expected_schema = "wasmhome-native-update-analysis-v1"
    else:
        try:
            from analyze_module_update import analyze as analyze_capture
        except ModuleNotFoundError:
            from tools.analyze_module_update import analyze as analyze_capture
        report = analyze_capture(raw["uart"], raw["observations"], raw["summary"],
                                 2 if scenario == "interp" else 3, 2)
        expected_schema = "wasmhome-module-update-analysis-v1"
    _require(report.get("schema") == expected_schema and report.get("result") == "passed",
             f"{scenario}: raw capture analyzer did not pass")
    if scenario != "native":
        _require(report.get("scenario") == scenario, f"{scenario}: analyzer scenario mismatch")
    input_hashes = {name: hashlib.sha256(content).hexdigest() for name, content in raw.items()}
    report_bytes = (json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    input_hashes["analysis"] = hashlib.sha256(report_bytes).hexdigest()
    return report, raw, input_hashes


def _check_public_private_hashes(source: dict, scenario: str, hashes: dict[str, str]) -> None:
    if scenario == "native":
        run = source[scenario].get("successful_hil", {})
        _require(run.get("observer_summary_receipt_sha256") == hashes.get("summary")
                 and run.get("analyzer_receipt_sha256") == hashes.get("analysis"),
                 "native: raw summary/analyzer receipt hashes differ from published evidence")
        return
    declared = (source[scenario].get("private_artifact_sha256") if scenario == "interp"
                else source[scenario].get("private_evidence_sha256"))
    _require(isinstance(declared, dict), f"{scenario}: public evidence has no private source hashes")
    keys = ({"uart": "main_uart_log", "observations": "main_observations",
             "summary": "main_observer_summary", "analysis": "main_analysis"}
            if scenario == "interp" else
            {"uart": "main_uart", "observations": "main_observations_jsonl",
             "summary": "main_observer_summary", "analysis": "module_update_analysis"})
    for input_name, public_name in keys.items():
        _require(declared.get(public_name) == hashes.get(input_name),
                 f"{scenario}: {input_name} hash does not match published evidence {public_name}")


def _raw_control_metrics(observations_raw: bytes, summary_raw: bytes,
                         analysis: dict, scenario: str) -> dict:
    """Reconcile sent CONTROL_REQUEST IDs with responses, timeouts and late results."""
    try:
        summary = loads(summary_raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError(f"{scenario}: invalid raw observer summary: {error}") from error
    _require(isinstance(summary, dict), f"{scenario}: raw observer summary must be an object")
    rows = []
    try:
        for line_number, line in enumerate(observations_raw.decode("utf-8-sig").splitlines(), 1):
            if line.strip():
                row = loads(line)
                _require(isinstance(row, dict), f"{scenario}: observations line {line_number} is not an object")
                rows.append(row)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError(f"{scenario}: invalid observations JSONL: {error}") from error

    sent = {row.get("packet", {}).get("body", {}).get("sample_id") for row in rows
            if row.get("kind") == "send_intent" and isinstance(row.get("packet"), dict)
            and row["packet"].get("message_type") == "CONTROL_REQUEST"}
    valid = {row.get("sample_id") for row in rows if row.get("kind") == "control_valid"}
    timeouts = {row.get("sample_id") for row in rows if row.get("kind") == "control_timeout"}
    late = {row.get("sample_id") for row in rows if row.get("kind") == "late_control"}
    telemetry_unavailable = sum(row.get("kind") == "telemetry_unavailable" for row in rows)
    _require(None not in sent | valid | timeouts | late, f"{scenario}: control event lacks sample_id")
    _require(len(sent) == len([row for row in rows if row.get("kind") == "send_intent"
                               and isinstance(row.get("packet"), dict)
                               and row["packet"].get("message_type") == "CONTROL_REQUEST"]),
             f"{scenario}: duplicate sent sample_id")
    _require(valid <= sent and timeouts <= sent and late <= timeouts,
             f"{scenario}: control outcome IDs do not reconcile with requests/timeouts")
    counters = summary.get("counters")
    _require(isinstance(counters, dict), f"{scenario}: observer counters are missing")
    _require(summary.get("sent_controls") == len(sent)
             and counters.get("control_valid") == len(valid)
             and counters.get("control_timeout") == len(timeouts)
             and counters.get("late_control", 0) == len(late)
             and counters.get("telemetry_unavailable", 0) == telemetry_unavailable,
             f"{scenario}: raw control IDs differ from observer summary counters")
    service = analysis.get("service", {})
    _require(service.get("lost_controls") == len(timeouts)
             and service.get("control_max_gap_ns") == summary.get("control_max_gap_ns"),
             f"{scenario}: analyzer service counters differ from raw observer summary")
    accepted_events = [row for row in rows if row.get("kind") == "update_accepted"]
    v2_events = [row for row in rows if row.get("kind") == "v2_observed"]
    _require(len(accepted_events) == len(v2_events) == 1,
             f"{scenario}: expected one acceptance and first-v2 boundary")
    accepted_ns = accepted_events[0].get("host_monotonic_ns")
    first_v2_ns = v2_events[0].get("host_monotonic_ns")
    _require(isinstance(accepted_ns, int) and isinstance(first_v2_ns, int)
             and first_v2_ns >= accepted_ns
             and (service.get("accepted_ns") is None or accepted_ns == service.get("accepted_ns"))
             and (service.get("activated_ns") is None or first_v2_ns == service.get("activated_ns")),
             f"{scenario}: acceptance/v2 host-clock boundaries differ from analyzer")
    return {
        "control_requests": len(sent),
        "valid_controls": len(valid),
        "control_timeouts": len(timeouts),
        "control_timeout_rate": len(timeouts) / len(sent) if sent else None,
        "control_timeout_sample_ids": sorted(timeouts),
        "late_control_sample_ids": sorted(late),
        "late_control_count": len(late),
        "telemetry_unavailable_events": telemetry_unavailable,
        "control_max_gap_ns": summary.get("control_max_gap_ns"),
        "control_unavailability_estimate_ns": summary.get("control_unavailability_estimate_ns"),
        "control_tail_censored_ns": summary.get("control_tail_censored_ns"),
        "acceptance_to_first_v2_ns": first_v2_ns - accepted_ns,
        "observation_window_ns": (summary.get("observation_end_ns", 0)
                                   - summary.get("observation_start_ns", 0)),
        "telemetry_max_gap_full_capture_ns": summary.get("telemetry_max_gap_ns"),
    }


def _enrich_module(evidence: dict, raw_report: dict | None,
                   raw_bytes: dict[str, bytes] | None, raw_hashes: dict[str, str] | None,
                   scenario: str) -> None:
    """Replace summary-only nulls when the matching private raw capture is supplied."""
    if raw_report is None or raw_bytes is None or raw_hashes is None:
        return
    _require(raw_report.get("payload", {}).get("bytes_expected")
             == evidence["payload"]["artifact_bytes"]
             == raw_report.get("payload", {}).get("bytes_received"),
             f"{scenario}: raw payload differs from published evidence")
    _require(raw_report.get("memory", {}).get("minimum_internal_free_bytes")
             == evidence["memory"]["internal_free_min_bytes"]
             and raw_report.get("memory", {}).get("minimum_psram_free_bytes")
             == evidence["memory"]["psram_free_min_bytes"],
             f"{scenario}: raw memory minima differ from published evidence")
    control = _raw_control_metrics(raw_bytes["observations"], raw_bytes["summary"],
                                   raw_report, scenario)
    phase_times = raw_report.get("phase_durations_us")
    snapshots = raw_report.get("memory", {}).get("heap_snapshots")
    lifecycle = raw_report.get("memory", {}).get("module_lifecycle", {}).get("snapshots")
    _require(isinstance(phase_times, dict) and phase_times,
             f"{scenario}: raw analyzer phase timings are missing")
    _require(isinstance(snapshots, list) and snapshots,
             f"{scenario}: raw analyzer phase heap snapshots are missing")
    _require(isinstance(lifecycle, list) and lifecycle,
             f"{scenario}: raw analyzer WAMR lifecycle snapshots are missing")
    evidence["timing"]["device_phases_us"] = phase_times
    evidence["timing"]["device_phase_clock"] = "mcu_monotonic_us_within_one_boot"
    evidence["timing"]["acceptance_to_first_v2_ns"] = control["acceptance_to_first_v2_ns"]
    evidence["timing"].pop("acceptance_to_first_v2_null_reason", None)
    evidence["timing"].pop("device_phases_null_reason", None)
    evidence["memory"]["phase_snapshots"] = snapshots
    evidence["memory"].pop("phase_snapshots_null_reason", None)
    evidence["memory"]["module_lifecycle_snapshots"] = lifecycle
    evidence["service"].update(control)
    evidence["service"]["control_requests_null_reason"] = None
    evidence["service"]["control_interval_max_gap_ns"] = control["control_max_gap_ns"]
    evidence["service"].pop("control_interval_max_gap_null_reason", None)
    evidence["service"]["telemetry_observation_window_ns"] = control["observation_window_ns"]
    evidence["service"]["telemetry_unavailable"] = control["telemetry_unavailable_events"]
    evidence["service"]["telemetry_unavailable_null_reason"] = None
    evidence["service"]["telemetry_max_gap_full_capture_ns"] = control["telemetry_max_gap_full_capture_ns"]
    evidence["private_capture_sha256"] = raw_hashes
    # Source input bytes stay private; the public report contains only derived, sanitized values.


def _enrich_native(evidence: dict, raw_report: dict, raw_bytes: dict[str, bytes],
                   raw_hashes: dict[str, str]) -> None:
    payload = raw_report.get("payload", {})
    source_payload = evidence["payload"]
    _require(payload.get("bytes_expected") == source_payload["artifact_bytes"]
             and payload.get("bytes_received") == payload.get("bytes_expected")
             and payload.get("sha256") == source_payload["sha256"]
             and payload.get("running_sha256") == payload.get("sha256"),
             "native: raw UART transfer/hash differs from signed artifact evidence")
    control = _raw_control_metrics(raw_bytes["observations"], raw_bytes["summary"],
                                   raw_report, "native")
    phases = raw_report.get("phase_durations_us_within_boot_only")
    snapshots = raw_report.get("memory", {}).get("heap_snapshots")
    _require(isinstance(phases, dict) and phases and isinstance(snapshots, list) and snapshots,
             "native: raw analyzer phase timings or heap snapshots are missing")
    evidence["payload"]["transport_bytes_received"] = payload["bytes_received"]
    evidence["payload"].pop("transport_bytes_received_null_reason", None)
    evidence["timing"]["device_phases_us"] = phases
    evidence["timing"]["device_phase_clock"] = "mcu_monotonic_us_within_each_boot"
    evidence["timing"]["trigger_to_first_v2_ns"] = raw_report["service"]["trigger_to_first_v2_host_ns"]
    evidence["timing"]["acceptance_to_first_v2_ns"] = control["acceptance_to_first_v2_ns"]
    evidence["memory"]["phase_snapshots"] = snapshots
    _require(min(row["internal_free_bytes"] for row in snapshots)
             == evidence["memory"]["internal_free_min_bytes"]
             and min(row["psram_free_bytes"] for row in snapshots)
             == evidence["memory"]["psram_free_min_bytes"],
             "native: raw phase snapshots differ from published minima")
    evidence["service"].update(control)
    evidence["service"]["telemetry_max_gap_ns"] = raw_report["service"]["telemetry_max_gap_ns"]
    evidence["service"]["telemetry_gap_window"] = "full observer capture across reboot"
    evidence["service"]["telemetry_unavailable"] = raw_report["service"]["observer_counters"].get(
        "telemetry_unavailable", 0)
    evidence["service"]["control_interval_max_gap_ns"] = control["control_max_gap_ns"]
    evidence["service"].pop("control_interval_max_gap_null_reason", None)
    evidence["service"]["late_controls"] = control["late_control_count"]
    evidence["service"].pop("late_controls_null_reason", None)
    evidence["private_capture_sha256"] = raw_hashes


def _physical_context(smoke: dict, restoration: dict, scenario: str) -> dict:
    if scenario == "interp":
        _require(smoke.get("passed") is True and smoke.get("trigger_sent") is False
                 and smoke.get("update_acceptance_count") == 0
                 and smoke.get("new_boot_distinct_from_main") is True
                 and smoke.get("all_controls_v2_and_expected_gpio") is True,
                 "interp: post-reboot smoke proof failed or is ambiguous")
        errors = smoke.get("errors")
        _require(isinstance(errors, dict) and errors and all(value == 0 for value in errors.values()),
                 "interp: post-reboot smoke has errors")
        _require(smoke.get("telemetry_valid", 0) > 0 and smoke.get("control_valid", 0) > 0,
                 "interp: post-reboot smoke lacks telemetry or control evidence")
        _require(restoration.get("result") == "passed"
                 and restoration.get("byte_for_byte_equal") is True
                 and restoration.get("source_bytes") == restoration.get("readback_bytes")
                 and restoration.get("source_sha256") == restoration.get("readback_sha256"),
                 "interp: byte-for-byte restoration verification failed")
        return {"smoke": {"result": "passed", "telemetry_valid": smoke["telemetry_valid"],
                           "control_valid": smoke["control_valid"],
                           "new_boot_distinct_from_main": True,
                           "unresolved_sequence_gaps": smoke.get("unresolved_sequence_gaps")},
                "restoration": {"result": "passed", "byte_for_byte_equal": True,
                                "bytes": restoration["readback_bytes"],
                                "sha256": restoration["readback_sha256"]}}
    sent, valid = smoke.get("sent_controls"), smoke.get("control_valid")
    _require(isinstance(sent, int) and sent > 0 and valid == sent
             and smoke.get("control_timeouts") == 0 and smoke.get("lost_controls") == 0
             and smoke.get("telemetry_valid", 0) > 0,
             "aot: post-reboot smoke proof failed or is ambiguous")
    unresolved = smoke.get("unresolved_sequence_gaps")
    _require(isinstance(unresolved, dict) and unresolved
             and all(value == 0 for value in unresolved.values()),
             "aot: post-reboot smoke has unresolved sequence gaps")
    _require(restoration.get("all_regions_match") is True
             and restoration.get("full_image_match") is True
             and restoration.get("baseline_bytes") == restoration.get("readback_bytes")
             and restoration.get("baseline_sha256") == restoration.get("readback_sha256"),
             "aot: full-image restoration verification failed")
    return {"smoke": {"result": "passed", "telemetry_valid": smoke["telemetry_valid"],
                       "control_valid": valid, "control_timeouts": 0,
                       "unresolved_sequence_gaps": unresolved,
                       "proof_scope": smoke.get("proof_scope")},
            "restoration": {"result": "passed", "full_image_match": True,
                            "bytes": restoration["readback_bytes"],
                            "sha256": restoration["readback_sha256"],
                            "device_state_after_readback": restoration.get("device_state_after_readback")}}


def compare(inputs: dict[str, Path],
            private_module_inputs: dict[str, dict[str, Path]] | None = None,
            private_context_inputs: dict[str, dict[str, Path]] | None = None) -> dict:
    required_scenarios = {"native", "interp", "aot"}
    _require(isinstance(private_module_inputs, dict)
             and set(private_module_inputs) == required_scenarios,
             "complete private raw capture groups are required for native, interp, and aot")
    required_capture_files = {"uart", "observations", "summary"}
    for scenario in required_scenarios:
        paths = private_module_inputs[scenario]
        _require(isinstance(paths, dict) and set(paths) == required_capture_files,
                 f"{scenario}: complete private raw capture requires {sorted(required_capture_files)}")
    source = {}
    hashes = {}
    for name, path in inputs.items():
        source[name], hashes[name] = _read(path, name)
    runs = {"native": _native(source["native"]),
            "interp": _interp(source["interp"]),
            "aot": _aot(source["aot"])}
    private_hashes = {}
    for scenario, paths in (private_module_inputs or {}).items():
        _require(scenario in {"native", "interp", "aot"}, f"unknown private capture scenario: {scenario}")
        raw_report, raw_bytes, raw_hashes = _raw_capture(paths, scenario)
        _check_public_private_hashes(source, scenario, raw_hashes)
        if scenario == "native":
            _enrich_native(runs[scenario], raw_report, raw_bytes, raw_hashes)
        else:
            _enrich_module(runs[scenario], raw_report, raw_bytes, raw_hashes, scenario)
        private_hashes[scenario] = raw_hashes
    private_context = {}
    for scenario, paths in (private_context_inputs or {}).items():
        _require(scenario in {"interp", "aot"}, f"unknown private context scenario: {scenario}")
        _require(set(paths) == {"smoke", "restoration"},
                 f"{scenario}: private context requires smoke and restoration verification files")
        smoke, smoke_hash = _read(paths["smoke"], f"{scenario} smoke verification")
        restoration, restoration_hash = _read(paths["restoration"], f"{scenario} restoration verification")
        private_context[scenario] = _physical_context(smoke, restoration, scenario)
        private_context[scenario]["evidence_sha256"] = {
            "smoke_verification": smoke_hash, "restoration_verification": restoration_hash}
        declared = (source[scenario].get("private_artifact_sha256") if scenario == "interp"
                    else source[scenario].get("private_evidence_sha256"))
        known_hashes = ({"smoke_observations": "smoke_observations",
                         "smoke_summary": "smoke_observer_summary"} if scenario == "interp" else
                        {"smoke_observations": "smoke_observations_jsonl",
                         "smoke_summary": "smoke_observer_summary",
                         "smoke_verification": "smoke_verification",
                         "restoration_verification": "restoration_verification"})
        for source_name, public_name in known_hashes.items():
            if public_name in declared:
                local_name = ("smoke_verification" if source_name == "smoke_verification"
                              else "restoration_verification" if source_name == "restoration_verification"
                              else None)
                if local_name:
                    _require(declared[public_name] == private_context[scenario]["evidence_sha256"][local_name],
                             f"{scenario}: {source_name} hash differs from published evidence")
                else:
                    # Smoke source streams are represented by the verification's input manifest.
                    input_hashes = smoke.get("input_sha256", {})
                    if source_name == "smoke_observations":
                        if input_hashes:
                            expected_smoke_observations = input_hashes.get("observations") or input_hashes.get(
                                "smoke/observer/observations.jsonl")
                            if expected_smoke_observations is not None:
                                _require(expected_smoke_observations == declared[public_name],
                                         f"{scenario}: smoke observations differ from published evidence")
                    elif source_name == "smoke_summary":
                        if input_hashes:
                            expected_smoke_summary = input_hashes.get("summary") or input_hashes.get(
                                "smoke/observer/summary.json")
                            if expected_smoke_summary is not None:
                                _require(expected_smoke_summary == declared[public_name],
                                         f"{scenario}: smoke summary differs from published evidence")

    benchmark = source["benchmark"]
    scenarios = benchmark.get("scenarios")
    _require(isinstance(scenarios, list) and len(scenarios) == 3,
             "benchmark: expected native/interp/aot records")
    bench = {row.get("scenario"): row for row in scenarios}
    _require(set(bench) == {"native", "interp", "aot"}, "benchmark: scenario set differs")
    checksums = {row.get("checksum") for row in scenarios}
    call_counts = {row.get("measured_calls") for row in scenarios}
    warmups = {row.get("checked_warmup_calls") for row in scenarios}
    _require(len(checksums) == 1 and next(iter(checksums)) == "0xDBB2F0D4",
             "benchmark: workload checksums do not match")
    _require(call_counts == {30} and warmups == {5}, "benchmark: workload repetitions differ")
    for name, row in bench.items():
        timing = row.get("timing_us")
        _require(isinstance(timing, dict), f"benchmark {name}: timing is missing")
        exec_stats = timing.get("execute_us")
        _require(isinstance(exec_stats, dict), f"benchmark {name}: execution stats are missing")
        for field in ("min", "median", "mean", "max"):
            _nonnegative(exec_stats.get(field), f"benchmark {name} execute {field}", allow_float=True)
        if name == "native":
            _require(timing.get("load_us") is None and timing.get("instantiate_us") is None,
                     "benchmark native: WAMR load/instantiate must be null")
        else:
            _nonnegative(timing.get("load_us"), f"benchmark {name} load", allow_float=True)
            _nonnegative(timing.get("instantiate_us"), f"benchmark {name} instantiate", allow_float=True)
    reference = next(iter(runs.values()))["functional_oracle"]
    _require(all(run["functional_oracle"]["temperature_c"] == 27
                 and run["functional_oracle"]["v1_gpio"] == "high"
                 and run["functional_oracle"]["v2_gpio"] == "low"
                 and isinstance(run["functional_oracle"]["v1_responses"], int)
                 and run["functional_oracle"]["v1_responses"] > 0
                 and isinstance(run["functional_oracle"]["v2_responses"], int)
                 and run["functional_oracle"]["v2_responses"] > 0 for run in runs.values()),
             "functional workload oracle differs or is missing")

    processing = {}
    for name, row in bench.items():
        timing = row["timing_us"]
        processing[name] = {
            "warmup_calls": row["checked_warmup_calls"],
            "measured_calls": row["measured_calls"],
            "checksum": row["checksum"],
            "execute_call_us": timing["execute_us"],
            "runtime_module_load_us": timing.get("load_us"),
            "runtime_module_instantiate_us": timing.get("instantiate_us"),
            "wamr_runtime_applicable": name != "native",
            "interpretation": ("native workload execution baseline" if name == "native"
                                else "guest workload plus WAMR invocation/runtime; not isolated host-call overhead"),
        }
    null_fields = {
        "processing_costs.native.runtime_module_load_us": "not_applicable",
        "processing_costs.native.runtime_module_instantiate_us": "not_applicable",
    }
    if "native" not in private_hashes:
        null_fields["scenarios.native.payload.transport_bytes_received"] = (
            "not_published; provide the matching private UART/observer capture")
        null_fields["scenarios.native.service.late_controls"] = "not_reported_by_public_native_summary"
    if "interp" not in private_hashes:
        null_fields.update({
            "scenarios.interp.timing.device_phases_us": "not_published_in_summary",
            "scenarios.interp.memory.phase_snapshots": "not_published_in_summary",
            "scenarios.interp.service.control_requests": "not_published_in_summary",
            "scenarios.interp.service.control_interval_max_gap_ns": "not_published_in_summary",
            "scenarios.interp.service.telemetry_unavailable": "not_published_in_summary",
        })
    if "aot" not in private_hashes:
        null_fields.update({
            "scenarios.aot.timing.acceptance_to_first_v2_ns": "not_published_in_summary",
            "scenarios.aot.memory.phase_snapshots": "not_published_in_summary",
            "scenarios.aot.service.control_requests": "not_published_in_summary",
            "scenarios.aot.service.control_interval_max_gap_ns": "not_published_in_summary",
            "scenarios.aot.service.telemetry_unavailable": "not_published_in_summary",
        })
    return {
        "schema": "wasmhome-update-metrics-comparison-v1",
        "result": "passed",
        "evidence_date": "2026-10-16",
        "source_evidence_sha256": hashes,
        "private_capture_sha256": private_hashes,
        "post_update_physical_context": private_context,
        "functional_equivalence": {
            "status": "passed",
            "oracle": "same control contract at 27 C: v1 drives GPIO high and v2 drives GPIO low",
            "artifact_bytes_equal": False,
            "artifact_sizes_are_transport_costs_not_workload_equivalence": True,
            "microbenchmark_checksum": next(iter(checksums)),
            "microbenchmark_calls_per_scenario": 30,
            "checked_warmup_calls_per_scenario": 5,
        },
        "scenarios": runs,
        "processing_costs": processing,
        "clock_policy": {
            "cross_reboot_and_end_to_end": "observer host_monotonic_ns only",
            "device_stages": "MCU elapsed_us only within a single boot_id; native OTA stages split at reboot",
            "cross_domain_subtraction": "forbidden",
        },
        "null_policy": {
            "meaning": "each null below has a reason; missing/unpublished is distinct from not_applicable",
            "fields": null_fields,
        },
        "limitations": [
            "Each update path has one accepted physical update in this calibration record; it is not a campaign or an estimate of between-session variance.",
            "The public evidence summaries omit raw per-phase fields; supply the matching private UART/observer files to derive them without publishing raw logs.",
            "The processing E1 microbenchmark is one physical session with repeated calls, not independent update samples.",
            "Execution timing combines the workload and, for WAMR, guest invocation/runtime overhead; module load and instantiation are separately observed, but pure host-call overhead is not isolated.",
            "Heap records are point snapshots; they do not establish transient global peaks. Internal heap and PSRAM remain separate.",
            "Timeouts, late responses, temporal gaps and unresolved sequence gaps are distinct measures. An unresolved gap count of zero does not mean zero temporal gaps.",
        ],
    }


def write_exclusive(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name, default in DEFAULT_INPUTS.items():
        parser.add_argument(f"--{name}", type=Path, default=default)
    for scenario in ("native", "interp", "aot"):
        parser.add_argument(f"--{scenario}-uart", type=Path,
                            help=f"private {scenario} UART capture; enables full phase analysis")
        parser.add_argument(f"--{scenario}-observations", type=Path,
                            help=f"private {scenario} observer JSONL; enables request/loss ID analysis")
        parser.add_argument(f"--{scenario}-summary", type=Path,
                            help=f"private {scenario} observer summary JSON")
        parser.add_argument(f"--{scenario}-smoke-verification", type=Path,
                            help=f"private {scenario} post-reboot smoke verification JSON")
        parser.add_argument(f"--{scenario}-restoration-verification", type=Path,
                            help=f"private {scenario} physical restoration verification JSON")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inputs = {name: getattr(args, name) for name in DEFAULT_INPUTS}
    private_module_inputs = {}
    private_context_inputs = {}
    for scenario in ("native", "interp", "aot"):
        capture = {name: getattr(args, f"{scenario}_{name}")
                   for name in ("uart", "observations", "summary")}
        provided = [value is not None for value in capture.values()]
        if any(provided):
            if not all(provided):
                parser.error(f"--{scenario}-uart, --{scenario}-observations and --{scenario}-summary must be supplied together")
            private_module_inputs[scenario] = capture
        context = {name: getattr(args, f"{scenario}_{name.replace('-', '_')}")
                   for name in ("smoke-verification", "restoration-verification")}
        context_provided = [value is not None for value in context.values()]
        if any(context_provided):
            if not all(context_provided):
                parser.error(f"--{scenario}-smoke-verification and --{scenario}-restoration-verification must be supplied together")
            private_context_inputs[scenario] = {
                "smoke": context["smoke-verification"],
                "restoration": context["restoration-verification"],
            }
    missing_captures = {"native", "interp", "aot"} - set(private_module_inputs)
    if missing_captures:
        parser.error("complete private raw capture groups are required for native, interp, and aot; "
                     f"missing: {', '.join(sorted(missing_captures))}")
    try:
        report = compare(inputs, private_module_inputs, private_context_inputs)
        write_exclusive(args.output, report)
    except (OSError, ValueError) as error:
        print(f"compare_update_metrics: {error}", file=sys.stderr)
        return 1
    print(f"result={report['result']} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
