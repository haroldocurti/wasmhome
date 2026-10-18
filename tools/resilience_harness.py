#!/usr/bin/env python3
"""Offline planning and evidence correlation for resilience trials.

This module never opens a serial port or network socket. It defines the exact
serial request shape, the approved 70-failure/two-control matrix, and joins
already captured serial and UDP records without inferring rejection from silence.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid


SERIAL_PREFIX = "WASMHOME_RESILIENCE,"
ABORT_MARKER_PREFIX = "resilience:controlled_abort"
FAULT_CASES = frozenset({
    "ENV-SIG", "ENV-HASH", "ENV-OLD", "WASM-ZERO", "WASM-OOB",
    "WASM-DIV0", "NAT-ABORT",
})
SCENARIOS = ("native", "interp", "aot")
SERIAL_SCENARIO_ALIASES = {
    "native": "native",
    "interp": "interp",
    "module-interp": "interp",
    "aot": "aot",
    "module-aot": "aot",
}
ENV_CASES = ("ENV-SIG", "ENV-HASH", "ENV-OLD")
WASM_MODES = ("interp", "aot")
EXPECTED_REJECTIONS = {
    "ENV-SIG": ("SIGNATURE_INVALID", "envelope_signature"),
    "ENV-HASH": ("HASH_MISMATCH", "payload_hash"),
    "ENV-OLD": ("SEQUENCE_NOT_NEWER", "envelope_sequence"),
}
EXPECTED_TRAPS = {
    "WASM-OOB": "out of bounds memory access",
    "WASM-DIV0": "integer divide by zero",
}
PLAN_NAMESPACE = uuid.UUID("1c806daf-7861-45d3-90e4-44c22d7084fc")


class ResilienceFormatError(ValueError):
    """Input does not satisfy the closed resilience protocol contract."""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ResilienceFormatError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _canonical_uuid(value: object) -> str:
    if not isinstance(value, str):
        raise ResilienceFormatError("attempt_id must be a UUID string")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as error:
        raise ResilienceFormatError("attempt_id must be a UUID string") from error
    canonical = str(parsed)
    if value != canonical:
        raise ResilienceFormatError("attempt_id must use canonical lowercase UUID form")
    return canonical


def _normalized_scenario(value: object) -> str | None:
    """Map firmware profile labels to the host plan's canonical scenarios."""
    if not isinstance(value, str):
        return None
    return SERIAL_SCENARIO_ALIASES.get(value)


def make_fault_request(attempt_id: str | uuid.UUID, case_id: str) -> bytes:
    """Return one canonical command line containing only the three allowed fields."""
    attempt = _canonical_uuid(str(attempt_id))
    if not isinstance(case_id, str) or case_id not in FAULT_CASES:
        raise ResilienceFormatError("case_id is not in the approved resilience matrix")
    return (json.dumps({"event": "fault_request", "attempt_id": attempt,
                        "case_id": case_id}, separators=(",", ":")) + "\n").encode("ascii")


def make_fault_request_record(attempt_id: str | uuid.UUID, case_id: str,
                              scenario: str, host_monotonic_ns: int) -> dict:
    """Build a host log record; call only after the complete line was written."""
    attempt = _canonical_uuid(str(attempt_id))
    if case_id not in FAULT_CASES or scenario not in SCENARIOS:
        raise ResilienceFormatError("request case or scenario is outside the approved matrix")
    if (not isinstance(host_monotonic_ns, int) or isinstance(host_monotonic_ns, bool)
            or host_monotonic_ns < 0):
        raise ResilienceFormatError("host_monotonic_ns must be a non-negative integer")
    raw_request = make_fault_request(attempt, case_id).decode("ascii")
    return {
        "event": "fault_request",
        "attempt_id": attempt,
        "case_id": case_id,
        "scenario": scenario,
        "host_monotonic_ns": host_monotonic_ns,
        "raw_request": raw_request,
        "dispatch_status": "sent",
    }


def parse_fault_request(line: bytes | str) -> dict:
    """Parse a request and reject duplicate, unknown, missing, or mistyped fields."""
    if isinstance(line, bytes):
        try:
            line = line.decode("ascii", errors="strict")
        except UnicodeDecodeError as error:
            raise ResilienceFormatError("fault request must be ASCII JSON") from error
    if not isinstance(line, str) or len(line) > 256:
        raise ResilienceFormatError("fault request line is missing or too long")
    try:
        value = json.loads(line, object_pairs_hook=_unique_object,
                           parse_constant=lambda token: (_ for _ in ()).throw(
                               ResilienceFormatError(f"invalid JSON constant: {token}")))
    except (json.JSONDecodeError, RecursionError) as error:
        raise ResilienceFormatError("fault request is not valid JSON") from error
    if not isinstance(value, dict) or set(value) != {"event", "attempt_id", "case_id"}:
        raise ResilienceFormatError("fault request must contain exactly event, attempt_id, and case_id")
    if value["event"] != "fault_request":
        raise ResilienceFormatError("event must be fault_request")
    attempt = _canonical_uuid(value["attempt_id"])
    case_id = value["case_id"]
    if not isinstance(case_id, str) or case_id not in FAULT_CASES:
        raise ResilienceFormatError("case_id is not in the approved resilience matrix")
    return {"event": "fault_request", "attempt_id": attempt, "case_id": case_id}


def resilience_plan(seed: str) -> list[dict]:
    """Build the approved 70-failure plus two-control plan with unique IDs."""
    if not isinstance(seed, str) or not seed or len(seed) > 128:
        raise ValueError("seed must be a non-empty string of at most 128 characters")
    cells: list[tuple[str, str, int, str]] = []
    for case_id in ENV_CASES:
        cells.extend((scenario, case_id, repetition, "failure")
                     for scenario in SCENARIOS for repetition in range(1, 6))
    cells.extend((scenario, "WASM-ZERO", 1, "control") for scenario in WASM_MODES)
    for case_id in ("WASM-OOB", "WASM-DIV0"):
        cells.extend((scenario, case_id, repetition, "failure")
                     for scenario in WASM_MODES for repetition in range(1, 6))
    cells.extend(("native", "NAT-ABORT", repetition, "failure")
                 for repetition in range(1, 6))

    plan = []
    for index, (scenario, case_id, repetition, purpose) in enumerate(cells, 1):
        identity = f"{seed}:{index}:{scenario}:{case_id}:{repetition}"
        attempt_id = str(uuid.uuid5(PLAN_NAMESPACE, identity))
        plan.append({
            "event": "fault_request",
            "attempt_id": attempt_id,
            "case_id": case_id,
            "scenario": scenario,
            "repetition": repetition,
            "purpose": purpose,
            "plan_index": index,
            "status": "planned",
        })
    if len(plan) != 72 or sum(item["purpose"] == "failure" for item in plan) != 70:
        raise AssertionError("internal resilience matrix count differs from the approved contract")
    return plan


