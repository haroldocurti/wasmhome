#!/usr/bin/env python3
"""Opt-in UDP-only physical harness for native-service hysteresis issue #11.

No serial API is imported. Importing this module or running without --execute
opens no sockets and sends no packets.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
import socket
import sys
import time
import uuid

import observation_protocol_v1 as protocol_codec  # noqa: E402
from observation_protocol_v1 import (  # noqa: E402
    AcceptanceStatus, Component, ControlStatus, MessageType, Packet,
    RejectionReason, Scenario, SensorStatus, ProtocolError, decode, encode,
)

VERSION_THRESHOLD_MC = {1: 25_000, 2: 30_000}
SILENT_CONTROL_ID = 0x7FFF_FFFF_FFFF_FF00
MAX_SENSOR_AGE_MS = 500
REQUIRED_DISTINCT_SAMPLES = 5


class CheckFailure(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def packet_json(packet: Packet) -> dict:
    value = asdict(packet)
    value["message_type"] = packet.message_type.name
    value["attempt_id"] = str(packet.attempt_id)
    return value


class JSONLEvents:
    def __init__(self, path: Path):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self.stream = os.fdopen(fd, "w", encoding="utf-8", buffering=1)

    def write(self, kind: str, **data):
        entry = {"host_monotonic_ns": time.monotonic_ns(), "kind": kind, **data}
        self.stream.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")
        self.stream.flush()

    def close(self):
        self.stream.close()


class UDPTransport:
    def __init__(self, bind: tuple[str, int], peer: tuple[str, int]):
        self.peer = peer
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(bind)

    def send(self, datagram: bytes):
        self.socket.sendto(datagram, self.peer)

    def receive(self, timeout_seconds: float):
        self.socket.settimeout(max(0.001, timeout_seconds))
        try:
            return self.socket.recvfrom(2048)
        except socket.timeout:
            return None

    def close(self):
        self.socket.close()


class Issue11Harness:
    def __init__(self, transport, events: JSONLEvents, *, control_version: int,
                 boot_id: int | None, device_peer: tuple[str, int],
                 attempt_id: uuid.UUID | None = None, silence_ms: int = 300,
                 response_timeout_ms: int = 300, telemetry_wait_seconds: float = 8.0):
        if control_version not in (1, 2):
            raise ValueError("control version must be 1 or 2")
        self.transport = transport
        self.events = events
        self.control_version = control_version
        self.threshold = VERSION_THRESHOLD_MC[control_version]
        self.boot_id = boot_id
        self.device_peer = device_peer
        self.attempt_id = attempt_id or uuid.uuid4()
        self.update_attempt_id = uuid.uuid4()
        self.silence_ns = silence_ms * 1_000_000
        self.response_timeout_ns = response_timeout_ms * 1_000_000
        self.telemetry_wait_seconds = telemetry_wait_seconds
        self.tx_sequences = defaultdict(int)
        self.sample_id = 0
        self.control_result_sequence = 0
        self.acceptance_sequence = 0
        self.telemetry_samples: dict[int, dict] = {}
        self.telemetry_last_id = 0
        self.checks: list[dict] = []
        self.foreign_boot_count = 0

    def _record_check(self, name: str, passed: bool, **fields):
        item = {"name": name, "passed": bool(passed), **fields}
        self.checks.append(item)
        self.events.write("check", **item)
        if not passed:
            raise CheckFailure(name)

    def _next_sequence(self, message_type: MessageType) -> int:
        self.tx_sequences[message_type] += 1
        return self.tx_sequences[message_type]

    def _packet(self, message_type: MessageType, body: dict[str, int], *,
                attempt_id: uuid.UUID | None = None, boot_id: int | None = None) -> Packet:
        if self.boot_id is None and boot_id is None:
            raise CheckFailure("device boot ID must be discovered before sending stimuli")
        return Packet(
            message_type=message_type,
            attempt_id=attempt_id or self.attempt_id,
            sequence=self._next_sequence(message_type),
            boot_id=self.boot_id if boot_id is None else boot_id,
            monotonic_us=time.monotonic_ns() // 1000,
            body=body,
        )

    def _send(self, datagram: bytes, stage: str):
        try:
            packet = decode(datagram)
        except ProtocolError as error:
            self.events.write("send_encode_invalid", stage=stage, error=str(error), hex=datagram.hex())
            raise
        self.events.write("send_intent", stage=stage, peer=list(self.device_peer),
                          hex=datagram.hex(), packet=packet_json(packet))
        try:
            self.transport.send(datagram)
        except OSError as error:
            self.events.write("send_error", stage=stage, error=str(error))
            raise
        self.events.write("send_complete", stage=stage, peer=list(self.device_peer),
                          hex=datagram.hex(), packet=packet_json(packet))

    def _receive_packet(self, deadline_ns: int):
        while True:
            remaining = deadline_ns - time.monotonic_ns()
            if remaining <= 0:
                return None
            result = self.transport.receive(remaining / 1_000_000_000)
            if result is None:
                return None
            raw, peer = result
            self.events.write("receive_raw", peer=list(peer), hex=raw.hex())
            if tuple(peer) != self.device_peer:
                self.events.write("foreign_peer", peer=list(peer))
                continue
            try:
                packet = decode(raw)
            except ProtocolError as error:
                self.events.write("malformed_packet", peer=list(peer), error=str(error), hex=raw.hex())
                continue
            self.events.write("receive_decoded", peer=list(peer), packet=packet_json(packet), hex=raw.hex())
            if packet.message_type != MessageType.TELEMETRY and packet.boot_id != self.boot_id:
                self.foreign_boot_count += 1
                self.events.write("foreign_boot_packet", expected_boot_id=self.boot_id,
                                  received_boot_id=packet.boot_id,
                                  message_type=packet.message_type.name)
                continue
            if packet.message_type == MessageType.TELEMETRY:
                if self.boot_id is None:
                    self.boot_id = packet.boot_id
                    self.events.write("boot_discovered_from_telemetry", boot_id=self.boot_id)
                if packet.boot_id != self.boot_id:
                    self.foreign_boot_count += 1
                    self.events.write("foreign_boot_telemetry", expected_boot_id=self.boot_id,
                                      received_boot_id=packet.boot_id)
                    continue
                self._observe_telemetry(packet)
            return packet

    def _observe_telemetry(self, packet: Packet):
        body = packet.body
        valid = (body["sensor_status"] == SensorStatus.VALID and
                 body["sample_id"] > 0 and 0 <= body["age_ms"] <= MAX_SENSOR_AGE_MS)
        if body["sample_id"] and body["sample_id"] < self.telemetry_last_id:
            self.events.write("telemetry_id_regression", previous=self.telemetry_last_id,
                              sample_id=body["sample_id"])
            return
        self.telemetry_last_id = max(self.telemetry_last_id, body["sample_id"])
        if valid:
            self.telemetry_samples.setdefault(body["sample_id"], {
                "temperature_mC": body["temperature_mC"],
                "age_ms": body["age_ms"],
                "sensor_status": body["sensor_status"],
                "sequence": packet.sequence,
            })
            self.events.write("telemetry_fresh_distinct", sample_id=body["sample_id"],
                              temperature_mC=body["temperature_mC"], age_ms=body["age_ms"],
                              boot_id=packet.boot_id)
        else:
            self.events.write("telemetry_not_fresh_valid", sample_id=body["sample_id"],
                              temperature_mC=body["temperature_mC"], age_ms=body["age_ms"],
                              sensor_status=body["sensor_status"])

    def discover_boot(self, timeout_seconds: float = 10.0):
        deadline = time.monotonic_ns() + int(timeout_seconds * 1_000_000_000)
        while self.boot_id is None and time.monotonic_ns() < deadline:
            packet = self._receive_packet(deadline)
            if packet and packet.message_type == MessageType.TELEMETRY and self.boot_id:
                self._record_check("boot_discovered_before_stimuli", True,
                                   boot_id=f"{self.boot_id:016x}")
                return
        self._record_check("boot_discovered_before_stimuli", self.boot_id is not None)

    def _send_control(self, sample_id: int, temperature_mC: int, *, boot_id: int | None = None):
        packet = self._packet(MessageType.CONTROL_REQUEST,
                              {"sample_id": sample_id,
                               "input_temperature_mC": temperature_mC},
                              boot_id=boot_id)
        raw = encode(packet)
        self._send(raw, "control")
        return raw, packet

    def _wait_control(self, sample_id: int, timeout_ns: int):
        deadline = time.monotonic_ns() + timeout_ns
        while time.monotonic_ns() < deadline:
            packet = self._receive_packet(deadline)
            if packet is None:
                break
            if packet.message_type != MessageType.CONTROL_RESULT:
                continue
            if packet.sequence <= self.control_result_sequence:
                self.events.write("control_result_sequence_not_increasing",
                                  previous=self.control_result_sequence,
                                  received=packet.sequence, sample_id=packet.body["sample_id"])
                continue
            self.control_result_sequence = packet.sequence
            if packet.body["sample_id"] != sample_id:
                self.events.write("unmatched_control_result", requested_sample_id=sample_id,
                                  packet=packet_json(packet))
                continue
            if packet.attempt_id != self.attempt_id:
                self._record_check("control_result_attempt_correlation", False,
                                   sample_id=sample_id, attempt_id=str(packet.attempt_id))
            return packet
        return None

    def _expect_control(self, name: str, packet: Packet, sample_id: int,
                        expected_gpio: int):
        body = packet.body
        self._record_check(name + ".boot", packet.boot_id == self.boot_id,
                           expected=f"{self.boot_id:016x}", actual=f"{packet.boot_id:016x}")
        self._record_check(name + ".version", body["module_version"] == self.control_version,
                           expected=self.control_version, actual=body["module_version"])
        self._record_check(name + ".sample_id", body["sample_id"] == sample_id,
                           expected=sample_id, actual=body["sample_id"])
        self._record_check(name + ".gpio", body["requested_gpio"] == expected_gpio and
                           body["observed_gpio"] == expected_gpio and
                           body["status"] == ControlStatus.APPLIED,
                           expected_gpio=expected_gpio, requested_gpio=body["requested_gpio"],
                           observed_gpio=body["observed_gpio"], status=body["status"])

    def _control_step(self, name: str, temperature_mC: int, expected_gpio: int):
        self.sample_id += 1
        sid = self.sample_id
        _, request = self._send_control(sid, temperature_mC)
        response = self._wait_control(sid, self.response_timeout_ns)
        self._record_check(name + ".response", response is not None, sample_id=sid)
        self._expect_control(name, response, sid, expected_gpio)
        return request, response

    def _expect_silent_control(self, name: str, sample_id: int,
                               temperature_mC: int, request_boot_id: int):
        watermark = self.control_result_sequence
        self._send_control(sample_id, temperature_mC, boot_id=request_boot_id)
        deadline = time.monotonic_ns() + self.silence_ns
        unexpected = None
        while time.monotonic_ns() < deadline:
            packet = self._receive_packet(deadline)
            if packet and packet.message_type == MessageType.CONTROL_RESULT and \
                    packet.body["sample_id"] == sample_id and packet.sequence > watermark:
                unexpected = packet
                break
        self._record_check(name + ".no_response", unexpected is None,
                           sample_id=sample_id,
                           unexpected_sequence=None if unexpected is None else unexpected.sequence)

    def _test_replay(self, raw: bytes, original_response: Packet, sample_id: int):
        watermark = self.control_result_sequence
        self._send(raw, "exact_datagram_replay")
        replay = self._wait_control(sample_id, self.response_timeout_ns)
        self._record_check("replay.response", replay is not None, sample_id=sample_id)
        self._record_check("replay.new_device_sequence", replay.sequence > watermark,
                           previous=watermark, replay_sequence=replay.sequence)
        self._record_check("replay.same_body_and_correlation",
                           replay.body == original_response.body and
                           replay.attempt_id == original_response.attempt_id and
                           replay.boot_id == original_response.boot_id,
                           original=original_response.body, replay=replay.body)

    def _test_busy_trigger(self):
        target_sequence = 1
        packet = self._packet(
            MessageType.UPDATE_TRIGGER,
            {"target_sequence": target_sequence, "scenario": Scenario.NATIVE,
             "component": Component.CONTROL, "target_version": 2},
            attempt_id=self.update_attempt_id,
        )
        self._send(encode(packet), "busy_update_trigger")
        deadline = time.monotonic_ns() + self.response_timeout_ns
        response = None
        while time.monotonic_ns() < deadline:
            item = self._receive_packet(deadline)
            if item is None:
                break
            if item.message_type != MessageType.UPDATE_ACCEPTANCE:
                continue
            if item.sequence <= self.acceptance_sequence:
                self.events.write("acceptance_sequence_not_increasing",
                                  previous=self.acceptance_sequence, received=item.sequence)
                continue
            self.acceptance_sequence = item.sequence
            if item.attempt_id == self.update_attempt_id and \
                    item.body["request_sequence"] == packet.sequence:
                response = item
                break
        self._record_check("busy_trigger.response", response is not None)
        if response is None:
            return
        self._record_check("busy_trigger.boot", response.boot_id == self.boot_id)
        self._record_check("busy_trigger.rejected_busy",
                           response.body["status"] == AcceptanceStatus.REJECTED and
                           response.body["reason"] == RejectionReason.BUSY and
                           response.body["target_sequence"] == target_sequence,
                           body=response.body)

    def run(self):
        if self.boot_id is None:
            self.discover_boot()
        if self.boot_id is None:
            raise CheckFailure("device boot identity was not observed before stimuli")

        t = self.threshold
        self.events.write("stimulus_matrix", boot_id=f"{self.boot_id:016x}",
                          control_version=self.control_version, threshold_mC=t,
                          vectors=[
                              {"name": "initial_inside_band_low", "temperature_mC": t, "expected_gpio": 0},
                              {"name": "upper_inclusive_edge", "temperature_mC": t + 500, "expected_gpio": 1},
                              {"name": "inside_band_after_high", "temperature_mC": t + 250, "expected_gpio": 1},
                              {"name": "lower_inclusive_edge", "temperature_mC": t - 500, "expected_gpio": 0},
                              {"name": "inside_band_after_low", "temperature_mC": t - 250, "expected_gpio": 0},
                          ])

        # First operation is an interior-band request; the firmware starts GPIO low.
        raw, first_request = self._send_control(1, t)
        first = self._wait_control(1, self.response_timeout_ns)
        self._record_check("initial_inside_band_low.response", first is not None)
        self._expect_control("initial_inside_band_low", first, 1, 0)
        self.sample_id = 1
        self._test_replay(raw, first, 1)

        self._control_step("upper_inclusive_edge", t + 500, 1)
        self._control_step("inside_band_after_high", t + 250, 1)

        # This high unadmitted sample ID would poison monotonic admission if the
        # platform failed to reject an old-boot packet before service dispatch.
        wrong_boot = self.boot_id ^ 1
        if wrong_boot == 0:
            wrong_boot = 2
        self._expect_silent_control("wrong_boot", SILENT_CONTROL_ID, t - 500,
                                    wrong_boot)
        self._control_step("inside_band_after_wrong_boot_high", t + 250, 1)

        # Reuse a cached ID with a different temperature while output is high.
        conflict_id = 2
        self._expect_silent_control("conflicting_sample_id", conflict_id, t - 500,
                                    self.boot_id)
        self._control_step("inside_band_after_conflict_high", t + 250, 1)

        self._control_step("lower_inclusive_edge", t - 500, 0)
        self._control_step("inside_band_after_low", t - 250, 0)
        self._test_busy_trigger()

        deadline = time.monotonic_ns() + int(self.telemetry_wait_seconds * 1_000_000_000)
        while len(self.telemetry_samples) < REQUIRED_DISTINCT_SAMPLES and time.monotonic_ns() < deadline:
            self._receive_packet(deadline)
        self._record_check("five_distinct_fresh_ds18b20_samples",
                           len(self.telemetry_samples) >= REQUIRED_DISTINCT_SAMPLES,
                           distinct_valid_samples=len(self.telemetry_samples),
                           required=REQUIRED_DISTINCT_SAMPLES,
                           max_age_ms=MAX_SENSOR_AGE_MS)
        self._record_check("no_foreign_boot_packets", self.foreign_boot_count == 0,
                           count=self.foreign_boot_count)
        return self.summary()

    def summary(self):
        return {
            "schema": "wasmhome-issue11-harness-result-v1",
            "outcome": "passed" if all(item["passed"] for item in self.checks) else "failed",
            "control_version": self.control_version,
            "boot_id": None if self.boot_id is None else f"{self.boot_id:016x}",
            "threshold_mC": self.threshold,
            "checks": self.checks,
            "distinct_valid_ds18b20_samples": len(self.telemetry_samples),
            "telemetry_sample_ids": sorted(self.telemetry_samples),
            "foreign_boot_packets": self.foreign_boot_count,
            "claims": {
                "gpio_observed": "UDP CONTROL_RESULT GPIO loopback value reported by firmware",
                "not_claimed": ["direct oscilloscope measurement", "callback non-execution on conflict",
                                "firmware provenance from this host script"],
            },
        }


def build_manifest(args, boot_id: int | None, peer: tuple[str, int], bind: tuple[str, int]):
    def artifact(path_arg):
        if path_arg is None:
            return None
        path = Path(path_arg).resolve(strict=True)
        return {"name": path.name, "sha256": sha256_file(path), "size_bytes": path.stat().st_size}

    evidence = None
    if args.boot_evidence:
        evidence_path = Path(args.boot_evidence).resolve(strict=True)
        evidence = {"name": evidence_path.name, "sha256": sha256_file(evidence_path)}
    return {
        "schema": "wasmhome-issue11-harness-manifest-v1",
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_revision": args.source_revision,
        "harness_sha256": sha256_file(Path(__file__).resolve()),
        "codec_sha256": sha256_file(Path(protocol_codec.__file__).resolve()),
        "firmware_control_version": args.control_version,
        "boot_id": None if boot_id is None else f"{boot_id:016x}",
        "boot_id_source": ("telemetry_discovery_before_stimuli" if boot_id is None
                           else "matching_boot_observed_line_identity_only"),
        "fresh_boot_required": True,
        "fresh_boot_verified_by_harness": False,
        "boot_evidence_validation": (
            "identity match only; reset status and log recency are not verified by this utility"
        ),
        "boot_evidence": evidence,
        "artifacts": {"elf": artifact(args.elf), "bin": artifact(args.bin),
                      "sdkconfig_sha256": sha256_file(Path(args.sdkconfig).resolve(strict=True))
                      if args.sdkconfig else None},
        "transport": {"bind": list(bind), "device_peer": list(peer), "device_port": peer[1]},
        "timeouts_ms": {"control_response": args.response_timeout_ms,
                         "silent_rejection": args.silence_ms,
                         "telemetry_wait_seconds": args.telemetry_wait_seconds,
                         "max_telemetry_age": MAX_SENSOR_AGE_MS},
        "control_stimuli": [
            {"name": "initial_inside_band_low", "mC": VERSION_THRESHOLD_MC[args.control_version], "gpio": 0},
            {"name": "upper_inclusive_edge", "mC": VERSION_THRESHOLD_MC[args.control_version] + 500, "gpio": 1},
            {"name": "inside_band_after_high", "mC": VERSION_THRESHOLD_MC[args.control_version] + 250, "gpio": 1},
            {"name": "lower_inclusive_edge", "mC": VERSION_THRESHOLD_MC[args.control_version] - 500, "gpio": 0},
            {"name": "inside_band_after_low", "mC": VERSION_THRESHOLD_MC[args.control_version] - 250, "gpio": 0},
        ],
        "device_actions": {
            "gpio_control_requests": (
                "UDP CONTROL_REQUEST datagrams intentionally exercise the firmware GPIO control path"
            ),
            "exact_replay": (
                "replayed admitted sample ID expects the cached result; it does not request a new GPIO application"
            ),
            "update_trigger": (
                "one UDP UPDATE_TRIGGER requests and checks a correlated BUSY rejection"
            ),
            "flash_or_ota_write_request": False,
        },
    }


def positive_boot_id(value: str) -> int:
    parsed = int(value, 0)
    if not 0 < parsed < 2**64:
        raise argparse.ArgumentTypeError("boot ID must be a nonzero uint64")
    return parsed


def verify_boot_evidence(path: Path, boot_id: int):
    expected = f"{boot_id:016x}"
    found = False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        marker = "WASMHOME_EVENT,"
        if marker not in line:
            continue
        try:
            event = json.loads(line.split(marker, 1)[1])
        except json.JSONDecodeError:
            continue
        if event.get("event") == "boot_observed" and str(event.get("boot_id", "")).lower() == expected:
            found = True
            break
    if not found:
        raise ValueError("boot evidence does not contain the requested boot_observed identity")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="open UDP socket and send the physical test requests")
    parser.add_argument("--device-ip")
    parser.add_argument("--bind-ip")
    parser.add_argument("--device-port", type=int, default=3333)
    parser.add_argument("--bind-port", type=int, default=3333)
    parser.add_argument("--control-version", type=int, choices=(1, 2), required=True)
    boot = parser.add_mutually_exclusive_group()
    boot.add_argument("--boot-id", type=positive_boot_id)
    boot.add_argument("--discover-boot", action="store_true")
    parser.add_argument("--boot-evidence", help="serial startup log used to obtain --boot-id; only its name and SHA-256 are recorded")
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--elf", required=True)
    parser.add_argument("--bin", required=True)
    parser.add_argument("--sdkconfig", help="optional config input; only SHA-256 is recorded")
    parser.add_argument("--output", required=True, help="new private output directory; existing paths are refused")
    parser.add_argument("--response-timeout-ms", type=int, default=300)
    parser.add_argument("--silence-ms", type=int, default=300)
    parser.add_argument("--telemetry-wait-seconds", type=float, default=8.0)
    args = parser.parse_args(argv)

    if not args.execute:
        print(json.dumps({"mode": "plan_only", "would_open_socket": False,
                          "would_send_udp": False, "control_version": args.control_version,
                          "threshold_mC": VERSION_THRESHOLD_MC[args.control_version],
                          "required_distinct_fresh_samples": REQUIRED_DISTINCT_SAMPLES}, indent=2))
        return 0
    if not args.device_ip or not args.bind_ip:
        parser.error("--execute requires explicit --device-ip and --bind-ip")
    if (args.boot_id is None) == (not args.discover_boot):
        parser.error("choose exactly one of --boot-id (serial evidence) or --discover-boot")
    if args.boot_id is not None and not args.boot_evidence:
        parser.error("--boot-id requires --boot-evidence from the device startup log")
    if args.discover_boot and args.boot_evidence:
        parser.error("--discover-boot cannot be combined with --boot-evidence")
    if not (1 <= args.device_port <= 65535 and 1 <= args.bind_port <= 65535):
        parser.error("UDP ports must be in 1..65535")
    if (args.response_timeout_ms <= 0 or args.silence_ms <= 0 or
            not math.isfinite(args.telemetry_wait_seconds) or
            not 0 < args.telemetry_wait_seconds <= 120):
        parser.error("timeouts must be positive and telemetry wait must be finite and at most 120 seconds")

    out = Path(args.output).expanduser().resolve()
    if out.exists():
        parser.error(f"refusing to overwrite output path: {out}")
    # Validate and hash all inputs before binding or transmitting.
    for required in (args.elf, args.bin):
        Path(required).resolve(strict=True)
    if args.sdkconfig:
        Path(args.sdkconfig).resolve(strict=True)
    if args.boot_evidence:
        evidence_path = Path(args.boot_evidence).resolve(strict=True)
        verify_boot_evidence(evidence_path, args.boot_id)
    peer = (args.device_ip, args.device_port)
    bind = (args.bind_ip, args.bind_port)
    try:
        socket.inet_pton(socket.AF_INET, peer[0])
        socket.inet_pton(socket.AF_INET, bind[0])
    except OSError as error:
        parser.error(f"IPv4 endpoint is invalid: {error}")

    out.mkdir(mode=0o700, parents=False)
    os.chmod(out, 0o700)
    manifest = build_manifest(args, args.boot_id, peer, bind)
    manifest_path = out / "manifest.json"
    manifest_fd = os.open(manifest_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(manifest_fd, "w", encoding="utf-8") as stream:
        json.dump(manifest, stream, indent=2, sort_keys=True)
        stream.write("\n")

    events = JSONLEvents(out / "events.jsonl")
    transport = None
    harness = None
    failure = None
    try:
        transport = UDPTransport(bind, peer)
        harness = Issue11Harness(
            transport, events, control_version=args.control_version,
            boot_id=args.boot_id, device_peer=peer,
            response_timeout_ms=args.response_timeout_ms,
            silence_ms=args.silence_ms,
            telemetry_wait_seconds=args.telemetry_wait_seconds,
        )
        summary = harness.run()
    except (Exception, KeyboardInterrupt) as error:  # Preserve failures and operator interrupts.
        failure = f"{type(error).__name__}: {error}"
        events.write("run_failed", error=failure)
        summary = harness.summary() if harness else {
            "schema": "wasmhome-issue11-harness-result-v1",
            "outcome": "failed", "checks": [],
            "distinct_valid_ds18b20_samples": 0,
            "telemetry_sample_ids": [], "foreign_boot_packets": 0,
        }
        summary["outcome"] = "failed"
        summary["failure"] = failure
    finally:
        try:
            if transport:
                transport.close()
        finally:
            events.close()
    manifest["boot_id"] = (None if harness is None or harness.boot_id is None
                           else f"{harness.boot_id:016x}")
    if args.discover_boot and harness is not None and harness.boot_id is not None:
        manifest["boot_id_source"] = "telemetry_discovery_before_stimuli"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                             encoding="utf-8")
    os.chmod(manifest_path, 0o600)
    summary_path = out / "result.json"
    summary_fd = os.open(summary_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(summary_fd, "w", encoding="utf-8") as stream:
        json.dump(summary, stream, indent=2, sort_keys=True)
        stream.write("\n")
    checksum_path = out / "SHA256SUMS"
    checksum_fd = os.open(checksum_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(checksum_fd, "w", encoding="utf-8") as stream:
        for path in (manifest_path, out / "events.jsonl", summary_path):
            stream.write(f"{sha256_file(path)}  {path.name}\n")
    print(json.dumps({"output": str(out), "outcome": summary["outcome"],
                      "checks": len(summary["checks"]),
                      "fresh_distinct_telemetry": summary["distinct_valid_ds18b20_samples"]}, indent=2))
    return 1 if failure or summary["outcome"] != "passed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
