#!/usr/bin/env python3
"""Release a scheduled slot after a verified pre-start SSH prepare timeout.

This narrow adjudicator never accesses the ESP32 serial port and never retries
the failed slot. It preserves the capture and advances only the journal state,
leaving the schedule index on the following unclaimed slot.
"""
from __future__ import annotations

import argparse
import base64
import copy
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import uuid

try:
    from . import issue20_hil_orchestrator as hil
except ImportError:
    import issue20_hil_orchestrator as hil

BASELINE_SHA256 = hil.BASELINE_SHA256
FLASH_BYTES = hil.FLASH_BYTES
TRANSPORT_CONFIG = Path.home() / ".ssh/config"


def _private_file(path: Path, label: str) -> Path:
    path = Path(path)
    hil.require(not any(part.is_symlink() for part in (path, *path.parents)),
                f"{label} path contains a symlink")
    hil.require(path.is_file() and stat.S_ISREG(path.stat().st_mode)
                and path.stat().st_uid == os.getuid()
                and stat.S_IMODE(path.stat().st_mode) == 0o600,
                f"{label} must be an owned regular 0600 file")
    return path


def _private_dir(path: Path, label: str) -> Path:
    path = Path(path)
    hil.require(not any(part.is_symlink() for part in (path, *path.parents)),
                f"{label} path contains a symlink")
    hil.require(path.is_dir() and path.stat().st_uid == os.getuid()
                and stat.S_IMODE(path.stat().st_mode) == 0o700,
                f"{label} must be an owned 0700 directory")
    return path


def _json(path: Path, label: str) -> dict:
    value = hil.campaign._read_json(_private_file(path, label), label)
    hil.require(isinstance(value, dict), f"{label} must contain a JSON object")
    return value


def _sha256(path: Path) -> str:
    return hil.campaign._sha256_file(Path(path))


def _ssh_effective_settings(options: dict, identity: Path, known_hosts: Path) -> dict:
    command = ["ssh", "-G", "-p", str(options["remote_ssh_port"]),
               "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
               "-o", "UserKnownHostsFile=" + str(known_hosts),
               "-o", "GlobalKnownHostsFile=/dev/null", "-o", "IdentitiesOnly=yes",
               "-o", "IdentityAgent=none", "-o", "PasswordAuthentication=no",
               "-o", "KbdInteractiveAuthentication=no", "-i", str(identity),
               "--", options["remote_observer"]]
    completed = subprocess.run(command, capture_output=True, timeout=5, check=True)
    settings: dict[str, object] = {}
    wanted = {"hostname", "user", "port", "identityfile", "userknownhostsfile",
              "globalknownhostsfile", "stricthostkeychecking", "identityagent",
              "proxycommand", "proxyjump", "controlmaster", "controlpath"}
    for line in completed.stdout.decode("utf-8").splitlines():
        key, separator, value = line.partition(" ")
        if separator and key in wanted:
            previous = settings.get(key)
            if previous is None:
                settings[key] = value
            elif isinstance(previous, list):
                previous.append(value)
            else:
                settings[key] = [previous, value]
    return settings


def _verify_gateway_command(settings: dict) -> dict:
    proxy = settings.get("proxycommand")
    hil.require(isinstance(proxy, str), "effective SSH configuration has no Proxmox gateway")
    argv = shlex.split(proxy)
    try:
        identity = Path(argv[argv.index("-i") + 1])
        known_hosts_token = next(item for item in argv
                                 if item.startswith("UserKnownHostsFile="))
        known_hosts = Path(known_hosts_token.split("=", 1)[1])
    except (ValueError, StopIteration, IndexError) as exc:
        raise hil.HilError("effective Proxmox command lacks pinned identity/known_hosts") from exc
    target = "root@100.89.142.40"
    expected = ["ssh", "-F", "/dev/null", "-T", "-i", str(identity),
                "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
                "-o", "StrictHostKeyChecking=yes",
                "-o", "UserKnownHostsFile=" + str(known_hosts),
                "-o", "GlobalKnownHostsFile=/dev/null", "-o", "ConnectTimeout=5",
                "-W", "%h:%p", target]
    hil.require(argv == expected,
                "effective Proxmox hop is not the pinned forwarding-only command")
    identity = _private_file(identity, "pinned Proxmox SSH identity")
    known_hosts = _private_file(known_hosts, "pinned Proxmox known_hosts")
    return {"target": target, "identity_path": str(identity),
            "identity_sha256": _sha256(identity), "known_hosts_path": str(known_hosts),
            "known_hosts_sha256": _sha256(known_hosts),
            "proxycommand_sha256": hashlib.sha256(proxy.encode()).hexdigest()}