def resilience_preliminary_plan(seed: str) -> list[dict]:
    """Build the closed 16-attempt preliminary matrix required by issue #22.

    This proof samples each ENV failure once in native/interp/AOT order, then
    runs both Wasm controls, one OOB and DIV0 trap per modular profile, and one
    native controlled abort. The separate 72-row issue #23 planner is unchanged.
    """
    if not isinstance(seed, str) or not seed or len(seed) > 128:
        raise ValueError("seed must be a non-empty string of at most 128 characters")
    cells: list[tuple[str, str, int, str]] = []
    for case_id in ENV_CASES:
        cells.extend((scenario, case_id, 1, "failure") for scenario in SCENARIOS)
    cells.extend((scenario, "WASM-ZERO", 1, "control") for scenario in WASM_MODES)
    for case_id in ("WASM-OOB", "WASM-DIV0"):
        cells.extend((scenario, case_id, 1, "failure") for scenario in WASM_MODES)
    cells.append(("native", "NAT-ABORT", 1, "failure"))

    plan = []
    for index, (scenario, case_id, repetition, purpose) in enumerate(cells, 1):
        identity = f"{seed}:{index}:{scenario}:{case_id}:{repetition}"
        attempt_id = str(uuid.uuid5(PLAN_NAMESPACE, identity))
        plan.append({
            "event": "fault_request",
            "attempt_id": attempt_id,
            "case_id": case_id,
            "scenario": scenario,
            "repetition": repetition,
            "purpose": purpose,
            "plan_index": index,
            "status": "planned",
        })
    if len(plan) != 16 or sum(item["purpose"] == "failure" for item in plan) != 14:
        raise AssertionError("internal issue #22 preliminary matrix differs from its 16-attempt contract")
    return plan


def parse_serial_event(raw_line: bytes | str) -> dict | None:
    """Decode one prefixed event; return None for unrelated serial output."""
    if isinstance(raw_line, bytes):
        abort_marker = ABORT_MARKER_PREFIX.encode("ascii")
        serial_marker = SERIAL_PREFIX.encode("ascii")
        abort_at = raw_line.find(abort_marker)
        serial_at = raw_line.find(serial_marker)
        if abort_at >= 0:
            raw_line = raw_line[abort_at:]
        elif serial_at >= 0:
            raw_line = raw_line[serial_at:]
        else:
            # UART capture is raw evidence and can contain arbitrary bytes.
            # Only bytes belonging to a recognized event need to be UTF-8.
            return None
        try:
            line = raw_line.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise ResilienceFormatError("resilience event is not UTF-8") from error
    else:
        line = raw_line
    line = line.rstrip("\r\n")
    marker_at = line.find(ABORT_MARKER_PREFIX)
    if marker_at >= 0:
        marker = line[marker_at:]
        # ESP-IDF repeats esp_system_abort()'s reason as a bare panic line
        # after the firmware has already flushed its attributed marker. It is
        # diagnostic text, not an event. Keep rejecting partial fieldful
        # markers so malformed evidence cannot be silently accepted.
        if marker == ABORT_MARKER_PREFIX:
            return None
        match = re.fullmatch(
            r"resilience:controlled_abort attempt_id=([0-9a-f]{8}-[0-9a-f]{4}-"
            r"[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}) case_id=(NAT-ABORT) "
            r"boot_id=([0-9a-f]{16}) monotonic_us=([0-9]+)",
            marker,
        )
        if match is None:
            raise ResilienceFormatError("controlled abort marker has invalid fields")
        return {
            "event": "abort_marker",
            "attempt_id": _canonical_uuid(match.group(1)),
            "case_id": match.group(2),
            "scenario": "native",
            "boot_id": match.group(3),
            "monotonic_us": int(match.group(4)),
            "raw_marker": marker,
        }
    marker = line.find(SERIAL_PREFIX)
    if marker < 0:
        return None
    payload = line[marker + len(SERIAL_PREFIX):]
    try:
        value = json.loads(payload, object_pairs_hook=_unique_object,
                           parse_constant=lambda token: (_ for _ in ()).throw(
                               ResilienceFormatError(f"invalid JSON constant: {token}")))
    except (json.JSONDecodeError, RecursionError) as error:
        raise ResilienceFormatError("prefixed resilience event contains invalid JSON") from error
    required = {"event", "attempt_id", "case_id", "scenario", "boot_id", "monotonic_us"}
    if not isinstance(value, dict) or not required.issubset(value):
        raise ResilienceFormatError("resilience event is missing required correlation fields")
    if not isinstance(value["event"], str) or not value["event"]:
        raise ResilienceFormatError("resilience event name must be text")
    _canonical_uuid(value["attempt_id"])
    if not isinstance(value["case_id"], str) or value["case_id"] not in FAULT_CASES:
        raise ResilienceFormatError("resilience event case_id is outside the approved matrix")
    if not isinstance(value["scenario"], str) or not value["scenario"]:
        raise ResilienceFormatError("resilience event scenario must be text")
    boot = value["boot_id"]
    if isinstance(boot, int) and not isinstance(boot, bool):
        if boot < 0 or boot >= 1 << 64:
            raise ResilienceFormatError("boot_id integer must fit uint64")
    elif not isinstance(boot, str) or re.fullmatch(r"[0-9a-f]{16}", boot) is None:
        raise ResilienceFormatError("boot_id string must be 16 lowercase hexadecimal digits")
    if (not isinstance(value["monotonic_us"], int) or isinstance(value["monotonic_us"], bool)
            or value["monotonic_us"] < 0):
        raise ResilienceFormatError("monotonic_us must be a non-negative integer")
    return value


