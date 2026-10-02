"""Functional UDP observer. Host timestamps are never compared to MCU clocks."""
import argparse
from collections import Counter, defaultdict
from dataclasses import asdict
import json
import math
import os
from pathlib import Path
import select
import socket
import time
import uuid

from observation_protocol_v1 import (
    AcceptanceStatus, ControlStatus, MessageType, Packet, ProtocolError,
    SensorStatus, decode, encode,
)


# All inputs are outside both hysteresis bands, so the oracle needs no inferred
# initial GPIO state. 27 C distinguishes v1 from v2.
STIMULI = (23000, 32000, 27000)


def packet_json(packet):
    value = asdict(packet)
    value["attempt_id"] = str(packet.attempt_id)
    value["message_type"] = packet.message_type.name
    return value


class Observer:
    def __init__(self, log, peer, period_ns=100_000_000, timeout_ns=300_000_000,
                 initial_version=1, attempt_id=None, max_sample_age_ms=1000):
        self.log = log
        self.peer = peer
        self.period_ns = period_ns
        self.timeout_ns = timeout_ns
        self.initial_version = initial_version
        self.max_sample_age_ms = max_sample_age_ms
        self.attempt_id = attempt_id or uuid.uuid4()
        self.boot_id = None
        self.retired_boots = set()
        self.boots = []
        self.seen = defaultdict(dict)
        self.highest = defaultdict(int)
        self.requests = {}
        self.counters = Counter()
        self.tx_sequence = Counter()
        self.success_times = []
        self.success_by_boot = Counter()
        self.telemetry_times = []
        self.last_sample = {}
        self.trigger = None
        self.accepted_ns = None
        self.activated_ns = None

    def record(self, kind, now, **values):
        self.log.write(json.dumps({"host_monotonic_ns": now, "kind": kind, **values}) + "\n")
        self.log.flush()

    def event(self, kind, now, **values):
        self.counters[kind] += 1
        self.record(kind, now, **values)

    def transmit(self, udp, kind, body, now, planned_ns=None):
        self.tx_sequence[kind] += 1
        packet = Packet(kind, self.attempt_id, self.tx_sequence[kind], self.boot_id,
                        now // 1000, body)
        raw = encode(packet)
        # Log intent and the outcome even if sendto fails.
        self.record("send_intent", now, planned_ns=planned_ns, peer=list(self.peer),
                    hex=raw.hex(), packet=packet_json(packet))
        sent_ns = time.monotonic_ns()
        try:
            udp.sendto(raw, self.peer)
        except OSError as error:
            self.event("send_error", time.monotonic_ns(), error=str(error))
        else:
            self.record("send", sent_ns, planned_ns=planned_ns,
                        completed_ns=time.monotonic_ns(), peer=list(self.peer), hex=raw.hex())
        return packet, sent_ns

    def send_control(self, udp, now, planned_ns):
        sample_id = len(self.requests) + 1
        temperature = STIMULI[(sample_id - 1) % len(STIMULI)]
        packet, sent_ns = self.transmit(udp, MessageType.CONTROL_REQUEST,
                               {"sample_id": sample_id, "input_temperature_mC": temperature},
                               now, planned_ns)
        self.requests[sample_id] = {"packet": packet, "sent_ns": sent_ns, "expired": False,
                                    "response": False, "success": False}

    def send_trigger(self, udp, now, target_sequence, scenario):
        packet, sent_ns = self.transmit(udp, MessageType.UPDATE_TRIGGER,
                               {"target_sequence": target_sequence, "scenario": scenario,
                                "component": 1, "target_version": 2}, now)
        self.trigger = {"packet": packet, "sent_ns": sent_ns, "expired": False, "response": False}

    def expire(self, now):
        for sample_id, request in self.requests.items():
            if not request["response"] and not request["expired"] and now > request["sent_ns"] + self.timeout_ns:
                request["expired"] = True
                self.event("control_timeout", now, sample_id=sample_id)
        if self.trigger and not self.trigger["response"] and not self.trigger["expired"] and now > self.trigger["sent_ns"] + self.timeout_ns:
            self.trigger["expired"] = True
            self.event("acceptance_timeout", now)

    def receive(self, raw, peer, now):
        self.record("receive", now, peer=list(peer), hex=raw.hex())
        if peer != self.peer:
            self.event("foreign_peer", now)
            return
        try:
            packet = decode(raw)
        except ProtocolError as error:
            self.event("malformed", now, error=str(error))
            return
        self.record("decoded", now, packet=packet_json(packet))
        if packet.message_type not in (MessageType.TELEMETRY, MessageType.CONTROL_RESULT, MessageType.UPDATE_ACCEPTANCE):
            self.event("wrong_direction", now)
            return
        key = (packet.boot_id, packet.message_type)
        previous = self.seen[key].get(packet.sequence)
        if previous is not None:
            self.event("duplicate" if previous == raw else "sequence_conflict", now)
            return
        self.seen[key][packet.sequence] = raw
        if packet.sequence < self.highest[key]:
            self.event("out_of_order", now)
            # A reordered result can still meet its own request deadline.
            # Telemetry must not move the latest sensor state backwards.
            if packet.message_type == MessageType.TELEMETRY:
                return
        if self.highest[key] and packet.sequence > self.highest[key] + 1:
            self.event("sequence_gap", now, boot_id=packet.boot_id,
                       message_type=packet.message_type.name,
                       first=self.highest[key] + 1, last=packet.sequence - 1)
        self.highest[key] = max(self.highest[key], packet.sequence)
        if packet.boot_id in self.retired_boots:
            self.event("retired_boot", now)
            return
        if packet.message_type == MessageType.TELEMETRY:
            if packet.boot_id != self.boot_id:
                if self.boot_id is not None:
                    self.retired_boots.add(self.boot_id)
                self.boot_id = packet.boot_id
                self.boots.append(packet.boot_id)
                self.event("boot_observed", now, boot_id=packet.boot_id)
            last = self.last_sample.get(packet.boot_id)
            sample = packet.body["sample_id"]
            if last and (sample < last[0] or (sample == last[0] and packet.body["temperature_mC"] != last[1])):
                self.event("telemetry_id_conflict", now)
                return
            self.last_sample[packet.boot_id] = (sample, packet.body["temperature_mC"])
            if packet.body["sensor_status"] != SensorStatus.VALID or packet.body["age_ms"] > self.max_sample_age_ms:
                self.event("telemetry_unavailable", now)
            else:
                self.telemetry_times.append(now)
                self.event("telemetry_valid", now)
            return
        if packet.attempt_id != self.attempt_id:
            self.event("wrong_attempt", now)
            return
        if packet.message_type == MessageType.UPDATE_ACCEPTANCE:
            request = self.trigger
            if (request is None or packet.boot_id != request["packet"].boot_id
                    or packet.body["request_sequence"] != request["packet"].sequence
                    or packet.body["target_sequence"] != request["packet"].body["target_sequence"]):
                self.event("uncorrelated_acceptance", now)
                return
            if request["response"]:
                self.event("duplicate_acceptance", now)
                return
            request["response"] = True
            if now - request["sent_ns"] > self.timeout_ns:
                self.event("late_acceptance", now)
            elif packet.body["status"] == AcceptanceStatus.ACCEPTED:
                self.accepted_ns = now
                self.event("update_accepted", now)
            else:
                self.event("update_rejected", now, reason=packet.body["reason"])
            return
        request = self.requests.get(packet.body["sample_id"])
        if request is None:
            self.event("unknown_control_id", now)
            return
        if packet.boot_id != request["packet"].boot_id or packet.boot_id != self.boot_id:
            self.event("wrong_control_boot", now)
            return
        if request["response"]:
            self.event("duplicate_control_id", now)
            return
        request["response"] = True
        latency = now - request["sent_ns"]
        version = packet.body["module_version"]
        allowed = {self.initial_version}
        if self.accepted_ns is not None:
            allowed.add(2)
        if self.activated_ns is not None:
            allowed = {2}
        threshold = {1: 25000, 2: 30000}[version]
        expected = int(request["packet"].body["input_temperature_mC"] > threshold)
        correct = (version in allowed and packet.body["status"] == ControlStatus.APPLIED
                   and packet.body["requested_gpio"] == expected
                   and packet.body["observed_gpio"] == expected)
        if latency > self.timeout_ns:
            self.event("late_control", now, sample_id=packet.body["sample_id"], latency_ns=latency, correct=correct)
        elif not correct:
            self.event("incorrect_control", now, sample_id=packet.body["sample_id"], expected_gpio=expected)
        else:
            request["success"] = True
            self.success_times.append(now)
            self.success_by_boot[packet.boot_id] += 1
            self.event("control_valid", now, sample_id=packet.body["sample_id"], latency_ns=latency,
                       boot_id=packet.boot_id, module_version=version)
            if self.accepted_ns is not None and version == 2 and self.activated_ns is None:
                self.activated_ns = now
                self.event("v2_observed", now)

    def summary(self):
        def gap(times):
            return max((b - a for a, b in zip(times, times[1:])), default=None)
        control_gap = gap(self.success_times)
        return {
            "attempt_id": str(self.attempt_id), "boot_ids": self.boots,
            "period_ns": self.period_ns, "timeout_ns": self.timeout_ns,
            "max_sample_age_ms": self.max_sample_age_ms,
            "sent_controls": len(self.requests),
            "valid_controls_by_boot": dict(self.success_by_boot),
            "lost_controls": sum(not r["success"] for r in self.requests.values()),
            "counters": dict(self.counters),
            "control_max_gap_ns": control_gap,
            "control_unavailability_estimate_ns": None if control_gap is None else max(0, control_gap - self.period_ns),
            "telemetry_max_gap_ns": gap(self.telemetry_times),
            "first_control_valid_ns": self.success_times[0] if self.success_times else None,
            "last_control_valid_ns": self.success_times[-1] if self.success_times else None,
            "last_telemetry_valid_ns": self.telemetry_times[-1] if self.telemetry_times else None,
            "unresolved_sequence_gaps": {
                f"{boot}:{kind.name}": maximum - min(self.seen[(boot, kind)]) + 1 - len(self.seen[(boot, kind)])
                for (boot, kind), maximum in self.highest.items()
            },
            "accepted_ns": self.accepted_ns, "activated_ns": self.activated_ns,
            "update_state": ("activated" if self.activated_ns is not None else
                             "accepted_without_v2" if self.accepted_ns is not None else
                             "no_timely_acceptance" if self.trigger is not None else "not_triggered"),
            "update_total_ns": None if self.activated_ns is None else self.activated_ns - self.trigger["sent_ns"],
        }


def run(device_ip, output, seconds=10, bind_ip="0.0.0.0", port=3333,
        device_port=3333, period_ms=100, timeout_ms=300, initial_version=1,
        trigger_after=None, target_sequence=None, scenario=1, interface_index=None,
        max_sample_age_ms=1000):
    period_ns = round(period_ms * 1_000_000)
    timeout_ns = round(timeout_ms * 1_000_000)
    if (not all(math.isfinite(v) and v > 0 for v in (seconds, period_ms, timeout_ms))
            or period_ns <= 0 or timeout_ns <= 0 or initial_version not in (1, 2)):
        raise ValueError("duration, cadence and timeout must be positive; version must be 1 or 2")
    if trigger_after is not None and (not math.isfinite(trigger_after) or not 0 <= trigger_after < seconds or not target_sequence or initial_version != 1):
        raise ValueError("trigger requires v1, a positive target sequence and a time within the run")
    if target_sequence is not None and (trigger_after is None or not 0 < target_sequence < 2**64):
        raise ValueError("target sequence requires a trigger and must fit positive uint64")
    if scenario not in (1, 2, 3) or not 0 <= port <= 65535 or not 1 <= device_port <= 65535 or not 0 <= max_sample_age_ms < 65535:
        raise ValueError("invalid scenario, UDP port or sample freshness limit")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    with (output / "observations.jsonl").open("x", encoding="utf-8") as log, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        if interface_index is not None:
            if os.name != "nt":
                raise ValueError("interface-index is supported only on Windows")
            udp.setsockopt(socket.IPPROTO_IP, 31, socket.htonl(interface_index))
        udp.bind((bind_ip, port))
        udp.setblocking(False)
        observer = Observer(log, (socket.gethostbyname(device_ip), device_port), period_ns, timeout_ns, initial_version,
                            max_sample_age_ms=max_sample_age_ms)
        start = time.monotonic_ns()
        end = start + round(seconds * 1e9)
        drain_end = end + timeout_ns + 1_000_000
        next_send = start
        observer.record("session_start", start, seconds=seconds, period_ns=period_ns,
                        timeout_ns=timeout_ns, initial_version=initial_version,
                        max_sample_age_ms=max_sample_age_ms,
                        local=list(udp.getsockname()), peer=list(observer.peer),
                        trigger_after=trigger_after, target_sequence=target_sequence, scenario=scenario)
        try:
            while time.monotonic_ns() < drain_end:
                now = time.monotonic_ns()
                observer.expire(now)
                if now < end and now >= next_send:
                    if observer.boot_id is not None:
                        observer.send_control(udp, now, next_send)
                    else:
                        observer.event("awaiting_boot", now)
                    # Never burst/retransmit to hide a scheduling delay.
                    skipped = max(0, (now - next_send) // period_ns)
                    if skipped:
                        observer.event("schedule_skipped", now, slots=skipped)
                    next_send += (skipped + 1) * period_ns
                if (now < end and trigger_after is not None and observer.trigger is None
                        and observer.boot_id is not None and now >= start + trigger_after * 1e9):
                    observer.send_trigger(udp, now, target_sequence, scenario)
                wait_ns = min(drain_end, next_send if now < end else drain_end) - time.monotonic_ns()
                readable, _, _ = select.select([udp], [], [], max(0, min(wait_ns / 1e9, 0.01)))
                if readable:
                    try:
                        raw, peer = udp.recvfrom(65535)
                    except OSError as error:
                        observer.event("receive_error", time.monotonic_ns(), error=str(error))
                    else:
                        observer.receive(raw, peer, time.monotonic_ns())
        except KeyboardInterrupt:
            observer.event("interrupted", time.monotonic_ns())
        observer.expire(time.monotonic_ns())
        result = observer.summary()
        result["observation_start_ns"] = start
        result["observation_end_ns"] = time.monotonic_ns()
        result["control_tail_censored_ns"] = (None if not observer.success_times else
                                               result["observation_end_ns"] - observer.success_times[-1])
        if trigger_after is not None and observer.trigger is None:
            result["update_state"] = "trigger_not_sent_no_boot"
        observer.record("session_end", time.monotonic_ns(), summary=result)
    (output / "summary.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device-ip", required=True)
    parser.add_argument("--output", type=Path, required=True, help="new directory or unused JSONL path")
    parser.add_argument("--seconds", type=float, default=10)
    parser.add_argument("--bind-ip", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=3333)
    parser.add_argument("--device-port", type=int, default=3333)
    parser.add_argument("--period-ms", type=float, default=100)
    parser.add_argument("--timeout-ms", type=float, default=300)
    parser.add_argument("--initial-version", type=int, choices=(1, 2), default=1)
    parser.add_argument("--trigger-after", type=float)
    parser.add_argument("--target-sequence", type=int)
    parser.add_argument("--scenario", type=int, choices=(1, 2, 3), default=1)
    parser.add_argument("--interface-index", type=int)
    parser.add_argument("--max-sample-age-ms", type=int, default=1000)
    args = parser.parse_args()
    try:
        result = run(**vars(args))
    except (ValueError, OSError) as error:
        parser.exit(2, f"{error}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
