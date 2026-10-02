"""Calibrate the external observer against a loopback UDP functional fixture."""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import select
import socket
import threading
import time
import uuid

from external_observer import run
from observation_protocol_v1 import MessageType, Packet, ProtocolError, decode, encode


class Fixture:
    def __init__(self, sock, log, outage_ms, period_ms, reboot=True):
        self.sock = sock
        self.log = log
        self.outage_ns = round(outage_ms * 1e6)
        self.period_ns = round(period_ms * 1e6)
        self.reboot = reboot
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.error = None
        self.boundaries = {}

    def record(self, kind, now, **values):
        self.log.write(json.dumps({"host_monotonic_ns": now, "kind": kind, **values}) + "\n")
        self.log.flush()

    def serve(self, peer):
        try:
            self._serve(peer)
        except Exception as error:
            self.error = error
            self.ready.set()

    def _serve(self, peer):
        start = time.monotonic_ns()
        # A full second of baseline, plus the known controlled silence.
        silence_start = start + 1_000_000_000
        silence_end = silence_start + self.outage_ns
        self.boundaries = {"start_ns": silence_start, "end_ns": silence_end,
                           "known_duration_ns": self.outage_ns}
        self.record("configured_silence", start, **self.boundaries)
        boot = 101
        seq = Counter()
        version = 1
        next_telemetry = start
        self.ready.set()
        while not self.stop.is_set():
            now = time.monotonic_ns()
            silent = silence_start <= now < silence_end
            if now >= silence_end and boot == 101 and self.reboot:
                boot = 102
                seq.clear()
                self.record("fixture_reboot", now, boot_id=boot)
            if now >= next_telemetry:
                if not silent:
                    seq[MessageType.TELEMETRY] += 1
                    sample = (now - start) // 500_000_000 + 1
                    packet = Packet(MessageType.TELEMETRY, uuid.UUID(int=0),
                                    seq[MessageType.TELEMETRY], boot, now // 1000,
                                    {"sample_id": sample, "temperature_mC": 25250,
                                     "age_ms": 100, "sensor_status": 0})
                    self.sock.sendto(encode(packet), peer)
                    self.record("send", now, hex=encode(packet).hex())
                next_telemetry = now + self.period_ns
            readable, _, _ = select.select([self.sock], [], [], 0.002)
            if not readable:
                continue
            try:
                raw, sender = self.sock.recvfrom(65535)
            except ConnectionResetError as error:
                # The observer may not have bound yet when the first packet left.
                self.record("icmp_unreachable", time.monotonic_ns(), error=str(error))
                continue
            now = time.monotonic_ns()
            self.record("receive", now, hex=raw.hex(), peer=list(sender))
            if silence_start <= now < silence_end:
                self.record("silence_drop", now)
                continue
            try:
                packet = decode(raw)
            except ProtocolError:
                continue
            if packet.boot_id != boot:
                self.record("wrong_boot_drop", now)
                continue
            if packet.message_type == MessageType.CONTROL_REQUEST:
                kind = MessageType.CONTROL_RESULT
                gpio = int(packet.body["input_temperature_mC"] > {1: 25000, 2: 30000}[version])
                body = {"sample_id": packet.body["sample_id"], "module_version": version,
                        "requested_gpio": gpio, "observed_gpio": gpio, "status": 0}
            elif packet.message_type == MessageType.UPDATE_TRIGGER:
                kind = MessageType.UPDATE_ACCEPTANCE
                body = {"request_sequence": packet.sequence,
                        "target_sequence": packet.body["target_sequence"], "status": 1, "reason": 0}
                version = 2
            else:
                continue
            seq[kind] += 1
            response = Packet(kind, packet.attempt_id, seq[kind], boot, now // 1000, body)
            data = encode(response)
            self.sock.sendto(data, sender)
            self.record("send", now, hex=data.hex())


def calibrate(output, outage_ms=1000, period_ms=100, reboot=True):
    if not all(math.isfinite(v) and v > 0 for v in (outage_ms, period_ms)) or outage_ms < period_ms * 2:
        raise ValueError("silence must span at least two positive observation periods")
    # Discovering a new boot can add a telemetry period before the next probe.
    resolution = round(period_ms * 1e6) * (2 if reboot else 1)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    # Reserve both loopback ports before starting either participant.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as device, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reservation, (output / "fixture.jsonl").open("x", encoding="utf-8") as log:
        device.bind(("127.0.0.1", 0))
        reservation.bind(("127.0.0.1", 0))
        observer_port = reservation.getsockname()[1]
        reservation.close()
        fixture = Fixture(device, log, outage_ms, period_ms, reboot)
        thread = threading.Thread(target=fixture.serve, args=(("127.0.0.1", observer_port),), daemon=True)
        thread.start()
        try:
            if not fixture.ready.wait(5):
                raise RuntimeError("fixture failed to start")
            summary = run("127.0.0.1", output, seconds=3 + outage_ms / 1000,
                          bind_ip="127.0.0.1", port=observer_port,
                          device_port=device.getsockname()[1], period_ms=period_ms,
                          trigger_after=0.5, target_sequence=2)
        finally:
            fixture.stop.set()
            thread.join(5)
        if thread.is_alive():
            raise RuntimeError("fixture failed to stop")
        if fixture.error:
            raise fixture.error
    estimated = summary["control_unavailability_estimate_ns"]
    known = fixture.boundaries["known_duration_ns"]
    # The reboot run first needs a fresh telemetry packet to address the new
    # boot, then the next scheduled control probe. Without a reboot, one probe
    # period bounds the sampling error. These are declared before the run.
    error = None if estimated is None else abs(estimated - known)
    result = {"purpose": "host UDP calibration; no MCU or OTA evidence",
              "known_duration_ns": known, "estimated_duration_ns": estimated,
              "absolute_error_ns": error, "resolution_ns": resolution,
              "reboot_simulated": reboot,
              "passed": (error is not None and error <= resolution
                         and summary["lost_controls"] > 0
                         and summary["accepted_ns"] is not None
                         and summary["activated_ns"] is not None
                         and summary["counters"].get("incorrect_control", 0) == 0
                         and len(summary["boot_ids"]) == (2 if reboot else 1))}
    (output / "calibration.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--outage-ms", type=float, default=1000)
    parser.add_argument("--period-ms", type=float, default=100)
    parser.add_argument("--no-reboot", action="store_true")
    args = parser.parse_args()
    result = calibrate(args.output, args.outage_ms, args.period_ms, not args.no_reboot)
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