def _normalize_boot(value) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return f"{value:016x}"
    if isinstance(value, str):
        stripped = value.strip().lower()
        if stripped.startswith("0x"):
            stripped = stripped[2:]
        if stripped and all(character in "0123456789abcdef" for character in stripped):
            return stripped.zfill(16)
    return None


def _udp_correlation(record: dict) -> tuple[str | None, str | None]:
    packet = record.get("packet") if isinstance(record.get("packet"), dict) else record
    attempt = packet.get("attempt_id")
    try:
        attempt = _canonical_uuid(attempt)
    except ResilienceFormatError:
        attempt = None
    boot = _normalize_boot(packet.get("boot_id"))
    return attempt, boot


def correlate(serial_events: list[dict], udp_records: list[dict],
              requests: list[dict] | None = None) -> dict:
    """Join requests and observations while retaining unconfirmed attempts."""
    indexed_udp: dict[tuple[str | None, str | None], list[dict]] = {}
    for record in udp_records:
        key = _udp_correlation(record)
        indexed_udp.setdefault(key, []).append(record)
    attempts = {}
    for request in requests or []:
        if not isinstance(request, dict) or request.get("event") != "fault_request":
            raise ResilienceFormatError("request record must be a fault_request object")
        attempt = _canonical_uuid(request.get("attempt_id"))
        case_id = request.get("case_id")
        scenario = request.get("scenario")
        if (not isinstance(case_id, str) or case_id not in FAULT_CASES or
                not isinstance(scenario, str) or scenario not in SCENARIOS):
            raise ResilienceFormatError("request record has an unapproved case or scenario")
        host_time = request.get("host_monotonic_ns")
        if (not isinstance(host_time, int) or isinstance(host_time, bool) or
                host_time < 0):
            raise ResilienceFormatError("dispatched request requires host_monotonic_ns")
        dispatch_status = request.get("dispatch_status")
        if dispatch_status not in ("sent", "partial_write", "write_error", "write_interrupted"):
            raise ResilienceFormatError("request record has an unknown host dispatch status")
        parsed_request = parse_fault_request(request.get("raw_request", ""))
        if parsed_request["attempt_id"] != attempt or parsed_request["case_id"] != case_id:
            raise ResilienceFormatError("raw request differs from its host record")
        group = attempts.setdefault(attempt, {
            "attempt_id": attempt,
            "case_id": case_id,
            "scenario": scenario,
            "serial_events": [],
            "boots": [],
            "udp_by_boot": {},
            "status": "unconfirmed",
            "fault_requests": [],
        })
        if case_id != group["case_id"] or scenario != group["scenario"]:
            group["correlation_error"] = "attempt_id reused with different case or scenario"
        group["fault_requests"].append(request)
        group.setdefault("dispatch_statuses", []).append(dispatch_status)
    for event in serial_events:
        attempt = event["attempt_id"]
        normalized_scenario = _normalized_scenario(event.get("scenario"))
        group = attempts.setdefault(attempt, {
            "attempt_id": attempt,
            "case_id": event["case_id"],
            "scenario": normalized_scenario or event["scenario"],
            "serial_events": [],
            "boots": [],
            "udp_by_boot": {},
            "status": "unconfirmed",
            "fault_requests": [],
        })
        if normalized_scenario is None:
            group["correlation_error"] = "serial event has an unapproved scenario"
        elif event["case_id"] != group["case_id"] or normalized_scenario != group["scenario"]:
            group["correlation_error"] = "attempt_id reused with different case or scenario"
        boot = _normalize_boot(event["boot_id"])
        group["serial_events"].append(event)
        if boot and boot not in group["boots"]:
            group["boots"].append(boot)
        if event["event"] == "fault_accepted":
            group["status"] = "accepted_incomplete"
        elif event["event"] == "fault_request_rejected":
            group["status"] = "request_rejected"
        elif event["event"] == "update_rejected":
            group["status"] = "rejected_observed"
        elif event["event"] in ("wasm_trap", "panic_observed", "abort_marker", "boot_observed",
                                 "control_recovery_completed"):
            if group["status"] != "unconfirmed":
                group["status"] = "outcome_observed"
    for group in attempts.values():
        for boot in group["boots"]:
            records = indexed_udp.get((group["attempt_id"], boot), [])
            group["udp_by_boot"][boot] = records
        group["udp_record_count"] = sum(map(len, group["udp_by_boot"].values()))
        attempt_udp_records = [record for records in group["udp_by_boot"].values()
                               for record in records]
        # A successful-send receipt contains the exact wire hex but omits the
        # attempt/boot IDs. Reattach only receipts matching a send intent that
        # is already correlated to this attempt; otherwise the classifier
        # would discard proof that its control request actually left the host.
        attempt_send_hex = {
            record["hex"] for record in attempt_udp_records
            if record.get("kind") == "send_intent" and isinstance(record.get("hex"), str)
        }
        attempt_udp_records.extend(
            record for record in udp_records
            if record.get("kind") == "send" and record.get("hex") in attempt_send_hex)
        attempt_udp_records.extend(
            record for record in udp_records
            if record.get("kind") == "session_start" and
            record.get("attempt_id") == group["attempt_id"])
        # Preserve capture order: device monotonic clocks reset at reboot and
        # must never be sorted across boot IDs.
        group["classification"] = classify_attempt(
            group["serial_events"], expected_case=group["case_id"],
            udp_records=attempt_udp_records)
        if "correlation_error" in group:
            group["classification"] = {
                "status": "invalid_correlation",
                "reason": group["correlation_error"],
            }
        if (group["classification"]["status"] == "unconfirmed" and
                group["fault_requests"]):
            group["classification"]["request_preserved"] = True
    return {
        "schema": "wasmhome-resilience-correlation-v1",
        "serial_event_count": len(serial_events),
        "udp_record_count": len(udp_records),
        "attempts": list(attempts.values()),
        "unmatched_udp_records": [
            record for record in udp_records
            if (record.get("kind") != "session_start" and
                _udp_correlation(record) not in {
                (attempt, boot)
                for attempt, group in attempts.items()
                for boot in group["boots"]
                })
        ],
        "clock_rule": "device monotonic_us and host_monotonic_ns remain separate; never subtract them",
    }


