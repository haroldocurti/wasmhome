"""Session raw capture. Positive acceptance still requires the paired LXC log."""
import argparse
import json
from pathlib import Path
import re
import socket
import sys
import time

import serial

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
from observation_protocol_v1 import decode, MessageType, ProtocolError

DEVICE = "192.168.0.145"
SERVER = "192.168.0.138"
OBSERVER = "192.168.0.190"


def summarize(records, expected=None):
    commands = [r for r in records if r["kind"] == "serial_command_sent"]
    serial_boots = [r for r in records if r["kind"] == "WASMHOME_EVENT"
                    and r["value"].get("event") == "boot_observed"]
    results = [r for r in records if r["kind"] == "WASMHOME_LXC"]
    udp = [r for r in records if r["kind"] == "udp_received"
           and r["value"].get("message_type") == int(MessageType.TELEMETRY)
           and r["value"].get("peer") == [DEVICE, 3333]]
    reset_events = [r for r in records if r["kind"] == "host_reset_released"]
    fresh = []
    before, after = [], []
    serial_boot_id = None
    if len(commands) == 1:
        sent = commands[0]["host_monotonic_ns"]
        fresh = [r for r in results if r["host_monotonic_ns"] > sent]
        if len(fresh) == 1:
            reply = fresh[0]["host_monotonic_ns"]
            before = [r for r in udp if r["host_monotonic_ns"] < sent]
            after = [r for r in udp if r["host_monotonic_ns"] > reply]
    if len(serial_boots) == 1:
        try:
            serial_boot_id = int(serial_boots[0]["value"]["boot_id"], 16)
        except (KeyError, TypeError, ValueError):
            pass
    ordered_boot = bool(len(reset_events) == 1 and len(serial_boots) == 1 and len(commands) == 1
                        and commands[0]["value"].get("command") == "WH_LXC"
                        and reset_events[0]["host_monotonic_ns"] < serial_boots[0]["host_monotonic_ns"]
                        < commands[0]["host_monotonic_ns"])
    same_boot = bool(serial_boot_id and udp and all(r["value"]["boot_id"] == serial_boot_id for r in udp))
    before_sequences = {r["value"]["sequence"] for r in before}
    after_sequences = {r["value"]["sequence"] for r in after}
    advancing = bool(before_sequences and after_sequences and max(before_sequences) < min(after_sequences))
    continuity = ordered_boot and same_boot and len(before_sequences) >= 3 and len(after_sequences) >= 4 and advancing
    result = fresh[0]["value"] if len(fresh) == 1 else {}
    nonce_valid = bool(isinstance(result.get("nonce"), str)
                       and re.fullmatch(r"[0-9a-f]{64}", result["nonce"]))
    configured_endpoint = result.get("server_ipv4") == SERVER and result.get("server_port") == 18080
    coherent_source = result.get("local_ipv4") in ("", DEVICE)
    transcript_checked = bool(continuity and len(results) == 1 and len(fresh) == 1
                              and nonce_valid and configured_endpoint and coherent_source)
    positive = bool(transcript_checked and result.get("result") == "ok"
                    and result.get("local_ipv4") == DEVICE)
    failed_attempt = bool(transcript_checked and result.get("result") in ("socket_error", "timeout"))
    classification = ("positive_transcript_pending_server_pairing" if positive else
                      "transport_attempt_failed" if failed_attempt else "inconclusive")
    return {"purpose": "platform TCP diagnostics, not campaign service evidence",
            "host_clock": "time.perf_counter_ns, monotonic high-resolution counter; Windows QPC",
            "host_clock_resolution_seconds": time.get_clock_info("perf_counter").resolution,
            "expected": expected, "instrumental_transcript_checked": transcript_checked,
            "local_capture_checked": positive, "classification": classification,
            "expected_result_confirmed": expected == "success" and positive,
            "negative_condition_confirmed": False,
            "negative_condition_note": "socket_error/timeout do not prove reader absence; errno is unavailable",
            "server_correlation": "pending paired LXC log verification",
            "explicit_resets": len(reset_events), "serial_boot_events": len(serial_boots),
            "serial_boot_id": serial_boot_id, "udp_packets": len(udp),
            "udp_before_command": len(before), "udp_after_result": len(after),
            "boot_continuity_checked": continuity,
            "boot_ids": sorted({r["value"]["boot_id"] for r in udp}),
            "probe_results": [r["value"] for r in results]}