def _authenticated_status_probe(campaign_root: Path, slot_id: str, session_id: str,
                                session_path: Path, terminal_path: Path,
                                options: dict, probe_dir: Path,
                                saved_probe: dict | None = None) -> dict:
    """Query status once over both pinned SSH hops and preserve its transcript."""
    identity = _private_file(Path(options["remote_identity"]), "pinned CT SSH identity")
    known_hosts = _private_file(Path(options["remote_known_hosts"]), "pinned CT known_hosts")
    transport_config = _private_file(TRANSPORT_CONFIG, "SSH transport config")
    effective = _ssh_effective_settings(options, identity, known_hosts)
    expected_identity = str(identity)
    hil.require(effective.get("hostname") == options["remote_observer"].split("@", 1)[1]
                and effective.get("user") == "wasmhome"
                and effective.get("port") == str(options["remote_ssh_port"])
                and effective.get("identityfile") == expected_identity
                and effective.get("userknownhostsfile") == str(known_hosts)
                and effective.get("globalknownhostsfile") == "/dev/null"
                and effective.get("stricthostkeychecking") == "true"
                and effective.get("identityagent") == "none"
                and effective.get("proxyjump") in (None, "")
                and effective.get("controlmaster") == "false",
                "effective CT SSH settings differ from the frozen destination and key pins")
    gateway = _verify_gateway_command(effective)

    remote = importlib.import_module("tools.remote_observer" if __package__ else "remote_observer")
    controller = Path(session_path.parent / "remote-code/remote_observer.py")
    controller_bytes = _private_file(controller, "remote status controller").read_bytes()
    controller_sha = hashlib.sha256(controller_bytes).hexdigest()
    session = _json(session_path, "remote session")
    hil.require(session.get("source_hashes", {}).get("remote_observer.py") == controller_sha,
                "status controller differs from the source frozen in remote-session.json")
    rpc_options = {"target": options["remote_observer"], "known_hosts": str(known_hosts),
                   "identity": str(identity), "root": options["remote_root"],
                   "ssh_port": options["remote_ssh_port"], "controller_path": str(controller)}
    request = {"schema": remote.SCHEMA, "root": rpc_options["root"],
               "session_id": session_id, "action": "status",
               "controller_source": base64.b64encode(controller_bytes).decode(),
               "controller_sha256": controller_sha}
    expected_request_sha = hashlib.sha256(remote._encoded(request)).hexdigest()

    def result_fields(transcript_path: Path, transcript: dict) -> dict:
        return {"path": str(transcript_path), "sha256": _sha256(transcript_path),
                "queried_at_utc": transcript["queried_at_utc"],
                "ssh_returncode": transcript["ssh_returncode"],
                "session_id_sha256": transcript["session_id_sha256"],
                "remote_session_sha256": transcript["remote_session_sha256"],
                "ct_known_hosts_sha256": transcript["ct"]["known_hosts_sha256"],
                "transport_config_sha256": transcript["ct"]["ssh_config_sha256"],
                "gateway": transcript["gateway"]}

    if saved_probe is not None:
        saved_path = _private_file(Path(saved_probe.get("path", "")),
                                  "saved authenticated status transcript")
        transcript = _json(saved_path, "saved authenticated status transcript")
        hil.require(_sha256(saved_path) == saved_probe.get("sha256"),
                    "saved authenticated status transcript hash changed")
        expected_command = [
            "ssh", "-T", "-p", str(options["remote_ssh_port"]),
            "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "UserKnownHostsFile=" + str(known_hosts),
            "-o", "GlobalKnownHostsFile=/dev/null", "-o", "IdentitiesOnly=yes",
            "-o", "IdentityAgent=none", "-o", "PasswordAuthentication=no",
            "-o", "KbdInteractiveAuthentication=no", "-o", "ConnectTimeout=2",
            "-o", "ServerAliveInterval=1", "-o", "ServerAliveCountMax=1",
            "-i", str(identity), "--", options["remote_observer"],
            shlex.join(["python3", "-c", remote.BOOTSTRAP]),
        ]
        saved_ct = transcript.get("ct")
        saved_effective = transcript.get("effective_ssh")
        saved_gateway = transcript.get("gateway")
        saved_terminal_mtime = terminal_path.stat().st_mtime_ns
        try:
            probe_time = datetime.fromisoformat(transcript["queried_at_utc"])
            stderr = base64.b64decode(transcript["ssh_stderr_base64"], validate=True)
            stdout = base64.b64decode(transcript["ssh_stdout_base64"], validate=True)
        except (KeyError, TypeError, ValueError) as exc:
            raise hil.HilError("saved authenticated status transcript is malformed") from exc
        hil.require(transcript.get("schema") == "issue20-authenticated-remote-status-transcript-v1"
                    and transcript.get("action") == "status"
                    and transcript.get("slot_id") == slot_id
                    and transcript.get("session_id_sha256") == hashlib.sha256(session_id.encode()).hexdigest()
                    and transcript.get("remote_session_sha256") == _sha256(session_path)
                    and transcript.get("terminal_error_sha256") == _sha256(terminal_path)
                    and transcript.get("terminal_error_mtime_ns") == saved_terminal_mtime
                    and probe_time.tzinfo is not None
                    and int(probe_time.timestamp() * 1_000_000_000) >= saved_terminal_mtime
                    and transcript.get("request_sha256") == expected_request_sha
                    and transcript.get("ssh_argv") == expected_command
                    and transcript.get("ssh_returncode") == 1
                    and stdout == b""
                    and b"RemoteObserverError: remote session does not exist" in stderr
                    and "SSH status failed (1):" in transcript.get("rpc_error", "")
                    and b"RemoteObserverError: remote session does not exist"
                    in transcript.get("rpc_error", "").encode()
                    and isinstance(saved_ct, dict)
                    and saved_ct.get("target") == options["remote_observer"]
                    and saved_ct.get("identity_path") == str(identity)
                    and saved_ct.get("identity_sha256") == _sha256(identity)
                    and saved_ct.get("known_hosts_path") == str(known_hosts)
                    and saved_ct.get("known_hosts_sha256") == _sha256(known_hosts)
                    and saved_ct.get("ssh_config_path") == str(transport_config)
                    and saved_ct.get("ssh_config_sha256") == _sha256(transport_config)
                    and saved_ct.get("controller_sha256") == controller_sha
                    and saved_effective == effective
                    and saved_gateway == gateway,
                    "saved authenticated status transcript no longer matches the frozen SSH evidence")
        return result_fields(saved_path, transcript)

    captured: list[dict] = []
    original_run = remote.subprocess.run

    def capture_run(command, *args, **kwargs):
        result = original_run(command, *args, **kwargs)
        captured.append({"argv": list(command), "request_sha256": hashlib.sha256(
            kwargs.get("input", b"")).hexdigest(), "returncode": result.returncode,
            "stdout": result.stdout, "stderr": result.stderr})
        return result

    remote.subprocess.run = capture_run
    rpc_error = None
    try:
        try:
            response = remote._rpc(rpc_options, session_id, "status")
        except remote.RemoteObserverError as exc:
            rpc_error = str(exc)
            response = None
    finally:
        remote.subprocess.run = original_run

    hil.require(response is None and rpc_error is not None and len(captured) == 1,
                "remote status did not fail in one authenticated SSH request")
    result = captured[0]
    argv = result["argv"]
    hil.require(result["request_sha256"] == expected_request_sha
                and result["returncode"] == 1 and result["stdout"] == b""
                and b"RemoteObserverError: remote session does not exist" in result["stderr"]
                and "SSH status failed (1):" in rpc_error
                and "RemoteObserverError: remote session does not exist" in rpc_error,
                "SSH transcript does not contain the authenticated remote absence response")
    hil.require(argv[0] == "ssh" and "StrictHostKeyChecking=yes" in argv
                and "UserKnownHostsFile=" + str(known_hosts) in argv
                and "GlobalKnownHostsFile=/dev/null" in argv
                and "IdentitiesOnly=yes" in argv and "IdentityAgent=none" in argv
                and str(identity) in argv and options["remote_observer"] in argv
                and argv[-1] == shlex.join(["python3", "-c", remote.BOOTSTRAP]),
                "SSH invocation differs from the pinned status-only RPC")

    queried_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    terminal_mtime_ns = terminal_path.stat().st_mtime_ns
    queried_ns = int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
    hil.require(queried_ns >= terminal_mtime_ns,
                "authenticated remote absence probe predates the terminal failure")
    transcript = {
        "schema": "issue20-authenticated-remote-status-transcript-v1",
        "queried_at_utc": queried_at,
        "terminal_error_sha256": _sha256(terminal_path),
        "terminal_error_mtime_ns": terminal_mtime_ns,
        "action": "status", "slot_id": slot_id,
        "session_id_sha256": hashlib.sha256(session_id.encode()).hexdigest(),
        "request_sha256": result["request_sha256"], "ssh_returncode": result["returncode"],
        "ssh_stdout_base64": base64.b64encode(result["stdout"]).decode(),
        "ssh_stderr_base64": base64.b64encode(result["stderr"]).decode(),
        "rpc_error": rpc_error, "ssh_argv": argv, "effective_ssh": effective,
        "gateway": gateway,
        "ct": {"target": options["remote_observer"],
               "identity_path": str(identity), "identity_sha256": _sha256(identity),
               "known_hosts_path": str(known_hosts), "known_hosts_sha256": _sha256(known_hosts),
               "ssh_config_path": str(transport_config),
               "ssh_config_sha256": _sha256(transport_config),
               "controller_sha256": controller_sha},
        "remote_session_sha256": _sha256(session_path),
    }
    transcript_path = probe_dir / ("authenticated-status-adjudication-" + uuid.uuid4().hex + ".json")
    hil.write_private(transcript_path, transcript, exclusive=True)
    return result_fields(transcript_path, transcript)