def _observer_control_requests(udp_records: list[dict], attempt_id: str,
                               boot_id: str) -> tuple[dict, int | None]:
    """Return controls that the observer successfully sent and the session deadline."""
    sessions = [record for record in udp_records
                if record.get("kind") == "session_start" and
                record.get("attempt_id") == attempt_id]
    timeout_ns = None
    if sessions:
        timeout = sessions[0].get("timeout_ns")
        if isinstance(timeout, int) and not isinstance(timeout, bool) and timeout > 0:
            timeout_ns = timeout
    successful_sends = {}
    for record in udp_records:
        sent_ns = record.get("host_monotonic_ns")
        raw_hex = record.get("hex")
        if (record.get("kind") != "send" or not isinstance(raw_hex, str) or
                not isinstance(sent_ns, int) or isinstance(sent_ns, bool) or sent_ns < 0):
            continue
        successful_sends.setdefault(raw_hex, sent_ns)
    control_requests = {}
    for record in udp_records:
        if record.get("kind") != "send_intent" or record.get("hex") not in successful_sends:
            continue
        packet = record.get("packet")
        if not isinstance(packet, dict) or packet.get("message_type") != "CONTROL_REQUEST":
            continue
        record_attempt, record_boot = _udp_correlation(record)
        body = packet.get("body")
        if (record_attempt == attempt_id and record_boot == boot_id and
                isinstance(body, dict) and
                isinstance(body.get("sample_id"), int) and
                not isinstance(body.get("sample_id"), bool) and
                isinstance(body.get("input_temperature_mC"), int) and
                not isinstance(body.get("input_temperature_mC"), bool)):
            control_requests[body["sample_id"]] = {
                "temperature_mC": body["input_temperature_mC"],
                "sent_ns": successful_sends[record["hex"]],
                "boot_id": record_boot,
            }
    return control_requests, timeout_ns


def _observer_packet_observations(udp_records: list[dict], attempt_id: str,
                                  boot_id: str, message_type: str) -> list[dict]:
    """Return semantically valid observer service receipts, never TX intent."""
    sessions = [record for record in udp_records
                if record.get("kind") == "session_start" and
                record.get("attempt_id") == attempt_id]
    freshness_limit_ms = 1000
    initial_version = None
    if sessions:
        age = sessions[0].get("max_sample_age_ms")
        version = sessions[0].get("initial_version")
        if isinstance(age, int) and not isinstance(age, bool) and 0 <= age < 65535:
            freshness_limit_ms = age
        if version in (1, 2):
            initial_version = version
    control_requests, timeout_ns = _observer_control_requests(
        udp_records, attempt_id, boot_id)

    observations = []
    seen_sequences = set()
    decoded_records = sorted(
        (record for record in udp_records if record.get("kind") == "decoded"),
        key=lambda record: (record.get("host_monotonic_ns")
                            if isinstance(record.get("host_monotonic_ns"), int)
                            and not isinstance(record.get("host_monotonic_ns"), bool)
                            else -1))
    for record in decoded_records:
        packet = record.get("packet")
        if not isinstance(packet, dict) or packet.get("message_type") != message_type:
            continue
        record_attempt, record_boot = _udp_correlation(record)
        host_time = record.get("host_monotonic_ns")
        if (record_attempt == attempt_id and record_boot == boot_id and
                isinstance(host_time, int) and not isinstance(host_time, bool) and
                host_time >= 0):
            sequence = packet.get("sequence")
            if (not isinstance(sequence, int) or isinstance(sequence, bool) or
                    not 0 < sequence < 1 << 64 or sequence in seen_sequences):
                continue
            # The observer writes `decoded` before its duplicate/sequence checks.
            # Count only the first copy of a sequence, even when the first copy
            # is semantically invalid and a later duplicate looks valid.
            seen_sequences.add(sequence)
            body = packet.get("body")
            if not isinstance(body, dict):
                continue
            if message_type == "TELEMETRY":
                sensor_status = body.get("sensor_status")
                if (isinstance(sensor_status, int) and
                        not isinstance(sensor_status, bool) and sensor_status == 0 and
                        isinstance(body.get("age_ms"), int) and
                        not isinstance(body.get("age_ms"), bool) and
                        body["age_ms"] <= freshness_limit_ms):
                    observations.append({"host_monotonic_ns": host_time,
                                         "sequence": sequence})
            elif message_type == "CONTROL_RESULT":
                sample_id = body.get("sample_id")
                request = control_requests.get(sample_id)
                version = body.get("module_version")
                status = body.get("status")
                requested_gpio = body.get("requested_gpio")
                observed_gpio = body.get("observed_gpio")
                if (not isinstance(sample_id, int) or isinstance(sample_id, bool) or
                        request is None or not isinstance(version, int) or
                        isinstance(version, bool) or version not in (1, 2) or
                        (initial_version is not None and version != initial_version)):
                    continue
                expected_gpio = int(request["temperature_mC"] >
                                    (25000 if version == 1 else 30000))
                latency_ns = host_time - request["sent_ns"]
                if (isinstance(status, int) and not isinstance(status, bool) and
                        status == 0 and
                        isinstance(requested_gpio, int) and
                        not isinstance(requested_gpio, bool) and
                        requested_gpio == expected_gpio and
                        isinstance(observed_gpio, int) and
                        not isinstance(observed_gpio, bool) and
                        observed_gpio == expected_gpio and
                        isinstance(timeout_ns, int) and
                        0 <= latency_ns <= timeout_ns):
                    observations.append({"host_monotonic_ns": host_time,
                                         "request_sent_ns": request["sent_ns"],
                                         "sample_id": sample_id,
                                         "sequence": sequence})
    return sorted(observations, key=lambda item: item["host_monotonic_ns"])


def _observer_packet_times(udp_records: list[dict], attempt_id: str,
                           boot_id: str, message_type: str) -> list[int]:
    return [item["host_monotonic_ns"] for item in _observer_packet_observations(
        udp_records, attempt_id, boot_id, message_type)]


