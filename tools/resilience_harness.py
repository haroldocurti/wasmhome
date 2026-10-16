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
ENV_CASES = ("ENV-SIG", "ENV-HASH", "ENV-OLD")
WASM_MODES = ("interp", "aot")
EXPECTED_REJECTIONS = {
    "ENV-SIG": ("SIGNATURE_INVALID", "envelope_signature"),
    "ENV-HASH": ("HASH_MISMATCH", "payload_hash"),
    "ENV-OLD": ("SEQUENCE_NOT_NEWER", "envelope_sequence"),
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


def parse_serial_event(raw_line: bytes | str) -> dict | None:
    """Decode one prefixed event; return None for unrelated serial output."""
    if isinstance(raw_line, bytes):
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
        if request.get("dispatch_status") != "sent":
            raise ResilienceFormatError("only successfully dispatched requests may seed correlation")
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
    for event in serial_events:
        attempt = event["attempt_id"]
        group = attempts.setdefault(attempt, {
            "attempt_id": attempt,
            "case_id": event["case_id"],
            "scenario": event["scenario"],
            "serial_events": [],
            "boots": [],
            "udp_by_boot": {},
            "status": "unconfirmed",
            "fault_requests": [],
        })
        if event["case_id"] != group["case_id"] or event["scenario"] != group["scenario"]:
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
        # Preserve capture order: device monotonic clocks reset at reboot and
        # must never be sorted across boot IDs.
        group["classification"] = classify_attempt(
            group["serial_events"], expected_case=group["case_id"])
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
            if _udp_correlation(record) not in {
                (attempt, boot)
                for attempt, group in attempts.items()
                for boot in group["boots"]
            }
        ],
        "clock_rule": "device monotonic_us and host_monotonic_ns remain separate; never subtract them",
    }


def classify_attempt(events: list[dict], *, expected_case: str | None = None) -> dict:
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
        return {"status": "passed" if invariants else "accepted_incomplete",
                "reason": rejection.get("reason"), "stage": rejection.get("stage")}
    if case_id.startswith("WASM-") and case_id != "WASM-ZERO":
        trap = next((event for event in post_acceptance if event.get("event") == "wasm_trap"), None)
        if trap is None:
            return {"status": "accepted_incomplete", "reason": None}
        recovery = next((event for event in post_acceptance
                         if event.get("event") == "control_recovery_completed"), None)
        if recovery is None:
            return {"status": "accepted_incomplete", "reason": trap.get("exception_raw"),
                    "recovered": False}
        passed = (trap.get("call_ok") is False and
                  trap.get("boot_id") == accepted.get("boot_id") and
                  recovery.get("telemetry_within_timeout") is True and
                  recovery.get("control_recovery_within_30s") is True and
                  recovery.get("control_slot_recovered") is True)
        return {"status": "passed" if passed else "accepted_incomplete",
                "reason": trap.get("exception_raw"), "recovered": True}
    if case_id == "WASM-ZERO":
        control = next((event for event in post_acceptance if event.get("event") == "wasm_control"), None)
        if control is None:
            return {"status": "accepted_incomplete", "reason": None}
        passed = control.get("call_ok") is True and control.get("return_value") == 0x12345678
        return {"status": "passed" if passed else "observed_failure", "reason": None}
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
        passed = (recovery is not None and
                  recovery.get("telemetry_udp_received") is True and
                  recovery.get("control_udp_received") is True and
                  recovery.get("recovery_within_30s") is True)
        return {"status": "passed" if passed else "accepted_incomplete",
                "reason": None, "recovered": recovery is not None}
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
    plan_parser = subparsers.add_parser("plan", help="write the approved 72-slot matrix offline")
    plan_parser.add_argument("--seed", required=True)
    plan_parser.add_argument("--output", type=Path, required=True)
    correlate_parser = subparsers.add_parser("correlate", help="join existing serial and UDP captures")
    correlate_parser.add_argument("--serial-log", type=Path, required=True)
    correlate_parser.add_argument("--observations", type=Path, required=True)
    correlate_parser.add_argument("--requests", type=Path,
                                  help="optional JSONL of host fault_request records")
    correlate_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            records = resilience_plan(args.seed)
            content = "".join(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
                              for record in records).encode("utf-8")
            _write_exclusive(args.output, content)
            print(json.dumps({"planned_slots": len(records), "failures": 70,
                              "controls": 2, "output": str(args.output)}, sort_keys=True))
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