def _slot_evidence(campaign_root: Path, failed_slot: dict, journal: dict,
                   options: dict, saved_probe: dict | None = None) -> dict:
    slot_id = failed_slot["slot_id"]
    raw_dir = _private_dir(campaign_root / "raw" / "slots" / slot_id,
                           "failed slot raw directory")
    children = {path.name for path in raw_dir.iterdir()}
    hil.require(children == {"device.jsonl", "remote-code", "remote-session.json", "terminal-error.json"},
                "raw capture inventory differs from the pre-start timeout evidence")

    terminal_path = _private_file(raw_dir / "terminal-error.json", "terminal error")
    terminal = _json(terminal_path, "terminal error")
    message = terminal.get("error")
    hil.require(terminal.get("kind") == "capture_failed"
                and terminal.get("automatic_retry_permitted") is False
                and isinstance(message, str)
                and message.startswith("RemoteObserverError: SSH prepare failed (255):")
                and "timed out" in message.lower(),
                "failure is not a non-retryable SSH timeout during prepare")
    raw_metadata = terminal.get("raw_files")
    hil.require(isinstance(raw_metadata, dict)
                and set(raw_metadata) == {"device.jsonl", "remote-session.json"},
                "terminal error does not seal exactly the pre-start raw files")
    verified_files = {}
    for name in ("device.jsonl", "remote-session.json"):
        metadata = raw_metadata[name]
        hil.require(isinstance(metadata, dict) and set(metadata) == {"size_bytes", "sha256"}
                    and type(metadata["size_bytes"]) is int
                    and metadata["size_bytes"] >= 0
                    and isinstance(metadata["sha256"], str)
                    and re.fullmatch(r"[0-9a-f]{64}", metadata["sha256"]),
                    f"invalid terminal evidence metadata for {name}")
        path = _private_file(raw_dir / name, f"raw evidence {name}")
        actual = {"size_bytes": path.stat().st_size, "sha256": _sha256(path)}
        hil.require(actual == metadata, f"raw evidence changed after capture: {name}")
        verified_files[name] = actual

    session_path = raw_dir / "remote-session.json"
    session = _json(session_path, "remote session")
    session_id = session.get("session_id")
    context = ((session.get("config") or {}).get("context") or {})
    hil.require(session.get("schema") == "wasmhome-remote-observer-v1"
                and isinstance(session_id, str)
                and str(uuid.UUID(session_id)) == session_id
                and context.get("campaign_id") == journal.get("campaign_id")
                and context.get("slot_id") == slot_id
                and context.get("git_commit") == journal.get("git_commit"),
                "remote session identity does not match this campaign slot")

    remote_code = _private_dir(raw_dir / "remote-code", "bundled remote source")
    source_names = {"remote_observer.py", "external_observer.py",
                    "observation_protocol_v1.py", "update_campaign.py"}
    hil.require({path.name for path in remote_code.iterdir()} == source_names,
                "bundled remote source inventory differs from the session receipt")
    source_hashes = session.get("source_hashes")
    hil.require(isinstance(source_hashes, dict) and set(source_hashes) == source_names,
                "remote session receipt lacks the complete source hash set")
    for name in source_names:
        path = _private_file(remote_code / name, f"bundled source {name}")
        hil.require(isinstance(source_hashes[name], str)
                    and _sha256(path) == source_hashes[name],
                    f"bundled source hash mismatch: {name}")

    probe_dir = _private_dir(campaign_root / "hil-orchestrator"
                             / ("transport-review-" + slot_id),
                             "remote status probe directory")
    probe_path = probe_dir / "remote-status-probe.json"
    probe = _json(probe_path, "authenticated remote status probe")
    known_hosts = _private_file(Path(options["remote_known_hosts"]), "pinned CT known_hosts")
    transport_config = _private_file(TRANSPORT_CONFIG, "SSH transport config")
    expected_probe = {
        "schema": "issue20-remote-prepare-status-probe-v1",
        "slot_id": slot_id,
        "session_id_sha256": hashlib.sha256(session_id.encode()).hexdigest(),
        "remote_receipt_sha256": _sha256(session_path),
        "transport_config_sha256": _sha256(transport_config),
        "ct_known_hosts_sha256": _sha256(known_hosts),
        "observation": {"result": "remote_session_absent",
                        "error_class": "RemoteObserverError",
                        "error_marker": "remote session does not exist"},
    }
    hil.require(all(probe.get(key) == value for key, value in expected_probe.items()),
                "authenticated probe does not prove this session is absent on CT 107")
    hil.require(isinstance(probe.get("queried_at_utc"), str)
                and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?\+00:00",
                                 probe["queried_at_utc"]),
                "probe timestamp is missing or not UTC")
    authenticated_probe = _authenticated_status_probe(
        campaign_root, slot_id, session_id, session_path, terminal_path, options, probe_dir,
        saved_probe=saved_probe)

    return {
        "terminal_error": {"sha256": _sha256(terminal_path)},
        "session_id_sha256": expected_probe["session_id_sha256"],
        "remote_session_sha256": expected_probe["remote_receipt_sha256"],
        "remote_session_absent_probe": {"path": str(probe_path),
                                        "sha256": _sha256(probe_path),
                                        "queried_at_utc": probe["queried_at_utc"]},
        "authenticated_remote_status": authenticated_probe,
        "transport_config_sha256": expected_probe["transport_config_sha256"],
        "ct_known_hosts_sha256": expected_probe["ct_known_hosts_sha256"],
        "raw_files": verified_files,
        "bundled_source_hashes": source_hashes,
    }


