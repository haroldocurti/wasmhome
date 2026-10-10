"""Bench-only diagnostics. Does not measure functional service availability."""
import argparse
from collections import Counter
import json
import pathlib
import os
import socket
import time
import uuid

import serial
from observation_protocol_v1 import Packet, MessageType, ProtocolError, decode, encode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM12")
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument("--watchdog", action="store_true", help="send the fixture-only WH_WDT command after 12 seconds")
    parser.add_argument("--bind-ip", default="0.0.0.0", help="local observer IPv4 address")
    parser.add_argument("--interface-index", type=int, help="Windows outgoing interface index; does not change routing tables")
    parser.add_argument("--no-reset", action="store_true", help="observe the current boot without resetting the board")
    parser.add_argument("--no-probes", action="store_true", help="passive capture without control probes")
    parser.add_argument("--device-ip", help="expected device address, useful when observing an existing boot")
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    if args.no_reset and not args.device_ip:
        parser.error("--no-reset requires --device-ip to identify the expected sender")
    args.output.mkdir(parents=True, exist_ok=True)
    records = []
    udp_samples = []
    device_ip = args.device_ip
    boot_id = None
    sent_probes = False
    sent_watchdog = False
    udp_ready = False
    raw_path = args.output / "serial.log"
    jsonl_path = args.output / "observations.jsonl"
    uart = serial.Serial()
    uart.port = args.port
    uart.baudrate = 115200
    uart.timeout = 0.01
    uart.dtr = False
    uart.rts = False
    with uart, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp, \
            raw_path.open("wb") as raw, jsonl_path.open("w", encoding="utf-8") as log:
        if args.interface_index is not None:
            if os.name != "nt":
                raise SystemExit("--interface-index currently supports Windows only")
            # IP_UNICAST_IF is 31 and takes an interface index in network order.
            # https://learn.microsoft.com/en-us/windows/win32/winsock/ipproto-ip-socket-options
            udp.setsockopt(socket.IPPROTO_IP, 31, socket.htonl(args.interface_index))
        udp.bind((args.bind_ip, 3333))
        udp.setblocking(False)
        if not args.no_reset:
            uart.rts = True
            time.sleep(0.1)
            uart.rts = False
        deadline = time.monotonic() + args.seconds
        watchdog_at = time.monotonic() + 12
        pending = b""

        def record(kind, value):
            entry = {"host_monotonic_ns": time.monotonic_ns(), "kind": kind, "value": value}
            records.append(entry)
            log.write(json.dumps(entry) + "\n")
            log.flush()

        while time.monotonic() < deadline:
            if args.watchdog and not sent_watchdog and time.monotonic() >= watchdog_at:
                uart.write(b"WH_WDT\n")
                record("watchdog_command_sent", {})
                sent_watchdog = True
            data = uart.read(max(1, uart.in_waiting))
            if data:
                raw.write(data)
                raw.flush()
                pending += data
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    text = line.decode("utf-8", errors="replace").strip()
                    for tag in ("WASMHOME_EVENT", "WASMHOME_NETWORK", "WASMHOME_WIFI", "WASMHOME_DIAG_RX", "WASMHOME_DIAG"):
                        marker = tag + ","
                        if marker not in text:
                            continue
                        try:
                            value = json.loads(text.split(marker, 1)[1])
                        except json.JSONDecodeError:
                            record("invalid_serial_json", {"tag": tag})
                            break
                        record(tag, value)
                        if tag == "WASMHOME_NETWORK":
                            device_ip = value["ip"]
                        if tag == "WASMHOME_EVENT" and value["event"] == "boot_observed":
                            boot_id = int(value["boot_id"], 16)
                            udp_ready = False
                        if tag == "WASMHOME_EVENT" and value["event"] == "udp_bound":
                            udp_ready = True
                        break
            if device_ip and boot_id and udp_ready and not sent_probes and not args.no_probes:
                request = Packet(MessageType.CONTROL_REQUEST, uuid.UUID(int=0), 1, boot_id, time.monotonic_ns() // 1000,
                                 {"sample_id": 291, "input_temperature_mC": 25000})
                good = encode(request)
                udp.sendto(good, (device_ip, 3333))
                record("probe_sent", {"case": "valid_control", "hex": good.hex()})
                stale = encode(Packet(MessageType.CONTROL_REQUEST, uuid.UUID(int=0), 2,
                    (boot_id ^ 1) or 2, time.monotonic_ns() // 1000,
                    {"sample_id": 292, "input_temperature_mC": 25000}))
                udp.sendto(stale, (device_ip, 3333))
                record("probe_sent", {"case": "wrong_boot", "hex": stale.hex()})
                udp.sendto(good[:-1], (device_ip, 3333))
                record("probe_sent", {"case": "truncated", "hex": good[:-1].hex()})
                sent_probes = True
            try:
                datagram, peer = udp.recvfrom(1201)
            except BlockingIOError:
                continue
            except ConnectionResetError:
                # Windows reports ICMP Port Unreachable on a UDP socket.
                # Preserve it as a failed transport observation, without retry.
                record("udp_icmp_unreachable", {})
                continue
            if device_ip and peer != (device_ip, 3333):
                continue
            try:
                packet = decode(datagram)
            except ProtocolError:
                record("invalid_udp", {"length": len(datagram)})
                continue
            record("udp_received", {"peer": list(peer), "hex": datagram.hex(), "boot_id": packet.boot_id,
                                    "sequence": packet.sequence, "body": packet.body})
            if packet.message_type == MessageType.TELEMETRY:
                udp_samples.append(packet)

    diagnostics = [r["value"] for r in records if r["kind"] == "WASMHOME_DIAG"]
    events = [r["value"] for r in records if r["kind"] == "WASMHOME_EVENT"]
    valid = [d for d in diagnostics if d["sample_id"] and d["sensor_status"] == 0]
    result = {
        "purpose": "platform diagnostics only; not functional availability",
        "device_ip": device_ip,
        "observer_bind_ip": args.bind_ip,
        "observer_interface_index": args.interface_index,
        "boot_ids": list(dict.fromkeys([e["boot_id"] for e in events if e["event"] == "boot_observed"] +
                                       [f"{p.boot_id:016x}" for p in udp_samples])),
        "reset_reasons": [e["status"] for e in events if e["event"] == "boot_observed"],
        "watchdog_trigger_seen": any(e["event"] == "selftest_watchdog_triggered" for e in events),
        "diagnostic_rows": len(diagnostics),
        "valid_sensor_rows": len(valid),
        "temperatures_mC": sorted(set(d["temperature_mC"] for d in valid)),
        "gpio_observations": sorted(set((d["requested_gpio"], d["observed_gpio"], d["gpio_error"]) for d in diagnostics)),
        "udp_packets": len(udp_samples),
        "udp_sequence_increases_within_boot": bool(udp_samples) and all(a.boot_id != b.boot_id or a.sequence < b.sequence
            for a, b in zip(udp_samples, udp_samples[1:])),
        "valid_probe_received": any(r["kind"] == "WASMHOME_DIAG_RX" and r["value"]["type"] == 2 and
                                    r["value"]["sequence"] == 1 and r["value"]["id"] == 291 for r in records),
        "wrong_boot_discarded": any(e["event"] == "control_wrong_boot" for e in events),
        "truncated_discarded": any(e["event"] == "udp_discarded" for e in events),
        "wifi_disconnect_reasons": dict(Counter(r["value"]["reason"] for r in records if r["kind"] == "WASMHOME_WIFI")),
        "error_counts": dict(Counter((e["event"] + ":" + str(e["status"])) for e in events
                             if e["status"] != 0 and e["event"] != "boot_observed")),
    }
    (args.output / "summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
