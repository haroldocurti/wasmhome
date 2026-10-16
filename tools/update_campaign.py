#!/usr/bin/env python3
"""Minimal reproducible planning, preflight, run-slot, and analysis workflow.

Planning and preflight are offline by default. ``run-slot`` is the only command
that opens hardware/network resources, and performs exactly one observer run.
Raw observations are retained; analysis writes only derived files.
"""
from __future__ import annotations

import argparse
import base64
import csv
from datetime import datetime, timezone
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import random
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid


SCHEMA_VERSION = 1
ISSUE20_PROTOCOL_REVISION = "issue20-v1.0"
ISSUE20_TIMEOUT_MS = 300
ISSUE20_PERIOD_MS = 100
ISSUE20_MAX_SAMPLE_AGE_MS = 1000
ISSUE20_STABLE_WINDOW_MS = 60_000
ISSUE20_MIN_STABLE_CONTROLS = 30
ISSUE20_MIN_STABLE_TELEMETRY = 30
ISSUE20_UPDATE_DEADLINE_MS = 60_000
ISSUE20_POST_WINDOW_MS = 30_000
ISSUE20_TRIGGER_AFTER_SECONDS = 60
ISSUE20_CAPTURE_SECONDS = 150
SCENARIOS = ("native", "interp", "aot")
SCENARIO_CODES = {"native": 1, "interp": 2, "aot": 3}
PERMUTATIONS = tuple(itertools.permutations(SCENARIOS))
DISPATCH_STATUSES = {"not_sent", "accepted", "rejected", "acceptance_timeout"}
INSTRUMENT_STATUSES = {"valid", "invalid", "unresolved"}
ATTEMPT_STATUSES = {"not_applicable", "not_evaluable", "success", "recovery_failure",
                    "incorrect_result", "update_timeout"}
SHA256_RE = __import__("re").compile(r"^[0-9a-f]{64}$")


