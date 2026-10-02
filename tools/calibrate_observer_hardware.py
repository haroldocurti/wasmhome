"""Bench integration with the explicitly enabled native observer fixture."""
import argparse
import json
from pathlib import Path
import threading
import time

from external_observer import run


def calibrate(output, device_ip, bind_ip, interface_index=None, port="COM12", mode="gap"):
    import serial
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    version = 2 if mode == "v2" else 1
    duration = 20 if mode == "reboot" else 10
    events = []
    errors = []
    stop = threading.Event()
    ready = threading.Event()
    uart = serial.Serial()
    uart.port = port
    uart.baudrate = 115200
    uart.timeout = 0.02
    uart.dtr = False
    uart.rts = False
    with uart, (output / "firmware.jsonl").open("x", encoding="utf-8") as log:
        def record(kind, **values):
            row = {"host_monotonic_ns": time.monotonic_ns(), "kind": kind, **values}
            events.append(row)
            log.write(json.dumps(row) + "\n")
            log.flush()

        def command(text):
            uart.write((text + "\n").encode("ascii"))
            record("serial_command", command=text)

        # Reconfigure only the fixture version, not flash or the serial lines.
        command(f"WH_V{version}")
        start = time.monotonic()

        def capture():
            pending = b""
            sent = False
            try:
                while not stop.is_set():
                    if not sent and time.monotonic() >= start + 3:
                        command("WH_REBOOT" if mode == "reboot" else "WH_GAP")
                        sent = True
                    data = uart.read(max(1, uart.in_waiting))
                    pending += data
                    while b"\n" in pending:
                        line, pending = pending.split(b"\n", 1)
                        text = line.decode("utf-8", errors="replace")
                        for tag in ("WASMHOME_EVENT", "WASMHOME_NETWORK", "WASMHOME_WIFI"):
                            marker = tag + ","
                            if marker in text:
                                try:
                                    value = json.loads(text.split(marker, 1)[1])
                                except json.JSONDecodeError:
                                    record("invalid_serial_event", tag=tag)
                                else:
                                    record("firmware_event", tag=tag, value=value)
                                    if tag == "WASMHOME_EVENT" and value.get("event") == f"observer_fixture_v{version}":
                                        ready.set()
                                break
            except Exception as error:
                errors.append(str(error))

        thread = threading.Thread(target=capture, daemon=True)
        thread.start()
        try:
            if not ready.wait(2):
                raise RuntimeError("fixture did not confirm its version; no UDP test started")
            summary = run(device_ip, output, seconds=duration, bind_ip=bind_ip,
                          initial_version=version, interface_index=interface_index,
                          trigger_after=1 if mode != "v2" else None,
                          target_sequence=2 if mode != "v2" else None)
        finally:
            stop.set()
            thread.join(2)
        if thread.is_alive() or errors:
            raise RuntimeError(f"serial capture failed: {errors}")
    result = evaluate(summary, events, mode)
    (output / "hardware-calibration.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def evaluate(summary, events, mode):
    version = 2 if mode == "v2" else 1
    fixture_events = [r["value"] for r in events if r["kind"] == "firmware_event" and r["tag"] == "WASMHOME_EVENT"]
    starts = [e for e in fixture_events if e["event"] == "observer_fixture_gap_start"]
    ends = [e for e in fixture_events if e["event"] == "observer_fixture_gap_end"]
    known = None
    if len(starts) == len(ends) == 1 and starts[0]["boot_id"] == ends[0]["boot_id"]:
        known = (ends[0]["monotonic_us"] - starts[0]["monotonic_us"]) * 1000
    estimate = summary["control_unavailability_estimate_ns"]
    error = None if known is None or estimate is None else abs(estimate - known)
    result = {
        "purpose": "ESP32-S3 observer fixture integration; no OTA or campaign modules",
        "mode": mode, "known_silence_ns": known, "estimated_silence_ns": estimate,
        "absolute_error_ns": error, "resolution_ns": summary["period_ns"],
        "boot_ids": summary["boot_ids"],
        "valid_controls_by_boot": summary["valid_controls_by_boot"],
        "reboot_command_seen": any(e["event"] == "observer_fixture_reboot" for e in fixture_events),
        "reboot_observed": any(e["event"] == "boot_observed" for e in fixture_events),
        "correct_controls": summary["counters"].get("control_valid", 0),
        "lost_controls": summary["lost_controls"],
        "incorrect_controls": summary["counters"].get("incorrect_control", 0),
        "update_rejection_seen": summary["counters"].get("update_rejected", 0) > 0,
    }
    result["passed"] = (result["correct_controls"] > 0 and result["lost_controls"] > 0
                        and result["incorrect_controls"] == 0
                        and summary["counters"].get("telemetry_valid", 0) > 0
                        and any(e["event"] == f"observer_fixture_v{version}" for e in fixture_events))
    if mode == "reboot":
        result["passed"] &= (len(result["boot_ids"]) == 2
                             and all(summary["valid_controls_by_boot"].get(boot,
                                 summary["valid_controls_by_boot"].get(str(boot), 0)) > 0 for boot in result["boot_ids"])
                             and result["reboot_command_seen"]
                             and result["reboot_observed"] and result["update_rejection_seen"])
    else:
        result["passed"] &= (error is not None and error <= result["resolution_ns"]
                             and len(result["boot_ids"]) == 1
                             and (mode == "v2" or result["update_rejection_seen"]))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device-ip", required=True)
    parser.add_argument("--bind-ip", required=True)
    parser.add_argument("--interface-index", type=int)
    parser.add_argument("--port", default="COM12")
    parser.add_argument("--mode", choices=("gap", "reboot", "v2"), default="gap")
    args = parser.parse_args()
    result = calibrate(**vars(args))
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
