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


def _normalize_attempt_id(attempt_id):
    if attempt_id is None or isinstance(attempt_id, uuid.UUID):
        return attempt_id
    if not isinstance(attempt_id, str):
        raise ValueError("attempt_id must be a canonical lowercase UUID")
    try:
        parsed = uuid.UUID(attempt_id)
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("attempt_id must be a canonical lowercase UUID") from error
    if str(parsed) != attempt_id:
        raise ValueError("attempt_id must be a canonical lowercase UUID")
    return parsed


class Observer:
    def __init__(self, log, peer, period_ns=100_000_000, timeout_ns=300_000_000,
                 initial_version=1, attempt_id=None, max_sample_age_ms=1000,
                 update_deadline_ns=None, post_window_ns=None,
                 stable_window_ns=None, minimum_stable_controls=0,
                 minimum_stable_telemetry=0):
        self.log = log
        self.peer = peer
        self.period_ns = period_ns
        self.timeout_ns = timeout_ns
        self.initial_version = initial_version
        self.max_sample_age_ms = max_sample_age_ms
        if (update_deadline_ns is None) != (post_window_ns is None):
            raise ValueError("update deadline and post window must be configured together")
        if update_deadline_ns is not None and (update_deadline_ns <= 0 or post_window_ns <= 0):
            raise ValueError("update deadline and post window must be positive")
        if stable_window_ns is not None and stable_window_ns <= 0:
            raise ValueError("stable window must be positive")
        if minimum_stable_controls < 0 or minimum_stable_telemetry < 0:
            raise ValueError("stable-window minima cannot be negative")
        self.update_deadline_duration_ns = update_deadline_ns
        self.post_window_ns = post_window_ns
        self.stable_window_ns = stable_window_ns
        self.minimum_stable_controls = minimum_stable_controls
        self.minimum_stable_telemetry = minimum_stable_telemetry
        self.attempt_id = _normalize_attempt_id(attempt_id)
        if self.attempt_id is None:
            self.attempt_id = uuid.uuid4()
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
        self.success_times_by_boot = defaultdict(list)
        self.telemetry_times = []
        self.telemetry_times_by_boot = defaultdict(list)
        self.last_sample = {}
        self.trigger = None
        self.trigger_not_sent_reason = None
        self.first_boot_seen_ns = None
        self.accepted_ns = None
        self.activated_ns = None
        self.deadline_ns = None
        self.deadline_expired_ns = None
        self.capture_end_ns = None
        self.pre_window = None

    def record(self, kind, now, **values):
        self.log.write(json.dumps({"host_monotonic_ns": now, "kind": kind, **values}) + "\n")
        self.log.flush()

    def event(self, kind, now, **values):
        self.counters[kind] += 1
        self.record(kind, now, **values)

    def transmit(self, udp, kind, body, now, planned_ns=None):
        if kind == MessageType.UPDATE_TRIGGER and self.boot_id is None:
            raise ProtocolError("cannot transmit UPDATE_TRIGGER before observing a boot_id")
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
        if self.boot_id is None:
            self.trigger_not_sent_reason = "no_boot_observed"
            self.event("trigger_not_sent", now, reason=self.trigger_not_sent_reason)
            return None
        packet, sent_ns = self.transmit(udp, MessageType.UPDATE_TRIGGER,
                               {"target_sequence": target_sequence, "scenario": scenario,
                                "component": 1, "target_version": 2}, now)
        self.trigger = {"packet": packet, "sent_ns": sent_ns, "expired": False, "response": False}
        if self.update_deadline_duration_ns is not None:
            self.deadline_ns = sent_ns + self.update_deadline_duration_ns

    def qualify_pre_window(self, now, session_start_ns):
        if self.stable_window_ns is None:
            return {"passed": True, "reason": "not_configured"}
        window_start = now - self.stable_window_ns
        controls = sum(window_start <= value <= now for value in self.success_times)
        telemetry = sum(window_start <= value <= now for value in self.telemetry_times)
        reasons = []
        if now - session_start_ns < self.stable_window_ns:
            reasons.append("window_too_short")
        if self.first_boot_seen_ns is None or self.first_boot_seen_ns > window_start:
            reasons.append("boot_not_stable_for_full_window")
        if len(self.boots) != 1:
            reasons.append("boot_changed_during_window")
        if controls < self.minimum_stable_controls:
            reasons.append("too_few_correct_controls")
        if telemetry < self.minimum_stable_telemetry:
            reasons.append("too_few_valid_telemetry_messages")
        result = {"passed": not reasons, "reason": "passed" if not reasons else ",".join(reasons),
                  "window_start_ns": window_start, "window_end_ns": now,
                  "stable_window_ns": self.stable_window_ns,
                  "correct_controls": controls, "minimum_correct_controls": self.minimum_stable_controls,
                  "valid_telemetry_messages": telemetry,
                  "minimum_valid_telemetry_messages": self.minimum_stable_telemetry,
                  "boot_ids": list(self.boots)}
        self.pre_window = result
        self.record("pre_window_check", now, **result)
        if not result["passed"]:
            self.event("pre_window_failed", now, reason=result["reason"])
            self.capture_end_ns = now
        return result

    def expire(self, now):
        for sample_id, request in self.requests.items():
            if not request["response"] and not request["expired"] and now > request["sent_ns"] + self.timeout_ns:
                request["expired"] = True
                self.event("control_timeout", now, sample_id=sample_id)
        if self.trigger and not self.trigger["response"] and not self.trigger["expired"] and now > self.trigger["sent_ns"] + self.timeout_ns:
            self.trigger["expired"] = True
            self.event("acceptance_timeout", now)
        if (self.deadline_ns is not None and now > self.deadline_ns
                and self.deadline_expired_ns is None):
            if self.activated_ns is None or self.activated_ns > self.deadline_ns:
                self.deadline_expired_ns = self.deadline_ns
                self.event("update_deadline_expired", now, deadline_ns=self.deadline_ns)
                if self.accepted_ns is not None:
                    self.event("update_timeout", now, deadline_ns=self.deadline_ns)
                self.capture_end_ns = self.deadline_ns + self.post_window_ns

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
                if self.first_boot_seen_ns is None:
                    self.first_boot_seen_ns = now
                self.event("boot_observed", now, boot_id=packet.boot_id)
            last = self.last_sample.get(packet.boot_id)
            sample = packet.body["sample_id"]
            if last and (sample < last[0] or (sample == last[0] and packet.body["temperature_mC"] != last[1])):
                self.event("telemetry_id_conflict", now)
                return
            self.last_sample[packet.boot_id] = (sample, packet.body["temperature_mC"])
            if packet.body["sensor_status"] != SensorStatus.VALID or packet.body["age_ms"] > self.max_sample_age_ms:
                self.event("telemetry_unavailable", now, boot_id=packet.boot_id,
                           sequence=packet.sequence, sample_id=sample,
                           age_ms=packet.body["age_ms"])
            else:
                self.telemetry_times.append(now)
                self.telemetry_times_by_boot[packet.boot_id].append(now)
                self.event("telemetry_valid", now, boot_id=packet.boot_id,
                           sequence=packet.sequence, sample_id=sample,
                           age_ms=packet.body["age_ms"])
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
            self.event("late_control", now, sample_id=packet.body["sample_id"], latency_ns=latency,
                       correct=correct, boot_id=packet.boot_id, module_version=version,
                       input_temperature_mC=request["packet"].body["input_temperature_mC"])
        elif not correct:
            self.event("incorrect_control", now, sample_id=packet.body["sample_id"],
                       expected_gpio=expected, boot_id=packet.boot_id, module_version=version,
                       input_temperature_mC=request["packet"].body["input_temperature_mC"],
                       requested_gpio=packet.body["requested_gpio"],
                       observed_gpio=packet.body["observed_gpio"])
        else:
            request["success"] = True
            self.success_times.append(now)
            self.success_by_boot[packet.boot_id] += 1
            self.success_times_by_boot[packet.boot_id].append(now)
            self.event("control_valid", now, sample_id=packet.body["sample_id"], latency_ns=latency,
                       boot_id=packet.boot_id, module_version=version,
                       input_temperature_mC=request["packet"].body["input_temperature_mC"],
                       requested_gpio=packet.body["requested_gpio"],
                       observed_gpio=packet.body["observed_gpio"])
            if self.accepted_ns is not None and version == 2 and self.activated_ns is None:
                self.activated_ns = now
                self.event("v2_observed", now)
                if self.deadline_ns is None or now <= self.deadline_ns:
                    self.event("update_success", now, trigger_to_v2_ns=now - self.trigger["sent_ns"])
                    if self.post_window_ns is not None:
                        self.capture_end_ns = now + self.post_window_ns
                else:
                    self.event("late_v2_recovery", now,
                               trigger_to_v2_ns=now - self.trigger["sent_ns"])

    def summary(self):
        def gap(times):
            return max((b - a for a, b in zip(times, times[1:])), default=None)
        control_gap = gap(self.success_times)
        return {
            "attempt_id": str(self.attempt_id), "boot_ids": self.boots,
            "period_ns": self.period_ns, "timeout_ns": self.timeout_ns,
            "initial_version": self.initial_version,
            "max_sample_age_ms": self.max_sample_age_ms,
            "update_deadline_duration_ns": self.update_deadline_duration_ns,
            "post_window_ns": self.post_window_ns,
            "stable_window_ns": self.stable_window_ns,
            "minimum_stable_controls": self.minimum_stable_controls,
            "minimum_stable_telemetry": self.minimum_stable_telemetry,
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
            "trigger_not_sent_reason": self.trigger_not_sent_reason,
            "update_deadline_ns": self.deadline_ns,
            "update_deadline_expired_ns": self.deadline_expired_ns,
            "capture_end_ns": self.capture_end_ns,
            "pre_window": self.pre_window,
            "update_success_within_deadline": (
                None if self.deadline_ns is None or self.accepted_ns is None
                else self.activated_ns is not None and self.activated_ns <= self.deadline_ns),
            "update_state": (("activated" if self.deadline_ns is None or self.activated_ns <= self.deadline_ns
                              else "late_activated") if self.activated_ns is not None else
                             "accepted_without_v2" if self.accepted_ns is not None else
                             "no_timely_acceptance" if self.trigger is not None else "not_triggered"),
            "update_total_ns": None if self.activated_ns is None else self.activated_ns - self.trigger["sent_ns"],
        }


