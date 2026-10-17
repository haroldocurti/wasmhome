#!/usr/bin/env python3
"""Unprivileged SSH transport for one persistent, non-restartable observer run.

Only explicit client calls open SSH. Importing this module is offline. The remote
worker uses the copied observer and protocol, with all run() parameters intact.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import socket
import subprocess
import sys
import time
import uuid


SCHEMA = "wasmhome-remote-observer-v1"
SOURCES = ("remote_observer.py", "external_observer.py",
           "observation_protocol_v1.py", "update_campaign.py")
LEASE_NS = 3_000_000_000
SERIAL_MAX_SILENCE_NS = 2_000_000_000
CHUNK_BYTES = 256 * 1024
TERMINAL = {"complete", "aborted", "failed"}
BOOTSTRAP = """import base64,hashlib,json,sys
r=json.load(sys.stdin)
b=base64.b64decode(r.pop('controller_source'),validate=True)
assert hashlib.sha256(b).hexdigest()==r['controller_sha256']
n={'__name__':'wasmhome_remote_rpc','__file__':'<ssh-controller>'}
exec(compile(b,'<ssh-controller>','exec'),n)
print(json.dumps(n['_remote_rpc'](r),allow_nan=False))
"""


class RemoteObserverError(ValueError):
    pass


def _require(value, message):
    if not value:
        raise RemoteObserverError(message)


def _encoded(value):
    return (json.dumps(value, sort_keys=True, allow_nan=False, indent=2) + "\n").encode()


def _loads(data):
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def invalid(value):
        raise RemoteObserverError(f"non-finite JSON constant: {value}")

    return json.loads(data, object_pairs_hook=pairs, parse_constant=invalid)


def _read(path):
    return _loads(Path(path).read_text(encoding="utf-8"))


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _file_info(path):
    _require(path.is_file() and not path.is_symlink(), f"not a regular file: {path.name}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    return {"size_bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def _write(path, value, *, exclusive=False):
    data = _encoded(value)
    if exclusive:
        with path.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        _sync_directory(path.parent)
        return
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def clock_domain(name):
    try:
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        boot = None
    return {"name": name, "hostname": socket.gethostname(),
            "host_boot_id": boot, "clock": "monotonic_ns"}


def validate_options(options):
    options.setdefault("root", ".local/share/wasmhome-hil")
    _require(re.fullmatch(r"wasmhome@[A-Za-z0-9][A-Za-z0-9.-]*", options["target"] or ""),
             "remote target must be an explicit wasmhome@HOST (never root or a Proxmox identity)")
    for field in ("known_hosts", "identity"):
        path = Path(options[field]).expanduser().resolve()
        _require(path.is_file(), f"remote {field} must name an existing file")
        options[field] = str(path)
        if field == "identity":
            _require(not path.stat().st_mode & 0o077, "SSH private identity must exclude group/other permissions")
    _require(1 <= options.get("ssh_port", 22) <= 65535, "invalid SSH port")
    root = Path(options.get("root", ".local/share/wasmhome-hil"))
    _require(str(root) not in {"", "."} and ".." not in root.parts,
             "remote root must be a dedicated directory without '..'")
    if options.get("session_id") is not None:
        _require(str(uuid.UUID(options["session_id"])) == options["session_id"],
                 "remote session ID must be a canonical UUID")
    return options


def _rpc(options, session_id, action, **values):
    controller = Path(options.get("controller_path", __file__)).read_bytes()
    request = {"schema": SCHEMA, "root": options["root"], "session_id": session_id,
               "action": action, "controller_source": base64.b64encode(controller).decode(),
               "controller_sha256": _digest(controller), **values}
    command = ["ssh", "-T", "-p", str(options.get("ssh_port", 22)),
               "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
               "-o", "UserKnownHostsFile=" + options["known_hosts"],
               "-o", "GlobalKnownHostsFile=/dev/null", "-o", "IdentitiesOnly=yes",
               "-o", "IdentityAgent=none", "-o", "PasswordAuthentication=no",
               "-o", "KbdInteractiveAuthentication=no", "-o", "ConnectTimeout=2",
               "-o", "ServerAliveInterval=1", "-o", "ServerAliveCountMax=1",
               "-i", options["identity"], "--", options["target"],
               shlex.join(["python3", "-c", BOOTSTRAP])]
    try:
        completed = subprocess.run(command, input=_encoded(request), capture_output=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RemoteObserverError(f"SSH {action} did not complete: {exc}") from exc
    _require(completed.returncode == 0,
             f"SSH {action} failed ({completed.returncode}): "
             + completed.stderr.decode("utf-8", "replace")[-2000:])
    try:
        result = _loads(completed.stdout.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise RemoteObserverError(f"SSH {action} returned invalid JSON") from exc
    _require(isinstance(result, dict) and result.get("schema") == SCHEMA
             and result.get("session_id") == session_id, "invalid remote response identity")
    return result


def _session_directory(request):
    import pwd
    _require(os.geteuid() != 0 and pwd.getpwuid(os.geteuid()).pw_name == "wasmhome",
             "worker must run as the normal wasmhome user")
    session_id = request["session_id"]
    _require(str(uuid.UUID(session_id)) == session_id, "session_id must be a canonical UUID")
    home = Path.home().resolve()
    root = Path(request["root"])
    _require(".." not in root.parts, "remote root contains '..'")
    if not root.is_absolute():
        root = home / root
    _require(root != home and root.is_relative_to(home), "remote root must be inside the user's home")
    # Refuse links instead of silently writing through an existing deployment.
    for path in (root, *root.parents):
        if path == home:
            break
        _require(not path.is_symlink(), "remote root contains a symlink")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    _require(root.stat().st_uid == os.geteuid() and not root.stat().st_mode & 0o077,
             "remote root must be owned by wasmhome and private (0700)")
    directory = root / session_id
    _require(not directory.is_symlink(), "remote session is a symlink")
    if directory.exists():
        _require(directory.is_dir() and directory.stat().st_uid == os.geteuid()
                 and not directory.stat().st_mode & 0o077, "remote session is not private/owned")
    return directory


def _validate_observer(config):
    expected = {"seconds": 150, "period_ms": 100, "timeout_ms": 300,
                "initial_version": 1, "trigger_after": 60, "max_sample_age_ms": 1000,
                "update_deadline_ms": 60000, "post_window_ms": 30000,
                "stable_window_ms": 60000, "minimum_stable_controls": 30,
                "minimum_stable_telemetry": 30}
    _require(set(config) == set(expected) | {"device_ip", "bind_ip", "port", "device_port",
                                           "scenario", "target_sequence"},
             "remote observer configuration has missing or unexpected fields")
    for field, value in expected.items():
        _require(type(config[field]) in (int, float) and config[field] == value,
                 f"remote issue20 worker requires {field}={value}")
    for field in ("device_ip", "bind_ip"):
        address = ipaddress.IPv4Address(config[field])
        _require(not (address.is_unspecified or address.is_multicast or address.is_loopback
                      or address.is_link_local or int(address) == 0xffffffff),
                 f"{field} must be an explicit LAN unicast IPv4")
        _require(address not in ipaddress.IPv4Network("100.64.0.0/10"),
                 f"{field} must be the LAN address, not an overlay/Tailscale address")
    for field in ("port", "device_port"):
        _require(type(config[field]) is int and 1024 <= config[field] <= 65535,
                 f"{field} must be an unprivileged UDP port")
    _require(type(config["scenario"]) is int and config["scenario"] in (1, 2, 3), "invalid scenario")
    _require(type(config["target_sequence"]) is int and 0 < config["target_sequence"] < 2**64,
             "target_sequence must be positive uint64")


def _process_identity(pid):
    try:
        # starttime protects against PID reuse; /proc is read without privileges.
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        if fields[0] == "Z":
            return None
        return {"pid": pid, "start_ticks": fields[19],
                "host_boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip()}
    except (OSError, IndexError):
        return None


def _lease_deadline(challenge_created_ns, serial_age_ns, now_ns):
    """Validate challenge RTT and return a CT-clock-only conservative deadline."""
    _require(type(challenge_created_ns) is int and challenge_created_ns >= 0,
             "invalid remote challenge timestamp")
    _require(type(now_ns) is int and now_ns >= challenge_created_ns,
             "remote challenge timestamp is from the future")
    _require(type(serial_age_ns) is int and 0 <= serial_age_ns < SERIAL_MAX_SILENCE_NS,
             "a fresh UART byte timestamp is required to renew the observer lease")
    challenge_rtt_ns = now_ns - challenge_created_ns
    _require(serial_age_ns + challenge_rtt_ns < SERIAL_MAX_SILENCE_NS,
             "UART evidence plus remote challenge RTT is expired")

    # Anchor expiry at the CT timestamp from before the laptop sampled UART.
    # This intentionally expires earlier than a deadline based on acceptance time.
    serial_fresh_until_ns = challenge_created_ns + SERIAL_MAX_SILENCE_NS - serial_age_ns
    expires_ns = min(challenge_created_ns + LEASE_NS, serial_fresh_until_ns)
    _require(now_ns < expires_ns, "remote challenge expired before lease acceptance")
    return serial_fresh_until_ns, expires_ns


def _lease(directory, reason=None, *, challenge_created_ns=None,
           serial_age_ns=None, now_ns=None):
    now_ns = time.monotonic_ns() if now_ns is None else now_ns
    lease = {"expires_ns": now_ns + LEASE_NS, "cancel_reason": reason,
             "host_boot_id": clock_domain("observer_ct107")["host_boot_id"]}
    if reason is None:
        serial_fresh_until_ns, expires_ns = _lease_deadline(
            challenge_created_ns, serial_age_ns, now_ns)
        lease["serial_fresh_until_ns"] = serial_fresh_until_ns
        lease["expires_ns"] = expires_ns
    _write(directory / "lease.json", lease)


def _issue_challenge(directory):
    """Persist a single-use CT monotonic timestamp before UART is sampled."""
    challenge_path = directory / "challenge.json"
    previous_id = None
    if challenge_path.exists():
        previous_id = _read(challenge_path).get("challenge_id")
    challenge_id = str(uuid.uuid4())
    while challenge_id == previous_id:
        challenge_id = str(uuid.uuid4())
    host_boot_id = clock_domain("observer_ct107")["host_boot_id"]
    _require(isinstance(host_boot_id, str) and host_boot_id,
             "CT boot ID is unavailable; remote challenge cannot be trusted")
    challenge = {"challenge_id": challenge_id,
                 "created_ns": time.monotonic_ns(),
                 "host_boot_id": host_boot_id,
                 "consumed": False}
    _write(challenge_path, challenge)
    return challenge


def _consume_challenge(directory, challenge_id, serial_age_ns, *, now_ns=None):
    challenge = _read(directory / "challenge.json")
    _require(isinstance(challenge_id, str)
             and challenge_id == challenge.get("challenge_id"),
             "remote challenge is missing, superseded, or mismatched")
    _require(challenge.get("consumed") is False,
             "remote challenge was already consumed")
    now_ns = time.monotonic_ns() if now_ns is None else now_ns
    current_boot_id = clock_domain("observer_ct107")["host_boot_id"]
    _require(isinstance(current_boot_id, str) and current_boot_id
             and challenge.get("host_boot_id") == current_boot_id,
             "remote challenge belongs to a previous CT boot")
    _lease_deadline(challenge.get("created_ns"), serial_age_ns, now_ns)
    challenge["consumed"] = True
    challenge["consumed_ns"] = now_ns
    _write(directory / "challenge.json", challenge)
    return challenge["created_ns"], now_ns


def _guard(directory):
    try:
        lease = _read(directory / "lease.json")
        if lease.get("cancel_reason"):
            return lease["cancel_reason"]
        if lease.get("host_boot_id") != clock_domain("observer_ct107")["host_boot_id"]:
            return "remote_host_rebooted"
        now_ns = time.monotonic_ns()
        if lease.get("serial_fresh_until_ns") is None:
            return "laptop_uart_freshness_lease_missing"
        if now_ns >= lease["serial_fresh_until_ns"]:
            return "laptop_uart_freshness_lease_expired"
        if now_ns >= lease["expires_ns"]:
            return "laptop_health_lease_expired"
    except (OSError, ValueError, KeyError, TypeError):
        return "laptop_health_lease_missing_or_invalid"
    return None


def _prepare(directory, request):
    config = request["config"]
    _validate_observer(config["observer"])
    _require(config.get("protocol_revision") == "issue20-v1.0", "remote backend requires issue20-v1.0")
    _require(set(request["sources"]) == set(SOURCES), "incomplete observer source snapshot")
    source_info = {}
    decoded = {}
    for name in SOURCES:
        raw = base64.b64decode(request["sources"][name], validate=True)
        _require(len(raw) <= 1024 * 1024, "observer source too large")
        decoded[name] = raw
        source_info[name] = {"size_bytes": len(raw), "sha256": _digest(raw)}
    _require(source_info["remote_observer.py"]["sha256"] == request["controller_sha256"],
             "controller snapshot differs from bootstrap")
    record = {"schema": SCHEMA, "session_id": request["session_id"],
              "config": config, "sources": source_info}
    if directory.exists():
        _require(_read(directory / "request.json") == record,
                 "remote session already exists with different code/config; it cannot be reused")
        return
    directory.mkdir(mode=0o700)
    # If creation is interrupted, this directory is deliberately never retried.
    (directory / "data").mkdir(mode=0o700)
    for name, raw in decoded.items():
        with (directory / name).open("xb") as stream:
            stream.write(raw)
    _write(directory / "request.json", record, exclusive=True)
    _write(directory / "status.json", {"schema": SCHEMA, "session_id": request["session_id"],
                                       "state": "prepared", "clock_domain": clock_domain("observer_ct107")})


def _remote_rpc(request):
    import fcntl
    os.umask(0o077)
    _require(request.get("schema") == SCHEMA, "unsupported remote protocol")
    directory = _session_directory(request)
    action = request["action"]
    if action == "prepare":
        _prepare(directory, request)
    _require(directory.is_dir(), "remote session does not exist")
    record = _read(directory / "request.json")
    _require(record["sources"]["remote_observer.py"]["sha256"] == request["controller_sha256"],
             "remote controller changed; recover with the original source snapshot")
    for name, info in record["sources"].items():
        _require(_file_info(directory / name) == info, f"remote source changed: {name}")
    with (directory / "control.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        status = _read(directory / "status.json")
        issued_challenge = None
        if action == "challenge":
            _require(status["state"] in {"prepared", "launching", "running"},
                     "session is not active for a UART challenge")
            _require(not (directory / "cancel.claim").exists(),
                     "session cancellation is permanent")
            if status["state"] != "prepared":
                reason = _guard(directory)
                _require(not reason, reason or "observer lease is invalid")
            issued_challenge = _issue_challenge(directory)
        elif action == "start":
            age = request.get("serial_age_ns")
            created_ns, accepted_ns = _consume_challenge(
                directory, request.get("challenge_id"), age)
            if not (directory / "start.claim").exists():
                _require(not (directory / "cancel.claim").exists(),
                         "session cancellation is permanent")
                _require(status["state"] == "prepared", "session is not prepared")
                _lease(directory, challenge_created_ns=created_ns,
                       serial_age_ns=age, now_ns=time.monotonic_ns())
                _write(directory / "start.claim", {"claimed_ns": accepted_ns}, exclusive=True)
                status.update(state="launching")
                _write(directory / "status.json", status)
                with (directory / "worker.log").open("xb") as log:
                    process = subprocess.Popen([sys.executable, str(directory / "remote_observer.py"),
                                                "worker", str(directory)], cwd=directory,
                                               stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                               start_new_session=True, close_fds=True)
                # This separate receipt cannot overwrite a fast worker's terminal status.
                _write(directory / "process.json", _process_identity(process.pid), exclusive=True)
        elif action == "heartbeat":
            if status["state"] not in TERMINAL:
                _require(not (directory / "cancel.claim").exists(), "session cancellation is permanent")
                age = request.get("serial_age_ns")
                created_ns, _accepted_ns = _consume_challenge(
                    directory, request.get("challenge_id"), age)
                reason = _guard(directory)
                if reason:
                    _write(directory / "cancel.claim", {"reason": reason}, exclusive=True)
                    _lease(directory, reason)
                else:
                    _lease(directory, challenge_created_ns=created_ns,
                           serial_age_ns=age, now_ns=time.monotonic_ns())
        elif action == "cancel":
            if not (directory / "cancel.claim").exists():
                _write(directory / "cancel.claim", {"reason": request.get("reason", "laptop_cancelled")}, exclusive=True)
            _lease(directory, _read(directory / "cancel.claim")["reason"])
        elif action == "fetch":
            _require(status["state"] in TERMINAL, "remote outputs are not closed yet")
            manifest = _read(directory / "manifest.json")
            name = request["name"]
            _require(name in manifest["files"] and Path(name).name == name, "file is not in the sealed manifest")
            path = directory / "data" / name
            _require(_file_info(path) == manifest["files"][name], "sealed remote file changed")
            offset = request["offset"]
            _require(type(offset) is int and 0 <= offset <= path.stat().st_size, "invalid file offset")
            with path.open("rb") as stream:
                stream.seek(offset)
                chunk = stream.read(CHUNK_BYTES)
            return {"schema": SCHEMA, "session_id": request["session_id"],
                    "offset": offset, "data": base64.b64encode(chunk).decode()}
        elif action not in {"prepare", "start", "status", "heartbeat", "cancel", "challenge"}:
            raise RemoteObserverError("unknown remote action")
        status = _read(directory / "status.json")
        response = {"schema": SCHEMA, "session_id": request["session_id"], "status": status}
        if issued_challenge is not None:
            response["challenge_id"] = issued_challenge["challenge_id"]
        if status["state"] in TERMINAL:
            manifest_path = directory / "manifest.json"
            _require(_file_info(manifest_path) == status["manifest"], "sealed manifest changed")
            response["manifest"] = _read(manifest_path)
        elif (directory / "process.json").exists():
            identity = _read(directory / "process.json")
            response["worker_alive"] = bool(identity and _process_identity(identity["pid"]) == identity)
        return response


def _worker(directory):
    import fcntl
    os.umask(0o077)
    directory = directory.resolve()
    request = _read(directory / "request.json")
    _session_directory({"session_id": directory.name, "root": str(directory.parent)})
    with (directory / "worker.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for name, info in request["sources"].items():
            _require(_file_info(directory / name) == info, f"worker source changed: {name}")
        _validate_observer(request["config"]["observer"])
        status = {"schema": SCHEMA, "session_id": directory.name, "state": "running",
                  "clock_domain": clock_domain("observer_ct107"),
                  "started_ns": time.monotonic_ns(), "process": _process_identity(os.getpid())}
        _write(directory / "status.json", status)
        sys.path.insert(0, str(directory))
        from external_observer import run
        from update_campaign import _server_log_snapshot, _capture_server_log_delta, _close_server_log_snapshot
        output = directory / "data"
        result = {"schema": SCHEMA, "session_id": directory.name, "error": None,
                  "observer_summary": None, "server_log_capture": None}
        snapshot = None
        try:
            server_log = request["config"].get("server_log")
            if server_log:
                server_log = Path(server_log).expanduser()
                _require(server_log.is_absolute(), "remote server log must be absolute")
                snapshot = _server_log_snapshot(server_log)
            result["observer_summary"] = run(
                output=output, **request["config"]["observer"],
                health_check=lambda: _guard(directory), clock_domain=status["clock_domain"])
            status["state"] = "aborted" if result["observer_summary"].get("instrument_aborted") else "complete"
        except BaseException as exc:
            status["state"] = "failed"
            result["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            if snapshot is not None:
                try:
                    result["server_log_capture"] = _capture_server_log_delta(
                        server_log, snapshot, output / "server.jsonl")
                except Exception as exc:
                    status["state"] = "failed"
                    result["error"] = f"server log capture: {type(exc).__name__}: {exc}"
                finally:
                    _close_server_log_snapshot(snapshot)
            result["state"] = status["state"]
            _write(output / "remote-result.json", result, exclusive=True)
            manifest = {"schema": SCHEMA, "session_id": directory.name,
                        "request_sha256": _file_info(directory / "request.json")["sha256"],
                        "files": {path.name: _file_info(path) for path in sorted(output.iterdir())}}
            _write(directory / "manifest.json", manifest, exclusive=True)
            status.update(finished_ns=time.monotonic_ns(), error=result["error"],
                          manifest=_file_info(directory / "manifest.json"))
            _write(directory / "status.json", status)


def _archive(options, session_id, response, output):
    status, manifest = response["status"], response["manifest"]
    _require(manifest.get("schema") == SCHEMA and manifest.get("session_id") == session_id,
             "remote manifest identity mismatch")
    _require(manifest.get("request_sha256") == _read(output / "remote-session.json")["request_sha256"],
             "remote evidence belongs to a different configuration/source snapshot")
    files = manifest["files"]
    _require(isinstance(files, dict) and "remote-result.json" in files, "remote manifest is incomplete")
    staging = output / "remote-staging"
    staging.mkdir(mode=0o700, exist_ok=True)
    for name, expected in files.items():
        _require(isinstance(name, str) and Path(name).name == name and name not in {".", ".."}
                 and (name in {"observations.jsonl", "summary.json", "remote-result.json"}
                      or name.startswith("server") and name.endswith(".jsonl")),
                 "unsafe/unexpected remote output filename")
        _require(type(expected["size_bytes"]) is int and 0 <= expected["size_bytes"] <= 64 * 1024 * 1024
                 and re.fullmatch(r"[0-9a-f]{64}", expected["sha256"]), "invalid remote file metadata")
        destination = output / name
        if destination.exists():
            _require(_file_info(destination) == expected, f"existing local evidence differs: {name}")
            continue
        temporary = staging / (uuid.uuid4().hex + ".partial")
        try:
            with temporary.open("xb") as stream:
                offset = 0
                while offset < expected["size_bytes"]:
                    chunk = _rpc(options, session_id, "fetch", name=name, offset=offset)
                    raw = base64.b64decode(chunk["data"], validate=True)
                    _require(chunk["offset"] == offset and 0 < len(raw) <= CHUNK_BYTES
                             and offset + len(raw) <= expected["size_bytes"], "invalid remote file chunk")
                    stream.write(raw)
                    offset += len(raw)
                stream.flush()
                os.fsync(stream.fileno())
            _require(_file_info(temporary) == expected, f"download size/SHA-256 mismatch: {name}")
            # link() cannot overwrite an archived file if a recovery races us.
            os.link(temporary, destination)
            _sync_directory(output)
        finally:
            temporary.unlink(missing_ok=True)
    _write(output / "remote-manifest.json", manifest)
    _write(output / "remote-status.json", status)
    return _read(output / "remote-result.json")


def run_remote_observer(output, observer, options, *, context, health_check,
                        serial_age_check, server_log=None):
    """Start at most once; cease lease renewal permanently after any SSH loss."""
    output = Path(output)
    options = validate_options(dict(options))
    _validate_observer(observer)

    def current_serial_age_ns():
        age_ns = serial_age_check()
        _require(type(age_ns) is int and 0 <= age_ns < SERIAL_MAX_SILENCE_NS,
                 "UART byte evidence is too old to safely start or renew the remote observer")
        return age_ns

    def challenge_id(response):
        value = response.get("challenge_id")
        _require(isinstance(value, str) and str(uuid.UUID(value)) == value,
                 "CT107 returned an invalid UART challenge")
        return value

    def cancel_prestart(reason):
        # A cancellation claim makes the prepared session permanently unstartable.
        try:
            _rpc(options, session_id, "cancel", reason=str(reason))
        except RemoteObserverError:
            pass

    if server_log is not None:
        _require(Path(server_log).is_absolute(), "--remote-server-log requires an absolute CT107 path")
    session_id = options.get("session_id") or str(uuid.uuid4())
    _require(str(uuid.UUID(session_id)) == session_id, "remote session ID must be a canonical UUID")
    config = {"protocol_revision": "issue20-v1.0", "observer": observer,
              "context": context, "server_log": str(server_log) if server_log is not None else None}
    sources = {name: (Path(__file__).parent / name).read_bytes() for name in SOURCES}
    source_directory = output / "remote-code"
    source_directory.mkdir(mode=0o700)
    for name, raw in sources.items():
        with (source_directory / name).open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    _sync_directory(source_directory)
    options["controller_path"] = str((source_directory / "remote_observer.py").resolve())
    record = {"schema": SCHEMA, "session_id": session_id, "options": options,
              "config": config, "source_hashes": {name: _digest(raw) for name, raw in sources.items()},
              "laptop_clock_domain": clock_domain("serial_laptop"),
              "recovery_policy": "status/fetch only; never start again"}
    record["request_sha256"] = _digest(_encoded({
        "schema": SCHEMA, "session_id": session_id, "config": config,
        "sources": {name: {"size_bytes": len(raw), "sha256": _digest(raw)}
                    for name, raw in sources.items()}}))
    _write(output / "remote-session.json", record, exclusive=True)
    failure = health_check()
    _require(not failure, str(failure))
    response = _rpc(options, session_id, "prepare", config=config,
                    sources={name: base64.b64encode(raw).decode() for name, raw in sources.items()})
    _write(output / "remote-status.json", response["status"])
    failure = health_check()
    if failure:
        cancel_prestart(failure)
        raise RemoteObserverError(str(failure))
    try:
        # CT persists a fresh monotonic challenge first. Only then do we sample
        # UART, so the server can include the full challenge RTT in freshness.
        challenge_response = _rpc(options, session_id, "challenge")
        start_challenge_id = challenge_id(challenge_response)
        failure = health_check()
        _require(not failure, str(failure))
        serial_age_ns = current_serial_age_ns()
    except RemoteObserverError as exc:
        cancel_prestart(exc)
        raise
    # Persist the claim locally BEFORE the single start RPC. Loss of its reply
    # leaves the execution uncertain, and recovery never sends start.
    _write(output / "remote-start-claim.json", {"session_id": session_id,
                                               "laptop_monotonic_ns": time.monotonic_ns()}, exclusive=True)
    transport_failure = None
    try:
        response = _rpc(options, session_id, "start", challenge_id=start_challenge_id,
                        serial_age_ns=serial_age_ns)
    except RemoteObserverError as exc:
        transport_failure = str(exc)
    deadline = time.monotonic() + 330
    last_success = time.monotonic()
    while time.monotonic() < deadline:
        failure = health_check()
        challenge = None
        serial_age_ns = None
        if not failure and transport_failure is None:
            try:
                challenge = challenge_id(_rpc(options, session_id, "challenge"))
                failure = health_check()
                _require(not failure, str(failure))
                serial_age_ns = current_serial_age_ns()
            except RemoteObserverError as exc:
                failure = str(exc)
        if failure and transport_failure is None:
            transport_failure = str(failure)
        action = "cancel" if failure else "status" if transport_failure else "heartbeat"
        values = ({"reason": str(failure)} if failure else
                  {"challenge_id": challenge, "serial_age_ns": serial_age_ns}
                  if action == "heartbeat" else {})
        try:
            response = _rpc(options, session_id, action, **values)
            last_success = time.monotonic()
            _write(output / "remote-status.json", response["status"])
            if response["status"]["state"] in TERMINAL:
                result = _archive(options, session_id, response, output)
                result["transport_error"] = transport_failure
                return result
            if response.get("worker_alive") is False or (
                    transport_failure and response["status"]["state"] == "prepared"):
                transport_failure = transport_failure or (
                    "remote worker disappeared; status is unresolved and it must never be restarted")
                _write(output / "remote-transport-error.json", {"session_id": session_id,
                                                               "reason": transport_failure,
                                                               "retry_permitted": False})
                break
        except RemoteObserverError as exc:
            if transport_failure is None:
                transport_failure = str(exc)
            _write(output / "remote-transport-error.json", {"session_id": session_id,
                                                           "reason": transport_failure,
                                                           "retry_permitted": False})
            if time.monotonic() - last_success > 20:
                break
        time.sleep(0.5)
    raise RemoteObserverError("remote capture is incomplete or needs review; keep serial/remote evidence and "
                              "use status/recover with remote-session.json; NEVER rerun the slot. "
                              + str(transport_failure or "supervision deadline expired"))


def recover(receipt, *, status_only=False):
    receipt = Path(receipt).expanduser().resolve()
    record = _read(receipt)
    _require(record.get("schema") == SCHEMA, "unsupported local remote-session receipt")
    source_directory = Path(record["options"]["controller_path"]).parent
    for name, expected in record["source_hashes"].items():
        _require(name in SOURCES and _digest((source_directory / name).read_bytes()) == expected,
                 f"recover with the original source snapshot; {name} changed")
    options = validate_options(record["options"])
    response = _rpc(options, record["session_id"], "status")
    _write(receipt.parent / "remote-status.json", response["status"])
    if status_only:
        return response
    _require(response["status"]["state"] in TERMINAL,
             "worker has not sealed its outputs; recovery cannot relaunch or renew its lease")
    result = _archive(options, record["session_id"], response, receipt.parent)
    return {"schema": SCHEMA, "session_id": record["session_id"], "state": result["state"],
            "output": str(receipt.parent), "adjudication_required": True,
            "note": "recovery does not reopen serial, relaunch observer or create slot.json"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("status", "recover"):
        command = sub.add_parser(action)
        command.add_argument("receipt", type=Path)
    worker = sub.add_parser("worker", help=argparse.SUPPRESS)
    worker.add_argument("directory", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "worker":
            _worker(args.directory)
            return
        result = recover(args.receipt, status_only=args.action == "status")
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(2, f"{exc}\n")
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