def _observer_recovery(udp_records: list[dict], accepted: dict,
                       completed: dict, *, health_anchor: dict | None = None,
                       window_ns: int = 30_000_000_000) -> dict:
    """Measure acceptance-to-recovery using the first correct observer response."""
    attempt_id = accepted.get("attempt_id")
    boot_id = _normalize_boot(completed.get("boot_id"))
    start_ns = accepted.get("host_monotonic_ns")
    end_ns = completed.get("host_monotonic_ns")
    if (not isinstance(attempt_id, str) or boot_id is None or
            not isinstance(start_ns, int) or isinstance(start_ns, bool) or start_ns < 0 or
            not isinstance(end_ns, int) or isinstance(end_ns, bool) or end_ns < start_ns):
        return {"telemetry_udp_received": False,
                "control_udp_received": False,
                "control_udp_after_health_check": False,
                "recovery_within_30s": False,
                "recovery_endpoint_host_monotonic_ns": None}
    telemetry = [item for item in _observer_packet_observations(
        udp_records, attempt_id, boot_id, "TELEMETRY")
        if start_ns <= item["host_monotonic_ns"]]
    all_control_observations = _observer_packet_observations(
        udp_records, attempt_id, boot_id, "CONTROL_RESULT")
    controls = [item for item in all_control_observations
                if start_ns <= item["host_monotonic_ns"]]
    telemetry_times = [item["host_monotonic_ns"] for item in telemetry]
    control_times = [item["host_monotonic_ns"] for item in controls]
    telemetry_received = bool(telemetry_times)
    control_received = bool(control_times)
    health_ns = (health_anchor.get("host_monotonic_ns")
                 if health_anchor else start_ns)
    if health_anchor is None:
        control_after_health = controls
    elif isinstance(health_ns, int) and not isinstance(health_ns, bool):
        control_after_health = [item for item in controls
                               if health_ns <= item["host_monotonic_ns"]]
    else:
        control_after_health = []
    endpoint_ns = (control_after_health[0]["host_monotonic_ns"]
                   if control_after_health else None)
    telemetry_through_endpoint = (endpoint_ns is not None and
        any(start_ns <= timestamp <= endpoint_ns for timestamp in telemetry_times))
    requests, timeout_ns = _observer_control_requests(udp_records, attempt_id, boot_id)
    timely_results = {item["sample_id"]: item["host_monotonic_ns"]
                      for item in all_control_observations}
    checked_requests = [
        (sample_id, request) for sample_id, request in requests.items()
        if endpoint_ns is not None and isinstance(timeout_ns, int) and
        request["boot_id"] == boot_id and request["sent_ns"] <= endpoint_ns and
        request["sent_ns"] + timeout_ns >= start_ns]
    overdue_requests = []
    for sample_id, request in checked_requests:
        response_ns = timely_results.get(sample_id)
        if response_ns is not None and response_ns < start_ns:
            continue
        deadline_ns = request["sent_ns"] + timeout_ns
        if (deadline_ns <= endpoint_ns and
                (response_ns is None or response_ns < start_ns or
                 response_ns > min(endpoint_ns, deadline_ns))):
            overdue_requests.append(sample_id)
    control_deadlines = (isinstance(timeout_ns, int) and
                         len(overdue_requests) == 0)
    recovery_within = (telemetry_through_endpoint and
                       endpoint_ns - start_ns <= window_ns
                       if endpoint_ns is not None else False)
    return {"telemetry_udp_received": telemetry_received,
            "control_udp_received": control_received,
            "control_udp_after_health_check": bool(control_after_health),
            "control_requests_within_deadline": control_deadlines,
            "control_requests_checked": len(checked_requests),
            "control_requests_overdue": len(overdue_requests),
            "recovery_within_30s": recovery_within,
            "recovery_endpoint_host_monotonic_ns": endpoint_ns}


def _observer_gap_series(udp_records: list[dict], attempt_id: str, boot_id: str,
                         start_ns: int, end_ns: int, max_gap_ns: int) -> dict:
    """Bound every observed telemetry interval, including acceptance/recovery edges."""
    times = [timestamp for timestamp in _observer_packet_times(
        udp_records, attempt_id, boot_id, "TELEMETRY")
        if start_ns <= timestamp <= end_ns]
    if not times or end_ns < start_ns:
        return {"telemetry_gap_series_within_candidate": False,
                "telemetry_observer_packet_count": len(times),
                "telemetry_observer_max_gap_ms": None}
    gaps = [times[0] - start_ns]
    gaps.extend(later - earlier for earlier, later in zip(times, times[1:]))
    gaps.append(end_ns - times[-1])
    return {"telemetry_gap_series_within_candidate": all(
                0 <= gap <= max_gap_ns for gap in gaps),
            "telemetry_observer_packet_count": len(times),
            "telemetry_observer_max_gap_ms": max(gaps) / 1_000_000}


def _observer_recovery_passes(observer: dict) -> bool:
    return all(observer.get(key) is True for key in (
        "telemetry_udp_received", "control_udp_received",
        "control_udp_after_health_check", "control_requests_within_deadline",
        "recovery_within_30s"))


def _observer_nat_interruption(udp_records: list[dict], accepted: dict,
                               marker: dict, boot_observed: dict) -> dict:
    """Prove both old-boot services stopped at abort and resumed on the new boot."""
    attempt_id = accepted.get("attempt_id")
    old_boot = _normalize_boot(accepted.get("boot_id"))
    new_boot = _normalize_boot(boot_observed.get("boot_id"))
    start_ns = accepted.get("host_monotonic_ns")
    marker_ns = marker.get("host_monotonic_ns")
    telemetry_new = _observer_packet_times(
        udp_records, attempt_id, new_boot, "TELEMETRY") if new_boot else []
    control_new = _observer_packet_times(
        udp_records, attempt_id, new_boot, "CONTROL_RESULT") if new_boot else []
    valid = (isinstance(attempt_id, str) and old_boot is not None and
             new_boot is not None and old_boot != new_boot and
             isinstance(start_ns, int) and not isinstance(start_ns, bool) and
             isinstance(marker_ns, int) and not isinstance(marker_ns, bool) and
             start_ns <= marker_ns)
    if not valid:
        return {"telemetry_interruption_proven": False,
                "control_interruption_proven": False,
                "telemetry_outage_ms": None, "control_outage_ms": None}

    telemetry_old = _observer_packet_times(
        udp_records, attempt_id, old_boot, "TELEMETRY")
    control_old = _observer_packet_times(
        udp_records, attempt_id, old_boot, "CONTROL_RESULT")

    def outage_proven(old_times: list[int], new_times: list[int]) -> tuple[bool, int | None]:
        old_before = [timestamp for timestamp in old_times
                      if start_ns <= timestamp < marker_ns]
        new_after = [timestamp for timestamp in new_times if timestamp > marker_ns]
        if not old_before or not new_after:
            return False, None
        first_new = min(new_after)
        old_during_gap = any(marker_ns <= timestamp <= first_new for timestamp in old_times)
        return (not old_during_gap, first_new - marker_ns if not old_during_gap else None)

    telemetry_proven, telemetry_gap = outage_proven(telemetry_old, telemetry_new)
    control_proven, control_gap = outage_proven(control_old, control_new)
    return {"telemetry_interruption_proven": telemetry_proven,
            "control_interruption_proven": control_proven,
            "telemetry_outage_ms": (telemetry_gap / 1_000_000
                                     if telemetry_gap is not None else None),
            "control_outage_ms": (control_gap / 1_000_000
                                   if control_gap is not None else None)}


