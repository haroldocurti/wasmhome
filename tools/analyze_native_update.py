#!/usr/bin/env python3
"""Reconcile native OTA UART events with external UDP observations, offline only."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import uuid

from analyze_module_update import (
    SHA256_RE, _heap, _number, normalize_boot_id, parse_observations,
    parse_update_events, sha256_bytes, write_exclusive,
)

PREBOOT_PHASES = (
    "accepted", "manifest_download_started", "manifest_downloaded",
    "signature_verified", "payload_download_started", "payload_verified",
    "staged", "activating", "reboot_requested",
)
POSTBOOT_PHASES = ("boot_pending", "health_check_passed", "floor_committed")
REQUIRED_PHASES = PREBOOT_PHASES + POSTBOOT_PHASES
OPTIONAL_PHASES = {"payload_download_progress"}
PHASE_STATUSES = {
    "accepted": {"ok"},
    "manifest_download_started": {"in_progress"},
    "manifest_downloaded": {"ok"},
    "signature_verified": {"ok"},
    "payload_download_started": {"in_progress"},
    "payload_download_progress": {"in_progress", "ok"},
    "payload_verified": {"ok"},
    "staged": {"ok"},
    "activating": {"in_progress"},
    "reboot_requested": {"in_progress"},
    "boot_pending": {"in_progress"},
    "health_check_passed": {"ok"},
    "floor_committed": {"ok"},
}
STRICT_BOOT_RE = re.compile(r"^[0-9a-f]{16}$")


def _attempt(value) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


def _strict_boot(value) -> str | None:
    if not isinstance(value, str) or not STRICT_BOOT_RE.fullmatch(value):
        return None
    normalized = normalize_boot_id(value)
    return normalized if normalized is not None else None


def _phase(events: list[dict], phase: str) -> dict | None:
    values = [event for event in events if event.get("phase") == phase]
    return values[0] if len(values) == 1 else None


def analyze(update_raw: bytes, observations_raw: bytes, summary_raw: bytes,
            expected_scenario: int = 1, expected_sequence: int = 2) -> dict:
    update_events = parse_update_events(update_raw)
    observations = parse_observations(observations_raw)
    try:
        summary = json.loads(summary_raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid observer summary JSON: {error}") from error
    if not isinstance(summary, dict):
        raise ValueError("observer summary must be a JSON object")

    checks: dict[str, dict] = {}

    def put(name: str, passed: bool, observed):
        checks[name] = {"passed": bool(passed), "observed": observed}

    expected_attempt = _attempt(summary.get("attempt_id"))
    matching = [event for event in update_events
                if _attempt(event.get("attempt_id")) == expected_attempt and expected_attempt]
    put("attempt_id_correlated", bool(expected_attempt and matching),
        {"attempt_id": expected_attempt, "matching_event_count": len(matching)})

    phases = {phase: _phase(matching, phase) for phase in REQUIRED_PHASES}
    missing = [phase for phase, event in phases.items() if event is None]
    bad_status = [phase for phase, event in phases.items()
                  if event is not None and event.get("status") not in PHASE_STATUSES[phase]]
    bad_failure = [event.get("phase") for event in matching if event.get("failure") != "none"]
    bad_event_name = [event.get("phase") for event in matching
                      if event.get("event") != event.get("phase")]
    invalid_status = [event.get("phase") for event in matching
                      if event.get("status") not in ("in_progress", "ok", "rejected", "error")]
    put("required_stages_succeeded", not missing and not bad_status and not bad_failure
        and not bad_event_name and not invalid_status,
        {"missing_or_duplicate": missing, "unexpected_status": bad_status,
         "non_none_failure": bad_failure, "event_phase_mismatch": bad_event_name,
         "unknown_status": invalid_status})

    ordered = sorted(matching, key=lambda event: event.get("_source_line", -1))
    timeline_phases = {phase: rank for rank, phase in enumerate(REQUIRED_PHASES)}
    ranks = []
    for event in ordered:
        phase = event.get("phase")
        if phase in timeline_phases:
            ranks.append((event, timeline_phases[phase]))
        elif phase in OPTIONAL_PHASES:
            ranks.append((event, timeline_phases["payload_download_started"] + 0.5))
    elapsed_bad = [event.get("phase") for event, _ in ranks
                   if _number(event.get("elapsed_us")) is None]
    rank_ordered = all(later[1] >= earlier[1] for earlier, later in zip(ranks, ranks[1:]))
    scopes_by_boot: dict[str, set] = {}
    elapsed_by_boot: dict[str, list[int]] = {}
    invalid_event_boots = []
    invalid_origin_boots = []
    for event, _ in ranks:
        phase = event.get("phase")
        boot = _strict_boot(event.get("boot_id"))
        if boot is None:
            invalid_event_boots.append(phase)
            continue
        scopes_by_boot.setdefault(boot, set()).add(event.get("elapsed_scope"))
        elapsed = _number(event.get("elapsed_us"))
        if elapsed is not None:
            elapsed_by_boot.setdefault(boot, []).append(elapsed)
        if phase in POSTBOOT_PHASES:
            origin = _strict_boot(event.get("origin_boot_id"))
            if origin is None:
                invalid_origin_boots.append(phase)
    boots = sorted({_strict_boot(event.get("boot_id")) for event, _ in ranks} - {None})
    timeline_valid = (not missing and not elapsed_bad and rank_ordered
                      and not invalid_event_boots and not invalid_origin_boots and len(boots) == 2
                      and all(len(values) == 1 and next(iter(values)) == "boot"
                              for values in scopes_by_boot.values()))
    per_boot_monotonic = {
        boot: all(right >= left for left, right in zip(times, times[1:]))
        for boot, times in elapsed_by_boot.items()
    }
    timeline_valid = timeline_valid and all(per_boot_monotonic.values())
    put("device_timeline_is_ordered_per_boot", timeline_valid,
        {"required_phase_order": list(REQUIRED_PHASES), "missing_or_duplicate": missing,
         "phase_ordered_in_uart": rank_ordered, "invalid_elapsed_phases": elapsed_bad,
         "invalid_boot_id_phases": invalid_event_boots,
         "invalid_origin_boot_id_phases": invalid_origin_boots,
         "boot_ids": boots, "elapsed_scopes_by_boot":
             {key: sorted(str(item) for item in value) for key, value in scopes_by_boot.items()},
         "elapsed_monotonic_by_boot": per_boot_monotonic})

    phase_boots = {phase: _strict_boot((event or {}).get("boot_id"))
                   for phase, event in phases.items()}
    old_boot = phase_boots.get("accepted")
    new_boot = phase_boots.get("boot_pending")
    post_origins = [_strict_boot((phases[phase] or {}).get("origin_boot_id"))
                    for phase in POSTBOOT_PHASES]
    preboot_consistent = bool(old_boot) and all(phase_boots.get(phase) == old_boot
                                               for phase in PREBOOT_PHASES)
    postboot_consistent = bool(new_boot) and all(phase_boots.get(phase) == new_boot
                                                for phase in POSTBOOT_PHASES)
    origin_consistent = bool(old_boot) and all(origin == old_boot for origin in post_origins)
    observed_boots = [normalize_boot_id(value) for value in summary.get("boot_ids", [])]
    observed_boots = [value for value in observed_boots if value is not None]
    observer_boot_matches = (len(observed_boots) == 2 and len(set(observed_boots)) == 2
                             and observed_boots[0] == old_boot and observed_boots[1] == new_boot)
    put("reboot_correlates_old_and_new_boot", preboot_consistent and postboot_consistent
        and origin_consistent and observer_boot_matches,
        {"old_boot_id": old_boot, "new_boot_id": new_boot,
         "origin_boot_ids": post_origins, "observer_boot_ids": observed_boots,
         "pre_reboot_phases_on_old_boot": preboot_consistent,
         "post_reboot_phases_on_new_boot": postboot_consistent,
         "post_reboot_origin_matches": origin_consistent})

    metadata_ok = bool(matching) and all(
        type(event.get("scenario")) is int and event.get("scenario") == expected_scenario
        and type(event.get("component")) is int and event.get("component") == 1
        and event.get("release_version") == "v2"
        and type(event.get("target_version")) is int and event.get("target_version") == 2
        and type(event.get("sequence")) is int and event.get("sequence") == expected_sequence
        for event in matching)
    put("native_target_metadata_matches", metadata_ok,
        {"scenario": expected_scenario, "component": 1,
         "release_version": "v2", "target_version": 2,
         "sequence": expected_sequence, "all_events_match": metadata_ok})

    manifest = phases.get("manifest_downloaded") or {}
    signature = phases.get("signature_verified") or {}
    manifest_sha = manifest.get("manifest_sha256")
    signed_sha = signature.get("manifest_sha256")
    signature_bound = (isinstance(manifest_sha, str) and SHA256_RE.fullmatch(manifest_sha)
                       and manifest_sha == signed_sha)
    put("signature_verifies_downloaded_manifest", bool(signature_bound),
        {"manifest_downloaded_sha256": manifest_sha if isinstance(manifest_sha, str)
         and SHA256_RE.fullmatch(manifest_sha) else None,
         "signature_verified_sha256": signed_sha if isinstance(signed_sha, str)
         and SHA256_RE.fullmatch(signed_sha) else None})

    payload_event = phases.get("payload_verified") or {}
    payload_sha = payload_event.get("payload_sha256")
    payload_expected = _number(payload_event.get("payload_bytes_expected"))
    payload_received = _number(payload_event.get("payload_bytes_received"))
    payload_complete = (payload_expected is not None and payload_expected > 0
                        and payload_expected == payload_received
                        and isinstance(payload_sha, str) and SHA256_RE.fullmatch(payload_sha))
    running_sha = (phases.get("health_check_passed") or {}).get("running_sha256")
    running_matches = payload_complete and running_sha == payload_sha
    put("verified_payload_is_running_image", bool(running_matches),
        {"payload_bytes_expected": payload_expected, "payload_bytes_received": payload_received,
         "payload_sha256": payload_sha if payload_complete else None,
         "running_sha256": running_sha if isinstance(running_sha, str)
         and SHA256_RE.fullmatch(running_sha) else None})

    old_floor = _number((phases.get("accepted") or {}).get("accepted_floor"))
    new_floor = _number((phases.get("floor_committed") or {}).get("accepted_floor"))
    readback = _number((phases.get("floor_committed") or {}).get("ledger_readback_sequence"))
    floor_matches = (old_floor is not None and old_floor < expected_sequence
                     and new_floor == expected_sequence
                     and readback == expected_sequence)
    put("anti_rollback_floor_readback_matches_target", floor_matches,
        {"accepted_floor_before": old_floor, "accepted_floor_after": new_floor,
         "ledger_readback_sequence": readback, "expected_sequence": expected_sequence})

    heaps_required = ("accepted", "payload_verified", "boot_pending",
                      "health_check_passed", "floor_committed")
    missing_heap, malformed_heap = [], []
    heap_snapshots = []
    for phase in heaps_required:
        event = phases.get(phase)
        heap = _heap(event or {})
        if event is None or heap.get("internal_free_bytes") is None \
                or heap.get("internal_largest_free_block_bytes") is None:
            missing_heap.append(phase)
            continue
        if heap["internal_largest_free_block_bytes"] > heap["internal_free_bytes"]:
            malformed_heap.append(phase)
        if (heap.get("psram_free_bytes") is not None
                and heap.get("psram_largest_free_block_bytes") is not None
                and heap["psram_largest_free_block_bytes"] > heap["psram_free_bytes"]):
            malformed_heap.append(phase)
        heap_snapshots.append({"phase": phase, "boot_id": _strict_boot(event.get("boot_id")), **heap})
    put("heap_snapshots_recorded_on_both_boots", not missing_heap and not malformed_heap,
        {"required_phases": list(heaps_required), "missing_or_unmeasured": missing_heap,
         "inconsistent_heap_values": malformed_heap})

    sessions = [row for row in observations if row.get("kind") == "session_start"]
    trigger_intents = []
    for row in observations:
        packet = row.get("packet")
        if row.get("kind") == "send_intent" and isinstance(packet, dict) \
                and packet.get("message_type") == "UPDATE_TRIGGER":
            trigger_intents.append(packet)
    configured = (len(sessions) == 1 and sessions[0].get("initial_version") == 1
                  and sessions[0].get("scenario") == expected_scenario
                  and sessions[0].get("target_sequence") == expected_sequence)
    valid_trigger_rows = [row for row in observations
                          if row.get("kind") == "send_intent"
                          and isinstance(row.get("packet"), dict)
                          and row["packet"].get("message_type") == "UPDATE_TRIGGER"
                          and _attempt(row["packet"].get("attempt_id")) == expected_attempt
                          and normalize_boot_id(row["packet"].get("boot_id")) == old_boot
                          and isinstance(row["packet"].get("body"), dict)
                          and row["packet"]["body"].get("scenario") == expected_scenario
                          and row["packet"]["body"].get("component") == 1
                          and row["packet"]["body"].get("target_version") == 2
                          and row["packet"]["body"].get("target_sequence") == expected_sequence]
    valid_trigger = [row["packet"] for row in valid_trigger_rows]
    put("observer_trigger_matches_native_target", configured
        and len(trigger_intents) == 1 and len(valid_trigger) == 1,
        {"session_count": len(sessions), "trigger_count": len(trigger_intents),
         "matching_trigger_count": len(valid_trigger)})

    accepted_decoded = []
    for row in observations:
        packet = row.get("packet")
        if row.get("kind") != "decoded" or not isinstance(packet, dict) \
                or packet.get("message_type") != "UPDATE_ACCEPTANCE":
            continue
        body = packet.get("body")
        if (_attempt(packet.get("attempt_id")) == expected_attempt
                and normalize_boot_id(packet.get("boot_id")) == old_boot
                and isinstance(body, dict)
                and body.get("status") == 1 and body.get("reason") == 0
                and body.get("target_sequence") == expected_sequence
                and valid_trigger
                and body.get("request_sequence") == valid_trigger[0].get("sequence")):
            accepted_decoded.append(row)
    accepted_ns = summary.get("accepted_ns")
    accepted_events = [row for row in observations if row.get("kind") == "update_accepted"]
    udp_acceptance_ok = (summary.get("update_state") == "activated"
                         and isinstance(accepted_ns, int)
                         and len(accepted_decoded) == 1
                         and len(accepted_events) == 1
                         and accepted_decoded[0].get("host_monotonic_ns") == accepted_ns
                         and accepted_events[0].get("host_monotonic_ns") == accepted_ns)
    put("udp_acceptance_correlates_to_serial_attempt", udp_acceptance_ok,
        {"matching_udp_acceptance_count": len(accepted_decoded),
         "accepted_event_count": len(accepted_events),
         "accepted_host_monotonic_ns": accepted_ns,
         "observer_update_state": summary.get("update_state")})

    activated_ns = summary.get("activated_ns")
    v2_controls = [row for row in observations if row.get("kind") == "control_valid"
                   and normalize_boot_id(row.get("boot_id")) == new_boot
                   and row.get("module_version") == 2
                   and isinstance(row.get("host_monotonic_ns"), int)]
    v2_observed = [row for row in observations if row.get("kind") == "v2_observed"]
    first_v2_ns = min((row["host_monotonic_ns"] for row in v2_controls), default=None)
    activation_reconciled = (isinstance(activated_ns, int) and first_v2_ns == activated_ns
                             and len(v2_observed) == 1
                             and v2_observed[0].get("host_monotonic_ns") == activated_ns)
    put("observer_activation_matches_first_v2_control", activation_reconciled,
        {"summary_activated_ns": activated_ns,
         "first_valid_v2_control_ns": first_v2_ns,
         "v2_observed_count": len(v2_observed),
         "v2_observed_ns": [row.get("host_monotonic_ns") for row in v2_observed]})

    telemetry = [row for row in observations if row.get("kind") == "telemetry_valid"
                 and isinstance(row.get("host_monotonic_ns"), int)]
    controls = [row for row in observations if row.get("kind") == "control_valid"
                and isinstance(row.get("host_monotonic_ns"), int)]
    old_boot_number = int(old_boot, 16) if old_boot is not None else None
    new_boot_number = int(new_boot, 16) if new_boot is not None else None
    v1_27 = [row for row in controls if old_boot_number is not None
             and row.get("boot_id") == old_boot_number
             and row.get("module_version") == 1
             and row.get("input_temperature_mC") == 27000
             and row.get("requested_gpio") == 1 and row.get("observed_gpio") == 1
             and (not isinstance(accepted_ns, int) or row["host_monotonic_ns"] < accepted_ns)]
    v2_27 = [row for row in controls if new_boot_number is not None
             and row.get("boot_id") == new_boot_number
             and row.get("module_version") == 2
             and row.get("input_temperature_mC") == 27000
             and row.get("requested_gpio") == 0 and row.get("observed_gpio") == 0
             and isinstance(activated_ns, int) and row["host_monotonic_ns"] >= activated_ns]
    no_version_regression = not any(new_boot_number is not None
                                    and row.get("boot_id") == new_boot_number
                                    and row.get("module_version") == 1
                                    and isinstance(activated_ns, int)
                                    and row["host_monotonic_ns"] >= activated_ns
                                    for row in controls)
    put("functional_v1_to_v2_is_discriminating", bool(v1_27 and v2_27)
        and summary.get("initial_version") == 1
        and summary.get("update_state") == "activated" and no_version_regression,
        {"valid_v1_27c_results_before_acceptance": len(v1_27),
         "valid_v2_27c_results_after_activation": len(v2_27),
         "v1_results_after_first_v2": sum(1 for row in controls
             if new_boot_number is not None and row.get("boot_id") == new_boot_number
             and row.get("module_version") == 1 and isinstance(activated_ns, int)
             and row["host_monotonic_ns"] >= activated_ns)})

    telemetry_old_before = [row for row in telemetry
                            if normalize_boot_id(row.get("boot_id")) == old_boot
                            and (not isinstance(accepted_ns, int)
                                 or row["host_monotonic_ns"] < accepted_ns)]
    telemetry_new_after = [row for row in telemetry
                           if normalize_boot_id(row.get("boot_id")) == new_boot
                           and isinstance(activated_ns, int)
                           and row["host_monotonic_ns"] >= activated_ns]
    put("telemetry_observed_before_and_after_reboot",
        bool(telemetry_old_before and telemetry_new_after),
        {"valid_old_boot_before_acceptance": len(telemetry_old_before),
         "valid_new_boot_after_activation": len(telemetry_new_after)})

    bad_results = [row for row in observations if row.get("kind") == "incorrect_control"]
    failed_events = [event for event in matching
                     if event.get("status") in ("rejected", "error")
                     or event.get("failure") not in (None, "none")
                     or any(word in str(event.get("phase", "")).lower()
                            for word in ("fail", "rollback", "rejected"))]
    unattributed_failures = [event for event in update_events if event not in matching
                             and (event.get("status") in ("rejected", "error")
                                  or event.get("failure") not in (None, "none")
                                  or any(word in str(event.get("phase", "")).lower()
                                         for word in ("fail", "rollback", "rejected")))]
    put("no_failure_or_rollback", not bad_results and not failed_events and not unattributed_failures
        and not any(row.get("kind") in ("update_rejected", "acceptance_timeout")
                    for row in observations),
        {"invalid_control_result_count": len(bad_results),
         "failure_or_rollback_event_count": len(failed_events),
         "unattributed_failure_event_count": len(unattributed_failures),
         "rejected_or_timed_out_acceptances": sum(1 for row in observations
             if row.get("kind") in ("update_rejected", "acceptance_timeout"))})
    recovery_required = bool(bad_results or failed_events or unattributed_failures
                             or any(row.get("kind") in ("update_rejected", "acceptance_timeout")
                                    for row in observations))

    old_controls = [row for row in controls if old_boot_number is not None
                    and row.get("boot_id") == old_boot_number
                    and (first_v2_ns is None or row["host_monotonic_ns"] < first_v2_ns)]
    new_controls = [row for row in controls if new_boot_number is not None
                    and row.get("boot_id") == new_boot_number
                    and isinstance(activated_ns, int) and row["host_monotonic_ns"] >= activated_ns]
    last_old_control_ns = max((row["host_monotonic_ns"] for row in old_controls), default=None)
    control_gap_ns = (first_v2_ns - last_old_control_ns
                      if first_v2_ns is not None and last_old_control_ns is not None else None)
    period_ns = _number(summary.get("period_ns"))
    unavailable_ns = max(0, control_gap_ns - period_ns) if control_gap_ns is not None \
        and period_ns is not None else None
    telemetry_old_times = [row["host_monotonic_ns"] for row in telemetry
                           if normalize_boot_id(row.get("boot_id")) == old_boot]
    telemetry_new_times = [row["host_monotonic_ns"] for row in telemetry
                           if normalize_boot_id(row.get("boot_id")) == new_boot]
    telemetry_reboot_gap = (min(telemetry_new_times) - max(telemetry_old_times)
                            if telemetry_new_times and telemetry_old_times else None)

    durations = {}
    for phases_on_boot in (PREBOOT_PHASES, POSTBOOT_PHASES):
        for left, right in zip(phases_on_boot, phases_on_boot[1:]):
            a, b = phases.get(left), phases.get(right)
            if a is not None and b is not None:
                ta, tb = _number(a.get("elapsed_us")), _number(b.get("elapsed_us"))
                if ta is not None and tb is not None:
                    durations[f"{left}_to_{right}_us"] = tb - ta

    internal_free = [row["internal_free_bytes"] for row in heap_snapshots
                     if row["internal_free_bytes"] is not None]
    psram_free = [row["psram_free_bytes"] for row in heap_snapshots
                  if row["psram_free_bytes"] is not None]
    return {
        "schema": "wasmhome-native-update-analysis-v1",
        "scenario": "native",
        "expected_sequence": expected_sequence,
        "result": "passed" if all(check["passed"] for check in checks.values())
                  else "incomplete_or_failed",
        "checks": checks,
        "attempt_id": summary.get("attempt_id"),
        "old_boot_id": old_boot,
        "new_boot_id": new_boot,
        "payload": {"bytes_expected": payload_expected, "bytes_received": payload_received,
                    "sha256": payload_sha if payload_complete else None,
                    "running_sha256": running_sha},
        "phase_durations_us_within_boot_only": durations,
        "service": {
            "trigger_to_first_v2_host_ns": (first_v2_ns - valid_trigger_rows[0].get("host_monotonic_ns")
                if valid_trigger_rows and isinstance(valid_trigger_rows[0].get("host_monotonic_ns"), int)
                and first_v2_ns is not None else None),
            "last_old_control_to_first_v2_host_gap_ns": control_gap_ns,
            "estimated_control_unavailability_ns": unavailable_ns,
            "telemetry_old_to_new_boot_gap_host_ns": telemetry_reboot_gap,
            "lost_controls": summary.get("lost_controls"),
            "control_max_gap_ns": summary.get("control_max_gap_ns"),
            "telemetry_max_gap_ns": summary.get("telemetry_max_gap_ns"),
            "unresolved_sequence_gaps": summary.get("unresolved_sequence_gaps", {}),
            "observer_counters": summary.get("counters", {}),
        },
        "memory": {
            "heap_snapshots": heap_snapshots,
            "minimum_internal_free_bytes": min(internal_free) if internal_free else None,
            "minimum_psram_free_bytes": min(psram_free) if psram_free else None,
        },
        "recovery": {
            "recovery_required": recovery_required,
            "recovery_observed": bool(failed_events or unattributed_failures),
            "rollback_or_failure_events": [
                {"phase": event.get("phase"), "status": event.get("status"),
                 "failure": event.get("failure"), "source_line": event.get("_source_line")}
                for event in failed_events + unattributed_failures],
            "invalid_control_results": len(bad_results),
            "interpretation": ("recovery_required_after_observed_failure" if recovery_required
                               else "no_failure_observed_recovery_not_exercised"),
        },
        "provenance": {
            "update_events_sha256": sha256_bytes(update_raw),
            "observations_jsonl_sha256": sha256_bytes(observations_raw),
            "observer_summary_sha256": sha256_bytes(summary_raw),
            "update_event_count": len(matching),
        },
        "limitations": [
            "Device elapsed_us values are compared only within each boot; reboot-spanning durations use the observer host monotonic clock.",
            "The report reconciles captured records and does not establish physical provenance by itself.",
            "Control loss and sequence gaps are reported as measurements; they are not hidden by the successful OTA result.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--firmware-log", required=True, type=Path)
    parser.add_argument("--observations", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--scenario", type=int, default=1,
                        help="expected UDP scenario number (default: native=1)")
    parser.add_argument("--sequence", type=int, default=2)
    parser.add_argument("--output", required=True, type=Path,
                        help="new private report path; existing files are refused")
    args = parser.parse_args()
    if args.scenario != 1:
        parser.error("native analysis requires --scenario 1")
    if args.sequence <= 0:
        parser.error("--sequence must be positive")
    try:
        report = analyze(args.firmware_log.read_bytes(), args.observations.read_bytes(),
                         args.summary.read_bytes(), args.scenario, args.sequence)
        write_exclusive(args.output, report)
    except (OSError, ValueError) as error:
        parser.exit(2, f"{error}\n")
    print(json.dumps({"result": report["result"], "output": str(args.output),
                      "checks": {name: check["passed"] for name, check in report["checks"].items()}},
                     indent=2))
    return 0 if report["result"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