def adjudicate(campaign_root: Path) -> dict:
    os.umask(0o077)
    hil.require(os.geteuid() != 0, "adjudicator must run as an ordinary user")
    campaign_root = Path(campaign_root).absolute()
    journal_root = _private_dir(campaign_root / "hil-orchestrator", "HIL journal directory")
    journal_path = _private_file(journal_root / "journal.json", "HIL journal")

    with hil.adjudication_lock(journal_root):
        journal = _json(journal_path, "HIL journal")
        next_index = journal.get("next_index")
        slots = journal.get("slots")
        hil.require(journal.get("state") == "slot_failed"
                    and journal.get("restore_required") is False
                    and type(next_index) is int and next_index == 13
                    and isinstance(slots, list) and next_index < len(slots),
                    "journal is not the restored B0005-aot failure at index 13")
        failed_slot, next_slot = slots[next_index - 1], slots[next_index]
        hil.require(failed_slot == {"block_id": "B0005", "order_in_block": 1,
                                   "scenario": "aot", "slot_id": "B0005-aot"}
                    and next_slot.get("slot_id") == "B0005-native"
                    and journal.get("current_slot") == failed_slot["slot_id"]
                    and journal.get("claim_intent") == {"slot": failed_slot,
                                                         "retry_permitted": False},
                    "failed or next slot does not match the frozen r7 order")
        events = journal.get("events")
        hil.require(isinstance(events, list) and events
                    and events[-1] == {"slot_id": "B0005-aot", "state": "slot_failed",
                                       "error": "RemoteObserverError"},
                    "journal does not end in this terminal remote observer failure")

        claims_dir = _private_dir(journal_root / "claims", "slot claims directory")
        claim_path = _private_file(claims_dir / "B0005-aot.json", "failed slot claim")
        claim = _json(claim_path, "failed slot claim")
        hil.require(claim == {"slot": failed_slot, "retry_permitted": False},
                    "failed slot claim is not permanently non-retryable")
        hil.require(not os.path.lexists(claims_dir / "B0005-native.json")
                    and not os.path.lexists(campaign_root / "raw/slots/B0005-native"),
                    "next scheduled slot is already claimed")

        baseline = _private_file(Path(journal["baseline"]), "approved baseline snapshot")
        hil.require(baseline == journal_root / "inputs/baseline.bin"
                    and baseline.stat().st_size == FLASH_BYTES
                    and _sha256(baseline) == BASELINE_SHA256,
                    "approved baseline does not match the frozen 16 MiB hash")
        operation_dirs = [path for path in journal_root.iterdir()
                          if path.name.startswith("operation-")
                          and (path / "B0005-aot-after.bin").exists()]
        hil.require(len(operation_dirs) == 1
                    and re.fullmatch(r"operation-[0-9a-f]{32}", operation_dirs[0].name),
                    "restoration readback is missing or ambiguous")
        operation_dir = _private_dir(operation_dirs[0], "restoration operation directory")
        readback = _private_file(operation_dir / "B0005-aot-after.bin",
                                 "post-restoration full-flash readback")
        hil.require(readback.stat().st_size == FLASH_BYTES
                    and _sha256(readback) == BASELINE_SHA256,
                    "post-restoration full-flash readback differs from baseline")

        adjudications = hil.private_directory(journal_root / "adjudications")
        receipt_path = adjudications / "B0005-aot-prepare-timeout.json"
        prior_receipt = None
        if os.path.lexists(receipt_path):
            prior_receipt = _json(receipt_path, "existing adjudication receipt")
        saved_probe = ((prior_receipt.get("evidence") or {}).get("authenticated_remote_status")
                       if prior_receipt is not None else None)
        evidence = _slot_evidence(campaign_root, failed_slot, journal, journal["options"],
                                  saved_probe=saved_probe)
        batch_path = _private_file(journal_root / "batch.json", "blocked batch status")
        batch = _json(batch_path, "blocked batch status")
        tools_now = hil.tooling_snapshot()
        hil.require(batch.get("state") == "blocked"
                    and batch.get("restore_required") is False
                    and batch.get("next_index") == next_index
                    and batch.get("tooling_sha256") == tools_now
                    and batch.get("events")
                    and batch["events"][-1].get("before_index") == next_index - 1
                    and batch["events"][-1].get("after_index") == next_index
                    and type(batch["events"][-1].get("exit_code")) is int
                    and batch["events"][-1]["exit_code"] != 0,
                    "blocked batch does not match the failed child and frozen tooling")

        receipt = {
            "schema_version": 1,
            "adjudication": "pre_start_prepare_timeout_remote_session_absent",
            "campaign_id": journal["campaign_id"],
            "git_commit": journal["git_commit"],
            "slot_id": "B0005-aot",
            "failed_index": next_index - 1,
            "next_index_before": next_index,
            "next_index_after": next_index,
            "accepted": False,
            "retry_permitted": False,
            "journal_transition": {"from": "slot_failed", "to": "ready"},
            "claim_sha256": _sha256(claim_path),
            "evidence": evidence,
            "baseline": {"size_bytes": baseline.stat().st_size,
                         "sha256": _sha256(baseline)},
            "post_restore_readback": {"size_bytes": readback.stat().st_size,
                                       "sha256": _sha256(readback),
                                       "matches_baseline": True},
            "batch_status_sha256": _sha256(batch_path),
            "tooling_sha256": tools_now,
            "recorded_at_utc": hil.campaign._utc_now(),
        }
        if os.path.lexists(receipt_path):
            prior = _json(receipt_path, "existing adjudication receipt")
            comparable, previous = copy.deepcopy(receipt), copy.deepcopy(prior)
            comparable.pop("recorded_at_utc", None)
            recorded_at = previous.pop("recorded_at_utc", None)
            hil.require(isinstance(recorded_at, str) and previous == comparable,
                        "existing adjudication receipt differs from verified evidence")
            receipt = prior
        else:
            hil.write_private(receipt_path, receipt, exclusive=True)

        updated = copy.deepcopy(journal)
        updated["events"].append({
            "slot_id": "B0005-aot",
            "state": "terminal_prepare_timeout_adjudicated",
            "error": "RemoteObserverError",
            "accepted": False,
            "retry_permitted": False,
            "remote_session_absent": True,
            "next_index_before": next_index,
            "next_index_after": next_index,
            "receipt": str(receipt_path),
            "receipt_sha256": _sha256(receipt_path),
        })
        updated["state"] = "ready"
        hil.require(updated["next_index"] == next_index,
                    "adjudication may not advance or rewind the schedule")
        hil.write_private(journal_path, updated)
        return {"state": "ready", "slot_id": "B0005-aot", "accepted": False,
                "retry_permitted": False, "next_index": next_index,
                "next_slot": "B0005-native", "receipt": str(receipt_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-root", required=True)
    options = parser.parse_args()
    try:
        print(json.dumps(adjudicate(Path(options.campaign_root)), sort_keys=True))
        return 0
    except (hil.HilError, hil.campaign.CampaignError, OSError, ValueError, KeyError) as exc:
        print(f"Adjudication stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
