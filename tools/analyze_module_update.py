#!/usr/bin/env python3
"""Reconcile external UDP observations with signed modular-update UART events.

The tool reads existing files only. It does not open network or serial devices.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import uuid

UPDATE_PREFIX = "WASMHOME_UPDATE,"
SUCCESS_PHASES = (
    "accepted",
    "manifest_download_started",
    "manifest_downloaded",
    "signature_verified",
    "payload_download_started",
    "payload_verified",
    "staged",
    "activating",
    "candidate_loaded",
    "candidate_instantiated",
    "candidate_health_check",
    "floor_committed",
    "control_activated",
    "previous_control_released",
)
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
    "candidate_loaded": {"ok"},
    "candidate_instantiated": {"ok"},
    "candidate_health_check": {"ok"},
    "floor_committed": {"ok"},
    "control_activated": {"ok"},
    "previous_control_released": {"ok"},
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize_boot_id(value) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        if value <= 0 or value >= 2**64:
            return None
        return f"{value:016x}"
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().lower()
    if text.startswith("0x"):
        text = text[2:]
        try:
            number = int(text, 16)
        except ValueError:
            return None
    elif re.fullmatch(r"[0-9a-f]{16}", text):
        number = int(text, 16)
    elif text.isdecimal():
        number = int(text, 10)
    else:
        return None
    return f"{number:016x}" if 0 < number < 2**64 else None


def normalize_version(value) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value if value in (1, 2) else None
    if isinstance(value, str):
        text = value.strip().lower()
        if text.startswith("v"):
            text = text[1:]
        if text in ("1", "2"):
            return int(text)
    return None


def normalize_component(value) -> int | None:
    if value == "control":
        return 1
    if isinstance(value, int) and not isinstance(value, bool) and value in (1,):
        return value
    if isinstance(value, str) and value.strip() == "1":
        return 1
    return None


def parse_update_events(raw: bytes) -> list[dict]:
    events = []
    for line_number, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
        marker = line.find(UPDATE_PREFIX)
        if marker < 0:
            continue
        encoded = line[marker + len(UPDATE_PREFIX):].strip()
        try:
            event = json.loads(encoded)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid WASMHOME_UPDATE JSON on line {line_number}: {error}") from error
        if not isinstance(event, dict):
            raise ValueError(f"WASMHOME_UPDATE line {line_number} is not a JSON object")
        event["_source_line"] = line_number
        events.append(event)
    return events


def parse_observations(raw: bytes) -> list[dict]:
    rows = []
    for line_number, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid observer JSONL on line {line_number}: {error}") from error
        if not isinstance(row, dict):
            raise ValueError(f"observer line {line_number} is not a JSON object")
        row["_source_line"] = line_number
        rows.append(row)
    return rows


def _event_identity(event: dict) -> tuple:
    try:
        attempt = str(uuid.UUID(str(event.get("attempt_id"))))
    except (ValueError, TypeError, AttributeError):
        attempt = None
    return (
        attempt,
        normalize_boot_id(event.get("boot_id")),
        event.get("scenario"),
        normalize_component(event.get("component")),
        normalize_version(event.get("release_version")),
        event.get("target_version"),
        event.get("sequence"),
    )


def _phase_event(events: list[dict], phase: str) -> dict | None:
    matches = [event for event in events if event.get("phase") == phase]
    return matches[0] if len(matches) == 1 else None


def _number(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    return None


def _telemetry_identity(event: dict) -> tuple | None:
    generation = _number(event.get("telemetry_generation"))
    digest = event.get("telemetry_sha256")
    if generation is None or not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
        return None
    return generation, digest


def _control_identity(event: dict) -> tuple | None:
    generation = _number(event.get("control_generation"))
    raw_version = event.get("control_module_version")
    version = raw_version if isinstance(raw_version, int) and not isinstance(raw_version, bool) \
        and raw_version in (1, 2) else None
    digest = event.get("control_sha256")
    if generation is None or version is None or not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
        return None
    return generation, version, digest


def _heap(event: dict) -> dict:
    raw = event.get("heap")
    if not isinstance(raw, dict):
        return {}
    return {
        name: _number(raw.get(name))
        for name in ("internal_free_bytes", "internal_largest_free_block_bytes",
                     "psram_free_bytes", "psram_largest_free_block_bytes")
    }


def _median(values: list[int]) -> int | None:
    ordered = sorted(values)
    if not ordered:
        return None
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) // 2


def _checks_for_capture(update_events: list[dict], observations: list[dict],
                        observer_summary: dict, expected_scenario: int,
                        expected_sequence: int) -> dict:
    checks: dict[str, dict] = {}

    def put(name, passed, observed):
        checks[name] = {"passed": bool(passed), "observed": observed}

    expected_attempt = observer_summary.get("attempt_id")
    try:
        expected_attempt = str(uuid.UUID(str(expected_attempt)))
    except (ValueError, TypeError, AttributeError):
        expected_attempt = None
    matching = []
    for event in update_events:
        try:
            attempt_id = str(uuid.UUID(str(event.get("attempt_id"))))
        except (ValueError, TypeError, AttributeError):
            attempt_id = None
        if attempt_id == expected_attempt and expected_attempt is not None:
            matching.append(event)

    put("attempt_id_correlated", expected_attempt is not None and bool(matching),
        {"observer_attempt_id": expected_attempt, "update_event_count": len(matching)})
    if not matching:
        matching = []

    event_boots = [normalize_boot_id(event.get("boot_id")) for event in matching]
    unique_boots = sorted(set(event_boots) - {None})
    observed_boots = [normalize_boot_id(value) for value in observer_summary.get("boot_ids", [])]
    observed_boots = [value for value in observed_boots if value is not None]
    same_boot = (bool(matching) and all(value is not None for value in event_boots)
                 and len(unique_boots) == 1 and len(observed_boots) == 1
                 and unique_boots[0] == observed_boots[0])
    put("same_boot", same_boot,
        {"update_boot_ids": unique_boots, "invalid_update_boot_id_count": event_boots.count(None),
         "observer_boot_ids": observed_boots})
    elapsed_scopes = sorted({event.get("elapsed_scope") for event in matching})
    put("single_boot_elapsed_clock", bool(matching) and elapsed_scopes == ["boot"],
        {"elapsed_scopes": elapsed_scopes})

    event_metadata = all(
        type(event.get("scenario")) is int and event.get("scenario") == expected_scenario
        and type(event.get("component")) is int and event.get("component") == 1
        and event.get("release_version") == "v2"
        and type(event.get("target_version")) is int and event.get("target_version") == 2
        and type(event.get("sequence")) is int and event.get("sequence") == expected_sequence
        for event in matching
    )
    put("scenario_component_release_sequence", bool(matching) and event_metadata,
        {"expected_scenario": expected_scenario, "expected_component": 1,
         "expected_release_version": "v2", "expected_sequence": expected_sequence,
         "events_match": event_metadata})

    sessions = [row for row in observations if row.get("kind") == "session_start"]
    trigger_intents = []
    for row in observations:
        packet = row.get("packet")
        if (row.get("kind") == "send_intent" and isinstance(packet, dict)
                and packet.get("message_type") == "UPDATE_TRIGGER"):
            trigger_intents.append(packet)
    configured = (len(sessions) == 1
                  and sessions[0].get("initial_version") == 1
                  and sessions[0].get("scenario") == expected_scenario
                  and sessions[0].get("target_sequence") == expected_sequence)
    valid_trigger_intents = [packet for packet in trigger_intents
                             if packet.get("attempt_id") == expected_attempt
                             and isinstance(packet.get("body"), dict)
                             and packet["body"].get("component") == 1
                             and packet["body"].get("scenario") == expected_scenario
                             and packet["body"].get("target_version") == 2
                             and packet["body"].get("target_sequence") == expected_sequence]
    put("observer_trigger_matches_target", configured and len(trigger_intents) == 1
        and len(valid_trigger_intents) == 1,
        {"session_count": len(sessions), "trigger_count": len(trigger_intents),
         "matching_trigger_count": len(valid_trigger_intents),
         "initial_version": sessions[0].get("initial_version") if len(sessions) == 1 else None,
         "scenario": sessions[0].get("scenario") if len(sessions) == 1 else None,
         "target_sequence": sessions[0].get("target_sequence") if len(sessions) == 1 else None})

    phases = {phase: _phase_event(matching, phase) for phase in SUCCESS_PHASES}
    missing = [phase for phase, event in phases.items() if event is None]
    bad_status = [phase for phase, event in phases.items()
                  if event is not None and event.get("status") not in PHASE_STATUSES[phase]]
    bad_failure = [event.get("phase") for event in matching
                   if event.get("failure") != "none"]
    event_name_mismatch = [event.get("phase") for event in matching
                           if event.get("event") != event.get("phase")]
    put("required_stages_succeeded", not missing and not bad_status
        and not bad_failure and not event_name_mismatch,
        {"missing_or_duplicate": missing, "unexpected_status": bad_status,
         "non_none_failure": bad_failure, "event_phase_mismatch": event_name_mismatch})

    invalid_statuses = [event.get("phase") for event in matching
                        if event.get("status") not in ("in_progress", "ok", "rejected", "error")]
    invalid_optional = [event.get("phase") for event in matching
                        if event.get("phase") in OPTIONAL_PHASES
                        and event.get("status") not in PHASE_STATUSES[event.get("phase")]]
    put("event_statuses_valid", not invalid_statuses and not invalid_optional,
        {"unknown_status_events": invalid_statuses,
         "invalid_optional_phase_statuses": invalid_optional})

    phase_rank = {phase: index for index, phase in enumerate(SUCCESS_PHASES)}
    event_rows = [(event, (phase_rank[event["phase"]]
                           if event.get("phase") in phase_rank
                           else phase_rank["payload_download_started"] + 0.5))
                  for event in matching
                  if event.get("phase") in phase_rank or event.get("phase") in OPTIONAL_PHASES]
    event_rows.sort(key=lambda item: item[0]["_source_line"])
    malformed_elapsed = [event.get("phase") for event, _ in event_rows
                         if _number(event.get("elapsed_us")) is None]
    ordered_elapsed = [_number(event.get("elapsed_us")) for event, _ in event_rows]
    monotonic = (not malformed_elapsed and all(
        later >= earlier for earlier, later in zip(ordered_elapsed, ordered_elapsed[1:])))
    rank_ordered = all(later[1] >= earlier[1]
                       for earlier, later in zip(event_rows, event_rows[1:]))
    required_ordered = all(
        phases[left]["_source_line"] < phases[right]["_source_line"]
        for left, right in zip(SUCCESS_PHASES, SUCCESS_PHASES[1:])
        if phases[left] is not None and phases[right] is not None)
    put("device_timeline_monotonic", monotonic and rank_ordered and required_ordered
        and not missing and elapsed_scopes == ["boot"],
        {"missing_elapsed_us": malformed_elapsed,
         "phase_elapsed_us": {event.get("phase"): _number(event.get("elapsed_us"))
                              for event, _ in event_rows},
         "phase_lines_in_order": required_ordered,
         "all_recognized_phases_in_order": rank_ordered})

    manifest_downloaded = phases.get("manifest_downloaded") or {}
    signature_verified = phases.get("signature_verified") or {}
    manifest_digest = manifest_downloaded.get("manifest_sha256")
    signed_digest = signature_verified.get("manifest_sha256")
    manifest_valid = (isinstance(manifest_digest, str) and SHA256_RE.fullmatch(manifest_digest)
                      and manifest_digest == signed_digest)
    put("manifest_signature_bound_to_download", bool(manifest_valid),
        {"downloaded_manifest_sha256": manifest_digest
         if isinstance(manifest_digest, str) and SHA256_RE.fullmatch(manifest_digest) else None,
         "signature_verified_manifest_sha256": signed_digest
         if isinstance(signed_digest, str) and SHA256_RE.fullmatch(signed_digest) else None})

    floor = phases.get("floor_committed") or {}
    floor_sequence = _number(floor.get("ledger_readback_sequence"))
    put("rollback_floor_readback_matches_target", floor_sequence == expected_sequence,
        {"ledger_readback_sequence": floor_sequence,
         "expected_sequence": expected_sequence})

    pre_telemetry = _telemetry_identity(phases.get("accepted") or {})
    activated_telemetry = _telemetry_identity(phases.get("control_activated") or {})
    released_telemetry = _telemetry_identity(phases.get("previous_control_released") or {})
    telemetry_stable = (pre_telemetry is not None and
                        pre_telemetry == activated_telemetry == released_telemetry)
    put("telemetry_instance_unchanged", telemetry_stable,
        {"before": pre_telemetry, "at_activation": activated_telemetry,
         "after_old_control_release": released_telemetry})
    intermediate_telemetry = {
        phase: _telemetry_identity(event)
        for phase, event in phases.items()
        if event is not None and _telemetry_identity(event) is not None
    }
    intermediate_stable = (pre_telemetry is not None
                           and all(identity == pre_telemetry
                                   for identity in intermediate_telemetry.values()))
    put("reported_intermediate_telemetry_snapshots_stable", intermediate_stable,
        {"available_phase_snapshots": intermediate_telemetry})

    control_before = _control_identity(phases.get("accepted") or {})
    control_after = _control_identity(phases.get("control_activated") or {})
    control_swapped = (control_before is not None and control_after is not None
                       and control_before[1] == 1 and control_after[1] == 2
                       and control_before[0] != control_after[0]
                       and control_before[2] != control_after[2])
    put("control_v1_to_v2_generation_changed", control_swapped,
        {"before": control_before, "after": control_after})

    verified = phases.get("payload_verified")
    expected_bytes = _number(verified.get("payload_bytes_expected")) if verified else None
    received_bytes = _number(verified.get("payload_bytes_received")) if verified else None
    digest = verified.get("payload_sha256") if verified else None
    payload_ok = (expected_bytes is not None and expected_bytes > 0
                  and received_bytes == expected_bytes
                  and isinstance(digest, str) and SHA256_RE.fullmatch(digest) is not None)
    put("payload_complete_and_hashed", payload_ok,
        {"expected_bytes": expected_bytes, "received_bytes": received_bytes,
         "sha256": digest if isinstance(digest, str) and SHA256_RE.fullmatch(digest) else None})

    activated_control = _control_identity(phases.get("control_activated") or {})
    released_control = _control_identity(phases.get("previous_control_released") or {})
    activated_size = _number((phases.get("control_activated") or {}).get("control_artifact_bytes"))
    released_size = _number((phases.get("previous_control_released") or {}).get("control_artifact_bytes"))
    active_hash_matches = (payload_ok and activated_control is not None
                           and released_control is not None
                           and digest == activated_control[2] == released_control[2])
    active_size_matches = (all(value is None for value in (activated_size, released_size))
                           or (activated_size == expected_bytes == released_size))
    put("verified_payload_is_active_module", active_hash_matches and active_size_matches,
        {"verified_payload_sha256": digest if payload_ok else None,
         "activated_control_sha256": activated_control[2] if activated_control else None,
         "released_control_sha256": released_control[2] if released_control else None,
         "payload_bytes_expected": expected_bytes,
         "activated_control_artifact_bytes": activated_size,
         "released_control_artifact_bytes": released_size,
         "artifact_size_comparison_available": activated_size is not None or released_size is not None})

    memory_phases = ("accepted", "candidate_loaded", "candidate_instantiated",
                     "candidate_health_check", "floor_committed", "control_activated",
                     "previous_control_released")
    missing_heap = []
    malformed_heap = []
    for phase in memory_phases:
        event = phases.get(phase)
        heap = _heap(event or {})
        if event is None or heap.get("internal_free_bytes") is None or heap.get("internal_largest_free_block_bytes") is None:
            missing_heap.append(phase)
            continue
        if heap["internal_largest_free_block_bytes"] > heap["internal_free_bytes"]:
            malformed_heap.append(phase)
        if (heap.get("psram_free_bytes") is not None
                and heap.get("psram_largest_free_block_bytes") is not None
                and heap["psram_largest_free_block_bytes"] > heap["psram_free_bytes"]):
            malformed_heap.append(phase)
    put("memory_snapshots_recorded", not missing_heap and not malformed_heap,
        {"required_phases": list(memory_phases), "missing_or_unmeasured": missing_heap,
         "inconsistent_heap_values": malformed_heap})

    update_accepted = [row for row in observations if row.get("kind") == "update_accepted"]
    v2_observed = [row for row in observations if row.get("kind") == "v2_observed"]
    control_valid = [row for row in observations if row.get("kind") == "control_valid"]
    telemetry_valid = [row for row in observations if row.get("kind") == "telemetry_valid"]
    controls_for_boot = [row for row in control_valid
                         if same_boot and normalize_boot_id(row.get("boot_id")) == observed_boots[0]]
    accepted_ns = observer_summary.get("accepted_ns")
    activated_ns = observer_summary.get("activated_ns")
    initial_version = observer_summary.get("initial_version")
    # Older observer summaries did not save initial_version. The event stream is
    # authoritative for the baseline version, while the summary must prove activation.
    baseline_27 = [row for row in controls_for_boot
                   if row.get("module_version") == 1
                   and row.get("input_temperature_mC") == 27000
                   and row.get("requested_gpio") == 1
                   and row.get("observed_gpio") == 1
                   and isinstance(row.get("host_monotonic_ns"), int)
                   and (accepted_ns is None or row["host_monotonic_ns"] < accepted_ns)]
    updated_27 = [row for row in controls_for_boot
                  if row.get("module_version") == 2
                  and row.get("input_temperature_mC") == 27000
                  and row.get("requested_gpio") == 0
                  and row.get("observed_gpio") == 0
                  and isinstance(row.get("host_monotonic_ns"), int)
                  and (activated_ns is None or row["host_monotonic_ns"] >= activated_ns)]
    active_observer = (observer_summary.get("update_state") == "activated"
                       and isinstance(accepted_ns, int) and isinstance(activated_ns, int)
                       and len(update_accepted) == 1 and len(v2_observed) == 1
                       and initial_version == 1 and configured
                       and len(valid_trigger_intents) == 1)
    put("functional_control_v1_to_v2", active_observer and bool(baseline_27) and bool(updated_27),
        {"observer_update_state": observer_summary.get("update_state"),
         "accepted_event_count": len(update_accepted), "v2_observed_event_count": len(v2_observed),
         "valid_v1_27c_results": len(baseline_27), "valid_v2_27c_results": len(updated_27)})

    update_start_ns = accepted_ns
    update_end_ns = activated_ns
    telemetry_for_boot = [row for row in telemetry_valid
                          if isinstance(row.get("host_monotonic_ns"), int)
                          and same_boot and normalize_boot_id(row.get("boot_id")) == observed_boots[0]]
    baseline_telemetry = sorted(row["host_monotonic_ns"] for row in telemetry_for_boot
                                if isinstance(accepted_ns, int) and row["host_monotonic_ns"] < accepted_ns)
    baseline_intervals = [right - left for left, right in zip(baseline_telemetry,
                                                              baseline_telemetry[1:]) if right > left]
    telemetry_cadence_ns = _median(baseline_intervals)
    during_telemetry = [row for row in telemetry_for_boot
                        if isinstance(update_start_ns, int) and isinstance(update_end_ns, int)
                        and update_start_ns <= row["host_monotonic_ns"] <= update_end_ns]
    during_duration_ns = (update_end_ns - update_start_ns
                          if isinstance(update_start_ns, int) and isinstance(update_end_ns, int)
                          else None)
    expected_minimum_during = (
        max(0, during_duration_ns // telemetry_cadence_ns - 1)
        if during_duration_ns is not None and during_duration_ns >= 0
        and telemetry_cadence_ns is not None and telemetry_cadence_ns > 0 else None)
    coverage_points = ([update_start_ns] + sorted(row["host_monotonic_ns"] for row in during_telemetry)
                       + [update_end_ns]
                       if isinstance(update_start_ns, int) and isinstance(update_end_ns, int) else [])
    update_telemetry_gaps = [right - left for left, right in zip(coverage_points,
                                                                 coverage_points[1:])]
    max_update_telemetry_gap = max(update_telemetry_gaps, default=None)
    gap_coverage = (during_duration_ns is not None and telemetry_cadence_ns is not None
                    and telemetry_cadence_ns > 0
                    and (during_duration_ns < telemetry_cadence_ns
                         or (max_update_telemetry_gap is not None
                             and max_update_telemetry_gap <= 2 * telemetry_cadence_ns)))
    telemetry_coverage = (expected_minimum_during is not None
                          and len(during_telemetry) >= expected_minimum_during
                          and gap_coverage)
    put("telemetry_covered_update_interval", telemetry_coverage,
        {"interval_start_host_ns": update_start_ns, "interval_end_first_v2_host_ns": update_end_ns,
         "interval_duration_ns": during_duration_ns,
         "baseline_median_cadence_ns": telemetry_cadence_ns,
         "valid_telemetry_during_interval": len(during_telemetry),
         "minimum_expected_from_cadence": expected_minimum_during,
         "max_inter_sample_or_edge_gap_ns": max_update_telemetry_gap,
         "maximum_gap_allowed_ns": (2 * telemetry_cadence_ns
                                     if telemetry_cadence_ns is not None else None)})

    if same_boot and isinstance(accepted_ns, int) and isinstance(activated_ns, int):
        before_telemetry = [row for row in telemetry_valid
                            if normalize_boot_id(row.get("boot_id")) == observed_boots[0]
                            and isinstance(row.get("host_monotonic_ns"), int)
                            and row["host_monotonic_ns"] < accepted_ns]
        after_telemetry = [row for row in telemetry_valid
                           if normalize_boot_id(row.get("boot_id")) == observed_boots[0]
                           and isinstance(row.get("host_monotonic_ns"), int)
                           and row["host_monotonic_ns"] >= activated_ns]
    else:
        before_telemetry, after_telemetry = [], []
    put("telemetry_observed_before_and_after", bool(before_telemetry and after_telemetry),
        {"valid_before_acceptance": len(before_telemetry),
         "valid_after_v2_activation": len(after_telemetry)})

    pre_control = [row for row in controls_for_boot if row.get("module_version") == 1
                   and isinstance(row.get("host_monotonic_ns"), int)
                   and (accepted_ns is None or row["host_monotonic_ns"] < accepted_ns)]
    all_pre_v2_control = [row for row in controls_for_boot
                          if isinstance(row.get("host_monotonic_ns"), int)
                          and (activated_ns is None or row["host_monotonic_ns"] < activated_ns)]
    post_control = [row for row in controls_for_boot if row.get("module_version") == 2
                    and isinstance(row.get("host_monotonic_ns"), int)
                    and (activated_ns is None or row["host_monotonic_ns"] >= activated_ns)]
    switch_gap = None
    service_gap = None
    unavailability = None
    if pre_control and post_control:
        switch_gap = min(row["host_monotonic_ns"] for row in post_control) - max(
            row["host_monotonic_ns"] for row in pre_control)
    if all_pre_v2_control and post_control:
        service_gap = min(row["host_monotonic_ns"] for row in post_control) - max(
            row["host_monotonic_ns"] for row in all_pre_v2_control)
        period_ns = observer_summary.get("period_ns")
        if isinstance(period_ns, int) and period_ns > 0:
            unavailability = max(0, service_gap - period_ns)
    put("control_service_recovers", bool(post_control) and service_gap is not None and service_gap >= 0,
        {"last_v1_to_first_v2_gap_ns": switch_gap,
         "last_valid_control_to_first_v2_gap_ns": service_gap,
         "estimated_unavailability_ns": unavailability,
         "post_activation_v2_results": len(post_control)})

    failed_update_events = [event for event in matching
                            if event.get("status") in ("rejected", "error")
                            or any(marker in str(event.get("phase", "")).lower()
                                   for marker in ("fail", "rollback", "rejected"))]
    regressed_results = [row for row in controls_for_boot
                         if row.get("module_version") == 1
                         and isinstance(activated_ns, int)
                         and isinstance(row.get("host_monotonic_ns"), int)
                         and row["host_monotonic_ns"] >= activated_ns]
    regressed_results += [row for row in observations
                          if row.get("kind") in ("incorrect_control", "late_control")
                          and row.get("module_version") == 1
                          and same_boot
                          and normalize_boot_id(row.get("boot_id")) == observed_boots[0]
                          and isinstance(activated_ns, int)
                          and isinstance(row.get("host_monotonic_ns"), int)
                          and row["host_monotonic_ns"] >= activated_ns]
    put("no_failure_rollback_or_version_regression",
        not failed_update_events and not regressed_results,
        {"failure_or_rollback_event_count": len(failed_update_events),
         "v1_results_after_v2_activation": len(regressed_results)})

    return checks, matching, phases, {
        "accepted_ns": accepted_ns,
        "activated_ns": activated_ns,
        "update_total_ns_from_trigger": observer_summary.get("update_total_ns"),
        "control_v1_to_first_v2_gap_ns": switch_gap,
        "last_valid_control_to_first_v2_gap_ns": service_gap,
        "control_unavailability_estimate_ns": unavailability,
        "telemetry_baseline_median_cadence_ns": telemetry_cadence_ns,
        "valid_telemetry_during_update_interval": len(during_telemetry),
        "expected_minimum_telemetry_during_interval": expected_minimum_during,
        "maximum_inter_sample_or_edge_gap_during_update_ns": max_update_telemetry_gap,
        "valid_telemetry_before_acceptance": len(before_telemetry),
        "valid_telemetry_after_activation": len(after_telemetry),
        "valid_control_v1_before_acceptance": len(pre_control),
        "valid_control_v2_after_activation": len(post_control),
        "telemetry_max_gap_ns": observer_summary.get("telemetry_max_gap_ns"),
        "control_max_gap_ns": observer_summary.get("control_max_gap_ns"),
        "lost_controls": observer_summary.get("lost_controls"),
        "unresolved_sequence_gaps": observer_summary.get("unresolved_sequence_gaps", {}),
        "observer_counters": observer_summary.get("counters", {}),
    }


def analyze(update_raw: bytes, observations_raw: bytes, summary_raw: bytes,
            expected_scenario: int, expected_sequence: int = 2) -> dict:
    update_events = parse_update_events(update_raw)
    observations = parse_observations(observations_raw)
    try:
        observer_summary = json.loads(summary_raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid observer summary JSON: {error}") from error
    if not isinstance(observer_summary, dict):
        raise ValueError("observer summary must be a JSON object")

    checks, matching, phases, service_metrics = _checks_for_capture(
        update_events, observations, observer_summary, expected_scenario, expected_sequence)
    phase_times = {phase: _number(event.get("elapsed_us"))
                   for phase, event in phases.items() if event is not None}
    phase_durations = {}
    for (left, left_time), (right, right_time) in zip(
            [(phase, phase_times.get(phase)) for phase in SUCCESS_PHASES],
            [(phase, phase_times.get(phase)) for phase in SUCCESS_PHASES][1:]):
        if left_time is not None and right_time is not None:
            phase_durations[f"{left}_to_{right}_us"] = right_time - left_time

    timeline = []
    previous = None
    for phase in SUCCESS_PHASES:
        event = phases.get(phase)
        if event is None:
            continue
        elapsed = _number(event.get("elapsed_us"))
        timeline.append({"phase": phase, "status": event.get("status"),
                         "elapsed_us": elapsed,
                         "duration_from_previous_us": (None if elapsed is None or previous is None
                                                        else elapsed - previous)})
        if elapsed is not None:
            previous = elapsed

    payload_event = phases.get("payload_verified")
    payload = None if payload_event is None else {
        "bytes_expected": _number(payload_event.get("payload_bytes_expected")),
        "bytes_received": _number(payload_event.get("payload_bytes_received")),
        "sha256": payload_event.get("payload_sha256"),
    }
    snapshots = []
    for phase in SUCCESS_PHASES:
        event = phases.get(phase)
        if event is None:
            continue
        values = _heap(event)
        if values:
            snapshots.append({"phase": phase, **values})
    internal_free = [item["internal_free_bytes"] for item in snapshots
                     if item["internal_free_bytes"] is not None]
    psram_free = [item["psram_free_bytes"] for item in snapshots
                  if item["psram_free_bytes"] is not None]
    memory = {
        "heap_snapshots": snapshots,
        "minimum_internal_free_bytes": min(internal_free) if internal_free else None,
        "minimum_psram_free_bytes": min(psram_free) if psram_free else None,
        "internal_free_drop_from_acceptance_bytes": (
            snapshots[0]["internal_free_bytes"] - min(internal_free)
            if snapshots and snapshots[0]["internal_free_bytes"] is not None and internal_free else None),
    }
    return {
        "schema": "wasmhome-module-update-analysis-v1",
        "scenario": "interp" if expected_scenario == 2 else "aot",
        "expected_sequence": expected_sequence,
        "result": "passed" if all(item["passed"] for item in checks.values()) else "incomplete_or_failed",
        "checks": checks,
        "attempt_id": observer_summary.get("attempt_id"),
        "update_boot_ids": sorted({normalize_boot_id(event.get("boot_id")) for event in matching}
                                   - {None}),
        "update_timeline": timeline,
        "phase_durations_us": phase_durations,
        "payload": payload,
        "memory": memory,
        "service": service_metrics,
        "provenance": {
            "update_events_sha256": sha256_bytes(update_raw),
            "observations_jsonl_sha256": sha256_bytes(observations_raw),
            "observer_summary_sha256": sha256_bytes(summary_raw),
            "update_event_count": len(matching),
        },
        "limitations": [
            "Device durations use elapsed_us from one boot; host service gaps use the observer monotonic clock.",
            "The two clock domains are reported separately and are never subtracted from each other.",
            "Heap snapshots report observed free bytes; they do not identify every allocator or static-memory cost.",
            "This report validates supplied logs and does not establish physical provenance by itself.",
        ],
    }


def write_exclusive(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--firmware-log", required=True, type=Path,
                        help="captured serial log containing WASMHOME_UPDATE JSON events")
    parser.add_argument("--observations", required=True, type=Path,
                        help="external observer observations.jsonl")
    parser.add_argument("--summary", required=True, type=Path,
                        help="external observer summary.json")
    parser.add_argument("--scenario", choices=("interp", "aot"), required=True)
    parser.add_argument("--sequence", type=int, default=2)
    parser.add_argument("--output", required=True, type=Path,
                        help="new private analysis JSON path; existing files are refused")
    args = parser.parse_args()
    if args.sequence <= 0:
        parser.error("--sequence must be positive")
    try:
        report = analyze(args.firmware_log.read_bytes(), args.observations.read_bytes(),
                         args.summary.read_bytes(), 2 if args.scenario == "interp" else 3,
                         args.sequence)
        write_exclusive(args.output, report)
    except (OSError, ValueError) as error:
        parser.exit(2, f"{error}\n")
    print(json.dumps({"result": report["result"], "output": str(args.output),
                      "checks": {key: value["passed"] for key, value in report["checks"].items()}},
                     indent=2))
    return 0 if report["result"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