def _passed_status(passed: bool, accepted: dict) -> str:
    if not passed:
        return "accepted_incomplete"
    return ("passed" if accepted.get("functional_timeout_frozen") is True
            else "candidate_passed")


def _timeout_status(accepted: dict) -> str:
    return ("frozen" if accepted.get("functional_timeout_frozen") is True
            else "candidate_only")


def classify_attempt(events: list[dict], *, expected_case: str | None = None,
                     udp_records: list[dict] | None = None) -> dict:
    """Classify only explicit evidence; no confirmation never becomes rejection."""
    if not events:
        return {"status": "unconfirmed", "reason": None}
    attempt_ids = {event.get("attempt_id") for event in events}
    case_ids = {event.get("case_id") for event in events}
    if len(attempt_ids) != 1 or len(case_ids) != 1:
        return {"status": "invalid_correlation", "reason": "attempt_id/case_id changed"}
    case_id = next(iter(case_ids))
    if expected_case is not None and case_id != expected_case:
        return {"status": "invalid_correlation", "reason": "case_id differs from plan"}
    accepted = next((event for event in events if event.get("event") == "fault_accepted"), None)
    if accepted is None:
        request_rejection = next((event for event in events
                                  if event.get("event") == "fault_request_rejected"), None)
        if request_rejection is not None:
            return {"status": "request_rejected",
                    "reason": request_rejection.get("reason")}
        return {"status": "unconfirmed", "reason": None,
                "request_preserved": any(event.get("event") == "fault_request" for event in events)}
    acceptance_index = events.index(accepted)
    post_acceptance = events[acceptance_index + 1:]
    execution_failure = next((event for event in post_acceptance
                              if event.get("event") == "fault_execution_failed"), None)
    if execution_failure is not None:
        return {"status": "observed_failure",
                "reason": execution_failure.get("stage") or "fault_execution_failed"}
    if case_id in EXPECTED_REJECTIONS:
        expected_reason, expected_stage = EXPECTED_REJECTIONS[case_id]
        rejection = next((event for event in post_acceptance
                          if event.get("event") == "update_rejected"), None)
        if rejection is None:
            return {"status": "accepted_incomplete", "reason": None}
        if (rejection.get("reason") != expected_reason or
                rejection.get("stage") != expected_stage):
            return {"status": "observed_failure", "reason": rejection.get("reason"),
                    "stage": rejection.get("stage")}
        invariants = (
            rejection.get("boot_id") == accepted.get("boot_id") and
            rejection.get("real_envelope_validator") is True and
            rejection.get("active_slot_unchanged") is True and
            rejection.get("active_version_unchanged") is True and
            rejection.get("accepted_sequence_unchanged") is True
        )
        if case_id == "ENV-OLD":
            invariants = invariants and rejection.get("nvs_floor_checked") is True
        elif case_id == "ENV-HASH":
            invariants = (invariants and
                          rejection.get("inactive_slot_only") is True and
                          rejection.get("payload_invalidated") is True)
        elif case_id == "ENV-SIG":
            invariants = invariants and rejection.get("payload_written") is False
        recovery = next((event for event in post_acceptance
                         if event.get("event") == "control_recovery_completed"), None)
        slot_restored = next((event for event in post_acceptance
                              if event.get("event") == "control_slot_restored"), None)
        if recovery is None:
            return {"status": "accepted_incomplete",
                    "reason": rejection.get("reason"), "stage": rejection.get("stage")}
        if slot_restored is not None:
            health_anchor = slot_restored
        else:
            accepted_ns = accepted.get("host_monotonic_ns")
            rejection_ns = rejection.get("host_monotonic_ns")
            recovery_ns = recovery.get("host_monotonic_ns")
            rejection_index = post_acceptance.index(rejection)
            recovery_index = post_acceptance.index(recovery)
            native_ordered = (
                accepted.get("scenario") == rejection.get("scenario") ==
                recovery.get("scenario") == "native" and
                recovery_index > rejection_index and
                _normalize_boot(accepted.get("boot_id")) is not None and
                _normalize_boot(accepted.get("boot_id")) ==
                _normalize_boot(rejection.get("boot_id")) ==
                _normalize_boot(recovery.get("boot_id")) and
                all(isinstance(timestamp, int) and not isinstance(timestamp, bool)
                    for timestamp in (accepted_ns, rejection_ns, recovery_ns)) and
                0 <= accepted_ns <= rejection_ns <= recovery_ns)
            if not native_ordered:
                return {"status": "accepted_incomplete",
                        "reason": rejection.get("reason"), "stage": rejection.get("stage")}
            active_version = recovery.get("active_version")
            accepted_sequence = recovery.get("accepted_sequence")
            accepted_floor_after = rejection.get("accepted_floor_after")
            if not (
                recovery.get("telemetry_udp_tx") is True and
                recovery.get("control_udp_tx") is True and
                recovery.get("update_state_status") == "available" and
                isinstance(active_version, int) and not isinstance(active_version, bool) and
                active_version > 0 and
                isinstance(accepted_sequence, int) and
                not isinstance(accepted_sequence, bool) and accepted_sequence > 0 and
                isinstance(accepted_floor_after, int) and
                not isinstance(accepted_floor_after, bool) and
                accepted_sequence == accepted_floor_after):
                return {"status": "accepted_incomplete",
                        "reason": rejection.get("reason"), "stage": rejection.get("stage")}
            # Native recovery has no replaceable control module slot. Its
            # firmware emits completion only after persisted valid telemetry
            # and control results, then the external observer must see fresh
            # post-completion service traffic as an independent health check.
            health_anchor = recovery
        observer = _observer_recovery(udp_records or [], accepted, recovery,
                                      health_anchor=health_anchor)
        candidate_ms = accepted.get("functional_timeout_ms_candidate")
        boot_id = _normalize_boot(recovery.get("boot_id"))
        acceptance_ns = accepted.get("host_monotonic_ns")
        recovery_ns = observer.get("recovery_endpoint_host_monotonic_ns")
        if (isinstance(candidate_ms, int) and not isinstance(candidate_ms, bool) and
                isinstance(acceptance_ns, int) and not isinstance(acceptance_ns, bool) and
                isinstance(recovery_ns, int) and not isinstance(recovery_ns, bool) and
                boot_id is not None):
            gap_evidence = _observer_gap_series(
                udp_records or [], accepted["attempt_id"], boot_id, acceptance_ns,
                recovery_ns, candidate_ms * 1_000_000)
        else:
            gap_evidence = {"telemetry_gap_series_within_candidate": False,
                            "telemetry_observer_packet_count": 0,
                            "telemetry_observer_max_gap_ms": None}
        passed = (invariants and _observer_recovery_passes(observer) and
                  gap_evidence["telemetry_gap_series_within_candidate"])
        return {"status": _passed_status(passed, accepted),
                "functional_timeout_status": _timeout_status(accepted),
                "reason": rejection.get("reason"), "stage": rejection.get("stage"),
                **observer, **gap_evidence}
    if case_id.startswith("WASM-") and case_id != "WASM-ZERO":
        trap = next((event for event in post_acceptance if event.get("event") == "wasm_trap"), None)
        if trap is None:
            return {"status": "accepted_incomplete", "reason": None}
        expected_exception = EXPECTED_TRAPS.get(case_id)
        trap_matches = (
            expected_exception is not None and
            trap.get("call_ok") is False and
            trap.get("timed_out") is False and
            isinstance(trap.get("exception_raw"), str) and
            expected_exception in trap["exception_raw"]
        )
        if not trap_matches:
            return {"status": "observed_failure",
                    "reason": trap.get("exception_raw") or
                    ("unexpected timeout" if trap.get("timed_out") else
                     "unexpected Wasm trap result")}
        recovery = next((event for event in post_acceptance
                         if event.get("event") == "control_recovery_completed"), None)
        slot_restored = next((event for event in post_acceptance
                              if event.get("event") == "control_slot_restored"), None)
        if recovery is None or slot_restored is None:
            return {"status": "accepted_incomplete", "reason": trap.get("exception_raw"),
                    "recovered": False}
        candidate_ms = trap.get("functional_timeout_ms_candidate")
        observer = _observer_recovery(udp_records or [], accepted, recovery,
                                      health_anchor=slot_restored)
        acceptance_ns = accepted.get("host_monotonic_ns")
        recovery_ns = observer.get("recovery_endpoint_host_monotonic_ns")
        boot_id = _normalize_boot(trap.get("boot_id"))
        if (isinstance(candidate_ms, int) and not isinstance(candidate_ms, bool) and
                isinstance(acceptance_ns, int) and not isinstance(acceptance_ns, bool) and
                isinstance(recovery_ns, int) and not isinstance(recovery_ns, bool) and
                boot_id is not None):
            gap_evidence = _observer_gap_series(
                udp_records or [], trap["attempt_id"], boot_id, acceptance_ns,
                recovery_ns, candidate_ms * 1_000_000)
        else:
            gap_evidence = {
                "telemetry_gap_series_within_candidate": False,
                "telemetry_observer_packet_count": 0,
                "telemetry_observer_max_gap_ms": None,
            }
        telemetry_within_candidate = gap_evidence[
            "telemetry_gap_series_within_candidate"]
        passed = (trap_matches and
                  trap.get("boot_id") == accepted.get("boot_id") and
                  recovery.get("telemetry_within_timeout") is True and
                  recovery.get("control_recovery_within_30s") is True and
                  telemetry_within_candidate and
                  _observer_recovery_passes(observer) and
                  recovery.get("control_slot_recovered") is True)
        return {"status": _passed_status(passed, accepted),
                "functional_timeout_status": _timeout_status(accepted),
                "reason": trap.get("exception_raw"), "recovered": True,
                "telemetry_within_timeout_observed": telemetry_within_candidate,
                "control_recovery_within_30s_observed": observer["recovery_within_30s"],
                **observer, **gap_evidence}
    if case_id == "WASM-ZERO":
        control = next((event for event in post_acceptance if event.get("event") == "wasm_control"), None)
        if control is None:
            return {"status": "accepted_incomplete", "reason": None}
        recovery = next((event for event in post_acceptance
                         if event.get("event") == "control_recovery_completed"), None)
        slot_restored = next((event for event in post_acceptance
                              if event.get("event") == "control_slot_restored"), None)
        if recovery is None or slot_restored is None:
            return {"status": "accepted_incomplete", "reason": None}
        observer = _observer_recovery(udp_records or [], accepted, recovery,
                                      health_anchor=slot_restored)
        control_ok = (control.get("call_ok") is True and
                      control.get("return_value") == 0x12345678)
        if not control_ok:
            return {"status": "observed_failure", "reason": None}
        passed = (recovery.get("control_slot_recovered") is True and
                  _observer_recovery_passes(observer))
        return {"status": _passed_status(passed, accepted),
                "functional_timeout_status": _timeout_status(accepted),
                "reason": None, **observer}
    if case_id == "NAT-ABORT":
        marker = next((event for event in post_acceptance
                       if event.get("event") == "abort_marker"), None)
        boot_events = [event for event in post_acceptance
                       if event.get("event") == "boot_observed"]
        recovery = next((event for event in post_acceptance
                         if event.get("event") == "control_recovery_completed"), None)
        if marker is None or not boot_events:
            return {"status": "accepted_incomplete", "reason": None}
        if len(boot_events) != 1:
            return {"status": "observed_failure" if len(boot_events) > 1
                    else "accepted_incomplete", "reason": None,
                    "recovered": recovery is not None}
        boot = boot_events[0]
        if (marker.get("boot_id") != accepted.get("boot_id") or
                boot.get("boot_id") == accepted.get("boot_id") or
                boot.get("reset_reason") != "ESP_RST_PANIC"):
            return {"status": "observed_failure", "reason": boot.get("reset_reason"),
                    "recovered": recovery is not None}
        observer = (_observer_recovery(udp_records or [], accepted, recovery)
                    if recovery is not None else {})
        interruption = _observer_nat_interruption(
            udp_records or [], accepted, marker, boot)
        passed = (recovery is not None and _observer_recovery_passes(observer) and
                  interruption["telemetry_interruption_proven"] and
                  interruption["control_interruption_proven"])
        return {"status": _passed_status(passed, accepted),
                "functional_timeout_status": _timeout_status(accepted),
                "reason": None, "recovered": recovery is not None,
                **observer, **interruption}
    return {"status": "accepted_incomplete", "reason": None}


