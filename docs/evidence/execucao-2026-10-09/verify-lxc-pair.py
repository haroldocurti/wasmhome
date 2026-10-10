"""Derive strict transport evidence without changing raw captures or pairing host clocks."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys

SESSION = Path(__file__).resolve().parent
ROOT = SESSION.parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from observation_protocol_v1 import decode

spec = importlib.util.spec_from_file_location("lxc_capture", SESSION / "capture-lxc-probe.py")
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def jsonlines(path):
    raw = path.read_bytes()
    require(raw.endswith(b"\n"), f"unfinished JSONL: {path.name}")
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def capture_case(name):
    folder = SESSION / name
    saved = json.loads((folder / "summary.json").read_text(encoding="utf-8-sig"))
    records = jsonlines(folder / "observations.jsonl")
    times = [r["host_monotonic_ns"] for r in records]
    require(times == sorted(times), f"host timestamps moved backwards: {name}")
    serial_records = []
    for line in (folder / "serial.log").read_bytes().splitlines():
        for tag in ("WASMHOME_LXC", "WASMHOME_EVENT", "WASMHOME_NETWORK", "WASMHOME_DIAG"):
            marker = (tag + ",").encode()
            if marker in line:
                try:
                    value = json.loads(line.split(marker, 1)[1])
                except (ValueError, UnicodeDecodeError):
                    break
                serial_records.append((tag, value))
                break
    logged_serial = [(r["kind"], r["value"]) for r in records if r["kind"] in
                     ("WASMHOME_LXC", "WASMHOME_EVENT", "WASMHOME_NETWORK", "WASMHOME_DIAG")]
    require(serial_records == logged_serial, f"serial raw/JSONL disagree: {name}")
    for record in records:
        if record["kind"] != "udp_received":
            continue
        value = record["value"]
        require(value["peer"] == [capture.DEVICE, 3333], f"unexpected UDP peer: {name}")
        packet = decode(bytes.fromhex(value["hex"]))
        require((packet.boot_id, packet.sequence, int(packet.message_type)) ==
                (value["boot_id"], value["sequence"], value["message_type"]),
                f"decoded UDP differs from claimed fields: {name}")
    derived = capture.summarize(records, saved.get("expected"))
    for key in ("local_capture_checked", "instrumental_transcript_checked", "serial_boot_id", "boot_ids",
                "explicit_resets", "serial_boot_events", "udp_packets", "udp_before_command",
                "udp_after_result", "boot_continuity_checked", "probe_results"):
        require(saved[key] == derived[key], f"saved summary disagrees with raw records: {name}/{key}")
    return derived, records


def server_case(filename, expected_peer, expected_result):
    rows = jsonlines(SESSION / filename)
    require(len(rows) == 3, f"expected one isolated server request: {filename}")
    listening, request, stopped = rows
    require(listening.get("event") == "listening" and listening.get("bind") == capture.SERVER
            and listening.get("port") == 18080 and listening.get("expected_peer") == expected_peer,
            f"unexpected server fixture configuration: {filename}")
    require(stopped == {"event": "stopped", "reason": "fixture_duration_complete"},
            f"server has not completed: {filename}")
    require(request.get("event") == "request" and request.get("result") == expected_result
            and request.get("source_ipv4") == capture.DEVICE
            and request.get("server_ipv4") == capture.SERVER and request.get("server_port") == 18080,
            f"server request does not meet expected condition: {filename}")
    require(isinstance(request.get("source_port"), int) and 1 <= request["source_port"] <= 65535,
            f"invalid recorded source port: {filename}")
    return request


def pair_positive(client, server):
    require(client["local_capture_checked"] and client["boot_continuity_checked"],
            "positive client transcript/continuity rejected")
    require(len(client["probe_results"]) == 1, "positive must have one result")
    reply = client["probe_results"][0]
    require(reply.get("result") == "ok" and reply.get("local_ipv4") == capture.DEVICE
            and reply.get("server_ipv4") == capture.SERVER and reply.get("server_port") == 18080,
            "positive firmware endpoint/result mismatch")
    require(isinstance(reply.get("nonce"), str) and re.fullmatch(r"[0-9a-f]{64}", reply["nonce"])
            and server.get("nonce") == reply["nonce"], "positive nonce is missing or different")
    return {"transport_round_trip_verified": True, "nonce": reply["nonce"],
            "source_ipv4": capture.DEVICE, "server_ipv4": capture.SERVER, "server_port": 18080,
            "server_source_port": server["source_port"], "serial_boot_id": client["serial_boot_id"],
            "udp_packets": client["udp_packets"], "udp_before_command": client["udp_before_command"],
            "udp_after_result": client["udp_after_result"], "boot_continuity_verified": True,
            "correlation_basis": "matching random nonce and endpoint; host clocks are never compared"}


def source_rejection(client, server):
    require(client["instrumental_transcript_checked"] and client["boot_continuity_checked"],
            "controlled source-rejection client transcript/continuity rejected")
    require(len(client["probe_results"]) == 1 and not client["local_capture_checked"],
            "controlled source rejection unexpectedly accepted by firmware")
    reply = client["probe_results"][0]
    require(reply.get("result") == "response_mismatch" and reply.get("local_ipv4") == capture.DEVICE,
            "controlled source rejection must yield firmware response_mismatch")
    require(server.get("result") == "rejected_source" and "nonce" not in server,
            "rejected request must not claim nonce pairing")
    return {"controlled_server_source_rejection_observed": True,
            "firmware_response_mismatch_observed": True, "server_expected_peer": "192.168.0.146",
            "actual_source_ipv4": capture.DEVICE, "serial_boot_id": client["serial_boot_id"],
            "udp_packets": client["udp_packets"], "udp_before_command": client["udp_before_command"],
            "udp_after_result": client["udp_after_result"], "boot_continuity_verified": True,
            "transaction_nonce_paired": False,
            "correlation_limit": "isolated runs with matching endpoints; rejected server request contains no nonce",
            "reader_absence_proven": False}


def restoration():
    verify_log = (SESSION / "app-restore-verify.txt").read_text(encoding="utf-8-sig")
    flash_log = (SESSION / "app-restore-flash.txt").read_text(encoding="utf-8-sig")
    require("Wrote 1048576 bytes" in flash_log and "at 0x00020000" in flash_log
            and "Hash of data verified." in flash_log, "restoration write was not verified")
    require("Verifying 0x100000 (1048576) bytes @ 0x00020000" in verify_log
            and "-- verify OK (digest matched)" in verify_log, "restoration verify_flash failed")
    backup = ROOT / "build-session-e0/app-original-1m.bin"
    backup_sha = hashlib.sha256(backup.read_bytes()).hexdigest()
    require(backup.stat().st_size == 1048576 and backup_sha ==
            "b00a479e56a1a486703093e93683de845dd09ae5392152a10f49fe9b15cde171", "local restoration backup mismatch")
    summary = json.loads((SESSION / "restore/summary.json").read_text(encoding="utf-8-sig"))
    records = jsonlines(SESSION / "restore/observations.jsonl")
    diagnostics = [r["value"] for r in records if r["kind"] == "WASMHOME_DIAG"]
    valid = [d for d in diagnostics if d["sample_id"] and d["sensor_status"] == 0]
    boots = [r["value"]["boot_id"] for r in records if r["kind"] == "WASMHOME_EVENT"
             and r["value"].get("event") == "boot_observed"]
    require(boots == ["7a90e79f8778787c"] == summary["boot_ids"], "restored boot identity differs")
    require(len(diagnostics) == summary["diagnostic_rows"] == 11 and
            len(valid) == summary["valid_sensor_rows"] == 10, "restored diagnostic row counts differ")
    udp = [r["value"] for r in records if r["kind"] == "udp_received"]
    require(len(udp) == summary["udp_packets"] == 88, "restored UDP count differs")
    for value in udp:
        packet = decode(bytes.fromhex(value["hex"]))
        require(value["peer"] == [capture.DEVICE, 3333] and packet.boot_id == int(boots[0], 16)
                and (packet.boot_id, packet.sequence) == (value["boot_id"], value["sequence"]),
                "restored UDP identity differs from raw bytes")
    require(summary["valid_probe_received"] and summary["wrong_boot_discarded"] and
            summary["truncated_discarded"] and not summary["wifi_disconnect_reasons"],
            "restored transport smoke checks failed")
    final = (SESSION / "lxc-session-final-confirmed.txt").read_text(encoding="utf-8-sig")
    require("reader_script_absent=true" in final and "192.168.0.138/24" in final
            and "status: running" in final and "status: stopped" in final,
            "fixture cleanup/guest final state was not recorded")
    require(not re.search(r"\S+:18080\b", final), "temporary endpoint still has a listener")
    return {"verified_write_and_readback": True, "offset": "0x20000", "region_bytes": 1048576,
            "backup_sha256": backup_sha, "backup_local_only_not_published": True,
            "boot_id": boots[0], "diagnostic_rows": 11, "valid_sensor_rows": 10,
            "initial_sensor_row": "pending acquisition, preserved in raw evidence", "udp_packets": 88,
            "reader_script_absent": True, "port_18080_listener_absent": True,
            "final_state_scope": "recorded hostname/IP and guest status from final corrected command"}


def verify():
    positive, _ = capture_case("lxc-positive-r2")
    positive_request = server_case("lxc-positive-r2-server.jsonl", capture.DEVICE, "response_sent")
    negative, _ = capture_case("lxc-negative-source")
    negative_request = server_case("lxc-negative-source-server.jsonl", "192.168.0.146", "rejected_source")
    first, first_records = capture_case("lxc-positive")
    require(not first["local_capture_checked"] and first["udp_before_command"] == 2,
            "first capture must remain excluded from continuity acceptance")
    command_time = next(r["host_monotonic_ns"] for r in first_records if r["kind"] == "serial_command_sent")
    tied_frames = [r for r in first_records if r["kind"] == "udp_received"
                   and r["host_monotonic_ns"] == command_time]
    require(tied_frames, "first capture no longer contains the documented clock tie")
    identity = (SESSION / "lxc-probe-server-identity.txt").read_text(encoding="utf-8-sig").split()[0]
    require(identity.lower() == hashlib.sha256((ROOT / "tools/probe_lxc_network.py").read_bytes()).hexdigest(),
            "recorded LXC fixture source differs from current source")
    return {"schema": "wasmhome-lxc-transport-pair-v1", "date": "2026-10-09",
            "scope": "issue #6 minimum ESP32 to CT107 transport; no HTTPS, COSE or campaign service claim",
            "positive": pair_positive(positive, positive_request),
            "controlled_negative": source_rejection(negative, negative_request),
            "excluded_first_capture": {"directory": "lxc-positive", "reason": "third UDP frame and command timestamp tie",
                                       "udp_before_command": 2, "continuity_acceptance": False},
            "native_checksum": {"assessment": "not assessed here", "external_evidence": "native/derived-native.json"},
            "previous_app_restoration": restoration()}


def hashes():
    paths = [Path(__file__), SESSION / "capture-lxc-probe.py", SESSION / "test_capture_lxc_probe.py",
             ROOT / "main/platform_selftest.c", ROOT / "main/Kconfig.projbuild", ROOT / "tools/probe_lxc_network.py",
             ROOT / "tools/observation_protocol_v1.py", ROOT / "tools/probe-platform.py",
             ROOT / "build-session-e0/app-original-1m.bin", SESSION / "test_verify_lxc_pair.py"]
    paths += list(SESSION.glob("*.txt")) + list(SESSION.glob("*-server.jsonl"))
    for folder in ("lxc-positive", "lxc-positive-r2", "lxc-negative-source", "platform", "restore"):
        paths += [p for p in (SESSION / folder).iterdir() if p.is_file()]
    return {p.relative_to(ROOT).as_posix(): {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "bytes": p.stat().st_size}
            for p in sorted(set(paths))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=SESSION / "pair-verification.json",
                        help="new derived output file; existing files are never overwritten")
    args = parser.parse_args()
    before = hashes()
    result = verify()
    after = hashes()
    require(before == after, "evidence changed during verification; retry only after capture finishes")
    result["raw_and_source_hashes"] = after
    output = args.output
    with output.open("x", encoding="utf-8") as target:
        target.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"positive_transport_verified": True, "output": str(output),
                      "controlled_source_rejection_observed": True, "negative_nonce_paired": False}))


if __name__ == "__main__":
    main()