def capture(output, expected=None, seconds=20):
    # Atomic reservation precedes UART/socket access; never truncate prior evidence.
    output.mkdir(parents=True, exist_ok=False)
    records, pending = [], b""
    sent = False
    uart = serial.Serial()
    uart.port, uart.baudrate, uart.timeout = "COM12", 115200, 0.01
    uart.dtr = uart.rts = False
    with (output / "serial.log").open("xb") as raw, \
            (output / "pre-reset-serial.log").open("xb") as pre_reset, \
            (output / "observations.jsonl").open("x", encoding="utf-8") as log, \
            socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        def record(kind, value):
            # QPC avoids the 15 ms timestamp ties of monotonic_ns on Windows Python 3.11.
            entry = {"host_monotonic_ns": time.perf_counter_ns(), "kind": kind, "value": value}
            records.append(entry)
            log.write(json.dumps(entry) + "\n")
            log.flush()
            return entry

        udp.bind((OBSERVER, 3333))
        udp.setblocking(False)
        with uart:
            # Preserve already buffered data separately, before the explicit reset.
            drain_deadline = time.monotonic() + 0.2
            while uart.in_waiting and time.monotonic() < drain_deadline:
                pre_reset.write(uart.read(uart.in_waiting))
            pre_reset.flush()
            udp_drain_deadline = time.monotonic() + 0.2
            while time.monotonic() < udp_drain_deadline:
                try:
                    data, peer = udp.recvfrom(1201)
                    record("pre_reset_udp", {"peer": list(peer), "hex": data.hex()})
                except BlockingIOError:
                    break
            record("host_reset_asserted", {"method": "RTS", "dtr": False})
            uart.rts = True
            time.sleep(0.1)
            # The active reset prevents new boot traffic while leftover serial drains.
            if uart.in_waiting:
                pre_reset.write(uart.read(uart.in_waiting))
                pre_reset.flush()
            uart.rts = False
            record("host_reset_released", {"method": "RTS", "dtr": False})
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                data = uart.read(max(1, uart.in_waiting))
                if data:
                    raw.write(data)
                    raw.flush()
                    pending += data
                    while b"\n" in pending:
                        line, pending = pending.split(b"\n", 1)
                        for tag in ("WASMHOME_LXC", "WASMHOME_EVENT", "WASMHOME_NETWORK", "WASMHOME_DIAG"):
                            marker = (tag + ",").encode()
                            if marker in line:
                                try:
                                    value = json.loads(line.split(marker, 1)[1])
                                    if not isinstance(value, dict):
                                        raise ValueError("serial JSON must be an object")
                                    record(tag, value)
                                except (ValueError, UnicodeDecodeError):
                                    record("invalid_serial_json", {"tag": tag, "hex": line.hex()})
                                break
                udp_batch_deadline = min(deadline, time.monotonic() + 0.05)
                while time.monotonic() < udp_batch_deadline:
                    try:
                        data, peer = udp.recvfrom(1201)
                    except BlockingIOError:
                        break
                    except ConnectionResetError:
                        record("udp_icmp_unreachable", {})
                        break
                    if peer != (DEVICE, 3333):
                        record("foreign_udp", {"peer": list(peer), "hex": data.hex()})
                        continue
                    try:
                        packet = decode(data)
                    except ProtocolError:
                        record("invalid_udp", {"hex": data.hex()})
                        continue
                    record("udp_received", {"peer": list(peer), "hex": data.hex(),
                           "boot_id": packet.boot_id, "sequence": packet.sequence,
                           "message_type": int(packet.message_type)})
                serial_boots = [r for r in records if r["kind"] == "WASMHOME_EVENT"
                                and r["value"].get("event") == "boot_observed"]
                boot_id = None
                if len(serial_boots) == 1:
                    try:
                        boot_id = int(serial_boots[0]["value"]["boot_id"], 16)
                    except (KeyError, TypeError, ValueError):
                        pass
                current_frames = [r for r in records if r["kind"] == "udp_received"
                                  and r["value"]["boot_id"] == boot_id
                                  and r["value"]["message_type"] == int(MessageType.TELEMETRY)]
                if not sent and len(current_frames) >= 3 and not pending and not uart.in_waiting:
                    command_start = time.perf_counter_ns()
                    uart.write(b"WH_LXC\n")
                    uart.flush()
                    record("serial_command_sent", {"command": "WH_LXC", "write_started_ns": command_start})
                    sent = True
            if pending:
                record("incomplete_serial_line", {"hex": pending.hex()})
    summary = summarize(records, expected)
    with (output / "summary.json").open("x", encoding="utf-8") as target:
        target.write(json.dumps(summary, indent=2) + "\n")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--expect", choices=("success", "failure"),
                        help="success checks the local positive transcript; failure records an unproven negative condition")
    parser.add_argument("--seconds", type=float, default=20)
    args = parser.parse_args()
    if not 5 <= args.seconds <= 60:
        parser.error("--seconds must be between 5 and 60")
    summary = capture(args.output, args.expect, args.seconds)
    print(json.dumps(summary))
    if args.expect == "success" and not summary["local_capture_checked"]:
        raise SystemExit("Positive capture is inconclusive; preserved raw evidence requires review")


if __name__ == "__main__":
    main()