def _read_jsonl(path: Path):
    records = []
    with path.open("r", encoding="utf-8", newline="") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line, object_pairs_hook=_unique_object)
            except (json.JSONDecodeError, ResilienceFormatError) as error:
                raise ResilienceFormatError(f"{path}:{line_number}: invalid JSONL record") from error
            if not isinstance(value, dict):
                raise ResilienceFormatError(f"{path}:{line_number}: JSONL record must be an object")
            records.append(value)
    return records


def _json_stream_records(text: str, path: Path) -> list[dict]:
    decoder = json.JSONDecoder(object_pairs_hook=_unique_object)
    records = []
    offset = 0
    while offset < len(text):
        while offset < len(text) and text[offset].isspace():
            offset += 1
        if offset == len(text):
            break
        try:
            value, offset = decoder.raw_decode(text, offset)
        except (json.JSONDecodeError, ResilienceFormatError) as error:
            raise ResilienceFormatError(f"{path}: invalid serial capture JSON stream") from error
        if not isinstance(value, dict):
            raise ResilienceFormatError(f"{path}: serial capture rows must be objects")
        records.append(value)
    return records


def _read_serial_events(path: Path) -> list[dict]:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        text = ""
    if text.lstrip().startswith("{"):
        try:
            rows = _json_stream_records(text, path)
        except ResilienceFormatError:
            rows = []
        if rows and rows[0].get("kind") == "serial_open":
            events = []
            pending = bytearray()
            last_host_ns = None

            def parse_complete_lines(host_ns: int, *, final: bool = False):
                while True:
                    newline = pending.find(b"\n")
                    if newline < 0:
                        if not final or not pending:
                            return
                        line = bytes(pending)
                        pending.clear()
                    else:
                        line = bytes(pending[:newline])
                        del pending[:newline + 1]
                    event = parse_serial_event(line)
                    if event is not None:
                        event["host_monotonic_ns"] = host_ns
                        events.append(event)
                    if newline < 0:
                        return

            for row in rows:
                kind = row.get("kind")
                if kind in ("serial_open", "serial_closed"):
                    continue
                if kind != "serial_bytes":
                    raise ResilienceFormatError(f"{path}: unexpected serial capture row kind")
                host_ns = row.get("host_monotonic_ns")
                if (not isinstance(host_ns, int) or isinstance(host_ns, bool) or host_ns < 0 or
                        (last_host_ns is not None and host_ns < last_host_ns)):
                    raise ResilienceFormatError(f"{path}: invalid serial capture host timestamp")
                encoded = row.get("raw_base64")
                count = row.get("byte_count")
                if not isinstance(encoded, str) or not isinstance(count, int) or count < 0:
                    raise ResilienceFormatError(f"{path}: invalid serial byte capture")
                try:
                    chunk = base64.b64decode(encoded, validate=True)
                except (ValueError, base64.binascii.Error) as error:
                    raise ResilienceFormatError(f"{path}: invalid base64 serial bytes") from error
                if len(chunk) != count:
                    raise ResilienceFormatError(f"{path}: serial byte_count differs from base64")
                pending.extend(chunk)
                last_host_ns = host_ns
                parse_complete_lines(host_ns)
            if pending and last_host_ns is not None:
                parse_complete_lines(last_host_ns, final=True)
            return events

    events = []
    for line_number, raw_line in enumerate(raw.splitlines(), 1):
        try:
            event = parse_serial_event(raw_line)
        except ResilienceFormatError as error:
            raise ResilienceFormatError(f"{path}:{line_number}: {error}") from error
        if event is not None:
            events.append(event)
    return events