def _not_triggered_update_state(observer):
    if observer.pre_window is not None and not observer.pre_window["passed"]:
        return "pre_window_failed"
    if observer.boot_id is None:
        return "trigger_not_sent_no_boot"
    return "trigger_not_sent"


def _trigger_eligible_ns(session_start_ns, trigger_after_seconds,
                         stable_window_ns, first_boot_seen_ns):
    eligible_ns = session_start_ns + round(trigger_after_seconds * 1_000_000_000)
    if stable_window_ns is not None and first_boot_seen_ns is not None:
        eligible_ns = max(eligible_ns, first_boot_seen_ns + stable_window_ns)
    return eligible_ns


def run(device_ip, output, seconds=10, bind_ip="0.0.0.0", port=3333,
        device_port=3333, period_ms=100, timeout_ms=300, initial_version=1,
        trigger_after=None, target_sequence=None, scenario=1, interface_index=None,
        max_sample_age_ms=1000, attempt_id=None, update_deadline_ms=None, post_window_ms=None,
        stable_window_ms=None, minimum_stable_controls=0,
        minimum_stable_telemetry=0, health_check=None, clock_domain=None):
    period_ns = round(period_ms * 1_000_000)
    timeout_ns = round(timeout_ms * 1_000_000)
    values = [seconds, period_ms, timeout_ms]
    values.extend(v for v in (update_deadline_ms, post_window_ms, stable_window_ms) if v is not None)
    if (not all(math.isfinite(v) and v > 0 for v in values)
            or period_ns <= 0 or timeout_ns <= 0 or initial_version not in (1, 2)):
        raise ValueError("duration, cadence and timeout must be positive; version must be 1 or 2")
    if (update_deadline_ms is None) != (post_window_ms is None):
        raise ValueError("update deadline and post window must be configured together")
    if (stable_window_ms is not None and trigger_after is not None
            and trigger_after * 1000 < stable_window_ms):
        raise ValueError("trigger cannot precede the complete stable window")
    if trigger_after is not None and (not math.isfinite(trigger_after) or not 0 <= trigger_after < seconds or not target_sequence or initial_version != 1):
        raise ValueError("trigger requires v1, a positive target sequence and a time within the run")
    if target_sequence is not None and (trigger_after is None or not 0 < target_sequence < 2**64):
        raise ValueError("target sequence requires a trigger and must fit positive uint64")
    if scenario not in (1, 2, 3) or not 0 <= port <= 65535 or not 1 <= device_port <= 65535 or not 0 <= max_sample_age_ms < 65535:
        raise ValueError("invalid scenario, UDP port or sample freshness limit")
    parsed_attempt_id = _normalize_attempt_id(attempt_id)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    with (output / "observations.jsonl").open("x", encoding="utf-8") as log, (output / "summary.json").open("x", encoding="utf-8") as summary_log, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        if interface_index is not None:
            if os.name != "nt":
                raise ValueError("interface-index is supported only on Windows")
            udp.setsockopt(socket.IPPROTO_IP, 31, socket.htonl(interface_index))
        udp.bind((bind_ip, port))
        udp.setblocking(False)
        observer = Observer(log, (socket.gethostbyname(device_ip), device_port), period_ns, timeout_ns, initial_version,
                            attempt_id=parsed_attempt_id,
                            max_sample_age_ms=max_sample_age_ms,
                            update_deadline_ns=(None if update_deadline_ms is None else
                                                round(update_deadline_ms * 1_000_000)),
                            post_window_ns=(None if post_window_ms is None else
                                            round(post_window_ms * 1_000_000)),
                            stable_window_ns=(None if stable_window_ms is None else
                                              round(stable_window_ms * 1_000_000)),
                            minimum_stable_controls=minimum_stable_controls,
                            minimum_stable_telemetry=minimum_stable_telemetry)
        start = time.monotonic_ns()
        end = start + round(seconds * 1e9)
        next_send = start
        observer.record("session_start", start, seconds=seconds, period_ns=period_ns,
                        clock_domain=clock_domain or {"name": "observer_local", "clock": "monotonic_ns"},
                        timeout_ns=timeout_ns, initial_version=initial_version,
                        max_sample_age_ms=max_sample_age_ms,
                        attempt_id=str(observer.attempt_id),
                        update_deadline_ms=update_deadline_ms,
                        post_window_ms=post_window_ms,
                        stable_window_ms=stable_window_ms,
                        minimum_stable_controls=minimum_stable_controls,
                        minimum_stable_telemetry=minimum_stable_telemetry,
                        local=list(udp.getsockname()), peer=list(observer.peer),
                        trigger_after=trigger_after, target_sequence=target_sequence, scenario=scenario)
        observer_error = None
        try:
            while True:
                now = time.monotonic_ns()
                failure = health_check() if health_check is not None else None
                if failure:
                    observer.event("instrument_abort", now, reason=str(failure))
                    break
                observer.expire(now)
                if (observer.first_boot_seen_ns is not None
                        and observer.stable_window_ns is not None
                        and observer.update_deadline_duration_ns is not None):
                    # Allow the complete frozen window and update/post budget after
                    # the first observed boot, even if boot reporting starts late.
                    end = max(end, observer.first_boot_seen_ns + observer.stable_window_ns
                              + observer.update_deadline_duration_ns + observer.post_window_ns)
                capture_end = min(end, observer.capture_end_ns) if observer.capture_end_ns is not None else end
                drain_end = capture_end + timeout_ns + 1_000_000
                if now >= drain_end:
                    break
                if now < capture_end and now >= next_send:
                    if observer.boot_id is not None:
                        observer.send_control(udp, now, next_send)
                    else:
                        observer.event("awaiting_boot", now)
                    # Never burst/retransmit to hide a scheduling delay.
                    skipped = max(0, (now - next_send) // period_ns)
                    if skipped:
                        observer.event("schedule_skipped", now, slots=skipped)
                    next_send += (skipped + 1) * period_ns
                trigger_eligible = (None if trigger_after is None else
                                    _trigger_eligible_ns(start, trigger_after,
                                                         observer.stable_window_ns,
                                                         observer.first_boot_seen_ns))
                # An UPDATE_TRIGGER always needs a boot identity, including
                # when a stable-window qualification is configured. Keep
                # waiting; a session without a boot ends trigger_not_sent_no_boot.
                if (now < capture_end and trigger_after is not None and observer.trigger is None
                        and observer.pre_window is None and now >= trigger_eligible
                        and observer.boot_id is not None):
                    qualification = observer.qualify_pre_window(now, start)
                    if qualification["passed"]:
                        # Check again at the dispatch boundary, including after
                        # writing the qualification evidence to disk.
                        failure = health_check() if health_check is not None else None
                        if failure:
                            observer.event("instrument_abort", time.monotonic_ns(), reason=str(failure))
                            break
                        observer.send_trigger(udp, now, target_sequence, scenario)
                wait_ns = min(drain_end, next_send if now < capture_end else drain_end) - time.monotonic_ns()
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
        except Exception as error:
            # Keep the cause and a summary on disk; the caller still sees the error.
            observer_error = error
            observer.event("observer_exception", time.monotonic_ns(),
                           error_type=type(error).__name__, error=str(error))
        observer.expire(time.monotonic_ns())
        if (trigger_after is not None and observer.trigger is None
                and observer.boot_id is None and observer.trigger_not_sent_reason is None):
            observer.trigger_not_sent_reason = "no_boot_observed"
            observer.event("trigger_not_sent", time.monotonic_ns(),
                           reason=observer.trigger_not_sent_reason)
        result = observer.summary()
        result["clock_domain"] = clock_domain or {"name": "observer_local", "clock": "monotonic_ns"}
        result["instrument_aborted"] = bool(observer.counters.get("instrument_abort"))
        result["observation_start_ns"] = start
        result["observation_end_ns"] = time.monotonic_ns()
        result["control_tail_censored_ns"] = (None if not observer.success_times else
                                               result["observation_end_ns"] - observer.success_times[-1])
        if trigger_after is not None and observer.trigger is None:
            result["update_state"] = _not_triggered_update_state(observer)
        result["observer_exception"] = (None if observer_error is None else
                                        {"type": type(observer_error).__name__,
                                         "message": str(observer_error)})
        observer.record("session_end", time.monotonic_ns(), summary=result)
        summary_log.write(json.dumps(result, indent=2) + "\n")
    if observer_error is not None:
        raise observer_error
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device-ip", required=True)
    parser.add_argument("--output", type=Path, required=True, help="capture directory; existing output files are refused")
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
    parser.add_argument("--attempt-id", help="optional canonical attempt UUID for resilience correlation")
    args = parser.parse_args()
    try:
        result = run(**vars(args))
    except (ValueError, OSError) as error:
        parser.exit(2, f"{error}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
