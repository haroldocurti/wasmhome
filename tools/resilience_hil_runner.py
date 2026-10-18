#!/usr/bin/env python3
"""Explicit, one-attempt HIL runner for the opt-in issue #22 firmware.

Planning, preflight validation, and HTTP snapshot correlation are offline.
Only ``run`` opens the explicitly named serial port and UDP socket. Capture
files contain raw serial/UDP evidence and are created with private permissions.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import select
import socket
import stat
import sys
import time
import uuid


HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from external_observer import Observer
from resilience_harness import (
    FAULT_CASES, SCENARIOS, ResilienceFormatError, _read_jsonl,
    _read_serial_events, correlate, make_fault_request, resilience_plan,
    resilience_preliminary_plan,
)


CAMPAIGN_ID = "issues16-18-bench-20261016-observer13"
PREFLIGHT_SCHEMA = "wasmhome-issue22-readonly-preflight-v4"
RUN_SCHEMA = "wasmhome-issue22-hil-run-v1"
HTTP_SCHEMA = "wasmhome-issue22-fixture-http-v1"
HTTP_FIELDS = {
    "schema", "time_unix_ns", "peer", "host", "method", "path", "status",
    "bytes_sent", "sha256", "tls_protocol", "tls_cipher", "duration_ns",
}
PROFILE_FOR_SCENARIO = {"native": "native", "interp": "module-interp", "aot": "module-aot"}
CONTROL_VERSION_BY_PROFILE = {"native": 1, "module-interp": 2, "module-aot": 2}
UPDATE_RECORD_IDENTITY = {
    "native": {"scenario": 1, "component": "firmware"},
    "module-interp": {"scenario": 2, "component": "gpio-control"},
    "module-aot": {"scenario": 3, "component": "gpio-control"},
}
ACTIVE_SLOT_VALUES = {
    "native": {0x20000, 0x720000},
    "module-interp": {0, 1},
    "module-aot": {0, 1},
}
EXPECTED_RELEASE_ARTIFACTS = {
    # Native is the newly signed issue #22 profile-A v2; module payloads are unchanged.
    "native": ("224e0c01ae4843828a6faad010fc60f20d9795816fee1ca483f11412ff8c67cf", 916688),
    "module-interp": ("3a972948112397acdaad03c81714a3daf147ecce926f0854341e58a350f3d5c5", 328),
    "module-aot": ("293d78b1af5e0c7e76593d9b8e755692ee311d8427c43d94673ddfd499d5bf53", 1256),
}
PAYLOAD_FOR_SCENARIO = {"native": "firmware.bin", "interp": "control.wasm", "aot": "control.aot"}
MAX_HTTP_BYTES = 64 * 1024 * 1024
MIN_VALID_CONTROLS = 30
MIN_VALID_TELEMETRY = 30


class HILInputError(ValueError):
    """A plan or preflight record is unsafe or inconsistent for HIL."""


class SerialWriteTimeout(TimeoutError):
    def __init__(self, bytes_written: int):
        super().__init__("serial write timed out")
        self.bytes_written = bytes_written


class ExclusiveSerialPort:
    """Small POSIX raw-TTY adapter; opens only the path supplied by the operator."""

    def __init__(self, path: str, baudrate: int, write_timeout: float = 2.0):
        if os.name != "posix":
            raise HILInputError("physical HIL serial capture currently requires a POSIX host")
        import array
        import errno
        import fcntl
        import termios
        import struct

        speeds = {rate: getattr(termios, f"B{rate}", None)
                  for rate in (9600, 19200, 38400, 57600, 115200, 230400)}
        speed = speeds.get(baudrate)
        if speed is None:
            raise HILInputError("unsupported POSIX serial baud rate")
        self._array = array
        self._fcntl = fcntl
        self._termios = termios
        self._errno = errno
        self._struct = struct
        self.write_timeout = write_timeout
        self.port = path
        self.baudrate = baudrate
        self.is_open = False
        self.bytes_written = 0
        self._original_termios = None
        self._modem_mask = 0
        self._original_modem_lines = None
        flags = os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
        self._fd = os.open(path, flags)
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if hasattr(termios, "TIOCEXCL"):
                fcntl.ioctl(self._fd, termios.TIOCEXCL)
            # Some adapters use DTR/RTS for ROM bootloader entry. Clear them
            # only when the original state can be read and restored exactly.
            modem_constants = ("TIOCMGET", "TIOCMBIC", "TIOCMBIS", "TIOCM_DTR", "TIOCM_RTS")
            if all(hasattr(termios, name) for name in modem_constants):
                self._modem_mask = termios.TIOCM_DTR | termios.TIOCM_RTS
                try:
                    modem_state = array.array("i", [0])
                    fcntl.ioctl(self._fd, termios.TIOCMGET, modem_state, True)
                except OSError as error:
                    if error.errno not in (errno.ENOTTY, errno.EINVAL):
                        raise
                else:
                    self._original_modem_lines = modem_state[0] & self._modem_mask
                    asserted = self._original_modem_lines
                    if asserted:
                        fcntl.ioctl(self._fd, termios.TIOCMBIC, struct.pack("I", asserted))
            import copy
            attrs = termios.tcgetattr(self._fd)
            self._original_termios = copy.deepcopy(attrs)
            attrs[0] = 0
            attrs[1] = 0
            attrs[2] = termios.CS8 | termios.CLOCAL | termios.CREAD
            attrs[3] = 0
            attrs[4] = speed
            attrs[5] = speed
            attrs[6][termios.VMIN] = 0
            attrs[6][termios.VTIME] = 0
            termios.tcsetattr(self._fd, termios.TCSANOW, attrs)
            self.is_open = True
        except BaseException:
            try:
                self._restore_device_state()
            finally:
                os.close(self._fd)
            raise

    def fileno(self):
        return self._fd

    @property
    def in_waiting(self):
        pending = self._array.array("i", [0])
        self._fcntl.ioctl(self._fd, self._termios.FIONREAD, pending, True)
        return max(0, int(pending[0]))

    def read(self, count: int) -> bytes:
        try:
            return os.read(self._fd, count)
        except BlockingIOError:
            return b""

    def write(self, data: bytes) -> int:
        deadline = time.monotonic() + self.write_timeout
        written = 0
        self.bytes_written = 0
        while written < len(data):
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([], [self._fd], [], max(0.0, remaining))[1]:
                raise SerialWriteTimeout(written)
            try:
                written += os.write(self._fd, data[written:])
                self.bytes_written = written
            except BlockingIOError:
                continue
        return written

    def flush(self):
        self._termios.tcdrain(self._fd)

    def _restore_device_state(self):
        restore_error = None
        if self._original_termios is not None:
            try:
                self._termios.tcsetattr(self._fd, self._termios.TCSANOW,
                                        self._original_termios)
            except BaseException as error:
                restore_error = error
        if self._original_modem_lines is not None:
            try:
                current = self._array.array("i", [0])
                self._fcntl.ioctl(self._fd, self._termios.TIOCMGET, current, True)
                current_lines = current[0] & self._modem_mask
                to_clear = current_lines & ~self._original_modem_lines
                to_set = self._original_modem_lines & ~current_lines
                if to_clear:
                    self._fcntl.ioctl(self._fd, self._termios.TIOCMBIC,
                                      self._struct.pack("I", to_clear))
                if to_set:
                    self._fcntl.ioctl(self._fd, self._termios.TIOCMBIS,
                                      self._struct.pack("I", to_set))
            except BaseException as error:
                if restore_error is None:
                    restore_error = error
        if restore_error is not None:
            raise restore_error

    def close(self):
        if self._fd < 0:
            return
        try:
            self._restore_device_state()
        finally:
            try:
                self._fcntl.flock(self._fd, self._fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = -1
                self.is_open = False


def _validated_timing(pre_seconds: float, post_seconds: float,
                      boot_wait_seconds: float, period_ms: float,
                      timeout_ms: float) -> tuple[int, int, int, int, int]:
    values = (("pre_seconds", pre_seconds, 1_000_000_000),
              ("post_seconds", post_seconds, 1_000_000_000),
              ("boot_wait_seconds", boot_wait_seconds, 1_000_000_000),
              ("period_ms", period_ms, 1_000_000),
              ("timeout_ms", timeout_ms, 1_000_000))
    converted = []
    for name, value, scale in values:
        finite = False
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            try:
                finite = math.isfinite(value)
            except OverflowError:
                pass
        if not finite:
            raise HILInputError(f"{name} must be finite and >0 and <=300")
        if value <= 0 or value > 300:
            raise HILInputError(f"{name} must be >0 and <=300")
        nanoseconds = round(value * scale)
        if nanoseconds <= 0:
            raise HILInputError(f"{name} rounds to zero nanoseconds")
        converted.append(nanoseconds)
    pre_ns, post_ns, boot_wait_ns, period_ns, timeout_ns = converted
    minimum_window_ns = period_ns * MIN_VALID_CONTROLS
    if boot_wait_ns < minimum_window_ns:
        raise HILInputError("boot_wait_seconds is too short to collect 30 control samples at the selected period")
    if post_ns < minimum_window_ns:
        raise HILInputError("post_seconds is too short to collect 30 control samples at the selected period")
    return pre_ns, post_ns, boot_wait_ns, period_ns, timeout_ns


def _observation_coverage(control_count: int, telemetry_count: int) -> dict:
    controls = max(0, int(control_count))
    telemetry = max(0, int(telemetry_count))
    return {
        "valid_control_count": controls,
        "valid_telemetry_count": telemetry,
        "minimum_valid_controls": MIN_VALID_CONTROLS,
        "minimum_valid_telemetry": MIN_VALID_TELEMETRY,
        "passed": controls >= MIN_VALID_CONTROLS and telemetry >= MIN_VALID_TELEMETRY,
    }


def _boot_number(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value < 1 << 64 else None
    if isinstance(value, str) and 1 <= len(value) <= 16:
        try:
            result = int(value, 16)
        except ValueError:
            return None
        return result if result < 1 << 64 else None
    return None


def _preflight_observation_coverage(control_times_by_boot: dict,
                                    telemetry_times_by_boot: dict,
                                    expected_boot: int) -> dict:
    gate = _observation_coverage(
        len(control_times_by_boot.get(expected_boot, [])),
        len(telemetry_times_by_boot.get(expected_boot, [])))
    gate["boot_id"] = f"{expected_boot:016x}"
    return gate


def _execution_terminal_event(serial_events: list[dict], item: dict) -> tuple[int, dict] | None:
    case = item["case_id"]
    if case in ("ENV-SIG", "ENV-HASH", "ENV-OLD"):
        terminal_names = {"update_rejected", "fault_execution_failed"}
    elif case == "WASM-ZERO":
        terminal_names = {"wasm_control", "fault_execution_failed"}
    elif case.startswith("WASM-"):
        terminal_names = {"wasm_trap", "fault_execution_failed"}
    elif case == "NAT-ABORT":
        terminal_names = {"abort_marker", "fault_execution_failed"}
    else:
        return None
    accepted_index = next((index for index, event in enumerate(serial_events)
                           if event.get("event") == "fault_accepted" and
                           event.get("attempt_id") == item["attempt_id"] and
                           event.get("case_id") == case), None)
    if accepted_index is None:
        return None
    for index, event in enumerate(serial_events[accepted_index + 1:], accepted_index + 1):
        host_ns = event.get("host_monotonic_ns")
        if (event.get("attempt_id") == item["attempt_id"] and
                event.get("case_id") == case and
                event.get("event") in terminal_names and
                isinstance(host_ns, int) and not isinstance(host_ns, bool) and host_ns >= 0):
            return index, event
    return None


def _post_execution_coverage(serial_events: list[dict], item: dict,
                             control_times_by_boot: dict,
                             telemetry_times_by_boot: dict,
                             preflight_boot: int) -> dict:
    terminal = _execution_terminal_event(serial_events, item)
    if terminal is None:
        terminal_ns = None
        target_boot = None
        terminal_name = None
    else:
        terminal_index, terminal_event = terminal
        terminal_ns = terminal_event["host_monotonic_ns"]
        terminal_name = terminal_event["event"]
        accepted = next(event for event in reversed(serial_events[:terminal_index])
                        if event.get("event") == "fault_accepted" and
                        event.get("attempt_id") == item["attempt_id"] and
                        event.get("case_id") == item["case_id"])
        if item["case_id"] == "NAT-ABORT":
            old_boot = _boot_number(terminal_event.get("boot_id"))
            accepted_boot = _boot_number(accepted.get("boot_id"))
            recovery_events = [event for event in serial_events[terminal_index + 1:]
                               if event.get("event") in ("boot_observed", "control_recovery_completed")
                               and event.get("attempt_id") == item["attempt_id"]
                               and event.get("case_id") == item["case_id"]]
            observed_boots = [_boot_number(event.get("boot_id")) for event in recovery_events
                              if event.get("event") == "boot_observed"]
            recovery_boots = [_boot_number(event.get("boot_id")) for event in recovery_events
                              if event.get("event") == "control_recovery_completed"]
            target_boot = observed_boots[0] if len(observed_boots) == 1 else None
            if (target_boot is not None and
                    (accepted_boot != preflight_boot or old_boot != accepted_boot or
                     target_boot == old_boot or
                     any(boot != target_boot for boot in recovery_boots if boot is not None))):
                target_boot = None
            if any(boot is None for boot in recovery_boots):
                target_boot = None
        else:
            accepted_boot = _boot_number(accepted.get("boot_id"))
            terminal_boot = _boot_number(terminal_event.get("boot_id"))
            target_boot = (preflight_boot if accepted_boot == preflight_boot and
                           terminal_boot == preflight_boot else None)
    control_times = control_times_by_boot.get(target_boot, []) if target_boot is not None else []
    telemetry_times = telemetry_times_by_boot.get(target_boot, []) if target_boot is not None else []
    if terminal_ns is None:
        gate = _observation_coverage(0, 0)
    else:
        gate = _observation_coverage(
            sum(timestamp > terminal_ns for timestamp in control_times),
            sum(timestamp > terminal_ns for timestamp in telemetry_times))
    gate["preflight_boot_id"] = f"{preflight_boot:016x}"
    gate["recovery_boot_id"] = None if target_boot is None else f"{target_boot:016x}"
    gate["execution_terminal_event"] = terminal_name
    gate["execution_terminal_event_host_monotonic_ns"] = terminal_ns
    return gate


def _gate_report_classifications(report: dict, coverage_gate: dict) -> None:
    if coverage_gate.get("passed") is True:
        return
    for attempt in report.get("attempts", []):
        classification = attempt.get("classification")
        if (not isinstance(classification, dict) or
                classification.get("status") in ("unconfirmed", "invalid_correlation")):
            continue
        attempt["classification"] = {
            "status": "insufficient_observation_coverage",
            "reason": "30 valid controls and 30 valid telemetry samples are required before classification",
            "observed_classification": classification.get("status"),
        }


def _strict_json(data: bytes, where: str):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise HILInputError(f"{where}: duplicate JSON key")
            value[key] = item
        return value

    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=unique,
                          parse_constant=lambda token: (_ for _ in ()).throw(
                              HILInputError(f"{where}: invalid JSON constant")))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise HILInputError(f"{where}: invalid JSON") from error


def load_plan(path: Path, scope: str = "full") -> list[dict]:
    """Read and validate one exact approved #22 or #23 plan.

    Scope is explicit so a preliminary #22 plan cannot be mistaken for the
    full #23 campaign. The default preserves the existing full-matrix runner.
    """
    if scope not in ("full", "preliminary"):
        raise HILInputError("plan scope must be explicitly recognized as full or preliminary")
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise HILInputError(f"cannot read plan: {error}") from error
    rows = []
    for number, line in enumerate(raw.splitlines(), 1):
        row = _strict_json(line, f"plan line {number}")
        if not isinstance(row, dict):
            raise HILInputError(f"plan line {number}: expected object")
        rows.append(row)
    expected = (resilience_preliminary_plan("runner-contract") if scope == "preliminary"
                else resilience_plan("runner-contract"))
    if len(rows) != len(expected):
        count = 16 if scope == "preliminary" else 72
        raise HILInputError(f"plan must contain the complete {count}-attempt {scope} matrix")
    identifiers = set()
    for index, (row, contract) in enumerate(zip(rows, expected), 1):
        if set(row) != set(contract):
            raise HILInputError(f"plan row {index}: fields differ from the closed matrix schema")
        # Preserve the contract's insertion order so validation reports the
        # first differing field deterministically across Python hash seeds.
        for key in contract:
            if key == "attempt_id":
                continue
            if type(row[key]) is not type(contract[key]) or row[key] != contract[key]:
                raise HILInputError(f"plan row {index}: matrix differs at {key}")
        try:
            parsed_attempt = uuid.UUID(row.get("attempt_id", ""))
            attempt = str(parsed_attempt)
        except (ValueError, TypeError, AttributeError) as error:
            raise HILInputError(f"plan row {index}: invalid attempt_id") from error
        if (row["attempt_id"] != attempt or parsed_attempt.version != 5 or
                parsed_attempt.variant != uuid.RFC_4122 or attempt in identifiers):
            raise HILInputError(f"plan row {index}: attempt_id must be a unique canonical UUIDv5")
        identifiers.add(attempt)
    return rows


def validate_preflight(value: object, item: dict) -> dict:
    """Validate an offline NVS preflight before hardware I/O."""
    if not isinstance(value, dict):
        raise HILInputError("preflight must be a JSON object")
    allowed = {"schema", "campaign_id", "profile", "record_identity", "boot_id",
               "control_version", "release_artifact_sha256", "native_state", "module_state",
               "recovery_state"}
    if set(value) != allowed:
        raise HILInputError("preflight fields differ; use the documented privacy-minimized schema")
    if value["schema"] != PREFLIGHT_SCHEMA or value["campaign_id"] != CAMPAIGN_ID:
        raise HILInputError("preflight schema or campaign_id differs")
    scenario = item["scenario"]
    if value["profile"] != PROFILE_FOR_SCENARIO[scenario]:
        raise HILInputError("preflight profile does not match the selected plan row")
    profile = PROFILE_FOR_SCENARIO[scenario]
    if value["record_identity"] != UPDATE_RECORD_IDENTITY[profile]:
        raise HILInputError("preflight update-record identity does not match the selected profile")
    control_version = value["control_version"]
    if (isinstance(control_version, bool) or not isinstance(control_version, int) or
            control_version != CONTROL_VERSION_BY_PROFILE[profile]):
        raise HILInputError("preflight control version does not match the selected firmware profile")
    boot = value["boot_id"]
    if not isinstance(boot, str) or len(boot) != 16 or any(c not in "0123456789abcdef" for c in boot) or int(boot, 16) == 0:
        raise HILInputError("preflight boot_id must be 16 lowercase hex digits and nonzero")
    release_hash = value["release_artifact_sha256"]
    if (not isinstance(release_hash, str) or len(release_hash) != 64 or
            any(c not in "0123456789abcdef" for c in release_hash)):
        raise HILInputError("preflight release_artifact_sha256 must be lowercase SHA-256")
    expected_release_hash, expected_release_size = EXPECTED_RELEASE_ARTIFACTS[profile]
    if release_hash != expected_release_hash:
        raise HILInputError("preflight release artifact hash differs from the registered signed v2 artifact")

    recovery = value["recovery_state"]
    if (not isinstance(recovery, dict) or
            set(recovery) != {"previous_attempt_present", "recovery_complete"} or
            not isinstance(recovery["previous_attempt_present"], bool) or
            not isinstance(recovery["recovery_complete"], bool)):
        raise HILInputError("recovery_state must contain only prior-attempt presence and recovery completeness")
    if recovery["recovery_complete"] is not True:
        raise HILInputError("previous resilience attempt has not completed recovery")
    state_fields = {"ledger_present", "accepted_floor", "ledger_active_target_version",
                    "ledger_active_sequence", "active_slot", "active_payload_size",
                    "active_payload_sha256", "active_manifest_sha256", "pending"}

    def validate_active_state(state: object, label: str) -> dict:
        if not isinstance(state, dict) or set(state) != state_fields:
            raise HILInputError(f"{label} does not match the sanitized ledger-state schema")
        integers = (state["accepted_floor"], state["ledger_active_target_version"],
                    state["ledger_active_sequence"], state["active_slot"],
                    state["active_payload_size"])
        if (state["ledger_present"] is not True or state["pending"] is not False or
                any(isinstance(number, bool) or not isinstance(number, int) or number < 0
                    for number in integers)):
            raise HILInputError(f"{label} must report a present, non-pending active ledger record")
        if (state["accepted_floor"], state["ledger_active_target_version"],
                state["ledger_active_sequence"]) != (2, 2, 2):
            raise HILInputError(f"{label} requires ledger floor, target version, and sequence 2")
        if (state["active_slot"] not in ACTIVE_SLOT_VALUES[profile] or
                state["active_payload_size"] != expected_release_size):
            raise HILInputError(f"{label} has an invalid active slot or payload size")
        for field in ("active_payload_sha256", "active_manifest_sha256"):
            digest = state[field]
            if (not isinstance(digest, str) or len(digest) != 64 or
                    any(character not in "0123456789abcdef" for character in digest) or
                    digest == "0" * 64):
                raise HILInputError(f"{label} must contain a nonzero lowercase {field} hash")
        if state["active_payload_sha256"] != release_hash:
            raise HILInputError(f"{label} active payload hash differs from the registered release artifact")
        return state

    if scenario == "native":
        validate_active_state(value["native_state"], "native_state")
        if value["module_state"] is not None:
            raise HILInputError("native preflight must not include modular state")
    else:
        if value["native_state"] is not None:
            raise HILInputError("modular preflight must not include native state")
        validate_active_state(value["module_state"], "module_state")
    return value


def _read_preflight(path: Path, item: dict) -> tuple[dict, str]:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise HILInputError(f"cannot read preflight: {error}") from error
    value = validate_preflight(_strict_json(raw, "preflight"), item)
    return value, hashlib.sha256(raw).hexdigest()


def _write_jsonl(stream, value):
    stream.write(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
    stream.flush()


def _private_file(directory: Path, name: str, mode="w"):
    descriptor = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    return os.fdopen(descriptor, mode, encoding="utf-8" if "b" not in mode else None)


def _open_http_log(path: Path | None):
    if path is None:
        return None, None
    if path.is_symlink():
        raise HILInputError("HTTP log must not be a symlink")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise HILInputError(f"cannot open read-only HTTP log: {error}") from error
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode):
        os.close(descriptor)
        raise HILInputError("HTTP log must be a regular file")
    return descriptor, info.st_size


def _http_rows(data: bytes) -> list[dict]:
    if not data:
        return []
    if not data.endswith(b"\n"):
        raise HILInputError("HTTP log delta ends with an incomplete JSONL row")
    rows = []
    for number, line in enumerate(data.splitlines(), 1):
        row = _strict_json(line, f"HTTP row {number}")
        if (not isinstance(row, dict) or set(row) != HTTP_FIELDS or
                row.get("schema") != HTTP_SCHEMA):
            raise HILInputError(f"HTTP row {number}: server log schema differs")
        integer_fields = ("time_unix_ns", "status", "bytes_sent", "duration_ns")
        if any(isinstance(row.get(key), bool) or not isinstance(row.get(key), int) or row[key] < 0
               for key in integer_fields):
            raise HILInputError(f"HTTP row {number}: numeric fields are invalid")
        peer_valid = False
        if isinstance(row["peer"], str):
            try:
                ipaddress.IPv4Address(row["peer"])
                peer_valid = True
            except ipaddress.AddressValueError:
                pass
        path_value = row["path"]
        path_valid = (isinstance(path_value, str) and len(path_value) <= 256 and
                      all(character.isalnum() or character in "._/-" for character in path_value))
        host = row["host"]
        host_valid = False
        if isinstance(host, str) and len(host) <= 280:
            # The campaign listener moved to the unprivileged user-mode port
            # 18445. Accept the prior 8445 logs for historical evidence, while
            # continuing to bind correlation to the two registered campaign
            # host identities and exact allowed ports.
            host_valid = host.lower() in {
                "wasmhome-issue22.test", "wasmhome-issue22.test:8445",
                "wasmhome-issue22.test:18445", "192.168.0.138",
                "192.168.0.138:8445", "192.168.0.138:18445"}
        if (not peer_valid or
                not host_valid or
                row["method"] not in ("GET", "HEAD", "POST", "UNKNOWN") or
                not path_valid or
                row["tls_protocol"] not in (None, "TLSv1.3") or
                (row["tls_cipher"] is not None and not isinstance(row["tls_cipher"], str)) or
                (row["sha256"] is not None and
                 (not isinstance(row["sha256"], str) or len(row["sha256"]) != 64 or
                  any(character not in "0123456789abcdef" for character in row["sha256"])) )):
            raise HILInputError(f"HTTP row {number}: non-contract fields or private host value")
        rows.append(row)
    return rows


def correlate_http_rows(item: dict, rows: list[dict]) -> dict:
    """Assess fixture transport routes separately; never infer firmware acceptance."""
    expected = set()
    if item["case_id"] in ("ENV-SIG", "ENV-HASH", "ENV-OLD"):
        scenario_number = {"native": 1, "interp": 2, "aot": 3}[item["scenario"]]
        base = f"resilience/{item['case_id']}/scenario-{scenario_number}"
        expected.add(f"{base}/manifest.cose")
        if item["case_id"] == "ENV-HASH":
            expected.add(f"{base}/{PAYLOAD_FOR_SCENARIO[item['scenario']]}")
    observed = []
    for row in rows:
        observed.append({key: row.get(key) for key in (
            "path", "method", "status", "bytes_sent", "sha256", "tls_protocol", "time_unix_ns")})
    seen = {row.get("path") for row in rows
            if (row.get("path") in expected and row.get("method") == "GET" and
                row.get("status") == 200 and row.get("tls_protocol") == "TLSv1.3" and
                isinstance(row.get("bytes_sent"), int) and row["bytes_sent"] > 0 and
                isinstance(row.get("sha256"), str))}
    unexpected = [row for row in observed if row.get("path") not in expected]
    return {
        "schema": "wasmhome-issue22-http-correlation-v1",
        "attempt_id": item["attempt_id"],
        "case_id": item["case_id"],
        "scenario": item["scenario"],
        "linkage": "HTTP log append window for one operator-serialized attempt; server does not emit attempt_id",
        "expected_routes": sorted(expected),
        "observed_routes": observed,
        "missing_expected_routes": sorted(expected - seen),
        "unexpected_route_count": len(unexpected),
        "transport_routes_complete": seen == expected and not unexpected,
        "fault_accepted_inferred": False,
        "interpretation": "HTTP transport observations do not establish fault_accepted, firmware validation, or recovery.",
    }


def _append_http_capture(descriptor: int | None, start_size: int | None,
                         output: Path, item: dict) -> dict:
    if descriptor is None:
        return {"status": "not_captured", "row_count": 0}
    info = os.fstat(descriptor)
    if info.st_size < start_size or info.st_size - start_size > MAX_HTTP_BYTES:
        return {"status": "invalid_log_growth", "row_count": 0}
    data = os.pread(descriptor, info.st_size - start_size, start_size)
    try:
        rows = _http_rows(data)
    except HILInputError as error:
        _write_private_bytes(output / "http-delta.jsonl", data)
        return {"status": "invalid_delta", "row_count": 0, "error": str(error)}
    _write_private_bytes(output / "http-delta.jsonl", data)
    result = correlate_http_rows(item, rows)
    _write_private_bytes(output / "http-correlation.json", (json.dumps(result, sort_keys=True, indent=2) + "\n").encode())
    return {"status": "captured", "start_offset": start_size,
            "end_offset": info.st_size, "row_count": len(rows),
            "transport_routes_complete": result["transport_routes_complete"]}


def _write_private_bytes(path: Path, data: bytes):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _capture_serial(serial_port, stream):
    available = int(getattr(serial_port, "in_waiting", 0))
    if available <= 0:
        return 0
    chunk = serial_port.read(min(available, 4096))
    if chunk:
        _write_jsonl(stream, {"kind": "serial_bytes", "host_monotonic_ns": time.monotonic_ns(),
                              "raw_base64": base64.b64encode(chunk).decode("ascii"),
                              "byte_count": len(chunk)})
    return len(chunk)


def _dispatch(serial_port, requests_log, item: dict) -> dict:
    raw = make_fault_request(item["attempt_id"], item["case_id"])
    started = time.monotonic_ns()
    written = 0
    error_kind = None
    interrupted = False
    try:
        written = serial_port.write(raw)
        if written is None:
            written = 0
        serial_port.flush()
    except KeyboardInterrupt:
        written = max(written, int(getattr(serial_port, "bytes_written", 0)))
        error_kind = "KeyboardInterrupt"
        interrupted = True
    except (OSError, TimeoutError) as error:
        written = max(written, int(getattr(error, "bytes_written", 0)))
        error_kind = type(error).__name__
    completed = time.monotonic_ns()
    status = "write_interrupted" if interrupted else ("sent" if written == len(raw) and error_kind is None else (
        "partial_write" if written else "write_error")
    )
    record = {
        "event": "fault_request", "attempt_id": item["attempt_id"],
        "case_id": item["case_id"], "scenario": item["scenario"],
        "host_monotonic_ns": completed, "host_write_started_ns": started,
        "host_write_completed_ns": completed, "raw_request": raw.decode("ascii"),
        "dispatch_status": status, "bytes_written": written,
    }
    if error_kind:
        record["write_error_kind"] = error_kind
    _write_jsonl(requests_log, record)
    if interrupted:
        raise KeyboardInterrupt
    return record


def execute_trial(*, item: dict, preflight: dict, serial_port, udp, output: Path,
                  bind_ip: str, device_ip: str, udp_port: int, device_port: int,
                  pre_seconds: float, post_seconds: float, boot_wait_seconds: float,
                  period_ms: float, timeout_ms: float, max_sample_age_ms: int,
                  http_descriptor: int | None = None, http_start_size: int | None = None) -> dict:
    """Run one armed attempt. Hardware/socket objects are injected for offline tests."""
    pre_ns, post_ns, boot_wait_ns, period_ns, timeout_ns = _validated_timing(
        pre_seconds, post_seconds, boot_wait_seconds, period_ms, timeout_ms)
    if not 0 <= max_sample_age_ms < 65535:
        raise HILInputError("max_sample_age_ms outside UDP protocol range")
    profile = PROFILE_FOR_SCENARIO[item["scenario"]]
    scenario_version = CONTROL_VERSION_BY_PROFILE[profile]
    if preflight.get("control_version") != scenario_version:
        raise HILInputError("preflight control version does not match the selected firmware profile")
    peer = (str(ipaddress.IPv4Address(device_ip)), device_port)
    local = (str(ipaddress.IPv4Address(bind_ip)), udp_port)

    current_local = udp.getsockname()
    if current_local[1] == 0:
        udp.bind(local)
    elif (str(ipaddress.IPv4Address(current_local[0])), current_local[1]) != local:
        raise HILInputError("UDP socket is already bound to a different local address")
    udp.setblocking(False)
    if http_descriptor is not None:
        http_start_size = os.fstat(http_descriptor).st_size

    serial_log = _private_file(output, "serial.jsonl")
    observations_log = _private_file(output, "observations.jsonl")
    requests_log = _private_file(output, "requests.jsonl")
    observer = Observer(observations_log, peer, period_ns, timeout_ns,
                        initial_version=scenario_version,
                        attempt_id=item["attempt_id"], max_sample_age_ms=max_sample_age_ms)
    start_mono = time.monotonic_ns()
    start_unix = time.time_ns()
    _write_jsonl(serial_log, {"kind": "serial_open", "host_monotonic_ns": start_mono,
                              "port": str(getattr(serial_port, "port", "explicit")),
                              "baud": int(getattr(serial_port, "baudrate", 115200))})
    observer.record("session_start", start_mono, seconds=None,
                    period_ns=period_ns, timeout_ns=timeout_ns,
                    initial_version=scenario_version, max_sample_age_ms=max_sample_age_ms,
                    attempt_id=item["attempt_id"], local=list(udp.getsockname()), peer=list(peer),
                    trigger_after=None, target_sequence=None, scenario=item["scenario"],
                    host_unix_ns=start_unix,
                    http_log_start_offset=http_start_size)

    dispatch_after = start_mono + pre_ns
    boot_deadline = dispatch_after + boot_wait_ns
    next_control = start_mono
    dispatch = None
    post_deadline = None
    finish_deadline = boot_deadline
    status = "awaiting_boot"
    interrupted = False
    expected_boot_number = int(preflight["boot_id"], 16)
    preflight_coverage = None
    post_coverage = None
    try:
        while True:
            now = time.monotonic_ns()
            observer.expire(now)
            if (dispatch is None and now >= dispatch_after and observer.boot_id is not None):
                observed_boot = f"{observer.boot_id:016x}"
                if observed_boot != preflight["boot_id"]:
                    status = "preflight_boot_mismatch"
                    observer.event("preflight_boot_mismatch", now,
                                   expected_boot_id=preflight["boot_id"], observed_boot_id=observed_boot)
                    finish_deadline = now + 100_000_000
                    dispatch = {"dispatch_status": "not_sent_boot_mismatch"}
                else:
                    # Drain datagrams already queued before the fault request so
                    # they cannot satisfy the post-request telemetry gate.
                    while True:
                        try:
                            raw, source = udp.recvfrom(65535)
                        except BlockingIOError:
                            break
                        except OSError as error:
                            observer.event("receive_error", time.monotonic_ns(),
                                           error=type(error).__name__)
                            break
                        else:
                            observer.receive(raw, source, time.monotonic_ns())
                    if observer.boot_id != expected_boot_number:
                        observed_boot = f"{observer.boot_id:016x}"
                        status = "preflight_boot_mismatch"
                        observer.event("preflight_boot_mismatch", time.monotonic_ns(),
                                       expected_boot_id=preflight["boot_id"],
                                       observed_boot_id=observed_boot)
                        dispatch = {"dispatch_status": "not_sent_boot_mismatch"}
                        finish_deadline = time.monotonic_ns() + 100_000_000
                    else:
                        preflight_coverage = _preflight_observation_coverage(
                            observer.success_times_by_boot,
                            observer.telemetry_times_by_boot,
                            expected_boot_number)
                        if preflight_coverage["passed"]:
                            dispatch = _dispatch(serial_port, requests_log, item)
                            status = "fault_request_sent" if dispatch["dispatch_status"] == "sent" else "dispatch_error"
                            dispatch_completed_ns = dispatch["host_write_completed_ns"]
                            post_deadline = dispatch_completed_ns + post_ns
                            finish_deadline = post_deadline + timeout_ns + 50_000_000
                            observer.event("host_fault_request_write_completed", dispatch_completed_ns,
                                           case_id=item["case_id"], dispatch_status=dispatch["dispatch_status"])
            if dispatch is None and now >= boot_deadline:
                if observer.boot_id is None:
                    status = "no_boot_observed"
                    observer.event("fault_request_not_sent", now, reason="no_boot_observed_before_deadline")
                    dispatch = {"dispatch_status": "not_sent_no_boot"}
                else:
                    preflight_coverage = _preflight_observation_coverage(
                        observer.success_times_by_boot,
                        observer.telemetry_times_by_boot,
                        expected_boot_number)
                    status = "insufficient_preflight_observation_coverage"
                    observer.event("fault_request_not_sent", now,
                                   reason="minimum_preflight_observation_coverage_not_met",
                                   coverage=preflight_coverage)
                    dispatch = {"dispatch_status": "not_sent_insufficient_preflight_coverage"}
                finish_deadline = now + timeout_ns + 50_000_000
            active_end = (post_deadline if post_deadline is not None else
                          boot_deadline if dispatch is None else now)
            if now < active_end and now >= next_control and observer.boot_id is not None:
                observer.send_control(udp, now, next_control)
                skipped = max(0, (now - next_control) // period_ns)
                if skipped:
                    observer.event("schedule_skipped", now, slots=skipped)
                next_control += (skipped + 1) * period_ns
            elif now < active_end and now >= next_control:
                observer.event("awaiting_boot", now)
                skipped = max(0, (now - next_control) // period_ns)
                next_control += (skipped + 1) * period_ns
            if now >= finish_deadline:
                break
            wait = max(0.0, min(0.02, (finish_deadline - now) / 1_000_000_000))
            readable, _, _ = select.select([udp, serial_port], [], [], wait)
            if udp in readable:
                try:
                    raw, source = udp.recvfrom(65535)
                except OSError as error:
                    observer.event("receive_error", time.monotonic_ns(), error=type(error).__name__)
                else:
                    observer.receive(raw, source, time.monotonic_ns())
            if serial_port in readable:
                _capture_serial(serial_port, serial_log)
    except KeyboardInterrupt:
        interrupted = True
        status = "interrupted"
        attempted = _read_jsonl(output / "requests.jsonl")
        if attempted:
            dispatch = attempted[-1]
        observer.event("interrupted", time.monotonic_ns())
    finally:
        ended_mono = time.monotonic_ns()
        observer.expire(ended_mono)
        observer.record("session_end", ended_mono, status=status,
                        fault_accepted_inferred=False)
        _write_jsonl(serial_log, {"kind": "serial_closed", "host_monotonic_ns": ended_mono})
        serial_log.close()
        observations_log.close()
        requests_log.close()
        try:
            serial_port.close()
        finally:
            udp.close()

    http_capture = _append_http_capture(http_descriptor, http_start_size, output, item)
    serial_events = _read_serial_events(output / "serial.jsonl")
    udp_rows = _read_jsonl(output / "observations.jsonl")
    request_rows = _read_jsonl(output / "requests.jsonl")
    if preflight_coverage is None:
        preflight_coverage = _preflight_observation_coverage(
            observer.success_times_by_boot,
            observer.telemetry_times_by_boot,
            expected_boot_number)
    if post_deadline is not None:
        post_coverage = _post_execution_coverage(
            serial_events, item, observer.success_times_by_boot,
            observer.telemetry_times_by_boot, expected_boot_number)
        if (not post_coverage["passed"] and status == "fault_request_sent"):
            status = "insufficient_post_execution_observation_coverage"
    coverage_gate = {
        "minimums_required_before_and_after": True,
        "before_fault_request": preflight_coverage,
        "after_fault_request": post_coverage,
        "passed": preflight_coverage["passed"] and post_coverage is not None and post_coverage["passed"],
    }
    report = correlate(serial_events, udp_rows, request_rows)
    report["coverage_gate"] = coverage_gate
    _gate_report_classifications(report, coverage_gate)
    report["http_transport"] = http_capture
    report["fault_accepted_inferred"] = False
    _write_private_bytes(output / "correlation.json",
                         (json.dumps(report, sort_keys=True, indent=2) + "\n").encode())
    summary = {
        "schema": RUN_SCHEMA,
        "status": status,
        "attempt": item,
        "campaign_id": CAMPAIGN_ID,
        "profile": preflight["profile"],
        "boot_id_preflight": preflight["boot_id"],
        "release_artifact_sha256_preflight_attestation": preflight["release_artifact_sha256"],
        "started_host_monotonic_ns": start_mono,
        "started_host_unix_ns": start_unix,
        "ended_host_monotonic_ns": ended_mono,
        "dispatch": dispatch,
        "coverage_gate": coverage_gate,
        "udp_observation_summary": observer.summary(),
        "http_capture": http_capture,
        "fault_accepted_inferred": False,
        "serial_event_count": len(serial_events),
        "udp_record_count": len(udp_rows),
        "interrupted": interrupted,
        "timeout_ms_is_candidate_only": True,
    }
    _write_private_bytes(output / "run.json", (json.dumps(summary, sort_keys=True, indent=2) + "\n").encode())
    return summary


def _http_snapshot(path: Path) -> bytes:
    if path.is_symlink():
        raise HILInputError("HTTP snapshot must not be a symlink")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_HTTP_BYTES:
            raise HILInputError("HTTP snapshot must be a regular file under 64 MiB")
        return os.read(descriptor, info.st_size)
    finally:
        os.close(descriptor)


def correlate_http_snapshots(run_dir: Path, before_path: Path, after_path: Path) -> dict:
    run_path = run_dir / "run.json"
    run = _strict_json(run_path.read_bytes(), "run.json")
    if not isinstance(run, dict) or run.get("schema") != RUN_SCHEMA:
        raise HILInputError("run directory does not contain a valid issue #22 run.json")
    before = _http_snapshot(before_path)
    after = _http_snapshot(after_path)
    if not after.startswith(before):
        raise HILInputError("after snapshot is not an append-only continuation of before snapshot")
    delta = after[len(before):]
    rows = _http_rows(delta)
    assessment = correlate_http_rows(run["attempt"], rows)
    assessment["snapshot_before_sha256"] = hashlib.sha256(before).hexdigest()
    assessment["snapshot_after_sha256"] = hashlib.sha256(after).hexdigest()
    assessment["snapshot_delta_bytes"] = len(delta)
    _write_private_bytes(run_dir / "http-delta.jsonl", delta)
    _write_private_bytes(run_dir / "http-correlation.json",
                         (json.dumps(assessment, sort_keys=True, indent=2) + "\n").encode())
    return assessment


def _command_run(args) -> int:
    plan = load_plan(args.plan, scope=args.plan_scope)
    if not 1 <= args.slot <= len(plan):
        raise HILInputError(f"slot must be in 1..{len(plan)} for the selected {args.plan_scope} matrix")
    item = plan[args.slot - 1]
    if args.arm_attempt_id != item["attempt_id"]:
        raise HILInputError("--arm-attempt-id must exactly match the selected plan row UUID")
    if item["case_id"] not in FAULT_CASES or item["scenario"] not in SCENARIOS:
        raise HILInputError("selected plan row is outside the approved matrix")
    preflight, preflight_hash = _read_preflight(args.preflight, item)
    # Literal IPv4 values prevent DNS lookups or implicit interface selection.
    bind_ip = str(ipaddress.IPv4Address(args.bind_ip))
    device_ip = str(ipaddress.IPv4Address(args.device_ip))
    if not 1024 <= args.udp_port <= 65535 or not 1 <= args.device_port <= 65535:
        raise HILInputError("UDP ports outside allowed range")
    _validated_timing(args.pre_seconds, args.post_seconds,
                      args.boot_wait_seconds, args.period_ms, args.timeout_ms)
    if not 0 <= args.max_sample_age_ms < 65535:
        raise HILInputError("max_sample_age_ms outside UDP protocol range")
    http_fd, http_start = _open_http_log(args.http_log)
    output = args.output
    try:
        output.mkdir(mode=0o700, parents=True, exist_ok=False)
    except BaseException:
        if http_fd is not None:
            os.close(http_fd)
        raise
    os.chmod(output, 0o700)
    _write_private_bytes(output / "inputs.json", (json.dumps({
        "schema": "wasmhome-issue22-hil-inputs-v1", "plan_scope": args.plan_scope,
        "attempt": item,
        "preflight_sha256": preflight_hash, "preflight": preflight,
        "serial_port": args.serial_port, "baud": args.baud,
        "device_ip": device_ip, "bind_ip": bind_ip,
        "udp_port": args.udp_port, "device_port": args.device_port,
        "http_log_size_at_open": http_start,
    }, sort_keys=True, indent=2) + "\n").encode())
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    serial_port = None
    try:
        udp.bind((bind_ip, args.udp_port))
        udp.setblocking(False)
        serial_port = ExclusiveSerialPort(args.serial_port, args.baud)
        result = execute_trial(item=item, preflight=preflight, serial_port=serial_port,
                               udp=udp, output=output, bind_ip=bind_ip,
                               device_ip=device_ip, udp_port=args.udp_port,
                               device_port=args.device_port, pre_seconds=args.pre_seconds,
                               post_seconds=args.post_seconds,
                               boot_wait_seconds=args.boot_wait_seconds,
                               period_ms=args.period_ms, timeout_ms=args.timeout_ms,
                               max_sample_age_ms=args.max_sample_age_ms,
                               http_descriptor=http_fd, http_start_size=http_start)
    finally:
        if http_fd is not None:
            os.close(http_fd)
        if serial_port is not None and getattr(serial_port, "is_open", False):
            serial_port.close()
        if not udp._closed:
            udp.close()
    print(json.dumps({"status": result["status"], "attempt_id": item["attempt_id"],
                      "case_id": item["case_id"], "scenario": item["scenario"],
                      "dispatch_status": result["dispatch"].get("dispatch_status"),
                      "serial_event_count": result["serial_event_count"],
                      "udp_record_count": result["udp_record_count"],
                      "http_capture": result["http_capture"],
                      "fault_accepted_inferred": False, "output": str(output)}, sort_keys=True))
    return 0


def _command_correlate_http(args) -> int:
    report = correlate_http_snapshots(args.run_dir, args.before, args.after)
    print(json.dumps({"attempt_id": report["attempt_id"],
                      "transport_routes_complete": report["transport_routes_complete"],
                      "missing_expected_routes": report["missing_expected_routes"],
                      "unexpected_route_count": report["unexpected_route_count"],
                      "fault_accepted_inferred": False,
                      "output": str(args.run_dir / "http-correlation.json")}, sort_keys=True))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="dispatch and capture one explicitly armed physical attempt")
    run.add_argument("--plan", type=Path, required=True)
    run.add_argument("--plan-scope", choices=("full", "preliminary"), default="full",
                     help="must match the generated plan: full #23 matrix or preliminary #22 proof")
    run.add_argument("--slot", type=int, required=True, help="1-based row in the selected plan")
    run.add_argument("--preflight", type=Path, required=True)
    run.add_argument("--arm-attempt-id", required=True, help="must exactly match the selected plan UUID")
    run.add_argument("--serial-port", required=True, help="explicit device path; never auto-discovered")
    run.add_argument("--baud", type=int, default=115200)
    run.add_argument("--device-ip", required=True, help="literal IPv4 address of the ESP32-S3")
    run.add_argument("--bind-ip", required=True, help="literal IPv4 LAN address for UDP observation")
    run.add_argument("--udp-port", type=int, default=3333)
    run.add_argument("--device-port", type=int, default=3333)
    run.add_argument("--output", type=Path, required=True, help="new private capture directory; existing paths are refused")
    run.add_argument("--http-log", type=Path, help="optional read-only local/ mounted append log for the isolated fixture server")
    run.add_argument("--pre-seconds", type=float, default=5)
    run.add_argument("--post-seconds", type=float, default=30)
    run.add_argument("--boot-wait-seconds", type=float, default=10)
    run.add_argument("--period-ms", type=float, default=100)
    run.add_argument("--timeout-ms", type=float, default=300, help="candidate observer timeout; not a frozen acceptance limit")
    run.add_argument("--max-sample-age-ms", type=int, default=1000)
    corr = sub.add_parser("correlate-http", help="offline correlate one run with read-only before/after CT log snapshots")
    corr.add_argument("--run-dir", type=Path, required=True)
    corr.add_argument("--before", type=Path, required=True)
    corr.add_argument("--after", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            if args.baud <= 0:
                raise HILInputError("baud must be positive")
            return _command_run(args)
        return _command_correlate_http(args)
    except (HILInputError, ResilienceFormatError, OSError, ValueError) as error:
        parser.exit(2, f"Issue #22 HIL runner failed: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