def _write_exclusive(path: Path, data: bytes):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan_parser = subparsers.add_parser("plan", help="write an approved resilience matrix offline")
    plan_parser.add_argument("--seed", required=True)
    plan_parser.add_argument("--output", type=Path, required=True)
    plan_parser.add_argument("--scope", choices=("full", "preliminary"), default="full",
                             help="full 72-row issue #23 matrix (default) or preliminary 16-row issue #22 proof")
    correlate_parser = subparsers.add_parser("correlate", help="join existing serial and UDP captures")
    correlate_parser.add_argument("--serial-log", type=Path, required=True)
    correlate_parser.add_argument("--observations", type=Path, required=True)
    correlate_parser.add_argument("--requests", type=Path,
                                  help="optional JSONL of host fault_request records")
    correlate_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            if args.scope == "preliminary":
                records = resilience_preliminary_plan(args.seed)
                failures, controls = 14, 2
            else:
                records = resilience_plan(args.seed)
                failures, controls = 70, 2
            content = "".join(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
                              for record in records).encode("utf-8")
            _write_exclusive(args.output, content)
            print(json.dumps({"scope": args.scope, "planned_slots": len(records),
                              "failures": failures, "controls": controls,
                              "output": str(args.output)}, sort_keys=True))
            return 0

        events = _read_serial_events(args.serial_log)
        udp = _read_jsonl(args.observations)
        requests = _read_jsonl(args.requests) if args.requests else []
        report = correlate(events, udp, requests)
        payload = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")
        _write_exclusive(args.output, payload)
        print(json.dumps({"serial_events": len(events), "udp_records": len(udp),
                          "attempts": len(report["attempts"]), "output": str(args.output)},
                         sort_keys=True))
        return 0
    except (OSError, ValueError) as error:
        parser.exit(2, f"Resilience harness failed: {error}\n")


if __name__ == "__main__":
    sys.exit(main())