class CampaignError(ValueError):
    """A campaign file or operation failed a precondition."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CampaignError(message)


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False,
                       allow_nan=False) + "\n").encode("utf-8")


def _write_json(path: Path, value: object, *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "xb" if exclusive else "wb"
    with path.open(mode) as stream:
        stream.write(_json_bytes(value))


def _json_loads_strict(text: str, label: str):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError(f"non-finite JSON number: {value}")

    try:
        return json.loads(text, object_pairs_hook=unique_object, parse_constant=reject_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise CampaignError(f"{label}: cannot read valid UTF-8 JSON: {exc}") from exc


def _read_json(path: Path, label: str) -> dict:
    try:
        value = _json_loads_strict(path.read_text(encoding="utf-8"), label)
    except (OSError, UnicodeError) as exc:
        raise CampaignError(f"{label}: cannot read valid UTF-8 JSON: {exc}") from exc
    _require(isinstance(value, dict), f"{label}: top-level JSON value must be an object")
    return value


def _read_jsonl(path: Path, label: str) -> list[dict]:
    """Read, validate, and return every JSONL row without rewriting source bytes."""
    rows = []
    try:
        with path.open("r", encoding="utf-8", newline="") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    raise CampaignError(f"{label}:{line_number}: blank JSONL record")
                row = _json_loads_strict(line, f"{label}:{line_number}")
                _require(isinstance(row, dict), f"{label}:{line_number}: JSONL record must be an object")
                rows.append(row)
    except (OSError, UnicodeError) as exc:
        raise CampaignError(f"{label}: cannot read UTF-8 JSONL: {exc}") from exc
    return rows


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise CampaignError(f"cannot hash {path}: {exc}") from exc
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _repo_info(root: Path) -> dict:
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                                check=True, capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CampaignError(f"cannot determine repository revision: {exc}") from exc
    _require(len(commit) == 40 and all(c in "0123456789abcdef" for c in commit.lower()),
             "repository HEAD is not a full Git commit hash")
    script = Path(__file__).resolve()
    return {"git_commit": commit.lower(), "script_path": script.relative_to(root.resolve()).as_posix(),
            "script_sha256": _sha256_file(script)}


def _load_artifacts(path: Path) -> tuple[dict, bytes]:
    manifest = _read_json(path, "artifacts manifest")
    _require(manifest.get("schema_version") == SCHEMA_VERSION,
             "artifacts manifest: unsupported schema_version")
    rows = manifest.get("artifacts")
    _require(isinstance(rows, list) and rows, "artifacts manifest: artifacts must be a non-empty list")
    seen = set()
    coverage = set()
    normalized = []
    for index, row in enumerate(rows):
        label = f"artifacts[{index}]"
        _require(isinstance(row, dict), f"{label}: expected object")
        artifact_id = row.get("artifact_id")
        scenario = row.get("scenario")
        source_path = row.get("path")
        digest = row.get("sha256")
        _require(isinstance(artifact_id, str) and artifact_id.strip(), f"{label}: artifact_id is required")
        _require(artifact_id not in seen, f"{label}: duplicate artifact_id {artifact_id}")
        seen.add(artifact_id)
        _require(scenario in SCENARIOS, f"{label}: scenario must be one of {SCENARIOS}")
        _require(isinstance(source_path, str) and source_path.strip(), f"{label}: path is required")
        resolved = Path(source_path)
        if not resolved.is_absolute():
            resolved = path.resolve().parent / resolved
        resolved = resolved.resolve()
        _require(resolved.is_file(), f"{label}: artifact file does not exist: {resolved}")
        actual = _sha256_file(resolved)
        _require(isinstance(digest, str) and SHA256_RE.fullmatch(digest), f"{label}: invalid SHA-256")
        _require(actual == digest, f"{label}: SHA-256 mismatch for {resolved}")
        row_copy = dict(row)
        row_copy["path"] = str(resolved)
        row_copy["sha256"] = actual
        row_copy["size_bytes"] = resolved.stat().st_size
        normalized.append(row_copy)
        coverage.add(scenario)
    _require(coverage == set(SCENARIOS), "artifacts manifest must cover native, interp, and aot")
    normalized_manifest = dict(manifest)
    normalized_manifest["artifacts"] = normalized
    return normalized_manifest, _json_bytes(normalized_manifest)


def _validate_metadata(metadata: dict, n: int, seed: str) -> dict:
    _require(isinstance(metadata.get("protocol_revision"), str)
             and metadata["protocol_revision"].strip(),
             "metadata.protocol_revision must be a non-empty string")
    for key in ("bench", "toolchains", "observation", "environment"):
        if key in metadata:
            _require(isinstance(metadata[key], dict), f"metadata.{key} must be an object")
    if "N" in metadata:
        _require(metadata["N"] == n, "metadata.N differs from --n")
    if "seed" in metadata:
        _require(metadata["seed"] == seed, "metadata.seed differs from --seed")
    # Ensure the user-supplied metadata is representable as standards-compliant JSON.
    try:
        json.dumps(metadata, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise CampaignError(f"metadata contains a non-JSON value: {exc}") from exc
    metadata = dict(metadata)
    metadata["N"] = n
    metadata["seed"] = seed
    return metadata


def make_schedule(n: int, seed: str) -> dict:
    _require(isinstance(n, int) and not isinstance(n, bool) and n > 0,
             "N must be a positive integer")
    _require(isinstance(seed, str) and seed.strip(), "seed must be a non-empty string")
    # Hashing the textual seed makes the PRNG seed stable across Python versions.
    seed_digest = hashlib.sha256(seed.encode("utf-8")).digest()
    rng = random.Random(int.from_bytes(seed_digest, "big"))
    q, remainder = divmod(n, len(PERMUTATIONS))
    block_orders = list(PERMUTATIONS) * q + list(PERMUTATIONS[:remainder])
    rng.shuffle(block_orders)
    slots = []
    for block_index, order in enumerate(block_orders, start=1):
        block_id = f"B{block_index:04d}"
        for position, scenario in enumerate(order, start=1):
            slots.append({"slot_id": f"{block_id}-{scenario}", "block_id": block_id,
                          "scenario": scenario, "order_in_block": position})
    schedule = {
        "schema_version": SCHEMA_VERSION,
        "N": n,
        "seed": seed,
        "generation_method": "sha256-seed/python-random/shuffle-six-permutations-v1",
        "permutations": [list(item) for item in PERMUTATIONS],
        "permutation_counts": {" ".join(item): block_orders.count(item) for item in PERMUTATIONS},
        "slots": slots,
    }
    validate_schedule(schedule)
    return schedule


def validate_schedule(schedule: dict) -> None:
    _require(schedule.get("schema_version") == SCHEMA_VERSION, "schedule: unsupported schema_version")
    n = schedule.get("N")
    _require(isinstance(n, int) and not isinstance(n, bool) and n > 0,
             "schedule: N must be a positive integer")
    _require(isinstance(schedule.get("seed"), str) and schedule["seed"], "schedule: seed is missing")
    _require(schedule.get("permutations") == [list(p) for p in PERMUTATIONS],
             "schedule: six scenario permutations differ from the expected set")
    slots = schedule.get("slots")
    _require(isinstance(slots, list) and len(slots) == 3 * n,
             f"schedule: expected exactly {3 * n} slots")
    _require(len({row.get("slot_id") for row in slots if isinstance(row, dict)}) == len(slots),
             "schedule: slot_id values must be unique")
    grouped: dict[str, list[dict]] = {}
    for index, row in enumerate(slots):
        _require(isinstance(row, dict), f"schedule.slots[{index}] must be an object")
        block_id = row.get("block_id")
        scenario = row.get("scenario")
        _require(isinstance(block_id, str) and block_id, f"schedule.slots[{index}]: block_id is required")
        _require(scenario in SCENARIOS, f"schedule.slots[{index}]: unknown scenario")
        _require(row.get("slot_id") == f"{block_id}-{scenario}",
                 f"schedule.slots[{index}]: slot_id does not match block and scenario")
        _require(isinstance(row.get("order_in_block"), int)
                 and not isinstance(row.get("order_in_block"), bool),
                 f"schedule.slots[{index}]: order_in_block must be an integer")
        grouped.setdefault(block_id, []).append(row)
    _require(len(grouped) == n, "schedule: block count must equal N")
    observed_orders = []
    expected_blocks = [f"B{i:04d}" for i in range(1, n + 1)]
    _require(set(grouped) == set(expected_blocks), "schedule: block IDs must be contiguous and one-based")
    for block_id in expected_blocks:
        rows = grouped[block_id]
        _require(len(rows) == 3 and {r["scenario"] for r in rows} == set(SCENARIOS),
                 f"schedule: {block_id} must contain one slot per scenario")
        rows.sort(key=lambda row: row["order_in_block"])
        _require([row["order_in_block"] for row in rows] == [1, 2, 3],
                 f"schedule: {block_id} order_in_block must be 1, 2, 3")
        observed_orders.append(tuple(row["scenario"] for row in rows))
    _require(set(observed_orders) <= set(PERMUTATIONS), "schedule: block order is not a valid permutation")
    counts = {" ".join(p): observed_orders.count(p) for p in PERMUTATIONS}
    _require(max(counts.values()) - min(counts.values()) <= 1,
             "schedule: six permutation counts must differ by at most one")
    _require(schedule.get("permutation_counts") == counts,
             "schedule: recorded permutation_counts do not match slots")


def plan(output: Path, metadata_path: Path, artifacts_path: Path, n: int, seed: str) -> dict:
    output = output.resolve()
    _require(not output.exists(), f"plan output already exists: {output}")
    metadata = _validate_metadata(_read_json(metadata_path, "metadata"), n, seed)
    artifacts, artifacts_bytes = _load_artifacts(artifacts_path)
    repo = _repo_info(Path(__file__).resolve().parents[1])
    schedule = make_schedule(n, seed)
    schedule_bytes = _json_bytes(schedule)
    campaign_id = metadata.get("campaign_id")
    if campaign_id is None:
        campaign_id = str(uuid.uuid4())
    try:
        campaign_id = str(uuid.UUID(str(campaign_id)))
    except (TypeError, ValueError) as exc:
        raise CampaignError("metadata.campaign_id must be a UUID") from exc
    campaign = {
        "schema_version": SCHEMA_VERSION,
        "campaign_id": campaign_id,
        "created_at_utc": _utc_now(),
        "protocol_revision": metadata["protocol_revision"],
        "N": n,
        "scenario_codes": SCENARIO_CODES,
        "metadata": metadata,
        **repo,
        "schedule_sha256": _sha256_bytes(schedule_bytes),
        "artifacts_manifest_sha256": _sha256_bytes(artifacts_bytes),
        "source_metadata_sha256": _sha256_file(metadata_path),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir()
    _write_json(output / "schedule.json", schedule, exclusive=True)
    _write_json(output / "artifacts.json", artifacts, exclusive=True)
    _write_json(output / "campaign.json", campaign, exclusive=True)
    return {"campaign_id": campaign_id, "N": n, "planned_slots": 3 * n,
            "permutation_counts": schedule["permutation_counts"], "output": str(output)}


def _load_campaign(root: Path) -> tuple[dict, dict, dict]:
    root = root.resolve()
    campaign = _read_json(root / "campaign.json", "campaign.json")
    schedule = _read_json(root / "schedule.json", "schedule.json")
    artifacts = _read_json(root / "artifacts.json", "artifacts.json")
    _require(campaign.get("schema_version") == SCHEMA_VERSION, "campaign.json: unsupported schema_version")
    _require(campaign.get("N") == schedule.get("N"), "campaign.json N differs from schedule.json")
    _require(campaign.get("scenario_codes") == SCENARIO_CODES,
             "campaign.json scenario_codes differ from the protocol")
    validate_schedule(schedule)
    _require(campaign.get("schedule_sha256") == _sha256_file(root / "schedule.json"),
             "schedule.json SHA-256 differs from campaign.json")
    _require(campaign.get("artifacts_manifest_sha256") == _sha256_file(root / "artifacts.json"),
             "artifacts.json SHA-256 differs from campaign.json")
    _validate_artifact_manifest(artifacts)
    return campaign, schedule, artifacts


def _validate_repository(campaign: dict) -> dict:
    current_repo = _repo_info(Path(__file__).resolve().parents[1])
    _require(current_repo["git_commit"] == campaign.get("git_commit"),
             "repository HEAD differs from campaign.git_commit")
    _require(current_repo["script_path"] == campaign.get("script_path"),
             "tools/update_campaign.py path differs from campaign metadata")
    _require(current_repo["script_sha256"] == campaign.get("script_sha256"),
             "tools/update_campaign.py SHA-256 differs from campaign metadata")
    return current_repo


def _validate_artifact_manifest(artifacts: dict) -> None:
    _require(artifacts.get("schema_version") == SCHEMA_VERSION,
             "artifacts.json: unsupported schema_version")
    rows = artifacts.get("artifacts")
    _require(isinstance(rows, list) and rows, "artifacts.json: artifacts must be a non-empty list")
    seen = set()
    coverage = set()
    for i, row in enumerate(rows):
        _require(isinstance(row, dict), f"artifacts.json artifacts[{i}] must be an object")
        artifact_id, scenario, source, expected = (row.get("artifact_id"), row.get("scenario"),
                                                   row.get("path"), row.get("sha256"))
        _require(isinstance(artifact_id, str) and artifact_id and artifact_id not in seen,
                 f"artifacts.json artifacts[{i}] has missing or duplicate artifact_id")
        seen.add(artifact_id)
        _require(scenario in SCENARIOS, f"artifacts.json artifacts[{i}] has unknown scenario")
        _require(isinstance(source, str) and Path(source).is_absolute(),
                 f"artifacts.json artifacts[{i}] path must be absolute")
        _require(isinstance(expected, str) and SHA256_RE.fullmatch(expected),
                 f"artifacts.json artifacts[{i}] has invalid SHA-256")
        path = Path(source)
        _require(path.is_file(), f"artifact file does not exist: {path}")
        _require(_sha256_file(path) == expected, f"artifact SHA-256 mismatch: {path}")
        _require(row.get("size_bytes") == path.stat().st_size, f"artifact size mismatch: {path}")
        coverage.add(scenario)
    _require(coverage == set(SCENARIOS), "artifacts.json must cover all three scenarios")


def _probe_output(output: Path, minimum_free_bytes: int) -> dict:
    output = output.expanduser().resolve()
    target = output if output.exists() else output.parent
    _require(target.is_dir(), f"output parent directory does not exist: {target}")
    free = shutil.disk_usage(target).free
    _require(free >= minimum_free_bytes,
             f"insufficient output space: {free} bytes available, {minimum_free_bytes} required")
    probe = None
    try:
        fd, probe_name = tempfile.mkstemp(prefix=".campaign-write-probe-", dir=target)
        probe = Path(probe_name)
        os.write(fd, b"ok")
        os.close(fd)
    except OSError as exc:
        raise CampaignError(f"output is not writable: {target}: {exc}") from exc
    finally:
        if probe is not None:
            probe.unlink(missing_ok=True)
    return {"directory": str(output), "writable": True, "free_bytes": free,
            "minimum_free_bytes": minimum_free_bytes}


def preflight(root: Path, output: Path, minimum_free_bytes: int = 0,
              serial_port: str | None = None, serial_baud: int = 115200,
              probe_serial: bool = False, network_host: str | None = None,
              network_port: int | None = None, probe_network: bool = False,
              network_timeout: float = 2.0) -> dict:
    campaign, schedule, artifacts = _load_campaign(root)
    current_repo = _validate_repository(campaign)
    output_report = _probe_output(output, minimum_free_bytes)
    probes = {"serial": "not_requested", "network": "not_requested"}
    if probe_serial:
        _require(bool(serial_port), "--probe-serial requires --serial-port")
        try:
            import serial  # type: ignore[import-not-found]
            with serial.Serial(serial_port, serial_baud, timeout=0.2):
                pass
        except Exception as exc:
            raise CampaignError(f"serial probe failed for {serial_port}: {exc}") from exc
        probes["serial"] = "passed"
    if probe_network:
        _require(bool(network_host) and isinstance(network_port, int),
                 "--probe-network requires --network-host and --network-port")
        try:
            with socket.create_connection((network_host, network_port), timeout=network_timeout):
                pass
        except OSError as exc:
            raise CampaignError(f"network probe failed for {network_host}:{network_port}: {exc}") from exc
        probes["network"] = "passed"
    return {"result": "passed_with_device_checks_pending", "measurement_ready": False,
            "device_readiness": {"status": "not_verified", "missing": ["boot_id", "active_version"],
                                 "reason": "no device evidence input is supported; serial/network probes only test connectivity"},
            "campaign_id": campaign["campaign_id"],
            "planned_slots": len(schedule["slots"]), "artifact_count": len(artifacts["artifacts"]),
            "output": output_report, "probes": probes,
            "repo": current_repo}


def _slot_row(schedule: dict, slot_id: str) -> dict:
    matches = [row for row in schedule["slots"] if row["slot_id"] == slot_id]
    _require(len(matches) == 1, f"slot_id is not present exactly once in schedule: {slot_id}")
    return matches[0]


def _start_serial_capture(port: str, baud: int, output: Path):
    """Open serial before the observer starts and return its capture controller."""
    ready = threading.Event()
    stop = threading.Event()
    state: dict = {"error": None, "thread": None}

    def capture() -> None:
        serial_device = None
        try:
            import serial  # type: ignore[import-not-found]
            serial_device = serial.Serial(port, baudrate=baud, timeout=0.1)
            with output.open("xb") as stream:
                stream.write(_json_bytes({"kind": "serial_open", "host_monotonic_ns": time.monotonic_ns(),
                                          "port_alias": Path(port).name, "baud": baud}))
                stream.flush()
                ready.set()
                while not stop.is_set():
                    data = serial_device.read(max(1, min(getattr(serial_device, "in_waiting", 0), 4096)))
                    if data:
                        row = {"kind": "serial_bytes", "host_monotonic_ns": time.monotonic_ns(),
                               "byte_count": len(data), "raw_base64": base64.b64encode(data).decode("ascii")}
                        stream.write(_json_bytes(row))
                        stream.flush()
                stream.write(_json_bytes({"kind": "serial_closed", "host_monotonic_ns": time.monotonic_ns()}))
                stream.flush()
        except Exception as exc:  # propagate background failures before dispatch
            state["error"] = exc
            ready.set()
        finally:
            if serial_device is not None:
                try:
                    serial_device.close()
                except Exception:
                    pass

    worker = threading.Thread(target=capture, name="campaign-serial-capture", daemon=True)
    state["thread"] = worker
    worker.start()
    if not ready.wait(5.0):
        stop.set()
        raise CampaignError("serial capture did not become ready")
    if state["error"] is not None:
        raise CampaignError(f"serial capture failed before dispatch: {state['error']}")
    return stop, worker


def _observer_attempt_id(observations: Path, summary: dict) -> str | None:
    attempt_ids = set()
    for row in _read_jsonl(observations, "observations.jsonl"):
        packet = row.get("packet")
        if isinstance(packet, dict) and packet.get("message_type") == "UPDATE_TRIGGER":
            attempt_ids.add(packet.get("attempt_id"))
        if row.get("kind") == "decoded" and isinstance(row.get("packet"), dict):
            packet = row["packet"]
            if packet.get("message_type") == "UPDATE_ACCEPTANCE":
                attempt_ids.add(packet.get("attempt_id"))
    attempt_ids.discard(None)
    _require(len(attempt_ids) <= 1, "observer recorded more than one trigger attempt UUID")
    return next(iter(attempt_ids), None)


def run_slot(campaign_root: Path, slot_id: str, *, device_ip: str, serial_port: str,
             seconds: float, trigger_after: float, target_sequence: int,
             bind_ip: str = "0.0.0.0", udp_port: int = 3333, device_port: int = 3333,
             serial_baud: int = 115200, server_log: Path | None = None,
             period_ms: float = 100, timeout_ms: float = 300,
             max_sample_age_ms: int = 1000) -> dict:
    campaign, schedule, _ = _load_campaign(campaign_root)
    _validate_repository(campaign)
    slot = _slot_row(schedule, slot_id)
    _require(isinstance(device_ip, str) and device_ip, "device_ip is required")
    _require(isinstance(serial_port, str) and serial_port, "serial_port is required")
    _require(math.isfinite(seconds) and seconds > 0, "seconds must be positive")
    _require(math.isfinite(trigger_after) and 0 <= trigger_after < seconds,
             "trigger_after must be within the observer run")
    _require(isinstance(target_sequence, int) and not isinstance(target_sequence, bool)
             and 0 < target_sequence < 2**64, "target_sequence must be positive uint64")
    _require(0 <= udp_port <= 65535 and 1 <= device_port <= 65535,
             "invalid UDP port")
    root = campaign_root.resolve()
    issue20 = campaign.get("protocol_revision") == ISSUE20_PROTOCOL_REVISION
    if issue20:
        campaign_metadata = campaign.get("metadata")
        _require(isinstance(campaign_metadata, dict)
                 and campaign_metadata.get("campaign_type") in {"pilot", "normal"},
                 f"{ISSUE20_PROTOCOL_REVISION} requires metadata.campaign_type "
                 "to be 'pilot' or 'normal'")
        expected_blocks = 6 if campaign_metadata["campaign_type"] == "pilot" else 30
        _require(campaign.get("N") == expected_blocks,
                 f"{ISSUE20_PROTOCOL_REVISION} requires N={expected_blocks} blocks "
                 f"for campaign_type={campaign_metadata['campaign_type']!r}")
        _require(seconds == ISSUE20_CAPTURE_SECONDS,
                 f"{ISSUE20_PROTOCOL_REVISION} requires --seconds {ISSUE20_CAPTURE_SECONDS}")
        _require(trigger_after == ISSUE20_TRIGGER_AFTER_SECONDS,
                 f"{ISSUE20_PROTOCOL_REVISION} requires --trigger-after {ISSUE20_TRIGGER_AFTER_SECONDS}")
        _require(period_ms == ISSUE20_PERIOD_MS and timeout_ms == ISSUE20_TIMEOUT_MS
                 and max_sample_age_ms == ISSUE20_MAX_SAMPLE_AGE_MS,
                 f"{ISSUE20_PROTOCOL_REVISION} requires period/timeout/max-age "
                 f"{ISSUE20_PERIOD_MS}/{ISSUE20_TIMEOUT_MS}/{ISSUE20_MAX_SAMPLE_AGE_MS} ms")
    output = root / "raw" / "slots" / slot_id
    _require(not output.exists(), f"slot output already exists; no retry is allowed: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir()

    stop, worker = _start_serial_capture(serial_port, serial_baud, output / "device.jsonl")
    observer_summary = None
    try:
        # Import lazily so plan/preflight/analyze never import serial, bind UDP, or resolve a device.
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from external_observer import run as observer_run
        observer_summary = observer_run(
            device_ip=device_ip, output=output, seconds=seconds, bind_ip=bind_ip,
            port=udp_port, device_port=device_port, period_ms=period_ms,
            timeout_ms=timeout_ms, initial_version=1, trigger_after=trigger_after,
            target_sequence=target_sequence, scenario=SCENARIO_CODES[slot["scenario"]],
            max_sample_age_ms=max_sample_age_ms,
            update_deadline_ms=ISSUE20_UPDATE_DEADLINE_MS if issue20 else None,
            post_window_ms=ISSUE20_POST_WINDOW_MS if issue20 else None,
            stable_window_ms=ISSUE20_STABLE_WINDOW_MS if issue20 else None,
            minimum_stable_controls=ISSUE20_MIN_STABLE_CONTROLS if issue20 else 0,
            minimum_stable_telemetry=ISSUE20_MIN_STABLE_TELEMETRY if issue20 else 0)
    finally:
        stop.set()
        worker.join(timeout=2.0)
        _require(not worker.is_alive(), "serial capture thread did not stop cleanly")

    attempt_id = _observer_attempt_id(output / "observations.jsonl", observer_summary or {})
    rows = _read_jsonl(output / "observations.jsonl", "observations.jsonl")
    trigger_intents = [r for r in rows if r.get("kind") == "send_intent"
                       and isinstance(r.get("packet"), dict)
                       and r["packet"].get("message_type") == "UPDATE_TRIGGER"]
    _require(len(trigger_intents) <= 1, "observer emitted more than one UPDATE_TRIGGER")
    # The event stream includes all outbound packets; correlate raw bytes with the trigger intent.
    trigger_was_sent = bool(trigger_intents) and any(
        r.get("kind") == "send" and r.get("hex") and r.get("peer")
        and r.get("planned_ns") is None
        and r.get("hex") == next((x.get("hex") for x in rows
                                     if x.get("kind") == "send_intent"
                                     and isinstance(x.get("packet"), dict)
                                     and x["packet"].get("message_type") == "UPDATE_TRIGGER"), None)
        for r in rows)
    acceptance = (observer_summary or {}).get("update_state")
    counters = (observer_summary or {}).get("counters", {})
    if not trigger_intents or (counters.get("send_error") and not trigger_was_sent):
        dispatch_status = "not_sent"
    elif counters.get("update_accepted", 0):
        dispatch_status = "accepted"
    elif counters.get("update_rejected", 0):
        dispatch_status = "rejected"
    else:
        dispatch_status = "acceptance_timeout"
    late_acceptance = bool(counters.get("late_acceptance", 0))
    dispatch = {
        "attempt_id": attempt_id,
        "dispatch_status": dispatch_status,
        "late_acceptance_observed": late_acceptance,
        "instrument_status": None,
        "instrument_status_null_reason": "operator adjudication required from archived evidence",
        "sent": trigger_was_sent,
        "observed_update_state": acceptance,
        "observer_summary_path": "summary.json",
    }
    accepted_status = None if dispatch_status == "accepted" else "not_applicable"
    accepted_reason = ("terminal attempt result requires protocol adjudication; run-slot preserves observer evidence"
                      if accepted_status is None else None)
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "campaign_id": campaign["campaign_id"],
        "slot_id": slot_id,
        "block_id": slot["block_id"],
        "scenario": slot["scenario"],
        "order_in_block": slot["order_in_block"],
        "protocol_revision": campaign["protocol_revision"],
        "campaign_type": campaign.get("metadata", {}).get("campaign_type"),
        "observer_run_id": str(uuid.uuid4()),
        # Every scheduled run has a dispatch record, including the case where
        # boot never appeared and the observer therefore sent no trigger.
        "dispatches": [dispatch],
        "accepted_attempt_ids": [attempt_id] if dispatch_status == "accepted" and attempt_id else [],
        "accepted_attempt_status": accepted_status,
        "accepted_attempt_status_null_reason": accepted_reason,
        "slot_success": None,
        "slot_success_null_reason": "requires instrument and terminal outcome adjudication",
        "raw_files": {},
    }
    if server_log is not None:
        source = server_log.resolve()
        _require(source.is_file(), f"server log does not exist: {source}")
        destination = output / "server.jsonl"
        _require(source != destination.resolve(), "server log source cannot be the slot output file")
        shutil.copyfile(source, destination)
    for name in ("observations.jsonl", "summary.json", "device.jsonl", "server.jsonl"):
        path = output / name
        if path.is_file():
            metadata["raw_files"][name] = {"sha256": _sha256_file(path), "size_bytes": path.stat().st_size}
    _write_json(output / "slot.json", metadata, exclusive=True)
    return {"campaign_id": campaign["campaign_id"], "slot_id": slot_id,
            "dispatch_status": dispatch_status, "attempt_id": attempt_id,
            "accepted_attempt_status": accepted_status, "output": str(output)}


def _load_slot_records(root: Path, schedule: dict, campaign: dict) -> list[tuple[dict, dict, dict]]:
    result = []
    seen_attempts = set()
    for scheduled in schedule["slots"]:
        path = root / "raw" / "slots" / scheduled["slot_id"] / "slot.json"
        row = _read_json(path, f"slot {scheduled['slot_id']}")
        for field in ("schema_version", "campaign_id", "slot_id", "block_id", "scenario", "order_in_block"):
            expected = SCHEMA_VERSION if field == "schema_version" else (
                campaign["campaign_id"] if field == "campaign_id" else scheduled[field])
            _require(row.get(field) == expected, f"{path}: {field} differs from campaign/schedule")
        raw_files = row.get("raw_files")
        _require(isinstance(raw_files, dict), f"{path}: raw_files must be an object")
        _require({"observations.jsonl", "summary.json", "device.jsonl"} <= set(raw_files),
                 f"{path}: archived slot must include raw UDP, observer summary, and serial log hashes")
        slot_dir = path.parent
        for filename, info in raw_files.items():
            _require(isinstance(filename, str) and Path(filename).name == filename,
                     f"{path}: raw_files contains a non-local filename")
            _require(isinstance(info, dict), f"{path}: raw_files[{filename}] must be an object")
            raw_path = slot_dir / filename
            _require(raw_path.is_file(), f"{path}: declared raw file is missing: {filename}")
            _require(info.get("sha256") == _sha256_file(raw_path),
                     f"{path}: raw file SHA-256 mismatch: {filename}")
            _require(info.get("size_bytes") == raw_path.stat().st_size,
                     f"{path}: raw file size mismatch: {filename}")
            if filename.endswith(".jsonl"):
                _read_jsonl(raw_path, f"{path.parent.name}/{filename}")
        dispatches = row.get("dispatches")
        _require(isinstance(dispatches, list) and dispatches,
                 f"{path}: every planned slot requires at least one dispatch record")
        accepted_ids = row.get("accepted_attempt_ids")
        _require(isinstance(accepted_ids, list), f"{path}: accepted_attempt_ids must be a list")
        _require(all(isinstance(item, str) for item in accepted_ids),
                 f"{path}: accepted_attempt_ids must contain UUID strings")
        _require(len(accepted_ids) == len(set(accepted_ids)), f"{path}: accepted_attempt_ids contains duplicates")
        for i, dispatch in enumerate(dispatches):
            _require(isinstance(dispatch, dict), f"{path}: dispatches[{i}] must be an object")
            status = dispatch.get("dispatch_status")
            _require(status in DISPATCH_STATUSES, f"{path}: dispatches[{i}] has invalid dispatch_status")
            instrument = dispatch.get("instrument_status")
            _require(instrument is None or instrument in INSTRUMENT_STATUSES,
                     f"{path}: dispatches[{i}] has invalid instrument_status")
            _require(instrument in {"valid", "invalid"},
                     f"{path}: analyze requires a resolved instrument_status")
            attempt_id = dispatch.get("attempt_id")
            if attempt_id is not None:
                try:
                    canonical = str(uuid.UUID(attempt_id))
                except (TypeError, ValueError) as exc:
                    raise CampaignError(f"{path}: invalid attempt_id") from exc
                _require(canonical == attempt_id, f"{path}: attempt_id must be canonical UUID text")
                _require(canonical not in seen_attempts, f"duplicate attempt_id across campaign: {canonical}")
                seen_attempts.add(canonical)
            _require((status == "not_sent") or attempt_id is not None,
                     f"{path}: dispatched trigger requires attempt_id")
            attempt_status = row.get("accepted_attempt_status")
            _require(attempt_status is None or attempt_status in ATTEMPT_STATUSES,
                     f"{path}: invalid accepted_attempt_status")
            if status == "accepted":
                _require(attempt_status in ATTEMPT_STATUSES - {"not_applicable"},
                         f"{path}: analyze requires a terminal accepted_attempt_status")
                if attempt_status == "not_evaluable":
                    _require(bool(row.get("accepted_attempt_status_null_reason")
                                  or row.get("accepted_attempt_status_reason")),
                             f"{path}: not_evaluable accepted_attempt_status needs a reason")
            else:
                _require(attempt_status == "not_applicable",
                         f"{path}: non-accepted dispatch must use not_applicable attempt status")
        expected_accepted_ids = [d.get("attempt_id") for d in dispatches
                                 if d.get("dispatch_status") == "accepted"]
        _require(accepted_ids == expected_accepted_ids,
                 f"{path}: accepted_attempt_ids differ from accepted dispatches")
        if not expected_accepted_ids:
            _require(row.get("accepted_attempt_status") == "not_applicable",
                     f"{path}: slot without an accepted dispatch must use not_applicable attempt status")
        if row.get("slot_success") is True:
            _require(len(expected_accepted_ids) > 0 and row.get("accepted_attempt_status") == "success"
                     and any(d.get("instrument_status") == "valid" for d in dispatches
                             if d.get("dispatch_status") == "accepted"),
                     f"{path}: slot_success true requires a valid, successful accepted attempt")
        slot_success = row.get("slot_success")
        _require(isinstance(slot_success, bool), f"{path}: analyze requires a terminal boolean slot_success")
        expected_success = any(
            dispatch.get("dispatch_status") == "accepted"
            and dispatch.get("instrument_status") == "valid"
            and row.get("accepted_attempt_status") == "success"
            for dispatch in dispatches)
        _require(slot_success is expected_success,
                 f"{path}: slot_success disagrees with valid accepted-attempt outcome")
        result.append((scheduled, row, {"path": str(path.relative_to(root))}))
    return result


def _csv_write(path: Path, fields: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def analyze(campaign_root: Path) -> dict:
    root = campaign_root.resolve()
    campaign, schedule, artifacts = _load_campaign(root)
    records = _load_slot_records(root, schedule, campaign)
    dispatch_rows = []
    slot_rows = []
    attempt_rows = []
    per_scenario = {}
    for scenario in SCENARIOS:
        planned = [record for record in records if record[0]["scenario"] == scenario]
        rows = [record[1] for record in planned]
        dispatches = [d for row in rows for d in row["dispatches"]]
        accepted = [d for d in dispatches if d["dispatch_status"] == "accepted"]
        accepted_status_counts = {status: sum(row.get("accepted_attempt_status") == status for row in rows)
                                  for status in sorted(ATTEMPT_STATUSES - {"not_applicable"})}
        valid_evaluable = sum(d.get("instrument_status") == "valid" for d in accepted)
        evaluable_status = sum(row.get("accepted_attempt_status") in ATTEMPT_STATUSES - {"not_applicable", "not_evaluable"}
                               for row in rows)
        per_scenario[scenario] = {
            "planned_slot_denominator": len(planned),
            "slot_success_numerator": sum(row.get("slot_success") is True for row in rows),
            "slot_success_observed": sum(isinstance(row.get("slot_success"), bool) for row in rows),
            "dispatch_denominator": len(dispatches),
            "dispatch_status_counts": {status: sum(d["dispatch_status"] == status for d in dispatches)
                                        for status in sorted(DISPATCH_STATUSES)},
            "accepted_attempt_denominator": len(accepted),
            "accepted_attempt_evaluable_denominator": evaluable_status,
            "accepted_attempt_instrument_valid_count": valid_evaluable,
            "accepted_attempt_status_counts": accepted_status_counts,
            "observer_control_timeouts": sum(_read_observer_summary(root, row).get("counters", {}).get("control_timeout", 0)
                                              for row in rows),
            "observer_lost_controls": sum(_read_observer_summary(root, row).get("lost_controls", 0)
                                            for row in rows),
            "observer_acceptance_timeouts": sum(_read_observer_summary(root, row).get("counters", {}).get("acceptance_timeout", 0)
                                                 for row in rows),
            "null_slot_success_count": sum(row.get("slot_success") is None for row in rows),
            "null_slot_success_reasons": sorted({row.get("slot_success_null_reason") for row in rows
                                                  if row.get("slot_success") is None}),
        }
        for scheduled, row, _meta in planned:
            slot_rows.append({
                "campaign_id": campaign["campaign_id"], "slot_id": scheduled["slot_id"],
                "block_id": scheduled["block_id"], "scenario": scenario,
                "order_in_block": scheduled["order_in_block"],
                "slot_success": _csv_value(row.get("slot_success")),
                "slot_success_null_reason": row.get("slot_success_null_reason"),
                "dispatch_count": len(row["dispatches"]),
                "accepted_attempt_count": sum(d["dispatch_status"] == "accepted" for d in row["dispatches"]),
                "accepted_attempt_status": row.get("accepted_attempt_status"),
                "accepted_attempt_status_null_reason": row.get("accepted_attempt_status_null_reason"),
            })
            for dispatch in row["dispatches"]:
                dispatch_rows.append({
                    "campaign_id": campaign["campaign_id"], "slot_id": scheduled["slot_id"],
                    "block_id": scheduled["block_id"], "scenario": scenario,
                    "attempt_id": dispatch.get("attempt_id"),
                    "dispatch_status": dispatch["dispatch_status"],
                    "late_acceptance_observed": dispatch.get("late_acceptance_observed"),
                    "instrument_status": dispatch.get("instrument_status"),
                    "instrument_status_null_reason": dispatch.get("instrument_status_null_reason"),
                })
                if dispatch["dispatch_status"] == "accepted":
                    attempt_rows.append({
                        "campaign_id": campaign["campaign_id"], "slot_id": scheduled["slot_id"],
                        "block_id": scheduled["block_id"], "scenario": scenario,
                        "attempt_id": dispatch.get("attempt_id"),
                        "accepted_attempt_status": row.get("accepted_attempt_status"),
                        "accepted_attempt_status_null_reason": row.get("accepted_attempt_status_null_reason"),
                        "instrument_status": dispatch.get("instrument_status"),
                        "instrument_status_null_reason": dispatch.get("instrument_status_null_reason"),
                        "observed_update_state": dispatch.get("observed_update_state"),
                    })

    processed = root / "processed"
    _csv_write(processed / "dispatches.csv", ["campaign_id", "slot_id", "block_id", "scenario", "attempt_id",
               "dispatch_status", "late_acceptance_observed", "instrument_status", "instrument_status_null_reason"], dispatch_rows)
    _csv_write(processed / "slots.csv", ["campaign_id", "slot_id", "block_id", "scenario", "order_in_block",
               "slot_success", "slot_success_null_reason", "dispatch_count", "accepted_attempt_count",
               "accepted_attempt_status", "accepted_attempt_status_null_reason"], slot_rows)
    _csv_write(processed / "attempts.csv", ["campaign_id", "slot_id", "block_id", "scenario", "attempt_id",
               "accepted_attempt_status", "accepted_attempt_status_null_reason", "instrument_status",
               "instrument_status_null_reason", "observed_update_state"], attempt_rows)
    analysis = {
        "schema_version": SCHEMA_VERSION,
        "campaign_id": campaign["campaign_id"],
        "schedule_sha256": _sha256_file(root / "schedule.json"),
        "analysis_script_sha256": _sha256_file(Path(__file__).resolve()),
        "planned_slots_total": len(records),
        "planned_slot_denominator_by_scenario": {name: per_scenario[name]["planned_slot_denominator"]
                                                 for name in SCENARIOS},
        "accepted_attempt_denominator_by_scenario": {name: per_scenario[name]["accepted_attempt_denominator"]
                                                      for name in SCENARIOS},
        "scenario_summaries": per_scenario,
        "null_handling": "null outcomes remain unevaluated and carry their recorded null reasons",
        "raw_data_modified": False,
    }
    _write_json(root / "analysis.json", analysis)
    manifest_paths = _package_files(root)
    lines = []
    for path in sorted(manifest_paths, key=lambda p: p.as_posix()):
        lines.append(f"{_sha256_file(path)}  {path.relative_to(root).as_posix()}")
    script_path = Path(__file__).resolve()
    lines.append(f"{_sha256_file(script_path)}  EXTERNAL_SCRIPT/tools/update_campaign.py")
    for artifact in artifacts["artifacts"]:
        lines.append(f"{artifact['sha256']}  EXTERNAL/{artifact['artifact_id']}")
    (root / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return analysis


def _csv_value(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def _read_observer_summary(root: Path, row: dict) -> dict:
    path = root / "raw" / "slots" / row["slot_id"] / "summary.json"
    summary = _read_json(path, f"observer summary {row['slot_id']}")
    _require(isinstance(summary.get("counters"), dict), f"observer summary {row['slot_id']}: counters missing")
    _require(isinstance(summary.get("lost_controls"), int) and summary["lost_controls"] >= 0,
             f"observer summary {row['slot_id']}: lost_controls missing or invalid")
    _require(isinstance(summary.get("update_state"), str),
             f"observer summary {row['slot_id']}: update_state missing")
    return summary


def _package_files(root: Path) -> list[Path]:
    excluded = {root / "SHA256SUMS"}
    files = [path for path in root.rglob("*") if path.is_file() and path not in excluded]
    # The manifest is computed after all derived outputs have been written.
    return files


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_plan = sub.add_parser("plan", help="validate metadata and emit a balanced schedule")
    p_plan.add_argument("--output", required=True, type=Path)
    p_plan.add_argument("--metadata", required=True, type=Path)
    p_plan.add_argument("--artifacts", required=True, type=Path)
    p_plan.add_argument("--n", required=True, type=int)
    p_plan.add_argument("--seed", required=True)

    p_pre = sub.add_parser("preflight", help="validate campaign files without hardware by default")
    p_pre.add_argument("campaign_root", type=Path)
    p_pre.add_argument("--output-dir", required=True, type=Path)
    p_pre.add_argument("--minimum-free-bytes", type=int, default=0)
    p_pre.add_argument("--probe-serial", action="store_true")
    p_pre.add_argument("--serial-port")
    p_pre.add_argument("--serial-baud", type=int, default=115200)
    p_pre.add_argument("--probe-network", action="store_true")
    p_pre.add_argument("--network-host")
    p_pre.add_argument("--network-port", type=int)
    p_pre.add_argument("--network-timeout", type=float, default=2.0)

    p_run = sub.add_parser("run-slot", help="run one explicitly scheduled slot once")
    p_run.add_argument("campaign_root", type=Path)
    p_run.add_argument("slot_id")
    p_run.add_argument("--device-ip", required=True)
    p_run.add_argument("--serial-port", required=True)
    p_run.add_argument("--serial-baud", type=int, default=115200)
    p_run.add_argument("--seconds", required=True, type=float)
    p_run.add_argument("--trigger-after", required=True, type=float)
    p_run.add_argument("--target-sequence", required=True, type=int)
    p_run.add_argument("--bind-ip", default="0.0.0.0")
    p_run.add_argument("--udp-port", type=int, default=3333)
    p_run.add_argument("--device-port", type=int, default=3333)
    p_run.add_argument("--period-ms", type=float, default=100)
    p_run.add_argument("--timeout-ms", type=float, default=300)
    p_run.add_argument("--max-sample-age-ms", type=int, default=1000)
    p_run.add_argument("--server-log", type=Path)

    p_analyze = sub.add_parser("analyze", help="validate archived package and rebuild derived summaries")
    p_analyze.add_argument("campaign_root", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            result = plan(args.output, args.metadata, args.artifacts, args.n, args.seed)
        elif args.command == "preflight":
            _require(args.minimum_free_bytes >= 0, "minimum_free_bytes cannot be negative")
            result = preflight(args.campaign_root, args.output_dir,
                               minimum_free_bytes=args.minimum_free_bytes,
                               serial_port=args.serial_port, serial_baud=args.serial_baud,
                               probe_serial=args.probe_serial, network_host=args.network_host,
                               network_port=args.network_port, probe_network=args.probe_network,
                               network_timeout=args.network_timeout)
        elif args.command == "run-slot":
            result = run_slot(args.campaign_root, args.slot_id, device_ip=args.device_ip,
                              serial_port=args.serial_port, seconds=args.seconds,
                              trigger_after=args.trigger_after, target_sequence=args.target_sequence,
                              bind_ip=args.bind_ip, udp_port=args.udp_port, device_port=args.device_port,
                              serial_baud=args.serial_baud, server_log=args.server_log,
                              period_ms=args.period_ms, timeout_ms=args.timeout_ms,
                              max_sample_age_ms=args.max_sample_age_ms)
        else:
            result = analyze(args.campaign_root)
    except (CampaignError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(2, f"error: {exc}\n")
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
