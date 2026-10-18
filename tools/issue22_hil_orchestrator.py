#!/usr/bin/env python3
"""Run the 16-row issue #22 preliminary HIL as an ordinary user.

The coordinator serializes all local flash access with issue #20, provisions a
profile only through its signed sequence-2 OTA, claims each row once, captures
privacy-minimized preflight plus raw trial evidence, correlates one HTTPS log
window per row, and restores/verifies the original 16 MiB flash image in every
row's ``finally`` path.  It never writes an NVS partition directly.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
FLASH_BYTES = 0x1000000
NVS_OFFSET, NVS_BYTES = 0x9000, 0x6000
OTA_OFFSETS = (0x20000, 0x720000)
OTA_SLOT_BYTES = 0x700000
TABLE_OFFSET, TABLE_BYTES = 0x8000, 0x1000
LOCK_DIR = Path.home() / ".local/state/wasmhome-hil"
LOCK_FILE = LOCK_DIR / "hardware.lock"
DEFAULT_TIMEOUT_SCHEMA = "wasmhome-issue20-frozen-timeout-v1"
PROFILE_SCHEMA = "wasmhome-issue22-bootstrap-profiles-v3"
BOOTLOADER_OFFSET, BOOTLOADER_BYTES = 0x0, TABLE_OFFSET
JOURNAL_SCHEMA = "wasmhome-issue22-hil-orchestrator-v1"
PORT = 18445
MAX_REMOTE_LOG_BYTES = 64 * 1024 * 1024

if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import external_observer
import hil_bootstrap_validation as bootstrap_validation
import resilience_fixture_https_server as fixture_server
import resilience_harness
import resilience_hil_runner as hil_runner
import resilience_nvs_preflight as nvs_preflight
import signed_artifacts
import user_owned_https_server_manager as user_server_manager
from user_owned_https_batch_hook import Issue22FixtureServerHook


class OrchestratorError(ValueError):
    """Fail-closed validation or orchestration error."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise OrchestratorError(message)


def error_chain(error: BaseException, limit: int = 1000) -> str:
    """Return 'Type: message <- Cause: message' for journals and the CLI."""
    parts, seen = [], set()
    while error is not None and id(error) not in seen and len(parts) < 6:
        seen.add(id(error))
        parts.append(f"{type(error).__name__}: {error}")
        error = error.__cause__ or error.__context__
    return " <- ".join(parts)[:limit]


def _write_failure_traceback(operation: Path, error: BaseException) -> str | None:
    path = operation / "failure-traceback.txt"
    try:
        write_private(path, "".join(traceback.format_exception(error)).encode("utf-8"))
    except (OSError, OrchestratorError):
        return None
    return str(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def private_directory(path: Path, *, create: bool = True) -> Path:
    path = Path(path).expanduser().absolute()
    require(not any(parent.is_symlink() for parent in (path, *path.parents)),
            "private path contains a symlink")
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(path, 0o700)
    require(path.is_dir() and path.stat().st_uid == os.getuid()
            and stat.S_IMODE(path.stat().st_mode) == 0o700,
            f"private directory must be owned by this user and mode 0700: {path}")
    return path


def write_private(path: Path, payload: bytes, *, exclusive: bool = True) -> None:
    path = Path(path)
    require(not path.is_symlink(), "private output path must not be a symlink")
    parent = private_directory(path.parent)
    temporary = parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(temporary, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OrchestratorError("cannot finish private evidence file")
            view = view[written:]
        os.fsync(fd)
        os.close(fd)
        fd = -1
        if exclusive:
            os.link(temporary, path, follow_symlinks=False)
            temporary.unlink()
        else:
            os.replace(temporary, path)
        dir_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError as error:
        raise OrchestratorError("cannot create durable private evidence file") from error
    finally:
        if fd >= 0:
            os.close(fd)
        temporary.unlink(missing_ok=True)


def write_json(path: Path, value: object, *, exclusive: bool = False) -> None:
    data = (json.dumps(value, sort_keys=True, allow_nan=False, indent=2) + "\n").encode()
    path = Path(path)
    require(not path.is_symlink(), "private JSON path must not be a symlink")
    # Journal replacement must survive process/power loss without ever leaving
    # a truncated JSON file. Claims and initial journals use a hard-link
    # no-replace commit; later journal updates use atomic rename. In either
    # case, the complete temporary is synced before it becomes visible.
    parent = private_directory(path.parent)
    temporary = parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(temporary, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OrchestratorError("cannot finish atomic journal write")
            view = view[written:]
        os.fsync(fd)
        os.close(fd)
        fd = -1
        if exclusive:
            os.link(temporary, path, follow_symlinks=False)
            temporary.unlink()
        else:
            os.replace(temporary, path)
        dir_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if fd >= 0:
            os.close(fd)
        temporary.unlink(missing_ok=True)


@contextmanager
def hardware_lock(directory: Path | None = None):
    """Take the same per-user lock used by the issue #20 HIL coordinator."""
    root = private_directory(directory or LOCK_DIR)
    lock_path = root / "hardware.lock"
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                and stat.S_IMODE(info.st_mode) == 0o600,
                "unsafe shared hardware lock")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise OrchestratorError("another issue #20/#22 HIL operation owns the hardware lock") from error
        yield
    finally:
        os.close(fd)


def load_json(path: Path, label: str, *, max_bytes: int = 1_000_000) -> dict:
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), f"{label} must be a regular file")
    require(path.stat().st_size <= max_bytes, f"{label} exceeds size limit")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise OrchestratorError(f"{label} is invalid JSON") from error
    require(isinstance(value, dict), f"{label} must be a JSON object")
    return value


def load_plan(path: Path) -> list[dict]:
    try:
        rows = hil_runner.load_plan(Path(path), scope="preliminary")
    except (hil_runner.HILInputError, OSError) as error:
        raise OrchestratorError(f"issue #22 must use the exact 16-row preliminary plan: {error}") from error
    require(len(rows) == 16 and all(row["plan_index"] == index
                                    for index, row in enumerate(rows, 1)),
            "issue #22 preliminary matrix must contain exactly 16 ordered rows")
    return rows


def write_plan(path: Path, seed: str) -> list[dict]:
    rows = resilience_harness.resilience_preliminary_plan(seed)
    data = "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
                   for row in rows).encode()
    write_private(path, data)
    return load_plan(path)


def validate_timeout_proof(path: Path) -> dict:
    value = load_json(path, "frozen issue #20 timeout proof", max_bytes=64 * 1024)
    require(value.get("schema") == DEFAULT_TIMEOUT_SCHEMA and value.get("status") == "frozen"
            and value.get("functional_timeout_frozen") is True,
            "#22 HIL is blocked until #20 publishes a frozen functional-timeout proof")
    timeout_ms = value.get("functional_timeout_ms")
    require(type(timeout_ms) is int and 100 <= timeout_ms <= 1000,
            "frozen functional timeout is outside the firmware-supported range")
    digest = value.get("protocol_sha256")
    require(isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest),
            "timeout proof must bind the frozen protocol SHA-256")
    return value


def validate_accepted_baseline(path: Path, expected_hash: str) -> dict:
    image = Path(path).expanduser().absolute()
    require(image.is_file() and not image.is_symlink() and image.stat().st_size == FLASH_BYTES,
            "#20 accepted baseline image must be a regular 16 MiB file")
    require(image.stat().st_uid == os.getuid() and image.stat().st_mode & 0o077 == 0,
            "accepted baseline contains NVS and must be owned by this user with mode 0600/0400")
    require(isinstance(expected_hash, str) and re.fullmatch(r"[0-9a-f]{64}", expected_hash)
            and sha256_file(image) == expected_hash,
            "baseline image does not match the SHA-256 accepted by #20")
    with image.open("rb") as stream:
        stream.seek(TABLE_OFFSET)
        table = stream.read(TABLE_BYTES)
    missing = validate_accepted_baseline_partition_table(table)
    return {"path": image, "sha256": expected_hash, "bytes": FLASH_BYTES,
            "missing_profile_partitions": missing,
            "partition_table_sha256": hashlib.sha256(table).hexdigest()}


def _artifact_row(row: object, label: str, *, max_bytes: int) -> tuple[Path, str]:
    require(isinstance(row, dict) and set(row) == {"path", "sha256"},
            f"{label} artifact record fields differ")
    path = Path(row["path"]).expanduser().absolute()
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= max_bytes,
            f"{label} must be a regular bounded file")
    expected = row["sha256"]
    require(isinstance(expected, str) and re.fullmatch(r"[0-9a-f]{64}", expected)
            and sha256_file(path) == expected, f"{label} SHA-256 differs")
    return path, expected


def _embedded_issue22_build_marker(data: bytes) -> dict:
    start_tag = b"WASMHOME_ISSUE22_BUILD_V2\n"
    # The generated file starts with profile=. Including that first key
    # distinguishes its opening tag from the suffix of its END_ tag when
    # CMake embeds the template's final newline.
    start_prefix = start_tag + b"profile="
    end_tag = b"\nEND_WASMHOME_ISSUE22_BUILD_V2"
    require(data.count(start_prefix) == 1 and data.count(end_tag) == 1,
            "bootstrap app must embed exactly one CMake-generated #22 provenance marker")
    start = data.index(start_prefix) + len(start_tag)
    end = data.index(end_tag, start)
    try:
        lines = data[start:end].decode("ascii").splitlines()
    except UnicodeError as error:
        raise OrchestratorError("embedded #22 provenance marker is not ASCII") from error
    marker = {}
    for line in lines:
        require("=" in line, "embedded #22 provenance marker has a malformed line")
        key, value = line.split("=", 1)
        require(key not in marker, "embedded #22 provenance marker repeats a field")
        marker[key] = value
    require(set(marker) == {"profile", "source_commit", "source_tree", "functional_timeout_ms", "server_ipv4",
                            "server_port", "server_name", "ca_sha256",
                            "sdkconfig_sha256", "app_rollback_enabled", "update_server_ipv4",
                            "update_server_port", "update_server_name"},
            "embedded #22 provenance fields differ")
    try:
        marker["functional_timeout_ms"] = int(marker["functional_timeout_ms"], 10)
        marker["server_port"] = int(marker["server_port"], 10)
        marker["update_server_port"] = int(marker["update_server_port"], 10)
    except ValueError as error:
        raise OrchestratorError("embedded #22 timeout/port is invalid") from error
    require(marker["app_rollback_enabled"] in {"0", "1"}
            and re.fullmatch(r"[0-9a-f]{64}", marker["sdkconfig_sha256"]),
            "embedded #22 provenance must attest its sdkconfig rollback mode")
    require(all(re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", marker[key])
                for key in ("source_commit", "source_tree")),
            "embedded #22 provenance must identify its clean source commit and tree")
    require(isinstance(marker["server_ipv4"], str)
            and isinstance(marker["server_name"], str)
            and re.fullmatch(r"[0-9a-f]{64}", marker["ca_sha256"]),
            "embedded #22 endpoint/CA fields are malformed")
    return marker


def _validate_bootloader_bytes(data: bytes, profile: str) -> dict:
    try:
        return bootstrap_validation.validate_bootloader_image(data)
    except bootstrap_validation.BootstrapFormatError as error:
        raise OrchestratorError(f"{profile} bootstrap bootloader is invalid: {error}") from error


def validate_profile_manifest(path: Path, timeout_ms: int, server_ip: str,
                              server_port: int, server_name: str, ca_path: Path,
                              signing_public_key: Path | None = None) -> dict:
    value = load_json(path, "issue #22 profile build manifest", max_bytes=128 * 1024)
    require(value.get("schema") == PROFILE_SCHEMA and value.get("resilience_test_mode") is True,
            "profile manifest must attest opt-in issue #22 test hooks")
    require(value.get("functional_timeout_ms") == timeout_ms,
            "profile build timeout differs from the frozen #20 timeout")
    require(all(isinstance(value.get(key), str)
                and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value[key])
                for key in ("source_commit", "source_tree")),
            "profile build manifest must identify its source commit and tree")
    endpoint = value.get("endpoint")
    require(isinstance(endpoint, dict) and set(endpoint) == {"ipv4", "port", "server_name", "ca_sha256"}
            and endpoint["ipv4"] == server_ip and endpoint["port"] == server_port
            and endpoint["server_name"] == server_name
            and endpoint["ca_sha256"] == sha256_file(ca_path),
            "profile build HTTPS endpoint/CA differs from the user-level fixture server")
    require(value.get("update_endpoint") == endpoint,
            "normal signed provisioning and resilience fixture manifest endpoints must match")
    table_path, table_digest = _artifact_row(value.get("partition_table"),
        "profile partition table", max_bytes=TABLE_BYTES)
    table_bytes = table_path.read_bytes()
    require(0 < len(table_bytes) <= TABLE_BYTES,
            "profile partition table image must fit one 4 KiB sector")
    table_sector = table_bytes + b"\xff" * (TABLE_BYTES - len(table_bytes))
    validate_partition_table(table_sector)
    profiles = value.get("profiles")
    require(isinstance(profiles, dict) and set(profiles) == {"native", "interp", "aot"},
            "profile manifest must contain native/interp/aot bootstrap apps")
    snapshots = {}
    for name, profile in profiles.items():
        require(isinstance(profile, dict) and set(profile) == {"v1_app", "bootloader", "sdkconfig", "bootloader_config"},
                f"{name} bootstrap profile fields differ")
        config, config_digest = _artifact_row(profile["sdkconfig"], f"{name} build sdkconfig",
                                              max_bytes=1024 * 1024)
        config_lines = config.read_text(encoding="utf-8").splitlines()
        rollback_key = "CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE"
        rollback_lines = [line for line in config_lines if line.startswith(rollback_key + "=")
                          or line == f"# {rollback_key} is not set"]
        require(rollback_lines in ([f"{rollback_key}=y"], [f"# {rollback_key} is not set"]),
                f"{name} sdkconfig must specify one rollback mode")
        rollback_enabled = rollback_lines == [f"{rollback_key}=y"]
        require(name != "native" or rollback_enabled,
                "native sdkconfig must enable rollback on the registered S3 layout")
        for setting in ('CONFIG_IDF_TARGET="esp32s3"',
                        "CONFIG_ESPTOOLPY_FLASHSIZE_16MB=y",
                        "CONFIG_PARTITION_TABLE_OFFSET=0x8000"):
            key = setting.split("=", 1)[0]
            require([line for line in config_lines if line.startswith(key + "=")
                     or line == f"# {key} is not set"] == [setting],
                    f"{name} sdkconfig must use the registered S3 layout")
        boot_config, boot_config_digest = _artifact_row(profile["bootloader_config"],
            f"{name} bootloader generated config", max_bytes=1024 * 1024)
        try:
            boot_defines = bootstrap_validation.generated_config_defines(
                boot_config.read_text(encoding="utf-8"))
        except bootstrap_validation.BootstrapFormatError as error:
            raise OrchestratorError(f"{name} bootloader generated config is invalid: {error}") from error
        require(boot_defines.get(rollback_key, "0") == ("1" if rollback_enabled else "0"),
                f"{name} bootloader config rollback mode differs from the app sdkconfig")
        for key, expected in (("CONFIG_IDF_TARGET_ESP32S3", "1"),
                              ("CONFIG_ESPTOOLPY_FLASHSIZE_16MB", "1"),
                              ("CONFIG_PARTITION_TABLE_OFFSET", "0x8000")):
            require(boot_defines.get(key) == expected,
                    f"{name} bootloader config must use the registered S3 layout")
        bootloader, bootloader_digest = _artifact_row(profile["bootloader"],
            f"{name} matching bootloader", max_bytes=BOOTLOADER_BYTES - 1)
        bootloader_data = bootloader.read_bytes()
        _validate_bootloader_bytes(bootloader_data, name)
        app, digest = _artifact_row(profile["v1_app"], f"{name} v1 bootstrap app",
                                    max_bytes=OTA_SLOT_BYTES)
        app_bytes = app.read_bytes()
        header = app_bytes[:24]
        require(len(header) >= 24 and header[0] == 0xE9 and 1 <= header[1] <= 16
                and int.from_bytes(header[12:14], "little") == 9,
                f"{name} bootstrap file is not a valid ESP32-S3 application image")
        marker = _embedded_issue22_build_marker(app_bytes)
        require(marker == {"profile": name, "source_commit": value["source_commit"],
                           "source_tree": value["source_tree"], "functional_timeout_ms": timeout_ms,
                           "server_ipv4": server_ip, "server_port": server_port,
                           "server_name": server_name,
                           "update_server_ipv4": server_ip, "update_server_port": server_port,
                           "update_server_name": server_name,
                           "ca_sha256": sha256_file(ca_path),
                           "sdkconfig_sha256": config_digest,
                           "app_rollback_enabled": "1" if rollback_enabled else "0"},
                f"{name} BIN-embedded #22 config differs from its profile manifest/timeout/endpoint/CA")
        ca_bytes = Path(ca_path).read_bytes()
        require(0 < len(ca_bytes) <= 65536 and ca_bytes in app_bytes,
                f"{name} bootstrap BIN does not contain the manifest's exact HTTPS CA bytes")
        signing_identity = _profile_signing_identity(config_lines, app_bytes, signing_public_key)
        snapshots[name] = {"path": str(app), "sha256": digest, "bytes": app.stat().st_size,
                           "build_provenance": marker,
                           "signing_identity": signing_identity,
                           "bootloader": {"path": str(bootloader), "sha256": bootloader_digest,
                                          "bytes": len(bootloader_data)},
                           "sdkconfig": {"path": str(config), "sha256": config_digest},
                           "bootloader_config": {"path": str(boot_config), "sha256": boot_config_digest}}
    return {"schema": PROFILE_SCHEMA, "source_commit": value["source_commit"],
            "source_tree": value["source_tree"],
            "functional_timeout_ms": timeout_ms, "endpoint": endpoint,
            "update_endpoint": endpoint,
            "partition_table": {"path": str(table_path), "sha256": table_digest,
                                "bytes": len(table_bytes)},
            "profiles": snapshots}


OBSERVER_UDP_PORT = 3333
UFW_CONF = Path("/etc/ufw/ufw.conf")
UFW_DEFAULTS = Path("/etc/default/ufw")
UFW_USER_RULES = Path("/etc/ufw/user.rules")


def _sdkconfig_string(lines: list[str], key: str) -> str | None:
    values = [line[len(key) + 1:] for line in lines if line.startswith(key + "=")]
    if len(values) != 1:
        return None
    value = values[0]
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def validate_bench_network(profile_data: dict, bind_ip: str) -> None:
    """Reject builds that cannot reach this observer before any board access.

    Run r3 burned its first row because every profile was built with an empty
    Wi-Fi password. Only lengths and the observer endpoint are inspected; the
    credential itself is never logged.
    """
    for name, profile in profile_data["profiles"].items():
        lines = Path(profile["sdkconfig"]["path"]).read_text(encoding="utf-8").splitlines()
        require(_sdkconfig_string(lines, "CONFIG_WASMHOME_WIFI_SSID"),
                f"{name} build has no Wi-Fi SSID; rebuild with the private bench network defaults")
        require(_sdkconfig_string(lines, "CONFIG_WASMHOME_WIFI_PASSWORD"),
                f"{name} build has an empty Wi-Fi password; rebuild with the private bench network defaults")
        require(_sdkconfig_string(lines, "CONFIG_WASMHOME_OBSERVER_IPV4") == bind_ip,
                f"{name} build sends telemetry to a different observer than --bind-ip")
        require(_sdkconfig_string(lines, "CONFIG_WASMHOME_UDP_PORT") == str(OBSERVER_UDP_PORT),
                f"{name} build uses a different observer UDP port")


def validate_observer_host(bind_ip: str, device_ip: str, *, ufw_conf: Path = UFW_CONF,
                           ufw_defaults: Path = UFW_DEFAULTS,
                           ufw_rules: Path = UFW_USER_RULES) -> dict:
    """Check that this host owns --bind-ip and that UFW admits device telemetry.

    The device sends telemetry without a prior request, so a stateful firewall
    with a default-deny input policy drops it until an explicit rule exists.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.bind((bind_ip, 0))
        except OSError as error:
            raise OrchestratorError(f"--bind-ip {bind_ip} is not assigned to this host") from error
    if not ufw_conf.is_file():
        return {"firewall": "ufw_absent"}
    conf = ufw_conf.read_text(encoding="utf-8", errors="replace")
    if not re.search(r"^ENABLED=yes\s*$", conf, re.M):
        return {"firewall": "ufw_disabled"}
    defaults = ufw_defaults.read_text(encoding="utf-8", errors="replace") if ufw_defaults.is_file() else ""
    policy = re.search(r'^DEFAULT_INPUT_POLICY="?([A-Z]+)"?\s*$', defaults, re.M)
    default_accept = policy is not None and policy.group(1) == "ACCEPT"
    require(ufw_rules.is_file(),
            "UFW drops inbound traffic by default but its user rules are unreadable")
    device = ipaddress.IPv4Address(device_ip)
    local = ipaddress.IPv4Address(bind_ip)
    route_interface = None
    for line in ufw_rules.read_text(encoding="utf-8", errors="replace").splitlines():
        tokens = line.split()
        if tokens[:2] != ["-A", "ufw-user-input"]:
            continue
        # Only prove admission from simple UFW-generated rules. Extra matches
        # (interface, source port, connection state, negation, etc.) must not be
        # silently discarded and turned into unconditional admission.
        fields = tokens[2:]
        if len(fields) % 2 or len(set(fields[::2])) != len(fields[::2]):
            continue
        values = dict(zip(fields[::2], fields[1::2]))
        action = values.get("-j")
        if action not in {"ACCEPT", "DROP", "REJECT"}:
            continue
        if values.get("-p") not in {None, "all", "udp"}:
            continue
        if set(values) - {"-j", "-p", "-s", "-d", "-i", "-m", "--dport", "--dports"}:
            require(action == "ACCEPT", "cannot prove observer admission past a restricted UFW deny rule")
            continue
        if "-m" in values and values["-m"] not in {"udp", "multiport"}:
            require(action == "ACCEPT", "cannot prove observer admission past a restricted UFW deny rule")
            continue
        if "-i" in values:
            if route_interface is None:
                try:
                    route = subprocess.run(["ip", "-j", "route", "get", device_ip, "from", bind_ip],
                                           capture_output=True, text=True, timeout=5, check=True)
                    routes = json.loads(route.stdout)
                    route_interface = routes[0]["dev"]
                except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError, TypeError):
                    route_interface = ""
            if values["-i"] != route_interface:
                continue
        ports = values.get("--dport") or values.get("--dports") or ""
        try:
            port_match = not ports or any(
                (int(part.split(":")[0]) <= OBSERVER_UDP_PORT <= int(part.split(":")[-1]))
                for part in ports.split(","))
        except ValueError:
            port_match = False
        if not port_match:
            continue
        try:
            source_ok = "-s" not in values or device in ipaddress.IPv4Network(values["-s"], strict=False)
            local_ok = "-d" not in values or local in ipaddress.IPv4Network(values["-d"], strict=False)
        except ValueError:
            continue
        if source_ok and local_ok:
            require(action == "ACCEPT", "an earlier UFW deny rule blocks observer UDP traffic")
            return {"firewall": "ufw_rule_admits_device"}
    if default_accept:
        return {"firewall": "ufw_input_accept"}
    raise OrchestratorError(
        f"UFW drops inbound UDP {OBSERVER_UDP_PORT} from {device_ip}; run once: "
        f"sudo ufw allow proto udp from {device_ip} to {bind_ip} port {OBSERVER_UDP_PORT}")


def _profile_signing_identity(config_lines: list[str], app_bytes: bytes,
                              expected_public_key: Path | None = None) -> dict:
    """Check the config hashed in the BIN marker and the actual embedded DER.

    CMake copies the configured DER verbatim into EMBED_FILES. Reading its path
    alone cannot prove its contents at build time; the bytes must also be in the
    app. The existing marker binds the campaign setting without a rebuild.
    """
    def config_string(key: str) -> str:
        values = [line[len(key) + 1:] for line in config_lines if line.startswith(key + "=")]
        require(len(values) == 1, "profile signed-update identity config is missing or duplicated")
        try:
            value = json.loads(values[0])
        except (ValueError, TypeError) as error:
            raise OrchestratorError("profile signed-update identity config is invalid") from error
        require(isinstance(value, str) and value, "profile signed-update identity config is invalid")
        return value

    campaign = config_string("CONFIG_WASMHOME_UPDATE_CAMPAIGN_ID")
    require(campaign == hil_runner.CAMPAIGN_ID,
            "profile compiled campaign differs from the signed provisioning/fixture campaign")
    require(campaign.encode("ascii") + b"\0" in app_bytes,
            "profile BIN does not contain its configured campaign identity")
    key_path = Path(config_string("CONFIG_WASMHOME_UPDATE_SIGNING_PUBLIC_KEY_PATH"))
    require(key_path.is_absolute() and key_path.is_file() and not key_path.is_symlink()
            and 0 < key_path.stat().st_size <= 4096,
            "profile configured signing public DER is missing or invalid")
    key_bytes = key_path.read_bytes()
    try:
        public_der = signed_artifacts.public_der_from_key(key_path)
        expected_der = (signed_artifacts.public_der_from_key(expected_public_key)
                        if expected_public_key is not None else public_der)
    except (OSError, ValueError) as error:
        raise OrchestratorError("profile signing public key cannot be validated") from error
    require(key_bytes == public_der, "profile configured signing public key must be DER")
    require(public_der in app_bytes,
            "profile BIN does not contain the configured signing public DER bytes")
    require(public_der == expected_der,
            "profile compiled signing key differs from the signed provisioning key")
    return {"campaign_id": campaign, "signing_public_key_sha256": hashlib.sha256(public_der).hexdigest()}


def validate_tls_files(ca_path: Path, certificate: Path, private_key: Path,
                       server_name: str) -> dict:
    for path, label in ((ca_path, "TLS CA"), (certificate, "TLS certificate"),
                        (private_key, "TLS private key")):
        require(Path(path).is_file() and not Path(path).is_symlink(), f"{label} is missing or symlinked")
    require(Path(private_key).stat().st_mode & 0o077 == 0,
            "TLS private key permissions must exclude group and other")
    require(Path(private_key).stat().st_uid == os.getuid(),
            "TLS private key must be owned by the current user")
    ca_digest = sha256_file(Path(ca_path))
    try:
        result = subprocess.run(["openssl", "verify", "-verify_hostname", server_name,
                                 "-CAfile", str(ca_path), str(certificate)],
                                check=False, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise OrchestratorError("cannot validate HTTPS certificate chain/hostname") from error
    require(result.returncode == 0, "HTTPS certificate does not chain to the pinned CA/name")
    return {"ca_sha256": ca_digest, "certificate_sha256": sha256_file(Path(certificate))}


def validate_packages(package_root: Path, package_public_key: Path,
                      fixture_root: Path, fixture_trusted_key: Path,
                      fixture_untrusted_key: Path, expected_toolchain: str) -> dict:
    try:
        package = signed_artifacts.verify_set(package_root, package_public_key,
            expected_campaign_id=hil_runner.CAMPAIGN_ID,
            expected_aot_toolchain_id=expected_toolchain)
        _, campaign, fixture_routes, fixture_index = fixture_server.load_allowlist(
            fixture_root, fixture_trusted_key, fixture_untrusted_key,
            hil_runner.CAMPAIGN_ID, expected_toolchain,
            ota_package_root=package_root, ota_public_key=package_public_key)
    except (OSError, ValueError) as error:
        raise OrchestratorError(f"signed OTA/fixture catalog validation failed: {error}") from error
    require(campaign == hil_runner.CAMPAIGN_ID and len(fixture_routes) == 24,
            "combined HTTPS allowlist must contain nine invalid fixtures plus three signed v2 releases")
    require(package["signing_public_key_sha256"] == fixture_index["trusted_fixture_public_key_sha256"],
            "signed provisioning key differs from the trusted fixture key; firmware embeds one signing key")
    verified_v2 = {(row["scenario"], row["version"], row["sequence"], row["payload_sha256"])
                   for row in package["verified"] if row["version"] == "v2"}
    require(len(verified_v2) == 3 and all((scenario, "v2", 2, expected[0]) in verified_v2
            for scenario, expected in ((1, hil_runner.EXPECTED_RELEASE_ARTIFACTS["native"]),
                                       (2, hil_runner.EXPECTED_RELEASE_ARTIFACTS["module-interp"]),
                                       (3, hil_runner.EXPECTED_RELEASE_ARTIFACTS["module-aot"]))),
            "signed sequence-2 package hashes differ from the runner's registered release artifacts")
    return {"campaign_id": campaign, "package_signing_key_sha256": package["signing_public_key_sha256"],
            "fixture_index_sha256": sha256_file(Path(fixture_root) / "fixture-index.json"),
            "fixture_trusted_key_sha256": fixture_index["trusted_fixture_public_key_sha256"],
            # Fingerprints persist through JSON in the journal. Use lists so
            # the exact value compares equal after reload, rather than tuples
            # that JSON silently converts to lists and makes resume impossible.
            "ota_v2": [[scenario, digest]
                       for scenario, _version, _sequence, digest in sorted(verified_v2)]}


def strict_ssh_args(target: str, identity: Path, known_hosts: Path,
                    port: int = 22, host_key_alias: str | None = None) -> list[str]:
    require(re.fullmatch(r"wasmhome@[A-Za-z0-9_.:-]{1,253}", target or "") is not None,
            "CT access must use the provisioned wasmhome account")
    for path, label in ((identity, "SSH private key"), (known_hosts, "pinned known_hosts")):
        require(Path(path).is_file() and not Path(path).is_symlink(), f"{label} is missing or symlinked")
    require(Path(identity).stat().st_mode & 0o077 == 0,
            "CT identity key permissions must exclude group and other")
    require(Path(identity).stat().st_uid == os.getuid(),
            "CT identity key must be owned by the current user")
    known_info = Path(known_hosts).stat()
    require(known_info.st_uid == os.getuid() and known_info.st_mode & 0o022 == 0,
            "pinned known_hosts must be owned by this user and not group/other writable")
    require(type(port) is int and 1 <= port <= 65535, "invalid CT SSH port")
    result = ["-i", str(Path(identity).absolute()), "-p", str(port), "-o", "IdentitiesOnly=yes",
            "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", f"UserKnownHostsFile={Path(known_hosts).absolute()}",
            "-o", "PasswordAuthentication=no", "-o", "ControlMaster=no",
            "-o", "ConnectTimeout=7"]
    if host_key_alias:
        require(re.fullmatch(r"[A-Za-z0-9_.:-]{1,253}", host_key_alias),
                "invalid pinned CT host-key alias")
        result.extend(["-o", f"HostKeyAlias={host_key_alias}"])
    return result


def _multiplexed_args(args: list[str], control_path: str | None) -> list[str]:
    """Let a staging command reuse an existing authenticated master connection.

    The pinned transport keeps ControlMaster=no, so a client never becomes a
    master itself; with a ControlPath it only reuses a live socket and falls
    back to its own connection when the socket is absent.
    """
    if control_path is None:
        return args
    require("ControlMaster=no" in args and "%" not in control_path,
            "pinned SSH transport changed; refusing to multiplex it")
    index = args.index("ControlMaster=no") + 1
    return [*args[:index], "-o", "ControlPath=" + control_path, *args[index:]]


class SSHSession:
    def __init__(self, target: str, identity: Path, known_hosts: Path, port: int = 22,
                 host_key_alias: str | None = None, *, manager_ssh: dict | None = None):
        self.target = target
        self.manager_ssh = manager_ssh
        self.options = (strict_ssh_args(target, identity, known_hosts, port, host_key_alias)
                        if manager_ssh is None else None)
        self.control_path = None

    @contextmanager
    def multiplexed(self, transport: dict | None):
        """Share one master during staging's ~265 operations (measured 0.14 s to 0.01 s each)."""
        if transport is None or self.control_path is not None:
            yield
            return
        directory = Path(tempfile.mkdtemp(prefix="wh-ssh-", dir="/tmp"))
        os.chmod(directory, 0o700)
        control_path = str(directory / "m")
        base = _multiplexed_args(user_server_manager.ssh_args(transport), control_path)
        master = ["ControlMaster=yes" if value == "ControlMaster=no" else value
                  for value in base]
        master = [*master[:-2], "-N", "-f", *master[-2:]]
        try:
            started = subprocess.run(master, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, timeout=20, check=False)
        except subprocess.TimeoutExpired:
            started = None
        if started is not None and started.returncode == 0:
            self.control_path = control_path
        try:
            yield
        finally:
            self.control_path = None
            active_error = sys.exc_info()[0] is not None
            try:
                subprocess.run([*base[:-2], "-O", "exit", *base[-2:]], stdin=subprocess.DEVNULL,
                               capture_output=True, timeout=10, check=False)
            except (OSError, subprocess.TimeoutExpired):
                if not active_error:
                    raise
            finally:
                shutil.rmtree(directory, ignore_errors=True)

    def run(self, command: list[str], *, timeout: int = 30, check: bool = True,
            transport: dict | None = None) -> subprocess.CompletedProcess:
        if self.manager_ssh is None:
            argv = ["ssh", *self.options, self.target, shlex.join(command)]
            result = subprocess.run(argv, capture_output=True, timeout=timeout, check=False)
        elif transport is not None:
            argv = [*_multiplexed_args(user_server_manager.ssh_args(transport), self.control_path),
                    shlex.join(command)]
            result = subprocess.run(argv, capture_output=True, timeout=timeout, check=False)
        else:
            with user_server_manager.verified_ssh_snapshot(self.manager_ssh) as transport:
                argv = [*user_server_manager.ssh_args(transport), shlex.join(command)]
                result = subprocess.run(argv, capture_output=True, timeout=timeout, check=False)
        if check and result.returncode:
            # Never copy remote server logs or secrets into shared stdout.
            raise OrchestratorError("strict CT SSH command failed; private operation log has details")
        return result

    def scp(self, source: Path, destination: str, *, recursive: bool = False,
            transport: dict | None = None) -> None:
        if transport is not None:
            flags = _multiplexed_args(user_server_manager.scp_args(transport), self.control_path)
            if recursive:
                flags.append("-r")
            result = subprocess.run([*flags, str(source), f"{self.target}:{destination}"],
                                    capture_output=True, timeout=120, check=False)
            if result.returncode:
                raise OrchestratorError("strict CT SCP failed; no password or host-key fallback is allowed")
            return
        if self.manager_ssh is not None:
            with user_server_manager.verified_ssh_snapshot(self.manager_ssh) as transport:
                flags = user_server_manager.scp_args(transport)
                if recursive:
                    flags.append("-r")
                result = subprocess.run([*flags, str(source), f"{self.target}:{destination}"],
                                        capture_output=True, timeout=120, check=False)
            if result.returncode:
                raise OrchestratorError("strict CT SCP failed; no password or host-key fallback is allowed")
            return
        flags = ["scp"]
        index = 0
        while index < len(self.options):
            option = self.options[index]
            if option == "-p":
                flags.extend(["-P", self.options[index + 1]])
                index += 2
                continue
            flags.append(option)
            if option == "-i":
                flags.append(self.options[index + 1])
                index += 2
                continue
            if option == "-o":
                flags.append(self.options[index + 1])
                index += 2
                continue
            index += 1
        if recursive:
            flags.append("-r")
        result = subprocess.run([*flags, str(source), f"{self.target}:{destination}"],
                                capture_output=True, timeout=120, check=False)
        if result.returncode:
            raise OrchestratorError("strict CT SCP failed; no password or host-key fallback is allowed")


def _safe_tree(root: Path) -> list[Path]:
    root = Path(root)
    require(root.is_dir() and not root.is_symlink(), "remote bundle input must be a directory")
    result = []
    for path in sorted(root.rglob("*")):
        require(not path.is_symlink(), "remote bundle may not contain symlinks")
        if path.is_file():
            result.append(path)
    return result


class RemoteFixtureServer:
    """Stage and run the combined server as CT user wasmhome; never touch systemd."""
    def __init__(self, ssh: SSHSession, *, fixture_root: Path, package_root: Path,
                 fixture_trusted: Path, fixture_untrusted: Path, ota_public: Path,
                 certificate: Path, private_key: Path, campaign_root: Path,
                 server_ip: str, port: int, server_name: str, toolchain: str,
                 manager_spec: dict | None = None):
        self.ssh = ssh
        self.fixture_root = Path(fixture_root)
        self.package_root = Path(package_root)
        self.fixture_trusted = Path(fixture_trusted)
        self.fixture_untrusted = Path(fixture_untrusted)
        self.ota_public = Path(ota_public)
        self.certificate = Path(certificate)
        self.private_key = Path(private_key)
        self.root = Path(campaign_root)
        self.server_ip, self.port = server_ip, port
        self.server_name, self.toolchain = server_name, toolchain
        self.manager_spec = manager_spec
        self.log = self.root / "http-events.jsonl"
        self.pid_file = self.root / "server.pid"
        self.ready = False

    def _check_reservation(self, reservation) -> None:
        if reservation is not None:
            require(reservation.poll() is None,
                    "CT staging reservation was lost; aborting before the next staging operation")

    def _stage_run(self, reservation, command: list[str], *, timeout: int = 30) -> None:
        self._check_reservation(reservation)
        self.ssh.run(command, timeout=timeout,
                     transport=getattr(reservation, "transport", None))
        self._check_reservation(reservation)

    def _stage_mkdir_private(self, reservation, path: Path) -> None:
        # GNU mkdir -p applies its requested mode only to the leaf; ancestor
        # directories inherit the CT account's umask and may become group
        # writable. Create the complete new chain under a private umask.
        script = ("import os,pathlib,sys\n"
                  "os.umask(0o077)\n"
                  "pathlib.Path(sys.argv[1]).mkdir(mode=0o700, parents=True, exist_ok=True)\n")
        self._stage_run(reservation, ["python3", "-c", script, str(path)], timeout=20)

    def _stage_scp(self, reservation, source: Path, destination: str) -> None:
        self._check_reservation(reservation)
        self.ssh.scp(source, destination,
                     transport=getattr(reservation, "transport", None))
        self._check_reservation(reservation)

    def _validate_remote_path(self, path: Path, expected: str, reservation=None) -> None:
        """Refuse unsafe remote paths before mkdir/SCP/receipt operations."""
        self._check_reservation(reservation)
        self._validate_root_lexical()
        script = (
            "import os,pathlib,stat,sys\n"
            "root=pathlib.Path(sys.argv[1]); p=pathlib.Path(sys.argv[2]); kind=sys.argv[3]\n"
            "assert root.is_absolute() and p.is_absolute() and '..' not in root.parts and '..' not in p.parts\n"
            "home=pathlib.Path('/home/wasmhome')\n"
            "assert home in root.parents and root!=home and home.lstat().st_uid==os.getuid() and not home.lstat().st_mode & 0o022\n"
            "for q in reversed(home.parents): assert stat.S_ISDIR(q.lstat().st_mode), 'unsafe home ancestor'\n"
            "for q in reversed(root.parents):\n"
            " if q==home or home in q.parents:\n"
            "  try: s=q.lstat()\n"
            "  except FileNotFoundError: continue\n"
            "  assert stat.S_ISDIR(s.st_mode) and s.st_uid==os.getuid() and not s.st_mode & 0o022, 'unsafe campaign ancestor'\n"
            "assert p==root or root in p.parents, 'path escapes campaign root'\n"
            "for q in reversed(root.parents):\n"
            " try: s=q.lstat()\n"
            " except FileNotFoundError: continue\n"
            " assert not stat.S_ISLNK(s.st_mode) and stat.S_ISDIR(s.st_mode), 'unsafe root ancestor'\n"
            "chain=[root]\n"
            "for part in p.relative_to(root).parts: chain.append(chain[-1]/part)\n"
            "for q in chain if kind=='dir' else chain[:-1]:\n"
            " try: s=q.lstat()\n"
            " except FileNotFoundError: continue\n"
            " assert not stat.S_ISLNK(s.st_mode) and stat.S_ISDIR(s.st_mode) and s.st_uid==os.getuid() and stat.S_IMODE(s.st_mode)==0o700, 'unsafe staged directory component'\n"
            "if p.exists():\n"
            " s=p.lstat()\n"
            " assert s.st_uid==os.getuid() and not stat.S_ISLNK(s.st_mode), 'path owner/type differs'\n"
            " if kind=='dir': assert stat.S_ISDIR(s.st_mode) and stat.S_IMODE(s.st_mode)==0o700, 'unsafe directory mode'\n"
            " else:\n"
            "  assert stat.S_ISREG(s.st_mode) and s.st_mode & 0o022==0, 'unsafe file type/mode'\n"
            "  if kind=='secret': assert stat.S_IMODE(s.st_mode)==0o600, 'private file mode differs'\n"
            "try: target=p.lstat()\n"
            "except FileNotFoundError: target=None\n"
            "if target is not None:\n"
            " assert target.st_uid==os.getuid() and not stat.S_ISLNK(target.st_mode), 'target owner/type differs'\n"
            " if kind=='dir': assert stat.S_ISDIR(target.st_mode) and stat.S_IMODE(target.st_mode)==0o700, 'unsafe target directory'\n"
            " else:\n"
            "  assert stat.S_ISREG(target.st_mode) and target.st_mode & 0o022==0, 'unsafe target file'\n"
            "  if kind=='secret': assert stat.S_IMODE(target.st_mode)==0o600, 'private target mode differs'\n"
        )
        self.ssh.run(["python3", "-c", script, str(self.root), str(path), expected],
                     timeout=20, transport=getattr(reservation, "transport", None))
        self._check_reservation(reservation)

    def _validate_root_lexical(self) -> None:
        home = Path("/home/wasmhome")
        require(self.root.is_absolute() and self.root.as_posix() == str(self.root)
                and ".." not in self.root.parts and home in self.root.parents
                and self.root != home,
                "CT campaign root must be canonical and below /home/wasmhome")

    def _validate_remote_root(self, reservation=None) -> None:
        self._validate_root_lexical()
        self._validate_remote_path(self.root, "dir", reservation)

    def preflight(self) -> dict:
        """Read remote receipts/port owners; refuse existing owners before staging."""
        self._validate_root_lexical()
        require(self.manager_spec is not None,
                "stage requires a frozen issue22-fixture manager specification")
        result = user_server_manager.remote_preflight(self.manager_spec)
        require(not result["manager_busy"]
                and not result["manager_receipt_present"]
                and not result["server_pid_receipt_present"]
                and not result["port_owners"],
                "active manager, existing server receipt or TCP listener found; use cleanup-only recovery before staging")
        return result

    def _copy(self, source: Path, destination: Path, reservation=None) -> None:
        if source.is_dir():
            children = _safe_tree(source)
            # Check the complete path before mkdir can follow an existing symlink.
            self._validate_remote_path(destination, "dir", reservation)
            self._stage_mkdir_private(reservation, destination)
            self._validate_remote_path(destination, "dir", reservation)
            for child in children:
                relative = child.relative_to(source)
                remote_parent = destination / relative.parent
                self._validate_remote_path(remote_parent, "dir", reservation)
                self._stage_mkdir_private(reservation, remote_parent)
                self._validate_remote_path(remote_parent, "dir", reservation)
                remote_file = remote_parent / child.name
                self._validate_remote_path(remote_file, "file", reservation)
                self._stage_scp(reservation, child, str(remote_file))
                self._validate_remote_path(remote_file, "file", reservation)
        else:
            self._validate_remote_path(destination, "file", reservation)
            self._stage_scp(reservation, source, str(destination))
            self._validate_remote_path(destination, "file", reservation)

    def stage(self, reservation=None) -> list[str]:
        """Copy the existing 24-route fixture/release bundle without launching it."""
        require(self.manager_spec is not None,
                "stage requires a frozen issue22-fixture manager specification")
        if reservation is None:
            with user_server_manager.remote_reservation(self.manager_spec) as lease:
                return self.stage(lease)
        if not isinstance(self.ssh, SSHSession):
            return self._stage_reserved(reservation)
        with self.ssh.multiplexed(getattr(reservation, "transport", None)):
            return self._stage_reserved(reservation)

    def _stage_reserved(self, reservation) -> list[str]:
        self._validate_remote_root(reservation)
        server_py = TOOLS / "resilience_fixture_https_server.py"
        signed_py = TOOLS / "signed_artifacts.py"
        code_root = self.root / "tools"
        # Validate before each directory creation to prevent traversal through a
        # pre-existing symlink component.
        self._validate_remote_path(code_root, "dir", reservation)
        self._stage_mkdir_private(reservation, code_root)
        self._validate_remote_path(code_root, "dir", reservation)
        for source in (server_py, signed_py):
            target = code_root / source.name
            self._validate_remote_path(target, "file", reservation)
            self._stage_scp(reservation, source, str(target))
            self._validate_remote_path(target, "file", reservation)
        paths = {
            self.fixture_root: self.root / "fixtures",
            self.package_root: self.root / "ota-package",
        }
        for source, destination in paths.items():
            self._copy(source, destination, reservation)
        for source, filename in ((self.fixture_trusted, "fixture-trusted.pub"),
                                 (self.fixture_untrusted, "fixture-untrusted.pub"),
                                 (self.ota_public, "ota-signing.pub"),
                                 (self.certificate, "server.crt"),
                                 (self.private_key, "server.key")):
            target = self.root / filename
            self._validate_remote_path(target, "file", reservation)
            self._stage_scp(reservation, source, str(target))
            self._validate_remote_path(target, "file", reservation)
        self._stage_run(reservation,
                        ["chmod", "600", str(self.root / "server.key")], timeout=20)
        self._validate_remote_path(self.root / "server.key", "secret", reservation)
        command = ["python3", str(code_root / "resilience_fixture_https_server.py"),
            "--fixture-root", str(self.root / "fixtures"),
            "--trusted-public-key", str(self.root / "fixture-trusted.pub"),
            "--untrusted-public-key", str(self.root / "fixture-untrusted.pub"),
            "--ota-package-root", str(self.root / "ota-package"),
            "--ota-public-key", str(self.root / "ota-signing.pub"),
            "--certificate", str(self.root / "server.crt"),
            "--private-key", str(self.root / "server.key"),
            "--log", str(self.log), "--bind", self.server_ip,
            "--port", str(self.port), "--server-name", self.server_name,
            "--server-ip", self.server_ip, "--campaign-id", hil_runner.CAMPAIGN_ID,
            "--aot-toolchain-id", self.toolchain, "--log-mode", "600",
            "--pid-file", str(self.pid_file), "--listen-fd", "3"]
        self._stage_run(reservation, [*command, "--check"], timeout=30)
        return command

    def start(self) -> None:
        raise OrchestratorError(
            "direct fixture-server start is disabled; use the user-owned manager hook")

    def snapshot(self, destination: Path) -> bytes:
        py = ("import os,stat,sys\n"
              "p=sys.argv[1]\n"
              "try:\n"
              " f=os.open(p,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))\n"
              "except FileNotFoundError:\n"
              " raise SystemExit(0)\n"
              "s=os.fstat(f)\n"
              "assert stat.S_ISREG(s.st_mode) and s.st_size<=67108864\n"
              "with os.fdopen(f,'rb') as stream:\n"
              " data=stream.read(67108865)\n"
              "assert len(data)<=67108864\n"
              "sys.stdout.buffer.write(data)\n")
        result = self.ssh.run(["python3", "-c", py, str(self.log)], timeout=20, check=False)
        if result.returncode != 0:
            raise OrchestratorError("strict CT HTTP snapshot failed")
        data = result.stdout
        require(len(data) <= MAX_REMOTE_LOG_BYTES, "remote HTTP log exceeds 64 MiB")
        write_private(destination, data)
        return data

    def stop(self) -> None:
        raise OrchestratorError(
            "direct fixture-server stop is disabled; use cleanup-only manager recovery")


class Board:
    """User-owned, explicit-port esptool wrapper; never writes the NVS partition alone."""
    def __init__(self, options: argparse.Namespace, operation: Path):
        self.options, self.operation = options, operation
        prior = []
        for path in operation.glob("esptool-*.log"):
            require(path.is_file() and not path.is_symlink(), "unsafe prior esptool receipt")
            match = re.fullmatch(r"esptool-(\d{4})\.log", path.name)
            require(match is not None, "unexpected esptool receipt name")
            prior.append(int(match.group(1)))
        self.index = max(prior, default=0)

    def command(self, *arguments, after="no_reset", timeout=240) -> str:
        self.index += 1
        log = self.operation / f"esptool-{self.index:04d}.log"
        args = [self.options.python, self.options.esptool, "--chip", "esp32s3",
                "--port", self.options.serial_port, "--baud", str(self.options.flash_baud),
                "--after", after, *map(str, arguments)]
        try:
            with log.open("xb") as stream:
                os.chmod(log, 0o600)
                result = subprocess.run(args, stdout=stream, stderr=subprocess.STDOUT,
                                        timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise OrchestratorError(f"esptool failed; inspect private receipt {log.name}") from error
        require(result.returncode == 0, f"esptool failed; inspect private receipt {log.name}")
        return log.read_text(errors="replace")

    def identify(self) -> None:
        output = self.command("flash_id")
        macs = re.findall(r"MAC:\s*([0-9a-fA-F:]{17})", output)
        require(len(macs) == 1 and macs[0].lower() == self.options.expected_mac.lower()
                and re.search(r"Detected flash size:\s*16\s*(?:MB|MiB)\b", output)
                and "ESP32-S3" in output,
                "selected board is not the registered 16 MiB ESP32-S3")

    def read(self, offset: int, size: int, destination: Path, *, timeout: int = 600) -> str:
        require(not destination.exists(), "read destination already exists")
        output = self.command("read_flash", hex(offset), hex(size), str(destination), timeout=timeout)
        os.chmod(destination, 0o600)
        require(destination.stat().st_size == size, "esptool readback size differs")
        return output

    def overlay_profile(self, app: Path, bootloader: Path,
                        partition_table: Path) -> None:
        require(app.is_file() and not app.is_symlink()
                and 24 <= app.stat().st_size <= OTA_SLOT_BYTES,
                "profile bootstrap app is invalid")
        require(bootloader.is_file() and not bootloader.is_symlink()
                and 24 <= bootloader.stat().st_size < BOOTLOADER_BYTES,
                "profile bootloader is invalid")
        require(partition_table.is_file() and not partition_table.is_symlink()
                and 0 < partition_table.stat().st_size <= TABLE_BYTES,
                "profile partition table is invalid")
        _validate_bootloader_bytes(bootloader.read_bytes(), "selected")
        table_data = partition_table.read_bytes()
        table_sector = table_data + b"\xff" * (TABLE_BYTES - len(table_data))
        validate_partition_table(table_sector)
        bootloader_hash = sha256_file(bootloader)
        # The accepted #20 image predates the four empty module partitions.
        # Add only the signed build's validated table during this row; the
        # original 16 MiB baseline (including NVS and its table) is restored
        # in the row's finally path. No NVS offset is passed to write_flash.
        # Matching bootloader and v1 app copies make startup independent of
        # prior OTA metadata until signed sequence 2 is tested.
        output = self.command("write_flash", hex(BOOTLOADER_OFFSET), str(bootloader),
                              hex(TABLE_OFFSET), str(partition_table),
                              hex(OTA_OFFSETS[0]), str(app),
                              hex(OTA_OFFSETS[1]), str(app), timeout=300)
        self._require_write_verified(output, segments=4)
        table_readback = self.operation / f"partition-table-overlay-{uuid.uuid4().hex}.bin"
        self.read(TABLE_OFFSET, TABLE_BYTES, table_readback)
        require(table_readback.read_bytes() == table_sector,
                "profile partition-table readback differs; refusing reset")
        readback = self.operation / f"bootloader-overlay-{uuid.uuid4().hex}.bin"
        self.read(BOOTLOADER_OFFSET, bootloader.stat().st_size, readback)
        require(sha256_file(readback) == bootloader_hash,
                "profile bootloader readback hash differs; refusing reset")

    def run(self) -> None:
        self.command("run", after="hard_reset", timeout=30)

    def _require_write_verified(self, output: str, *, segments: int) -> None:
        # esptool skips the post-write digest silently if the loader lacks it.
        require(output.count("Hash of data verified.") == segments,
                "esptool did not confirm the on-chip MD5 of every written segment")

    def verify_image(self, image: Path, expected_hash: str) -> None:
        """Compare all 16 MiB with a pinned image by the stub's on-chip MD5.

        Protocol amendment 2026-10-18: replaces the per-row full readback
        (about 212 s) with a digest comparison (about 30 s). Full SHA-256
        readbacks remain at session start, session end and in recovery.
        """
        require(image.stat().st_size == FLASH_BYTES and sha256_file(image) == expected_hash,
                "refusing flash verification against an unverified baseline")
        output = self.command("verify_flash", "0x0", str(image), timeout=300)
        require(output.count("-- verify OK (digest matched)") == 1,
                "board full-flash MD5 differs from the #20 accepted baseline")

    def restore(self, baseline: Path, expected_hash: str, destination: Path, *,
                full_readback: bool = False) -> None:
        require(baseline.stat().st_size == FLASH_BYTES and sha256_file(baseline) == expected_hash,
                "refusing full-flash restoration from an unverified baseline")
        output = self.command("write_flash", "0x0", str(baseline), timeout=300)
        self._require_write_verified(output, segments=1)
        if full_readback:
            self.read(0, FLASH_BYTES, destination)
            require(sha256_file(destination) == expected_hash,
                    "full 16 MiB restoration readback hash differs")
            return
        # Row restores rely on write_flash's on-chip MD5 of all 16 MiB; the
        # session-end SHA-256 readback covers the final image.
        write_json(destination.with_suffix(".json"),
                   {"schema": "wasmhome-issue22-restore-receipt-v1",
                    "method": "write_flash_on_chip_md5", "baseline_sha256": expected_hash,
                    "esptool_receipt": f"esptool-{self.index:04d}.log"}, exclusive=True)


def validate_partition_table(data: bytes) -> None:
    rows = decode_partition_table(data)
    expected = expected_partition_rows()
    require(rows == expected, "live flash partition table differs from this checkout")
    require(rows.get("nvs", (None, None, None, None, None))[2:] == (NVS_OFFSET, NVS_BYTES, 0)
            and rows.get("ota_0", (None, None, None, None, None))[2:] == (OTA_OFFSETS[0], OTA_SLOT_BYTES, 0)
            and rows.get("ota_1", (None, None, None, None, None))[2:] == (OTA_OFFSETS[1], OTA_SLOT_BYTES, 0),
            "live NVS/OTA layout differs from the pinned issue #22 map")


def _partition_type_code(value: str) -> int:
    names = {"app": 0x00, "data": 0x01}
    value = value.strip().lower()
    code = names.get(value)
    if code is None:
        try:
            code = int(value, 0)
        except ValueError as error:
            raise OrchestratorError(f"unsupported partition type {value!r}") from error
    require(0 <= code <= 0xFF, "partition type is outside its encoded range")
    return code


def _partition_subtype_code(kind: int, value: str) -> int:
    names = {
        0x00: {"factory": 0x00, "ota_0": 0x10, "ota_1": 0x11},
        0x01: {"ota": 0x00, "phy": 0x01, "nvs": 0x02},
    }
    value = value.strip().lower()
    code = names.get(kind, {}).get(value)
    if code is None:
        try:
            code = int(value, 0)
        except ValueError as error:
            raise OrchestratorError(f"unsupported partition subtype {value!r}") from error
    require(0 <= code <= 0xFF, "partition subtype is outside its encoded range")
    return code


def _partition_flags_code(value: str) -> int:
    # The checked-in table currently uses no flags. Fail closed for any new
    # syntax until its encoded semantics are explicitly reviewed here.
    value = value.strip().lower()
    require(not value, f"unsupported partition flags {value!r}")
    return 0


def expected_partition_rows() -> dict[str, tuple[int, int, int, int, int]]:
    expected = {}
    import csv
    with (ROOT / "partitions.csv").open(newline="", encoding="utf-8") as stream:
        for row in csv.reader(line for line in stream if line.strip() and not line.lstrip().startswith("#")):
            require(len(row) == 6, "partition CSV row must contain six columns")
            name, type_name, subtype_name, offset, size, flags = (item.strip() for item in row)
            kind = _partition_type_code(type_name)
            expected[name] = (kind, _partition_subtype_code(kind, subtype_name),
                              int(offset, 0), int(size, 0), _partition_flags_code(flags))
    return expected


def decode_partition_table(data: bytes) -> dict[str, tuple[int, int, int, int, int]]:
    require(len(data) == TABLE_BYTES, "partition table must be exactly one 4 KiB sector")
    rows = {}
    checked_md5 = False
    terminated = False
    for offset in range(0, TABLE_BYTES, 32):
        entry = data[offset:offset + 32]
        if entry == b"\xff" * 32:
            require(data[offset:] == b"\xff" * (TABLE_BYTES - offset),
                    "partition table has data after terminator")
            terminated = True
            break
        if entry[:2] == b"\xeb\xeb":
            require(not checked_md5 and entry[2:16] == b"\xff" * 14
                    and entry[16:] == hashlib.md5(data[:offset]).digest(),
                    "partition table MD5 differs")
            require(data[offset + 32:] == b"\xff" * (TABLE_BYTES - offset - 32),
                    "partition table has data after MD5")
            checked_md5 = True
            terminated = True
            break
        magic, kind, subtype, start, size, label, flags = __import__("struct").unpack(
            "<HBBII16sI", entry)
        require(magic == 0x50AA, "unsupported partition-table entry")
        try:
            name = label.rstrip(b"\0").decode("ascii")
        except UnicodeError as error:
            raise OrchestratorError("partition name is not ASCII") from error
        require(name not in rows, "duplicate partition name")
        rows[name] = (kind, subtype, start, size, flags)
    require(checked_md5 and terminated, "partition table is missing its MD5 record or terminator")
    return rows


def validate_accepted_baseline_partition_table(data: bytes) -> list[str]:
    """Allow #20's exact original map, then add only #22 module slots per row."""
    rows = decode_partition_table(data)
    expected = expected_partition_rows()
    required = {"nvs", "otadata", "phy_init", "ota_0", "ota_1"}
    module_slots = {"interp_slot_a", "interp_slot_b", "aot_slot_a", "aot_slot_b"}
    require(required <= set(rows) and set(rows) <= set(expected),
            "#20 baseline partition map has unknown or missing required partitions")
    missing = set(expected) - set(rows)
    require(missing <= module_slots,
            "#20 baseline is missing a partition outside the four #22 module slots")
    require(all(rows[name] == expected[name] for name in rows),
            "#20 baseline changed an existing NVS, OTA or data-partition boundary")
    for name, (_kind, _subtype, start, size, _flags) in expected.items():
        require(0 <= start < FLASH_BYTES and 0 < size <= FLASH_BYTES - start,
                f"#22 partition {name} lies outside the registered flash")
    ranges = sorted((start, start + size, name)
                    for name, (_kind, _subtype, start, size, _flags) in expected.items())
    require(all(left[1] <= right[0] for left, right in zip(ranges, ranges[1:])),
            "#22 module partitions overlap an existing partition")
    return sorted(missing)


def _observer_once(options, output: Path, *, seconds: float, profile: str,
                   attempt_id: str, trigger: bool) -> dict:
    scenario = {"native": 1, "interp": 2, "aot": 3}[profile]
    initial_version = 1 if trigger or profile == "native" else 2
    return external_observer.run(
        device_ip=options.device_ip, output=output, seconds=seconds,
        bind_ip=options.bind_ip, port=3333, device_port=3333,
        period_ms=100, timeout_ms=options.timeout_ms,
        initial_version=initial_version,
        trigger_after=5 if trigger else None,
        target_sequence=2 if trigger else None,
        scenario=scenario, max_sample_age_ms=1000, attempt_id=attempt_id)


def _private_output(root: Path, name: str) -> Path:
    path = root / name
    path.mkdir(mode=0o700, parents=True, exist_ok=False)
    os.chmod(path, 0o700)
    return path


def _snapshot_artifact(source: Path, destination: Path, expected_hash: str) -> Path:
    require(not destination.exists(), "artifact snapshot destination exists")
    parent = private_directory(destination.parent)
    temporary = parent / f".{destination.name}.{uuid.uuid4().hex}.tmp"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                 getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with source.open("rb") as incoming, os.fdopen(fd, "wb") as outgoing:
            shutil.copyfileobj(incoming, outgoing)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        fd = -1
        require(sha256_file(temporary) == expected_hash,
                "artifact changed during private snapshot")
        os.link(temporary, destination, follow_symlinks=False)
        temporary.unlink()
        dir_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except BaseException:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        raise
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def _ensure_snapshot_artifact(source: Path, destination: Path, expected_hash: str) -> Path:
    """Create an immutable private input once, or verify the prior copy on resume."""
    destination = Path(destination)
    if destination.exists():
        require(not destination.is_symlink() and destination.is_file()
                and destination.stat().st_mode & 0o077 == 0
                and sha256_file(destination) == expected_hash,
                f"saved private input differs: {destination.name}")
        return destination
    return _snapshot_artifact(source, destination, expected_hash)


def _read_plan_or_create(root: Path, seed: str | None) -> list[dict]:
    plan_path = root / "plan.jsonl"
    if plan_path.exists():
        require(not plan_path.is_symlink(), "plan is symlinked")
        return load_plan(plan_path)
    require(seed, "new campaign requires an explicit or generated plan seed")
    return write_plan(plan_path, seed)


def _claim(root: Path, row: dict) -> Path:
    claims = private_directory(root / "claims")
    path = claims / f"{row['plan_index']:02d}-{row['attempt_id']}.json"
    write_json(path, {"schema": "wasmhome-issue22-one-shot-claim-v1",
                      "attempt_id": row["attempt_id"], "plan_index": row["plan_index"],
                      "case_id": row["case_id"], "scenario": row["scenario"],
                      "retry_permitted": False, "claimed_unix_ns": time.time_ns()}, exclusive=True)
    dir_fd = os.open(claims, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    return path


def _claimed_through(root: Path) -> int:
    claims = root / "claims"
    if not claims.exists():
        return 0
    require(claims.is_dir() and not claims.is_symlink(), "claim directory is unsafe")
    highest = 0
    for path in claims.iterdir():
        require(path.is_file() and not path.is_symlink(), "claim entry is not a regular file")
        value = load_json(path, "one-shot attempt claim", max_bytes=16 * 1024)
        index = value.get("plan_index")
        require(value.get("schema") == "wasmhome-issue22-one-shot-claim-v1"
                and value.get("retry_permitted") is False
                and type(index) is int and 1 <= index <= 16,
                "one-shot claim record is malformed")
        expected_name = f"{index:02d}-{value.get('attempt_id')}.json"
        require(path.name == expected_name, "claim filename differs from its immutable row")
        highest = max(highest, index)
    return highest


def _verify_claims_match_plan(root: Path, rows: list[dict]) -> int:
    highest = _claimed_through(root)
    claims = root / "claims"
    for index in range(1, highest + 1):
        row = rows[index - 1]
        path = claims / f"{index:02d}-{row['attempt_id']}.json"
        require(path.is_file() and not path.is_symlink(),
                "claim history is not a contiguous prefix of the immutable 16-row plan")
        value = load_json(path, "one-shot attempt claim", max_bytes=16 * 1024)
        require(value.get("attempt_id") == row["attempt_id"]
                and value.get("case_id") == row["case_id"]
                and value.get("scenario") == row["scenario"],
                "claim does not match its immutable row")
    return highest


def _verify_nvs_not_already_updated(nvs_dump: Path, profile: str) -> None:
    value = nvs_preflight.build_preflight(nvs_dump.read_bytes(), profile=profile,
                                          boot_id="0000000000000001")
    state = value["native_state"] if profile == "native" else value["module_state"]
    if state["ledger_present"]:
        require(state["accepted_floor"] < 2,
                "baseline already has sequence 2 for this profile; do not replay/update NVS directly")


def _validate_provisioning_acceptance(summary: dict, *, profile: str) -> None:
    """Require the observer's timely signed-trigger acceptance for every OTA.

    accepted_ns is emitted only for an accepted UPDATE response within the
    observer timeout. Policy version 2 is evidence of module activation, while
    native HIL deliberately retains control policy 1 in both firmware releases.
    Native release activation is proved later by its non-pending sequence-2
    ledger, registered payload hash/slot and fresh post-esptool boot evidence.
    """
    require(profile in {"native", "interp", "aot"}, "unknown provisioning profile")
    accepted = summary.get("accepted_ns")
    require(type(accepted) is int and accepted >= 0,
            "signed sequence-2 OTA lacks timely trigger acceptance")
    activated = summary.get("activated_ns")
    if profile == "native":
        require(summary.get("update_state") == "accepted_without_v2" and activated is None,
                "native provisioning must preserve control policy version 1")
    else:
        require(summary.get("update_state") == "activated"
                and type(activated) is int and activated >= accepted,
                "module signed sequence-2 OTA did not reach observed version-2 activation")


def _validate_observer_boot(summary: dict, *, expected_version: int,
                            previous_boot_ids: list[int] | None = None) -> str:
    boots = summary.get("boot_ids")
    require(isinstance(boots, list) and len(boots) == 1 and type(boots[0]) is int
            and 0 < boots[0] < 1 << 64,
            "post-esptool observer did not see exactly one fresh boot")
    require(summary.get("initial_version") == expected_version,
            "post-esptool observer was not configured for expected firmware profile version")
    boot = boots[0]
    if previous_boot_ids is not None:
        require(isinstance(previous_boot_ids, list) and previous_boot_ids
                and all(type(value) is int and 0 < value < 1 << 64 for value in previous_boot_ids),
                "provisioning observer lacks prior boot identities")
        require(boot not in previous_boot_ids,
                "post-esptool observer did not prove a new boot identity")
    controls = summary.get("valid_controls_by_boot", {})
    require(type(controls.get(str(boot), controls.get(boot, 0))) is int,
            "observer control coverage is malformed")
    counters = summary.get("counters", {})
    require(counters.get("telemetry_valid", 0) >= 1
            and controls.get(str(boot), controls.get(boot, 0)) >= 1,
            "post-esptool boot lacks fresh telemetry/control evidence")
    return f"{boot:016x}"


def _remote_staging_root(options, root: Path) -> Path:
    ct_root = Path(options.ct_root)
    home = Path("/home/wasmhome")
    require(ct_root.is_absolute() and ct_root.as_posix() == str(ct_root)
            and ".." not in ct_root.parts and home in ct_root.parents
            and ct_root != home,
            "--ct-root must be canonical and below /home/wasmhome")
    root_id = hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:12]
    return ct_root / "issue22" / f"{root.name}-{root_id}"


def _pinned_manager_ssh(options) -> dict:
    identity = Path(options.ct_identity).absolute()
    known_hosts = Path(options.ct_known_hosts).absolute()
    user_server_manager.owned_file(identity, private=True)
    user_server_manager.owned_file(known_hosts)
    target_host = options.ct_target.split("@", 1)[1]
    destination = {
        "target": options.ct_target,
        "identity": str(identity),
        "identity_sha256": sha256_file(identity),
        "known_hosts": str(known_hosts),
        "known_hosts_sha256": sha256_file(known_hosts),
        "port": options.ct_ssh_port,
        "host_key_alias": options.ct_host_key_alias or target_host,
    }
    jump_values = (getattr(options, "ct_jump_target", None),
                   getattr(options, "ct_jump_identity", None),
                   getattr(options, "ct_jump_known_hosts", None),
                   getattr(options, "ct_jump_host_key_alias", None))
    jump_port = getattr(options, "ct_jump_ssh_port", 22)
    if any(value is not None for value in jump_values) or jump_port != 22:
        require(all(value is not None for value in jump_values),
                "pinned Proxmox jump needs target, identity, known_hosts, and host-key alias")
        jump_identity = Path(options.ct_jump_identity).absolute()
        jump_known_hosts = Path(options.ct_jump_known_hosts).absolute()
        user_server_manager.owned_file(jump_identity, private=True)
        user_server_manager.owned_file(jump_known_hosts)
        destination["jump"] = {
            "target": options.ct_jump_target,
            "identity": str(jump_identity),
            "identity_sha256": sha256_file(jump_identity),
            "known_hosts": str(jump_known_hosts),
            "known_hosts_sha256": sha256_file(jump_known_hosts),
            "port": jump_port,
            "host_key_alias": options.ct_jump_host_key_alias,
        }
    user_server_manager.ssh_args(destination)
    return destination


def _manager_tool_fingerprints() -> dict:
    paths = {
        "orchestrator": Path(__file__).resolve(),
        "manager": TOOLS / "user_owned_https_server_manager.py",
        "remote_helper": TOOLS / "user_owned_https_server_remote.py",
        "batch_hook": TOOLS / "user_owned_https_batch_hook.py",
        "fixture_server": TOOLS / "resilience_fixture_https_server.py",
        "signed_artifacts": TOOLS / "signed_artifacts.py",
    }
    return {name: sha256_file(path) for name, path in paths.items()}


def _refresh_manager_fingerprint_after_recovery(journal: dict, fingerprint: dict,
                                               claimed: int, baseline_sha256: str) -> bool:
    previous = journal.get("server_manager")
    if previous is None:
        journal["server_manager"] = fingerprint
        return True
    if previous == fingerprint:
        return False
    recoveries = journal.get("recoveries")
    safe = (journal.get("state") == "interrupted_recovered"
            and journal.get("restore_required") is False
            and journal.get("next_index") == 0 and claimed == 0
            and journal.get("slots") == []
            and journal.get("current_attempt") is None
            and journal.get("current_plan_index") is None
            and isinstance(recoveries, list) and bool(recoveries))
    recovery = recoveries[-1] if safe else None
    safe = (safe and isinstance(recovery, dict)
            and recovery.get("baseline_sha256") == baseline_sha256
            and recovery.get("claimed_through") == 0
            and recovery.get("next_index") == 0
            and type(recovery.get("flash_rewritten")) is bool)
    require(safe,
            "frozen user-owned server manager inputs changed after issue #22 slots began")
    history = journal.get("tooling_rebindings", [])
    require(isinstance(history, list), "tooling rebind history is malformed")
    history.append({"time_unix_ns": time.time_ns(),
                    "baseline_sha256": baseline_sha256,
                    "previous_sha256": hashlib.sha256(
                        user_server_manager.canonical(previous).encode()).hexdigest(),
                    "current_sha256": hashlib.sha256(
                        user_server_manager.canonical(fingerprint).encode()).hexdigest(),
                    "claimed_through": 0})
    journal["tooling_rebindings"] = history
    journal["server_manager"] = fingerprint
    return True


def _freeze_server_manager(options, root: Path, fixture_root: Path) -> tuple[Path, Path, str, dict]:
    remote_root = _remote_staging_root(options, root)
    spec = user_server_manager.build_issue22_fixture_spec(
        ssh=_pinned_manager_ssh(options), staged_root=remote_root,
        bind_ip=options.server_ip, port=options.https_port,
        server_name=options.server_name, campaign_id=hil_runner.CAMPAIGN_ID,
        aot_toolchain_id=options.aot_toolchain_id, ca_path=options.tls_ca,
        local_fixture_root=fixture_root)
    spec_path = root / "server-manager-spec.json"
    expected = (user_server_manager.canonical(spec) + "\n").encode("utf-8")
    if spec_path.exists() or spec_path.is_symlink():
        raw = user_server_manager.owned_file(spec_path, private=True).read_bytes()
        require(raw == expected,
                "frozen user-owned server specification changed; use the original inputs")
        spec_sha256 = hashlib.sha256(raw).hexdigest()
    else:
        spec_sha256 = user_server_manager.write_frozen_spec(spec_path, spec)
    state_path = root / "server-manager-state.json"
    fingerprints = _manager_tool_fingerprints()
    return spec_path, state_path, spec_sha256, fingerprints


def _server_arguments(options, root: Path, package_root: Path, fixture_root: Path,
                      manager_spec: dict | None = None) -> RemoteFixtureServer:
    ssh = SSHSession(options.ct_target, options.ct_identity,
                     options.ct_known_hosts, options.ct_ssh_port,
                     options.ct_host_key_alias,
                     manager_ssh=manager_spec["ssh"] if manager_spec is not None else None)
    remote_root = _remote_staging_root(options, root)
    return RemoteFixtureServer(ssh, fixture_root=fixture_root, package_root=package_root,
        fixture_trusted=options.fixture_trusted_public,
        fixture_untrusted=options.fixture_untrusted_public,
        ota_public=options.ota_signing_public, certificate=options.tls_certificate,
        private_key=options.tls_private_key, campaign_root=remote_root,
        server_ip=options.server_ip, port=options.https_port,
        server_name=options.server_name, toolchain=options.aot_toolchain_id,
        manager_spec=manager_spec)


def _prepare_profile(options, board: Board, row: dict, profile_data: dict,
                     server: RemoteFixtureServer, operation: Path, baseline: Path,
                     baseline_hash: str) -> dict:
    profile = {"native": "native", "interp": "module-interp", "aot": "module-aot"}[row["scenario"]]
    app_path = Path(profile_data["profiles"][row["scenario"]]["path"])
    # _run_row has just verified the whole flash against the baseline and has
    # only read NVS since, so the former restore-before rewrite (about 296 s)
    # would write identical bytes. Every row still begins from the exact
    # baseline v1 app profile and NVS.
    board.overlay_profile(app_path,
        Path(profile_data["profiles"][row["scenario"]]["bootloader"]["path"]),
        Path(profile_data["partition_table"]["path"]))
    board.run()
    provision_id = str(uuid.uuid5(uuid.UUID(row["attempt_id"]), "signed-sequence-2-provision"))
    provision_dir = _private_output(operation, "ota-provisioning")
    summary = _observer_once(options, provision_dir, seconds=60, profile=row["scenario"],
                             attempt_id=provision_id, trigger=True)
    _validate_provisioning_acceptance(summary, profile=row["scenario"])

    nvs_path = operation / "nvs-after-ota.bin"
    board.read(NVS_OFFSET, NVS_BYTES, nvs_path)
    # Complete all esptool access before taking the boot-id used by preflight.
    prepared_flash = operation / "prepared-sequence-2-flash.bin"
    board.read(0, FLASH_BYTES, prepared_flash, timeout=600)
    prepared_hash = sha256_file(prepared_flash)
    table = prepared_flash.read_bytes()[TABLE_OFFSET:TABLE_OFFSET + TABLE_BYTES]
    validate_partition_table(table)
    require(nvs_path.read_bytes() == prepared_flash.read_bytes()[NVS_OFFSET:NVS_OFFSET + NVS_BYTES],
            "NVS partition changed between read-only preflight and full flash snapshot")
    board.run()
    # The r4 bench smoke saw Wi-Fi/UDP ready about 6 s after reset (5 s in r3
    # would already miss it); 20 s leaves room for fresh telemetry and controls.
    boot_summary = _observer_once(options, _private_output(operation, "post-esptool-boot"),
                                  seconds=20, profile=row["scenario"], attempt_id=provision_id,
                                  trigger=False)
    boot_id = _validate_observer_boot(boot_summary,
        expected_version=1 if row["scenario"] == "native" else 2,
        previous_boot_ids=summary.get("boot_ids", []))
    preflight = nvs_preflight.build_preflight(nvs_path.read_bytes(), profile=profile, boot_id=boot_id)
    hil_runner.validate_preflight(preflight, row)
    preflight_path = operation / "preflight.json"
    write_private(preflight_path, (json.dumps(preflight, sort_keys=True, indent=2) + "\n").encode())
    return {"profile": profile, "preflight": preflight_path, "prepared_flash_sha256": prepared_hash,
            "prepared_flash_bytes": FLASH_BYTES, "boot_id": boot_id,
            "ota_observer": provision_dir / "summary.json"}


def _runner_summary(log_path: Path) -> dict:
    """Return the runner's final JSON status line, or {} when it is absent."""
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        value = json.loads(lines[-1]) if lines else {}
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _run_row(options, row: dict, plan_path: Path, root: Path, board: Board,
             profile_data: dict, server: RemoteFixtureServer,
             baseline: Path, baseline_hash: str) -> dict:
    operation = _private_output(root / "operations", f"{row['plan_index']:02d}-{row['attempt_id']}")
    claim_path = _claim(root, row)
    journal_path = root / "journal.json"
    journal = load_json(journal_path, "orchestrator journal")
    journal.update(state="row_claimed", current_attempt=row["attempt_id"],
                   current_plan_index=row["plan_index"], restore_required=False)
    write_json(journal_path, journal, exclusive=False)
    failure = None
    result = None
    mutation_started = False
    try:
        board.identify()
        board.verify_image(baseline, baseline_hash)
        nvs_start = operation / "nvs-initial.bin"
        board.read(NVS_OFFSET, NVS_BYTES, nvs_start)
        _verify_nvs_not_already_updated(nvs_start, {"native": "native", "interp": "module-interp",
                                                   "aot": "module-aot"}[row["scenario"]])
        journal.update(state="row_provisioning", restore_required=True)
        write_json(journal_path, journal, exclusive=False)
        mutation_started = True
        prepared = _prepare_profile(options, board, row, profile_data, server,
                                    operation, baseline, baseline_hash)
        http_before = operation / "http-before.jsonl"
        server.snapshot(http_before)
        run_dir = root / "attempts" / f"{row['plan_index']:02d}-{row['attempt_id']}"
        run_args = [sys.executable, str(TOOLS / "resilience_hil_runner.py"), "run",
            "--plan", str(plan_path), "--plan-scope", "preliminary",
            "--slot", str(row["plan_index"]), "--preflight", str(prepared["preflight"]),
            "--arm-attempt-id", row["attempt_id"], "--serial-port", options.serial_port,
            "--baud", str(options.serial_baud), "--device-ip", options.device_ip,
            "--bind-ip", options.bind_ip, "--udp-port", "3333", "--device-port", "3333",
            "--output", str(run_dir), "--timeout-ms", str(options.timeout_ms)]
        log_path = operation / "runner.log"
        with log_path.open("xb") as log:
            os.chmod(log_path, 0o600)
            completed = subprocess.run(run_args, stdout=log, stderr=subprocess.STDOUT,
                                       timeout=options.trial_timeout_seconds, check=False)
        http_after = operation / "http-after.jsonl"
        server.snapshot(http_after)
        if run_dir.is_dir():
            correlation = subprocess.run([sys.executable, str(TOOLS / "resilience_hil_runner.py"),
                "correlate-http", "--run-dir", str(run_dir), "--before", str(http_before),
                "--after", str(http_after)], capture_output=True, timeout=30, check=False)
            require(correlation.returncode == 0,
                    "HTTP evidence did not correlate to the one-shot trial; see private operation")
        require(completed.returncode == 0,
                "one-shot issue #22 runner failed; this attempt is claimed and cannot be retried")
        report_path = run_dir / "analysis.json"
        runner_summary = _runner_summary(log_path)
        result = {"attempt_id": row["attempt_id"], "plan_index": row["plan_index"],
                  "state": "captured", "preflight_boot_id": prepared["boot_id"],
                  # The runner exits 0 for every recorded outcome, including
                  # non-success statuses; keep them visible for adjudication.
                  "runner_status": runner_summary.get("status"),
                  "dispatch_status": runner_summary.get("dispatch_status"),
                  "prepared_flash_sha256": prepared["prepared_flash_sha256"],
                  "run_dir": str(run_dir), "http_correlation": str(run_dir / "http-correlation.json"),
                  "claim": str(claim_path)}
    except BaseException as error:
        failure = error
    finally:
        journal_error = None
        if mutation_started:
            journal.update(state="restoring", restore_required=True)
            try:
                write_json(journal_path, journal, exclusive=False)
            except BaseException as error:
                # Journal I/O must never bypass the physical restore attempt.
                journal_error = error
            try:
                board.restore(baseline, baseline_hash, operation / "flash-restored.bin")
            except BaseException as restore_error:
                journal.update(state="restore_failed", restore_required=True,
                               last_error=type(restore_error).__name__,
                               last_error_message=error_chain(restore_error))
                try:
                    write_json(journal_path, journal, exclusive=False)
                except BaseException:
                    pass
                raise OrchestratorError("full-flash restoration failed; use recover, never replay this attempt") from restore_error
        journal["restore_required"] = False
        journal["next_index"] = row["plan_index"]
        if result is None:
            result = {"attempt_id": row["attempt_id"], "plan_index": row["plan_index"],
                      "state": "failed", "error": type(failure).__name__,
                      "error_message": error_chain(failure),
                      "traceback": _write_failure_traceback(operation, failure)}
        journal["slots"].append(result)
        journal["state"] = "row_failed" if failure else "row_complete"
        journal["current_attempt"] = None
        journal["current_plan_index"] = None
        try:
            write_json(journal_path, journal, exclusive=False)
        except BaseException as error:
            journal_error = journal_error or error
        if journal_error is not None:
            failure = failure or OrchestratorError(
                "flash was restored, but journal durability failed; run recover before resuming")
    if failure:
        raise OrchestratorError(f"issue #22 row {row['plan_index']} failed after one-shot claim; it will not be retried") from failure
    return result


def _rehearse_row(options, row: dict, root: Path, board: Board, profile_data: dict,
                  server: RemoteFixtureServer, baseline: Path, baseline_hash: str) -> dict:
    """Run one row's complete setup without a claim, runner trial or HTTP correlation.

    Signed sequence-2 provisioning, the NVS preflight and the post-esptool boot
    check are the same code a campaign row uses, so a setup defect shows up
    here instead of burning one-shot rows.
    """
    journal_path = root / "journal.json"
    operation = _private_output(root / "rehearsals", f"{row['scenario']}-{time.time_ns()}")
    journal = load_json(journal_path, "orchestrator journal")
    started = time.monotonic()
    result, failure, mutation_started = None, None, False
    try:
        board.identify()
        board.verify_image(baseline, baseline_hash)
        nvs_start = operation / "nvs-initial.bin"
        board.read(NVS_OFFSET, NVS_BYTES, nvs_start)
        _verify_nvs_not_already_updated(nvs_start, {"native": "native", "interp": "module-interp",
                                                   "aot": "module-aot"}[row["scenario"]])
        journal.update(state="rehearsal_provisioning", restore_required=True)
        write_json(journal_path, journal)
        mutation_started = True
        prepared = _prepare_profile(options, board, row, profile_data, server,
                                    operation, baseline, baseline_hash)
        result = {"scenario": row["scenario"], "state": "setup_passed",
                  "boot_id": prepared["boot_id"],
                  "prepared_flash_sha256": prepared["prepared_flash_sha256"]}
    except BaseException as error:
        failure = error
        result = {"scenario": row["scenario"], "state": "setup_failed",
                  "error": type(error).__name__, "error_message": error_chain(error),
                  "traceback": _write_failure_traceback(operation, error)}
    finally:
        if mutation_started:
            # A journal write failure must never skip the restore attempt.
            journal.update(state="restoring", restore_required=True)
            try:
                write_json(journal_path, journal)
            except BaseException as error:
                failure = failure or error
            try:
                board.restore(baseline, baseline_hash, operation / "flash-restored.bin")
            except BaseException as restore_error:
                journal.update(state="restore_failed", restore_required=True,
                               last_error=type(restore_error).__name__,
                               last_error_message=error_chain(restore_error))
                journal.setdefault("rehearsals", []).append(result)
                try:
                    write_json(journal_path, journal)
                except BaseException:
                    pass
                raise OrchestratorError("rehearsal restoration failed; use recover") from restore_error
        journal = load_json(journal_path, "orchestrator journal")
        result["operation"] = str(operation)
        result["seconds"] = round(time.monotonic() - started, 1)
        journal.setdefault("rehearsals", []).append(result)
        # The fixture manager accepts ready only after restoration. Keep the
        # rehearsal flag/history for isolation, while using its existing safe
        # handoff state before the next profile's before_child check.
        journal.update(state="ready", restore_required=False)
        write_json(journal_path, journal)
    if failure is not None and (not isinstance(failure, Exception)
                                or result["state"] == "setup_passed"):
        raise failure
    return result


def _clone_inputs(options, root: Path, profile_data: dict) -> dict:
    snapshots = private_directory(root / "inputs")
    profiles = {}
    for name, profile in profile_data["profiles"].items():
        source = Path(profile["path"])
        target = snapshots / f"{name}-v1-app.bin"
        _ensure_snapshot_artifact(source, target, profile["sha256"])
        auxiliary = {}
        for kind, suffix in (("bootloader", "bin"), ("sdkconfig", "txt"), ("bootloader_config", "h")):
            artifact = profile[kind]
            artifact_target = snapshots / f"{name}-{kind}.{suffix}"
            _ensure_snapshot_artifact(Path(artifact["path"]), artifact_target, artifact["sha256"])
            auxiliary[kind] = {**artifact, "path": str(artifact_target)}
        profiles[name] = {**profile, "path": str(target), **auxiliary}
    table = profile_data["partition_table"]
    table_target = snapshots / "profile-partition-table.bin"
    _ensure_snapshot_artifact(Path(table["path"]), table_target, table["sha256"])
    return {**profile_data, "partition_table": {**table, "path": str(table_target)},
            "profiles": profiles}


def _board_operations(root: Path) -> Path:
    return private_directory(root / "board-operations")


def _verify_live_baseline(board: Board, operation: Path, baseline_hash: str) -> Path:
    board.identify()
    live = operation / f"baseline-live-check-{uuid.uuid4().hex}.bin"
    board.read(0, FLASH_BYTES, live, timeout=600)
    require(sha256_file(live) == baseline_hash,
            "board full-flash SHA-256 differs from the #20 accepted baseline")
    return live


def _verify_session_end(board: Board, operation: Path, journal_path: Path,
                        baseline_hash: str) -> None:
    """Full SHA-256 readback after the last row of an invocation."""
    journal = load_json(journal_path, "orchestrator journal")
    if journal.get("restore_required") is not False:
        return
    try:
        live = _verify_live_baseline(board, operation, baseline_hash)
    except OrchestratorError as error:
        if "differs" in str(error):
            journal.update(state="final_verification_failed", restore_required=True,
                           last_error_message=error_chain(error))
            write_json(journal_path, journal)
        raise
    journal["final_readback"] = {"path": str(live), "sha256": baseline_hash,
                                 "time_unix_ns": time.time_ns()}
    write_json(journal_path, journal)


def _initial_journal(root: Path, options: argparse.Namespace,
                     profile_data: dict, timeout_proof: dict,
                     baseline: dict, package_data: dict, tls: dict) -> dict:
    return {"schema": JOURNAL_SCHEMA, "state": "initializing", "restore_required": False,
            "next_index": 0, "slots": [], "baseline": None, "baseline_sha256": baseline["sha256"],
            "profiles_source_commit": profile_data["source_commit"],
            "profiles_manifest_sha256": sha256_file(options.profiles),
            "timeout_proof_sha256": sha256_file(options.timeout_proof),
            "timeout_ms": timeout_proof["functional_timeout_ms"],
            "package_fingerprint": package_data,
            "tls_fingerprint": tls,
            "immutable_options": {key: str(value) if isinstance(value, Path) else value
                                  for key, value in vars(options).items()}}


def _validate_resume_options(saved: dict, options: argparse.Namespace) -> None:
    for key, value in vars(options).items():
        if key in {"action", "timeout_ms", "accepted_baseline"} or key not in saved:
            continue
        normalized = str(value) if isinstance(value, Path) else value
        require(saved[key] == normalized,
                f"resume configuration changed for {key}; use --action resume")


def execute(options: argparse.Namespace) -> dict:
    _validate_run_options(options)
    timeout_proof = validate_timeout_proof(options.timeout_proof)
    options.timeout_ms = timeout_proof["functional_timeout_ms"]
    tls = validate_tls_files(options.tls_ca, options.tls_certificate,
                             options.tls_private_key, options.server_name)
    profile_data = validate_profile_manifest(options.profiles, options.timeout_ms,
        options.server_ip, options.https_port, options.server_name, options.tls_ca,
        options.ota_signing_public)
    validate_bench_network(profile_data, options.bind_ip)
    validate_observer_host(options.bind_ip, options.device_ip)
    package_data = validate_packages(options.ota_package_root, options.ota_signing_public,
        options.fixture_root, options.fixture_trusted_public, options.fixture_untrusted_public,
        options.aot_toolchain_id)
    baseline_data = validate_accepted_baseline(options.accepted_baseline,
                                               options.accepted_baseline_sha256)
    strict_ssh_args(options.ct_target, options.ct_identity, options.ct_known_hosts,
                    options.ct_ssh_port, options.ct_host_key_alias)
    _pinned_manager_ssh(options)

    root = private_directory(options.output_root)
    journal_path = root / "journal.json"
    with hardware_lock():
        plan_path = root / "plan.jsonl"
        if not plan_path.exists():
            rows = write_plan(plan_path, options.plan_seed or str(uuid.uuid4()))
        else:
            rows = load_plan(plan_path)
        bundle = _clone_inputs(options, root, profile_data)
        inputs = private_directory(root / "inputs")
        baseline = _ensure_snapshot_artifact(baseline_data["path"],
            inputs / "accepted-full-flash.bin", baseline_data["sha256"])
        journal_baseline = str(baseline)
        rehearsal = options.action == "rehearse"
        if journal_path.exists():
            journal = load_json(journal_path, "orchestrator journal")
            require(journal.get("schema") == JOURNAL_SCHEMA,
                    "unsupported issue #22 journal schema")
            require(not rehearsal and journal.get("rehearsal") is not True,
                    "rehearsals need a fresh output root and are never resumed as campaigns")
            require(journal.get("restore_required") is False,
                    "interrupted campaign needs recover action before any further slot")
            require(journal.get("baseline") == journal_baseline
                    and journal.get("baseline_sha256") == baseline_data["sha256"],
                    "accepted #20 baseline differs from the campaign journal")
            require(journal.get("timeout_proof_sha256") == sha256_file(options.timeout_proof)
                    and journal.get("timeout_ms") == timeout_proof["functional_timeout_ms"],
                    "frozen timeout proof changed; use the original proof or start a new campaign")
            require(journal.get("profiles_source_commit") == profile_data["source_commit"]
                    and journal.get("profiles_manifest_sha256") == sha256_file(options.profiles)
                    and journal.get("package_fingerprint") == package_data
                    and journal.get("tls_fingerprint") == tls,
                    "signed OTA/profile/TLS inputs differ from the campaign journal")
            saved = journal.get("immutable_options", {})
            _validate_resume_options(saved, options)
        else:
            journal = _initial_journal(root, options, profile_data, timeout_proof,
                                       baseline_data, package_data, tls)
            journal["baseline"] = journal_baseline
            journal["recoveries"] = []
            journal["rehearsal"] = rehearsal
            write_json(journal_path, journal, exclusive=True)

        claimed = _verify_claims_match_plan(root, rows)
        next_index = journal.get("next_index")
        require(type(next_index) is int and 0 <= next_index <= 16,
                "journal schedule index is invalid")
        require(claimed <= next_index,
                "one-shot claim is newer than the journal; recover before resuming")
        if next_index == 16:
            # A process can be interrupted after persisting the final schedule
            # index but before its batch guard stops the listener. A completed
            # resume is cleanup-only and never stages or claims another row.
            manager_hook = Issue22FixtureServerHook(
                root / "server-manager-spec.json", root / "server-manager-state.json",
                journal_path, spec_sha256=None)
            cleanup = manager_hook.cleanup_only()
            require(cleanup.get("state") in {"stopped", "absent", "uninitialized"},
                    "completed campaign still has a manager-owned fixture listener")
            journal["state"] = "complete"
            write_json(journal_path, journal)
            return {"state": "complete", "attempted_slots": 0, "next_index": 16,
                    "campaign_root": str(root), "timeout_ms": options.timeout_ms}

        operation = _board_operations(root)
        board = Board(options, operation)
        spec_path, state_path, spec_sha256, tool_fingerprints = _freeze_server_manager(
            options, root, options.fixture_root)
        manager_fingerprint = {"server_kind": "issue22-fixture",
                               "spec_sha256": spec_sha256,
                               "tool_sha256": tool_fingerprints}
        if _refresh_manager_fingerprint_after_recovery(
                journal, manager_fingerprint, claimed, baseline_data["sha256"]):
            write_json(journal_path, journal, exclusive=False)
        hook = Issue22FixtureServerHook(spec_path, state_path, journal_path,
                                        spec_sha256=spec_sha256)
        server = _server_arguments(options, root, options.ota_package_root,
                                   options.fixture_root,
                                   manager_spec=user_server_manager.load_spec(
                                       spec_path, spec_sha256))
        # The remote lease performs its authoritative read-only preflight,
        # reserves the socket, and stays alive through staging and the first
        # before_child handoff. No hardware claim is written before ensure.
        with hook.stage_and_batch_guard(server):
            # This first board access is read-only. A mismatch must fail
            # without granting recover permission to overwrite a flash image
            # that #20 did not accept.
            journal.update(state="baseline_verification", restore_required=False)
            write_json(journal_path, journal)
            _verify_live_baseline(board, operation, baseline_data["sha256"])
            journal.update(state="ready", restore_required=False,
                           baseline=journal_baseline, baseline_sha256=baseline_data["sha256"])
            write_json(journal_path, journal)
            attempted = 0
            touched = False
            rehearsals = []
            try:
                if rehearsal:
                    for scenario in ("native", "interp", "aot"):
                        row = next(item for item in rows if item["scenario"] == scenario)
                        hook.before_child()
                        touched = True
                        rehearsals.append(_rehearse_row(options, row, root, board, bundle,
                                                        server, baseline, baseline_data["sha256"]))
                        attempted += 1
                else:
                    for row in rows[next_index:min(16, next_index + options.max_slots)]:
                        # Ensure the exact manager-owned listener before the one-shot
                        # claim is written inside _run_row. Readiness retries cannot
                        # claim or re-run a hardware trial.
                        hook.before_child()
                        touched = True
                        _run_row(options, row, plan_path, root, board, bundle, server,
                                 baseline, baseline_data["sha256"])
                        attempted += 1
            finally:
                if touched:
                    _verify_session_end(board, operation, journal_path, baseline_data["sha256"])
            if rehearsal:
                journal = load_json(journal_path, "orchestrator journal")
                passed = all(item["state"] == "setup_passed" for item in rehearsals)
                journal["state"] = "rehearsal_passed" if passed else "rehearsal_failed"
                write_json(journal_path, journal)
                require(passed, "setup rehearsal failed: " + "; ".join(
                    f"{item['scenario']}: {item.get('error_message')}" for item in rehearsals
                    if item["state"] != "setup_passed"))
                return {"state": journal["state"], "rehearsals": rehearsals,
                        "campaign_root": str(root)}
            journal = load_json(journal_path, "orchestrator journal")
            journal["state"] = "complete" if journal["next_index"] == 16 else "ready"
            write_json(journal_path, journal)
            return {"state": journal["state"], "attempted_slots": attempted,
                    "next_index": journal["next_index"], "campaign_root": str(root),
                    "timeout_ms": options.timeout_ms, "package": package_data, "tls": tls}


def _validate_run_options(options: argparse.Namespace) -> None:
    require(os.name == "posix" and os.geteuid() != 0,
            "issue #22 HIL must run as an ordinary POSIX user")
    for name in PATH_OPTIONS:
        value = getattr(options, name, None)
        if value is not None:
            setattr(options, name, Path(value).expanduser().absolute())
    mandatory = ("device_ip", "bind_ip", "server_ip", "expected_mac", "serial_port",
        "esptool", "output_root", "accepted_baseline", "accepted_baseline_sha256",
        "profiles", "timeout_proof", "ota_package_root", "ota_signing_public",
        "fixture_root", "fixture_trusted_public", "fixture_untrusted_public",
        "aot_toolchain_id", "tls_ca", "tls_certificate", "tls_private_key",
        "server_name")
    missing = [name for name in mandatory if not getattr(options, name, None)]
    require(not missing, "missing required execution inputs: " + ", ".join(missing))
    options.device_ip = str(ipaddress.IPv4Address(options.device_ip))
    options.bind_ip = str(ipaddress.IPv4Address(options.bind_ip))
    options.server_ip = str(ipaddress.IPv4Address(options.server_ip))
    require(options.server_ip not in {"0.0.0.0", "127.0.0.1"},
            "firmware HTTPS endpoint must be a literal CT/LAN address reachable by the device")
    require(options.https_port >= 1024 and options.https_port not in {8443, 8444},
            "fixture/package service must use an unprivileged non-campaign port")
    require(re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", options.expected_mac),
            "expected MAC must be a canonical six-octet address")
    require(1 <= options.max_slots <= 16, "#22 supports only slots 1..16; #23's 72 rows are separate")
    require(options.serial_baud > 0 and options.flash_baud > 0,
            "serial and esptool baud rates must be positive")
    require(options.trial_timeout_seconds >= 60,
            "trial timeout must be at least the 60-second signed OTA provisioning window")


def recover(options: argparse.Namespace) -> dict:
    require(os.name == "posix" and os.geteuid() != 0,
            "issue #22 recovery must run as an ordinary POSIX user")
    require(getattr(options, "serial_port", None) and getattr(options, "esptool", None)
            and getattr(options, "expected_mac", None),
            "recovery journal lacks the registered serial/esptool/board identity")
    root = private_directory(options.output_root, create=False)
    journal_path = root / "journal.json"
    inputs = private_directory(root / "inputs", create=False)
    baseline = inputs / "accepted-full-flash.bin"
    with hardware_lock():
        journal = load_json(journal_path, "orchestrator journal")
        require(journal.get("schema") == JOURNAL_SCHEMA,
                "unsupported issue #22 journal schema")
        baseline_hash = journal.get("baseline_sha256")
        require(isinstance(baseline_hash, str) and re.fullmatch(r"[0-9a-f]{64}", baseline_hash)
                and options.accepted_baseline_sha256 == baseline_hash
                and journal.get("baseline") == str(baseline)
                and baseline.is_file() and baseline.stat().st_size == FLASH_BYTES
                and sha256_file(baseline) == baseline_hash,
                "recovery baseline differs from the #20 accepted image/hash")
        validate_accepted_baseline_partition_table(
            baseline.read_bytes()[TABLE_OFFSET:TABLE_OFFSET + TABLE_BYTES])
        rows = load_plan(root / "plan.jsonl")
        claimed = _verify_claims_match_plan(root, rows)
        operation = _board_operations(root)
        board = Board(options, operation)
        needs_restore = journal.get("restore_required") is True
        remote_stopped = False
        try:
            manager_hook = Issue22FixtureServerHook(
                root / "server-manager-spec.json", root / "server-manager-state.json",
                journal_path, spec_sha256=None)
            cleanup = manager_hook.cleanup_only()
            remote_stopped = cleanup.get("state") in {"stopped", "absent", "uninitialized"}
        except (OrchestratorError, OSError, ValueError, subprocess.SubprocessError):
            # Never let CT reachability block local safety restoration. The
            # cleanup-only manager can signal only its saved exact-process
            # receipt and does not need the spec or hardware journal.
            remote_stopped = False
        board.identify()
        recovery_dir = _private_output(root / "recoveries", f"{time.time_ns()}")
        if needs_restore:
            board.restore(baseline, baseline_hash, recovery_dir / "flash-restored.bin",
                          full_readback=True)
            receipt = str(recovery_dir / "flash-restored.bin")
        else:
            # A durable false restore_required means no write phase began.
            # Verify this invariant instead of rewriting a device whose
            # read-only baseline preflight failed.
            _verify_live_baseline(board, operation, baseline_hash)
            receipt = None
        current = load_json(journal_path, "orchestrator journal")
        prior_index = current.get("next_index", 0)
        require(type(prior_index) is int and 0 <= prior_index <= 16,
                "journal schedule index is invalid")
        current["next_index"] = max(prior_index, claimed)
        current["restore_required"] = False
        current["state"] = "interrupted_recovered"
        current["current_attempt"] = None
        current["current_plan_index"] = None
        recoveries = current.get("recoveries", [])
        require(isinstance(recoveries, list), "recovery journal history is malformed")
        recoveries.append({"time_unix_ns": time.time_ns(), "claimed_through": claimed,
                           "next_index": current["next_index"],
                           "baseline_sha256": baseline_hash,
                           "restoration_receipt": receipt,
                           "flash_rewritten": needs_restore,
                           "remote_listener_stopped": remote_stopped})
        current["recoveries"] = recoveries
        write_json(journal_path, current)
        return {"state": current["state"], "next_index": current["next_index"],
                "claimed_through": claimed, "remote_listener_stopped": remote_stopped,
                "campaign_root": str(root)}


SMOKE_SCHEMA = "wasmhome-issue22-bench-smoke-v1"
UART_MARKERS = ("wifi_ip_acquired", "udp_bound", "wifi_disconnected", "WASMHOME_WIFI",
                "boot_observed", "telemetry_send_failed", "rst:", "Guru Meditation",
                "abort()", "Rebooting...")


def _capture_uart(port, destination: Path, stop, errors: list) -> None:
    try:
        with destination.open("xb") as stream:
            os.chmod(destination, 0o600)
            while not stop.is_set():
                data = port.read(4096)
                if data:
                    stream.write(data)
                    stream.flush()
                else:
                    stop.wait(0.02)
    except Exception as error:
        errors.append(error)
        stop.set()


def _smoke_diagnosis(uart: bytes, observations: list[dict], summary: dict) -> str:
    counters = summary.get("counters", {})
    received = sum(row.get("kind") == "receive" for row in observations)
    text = uart.decode("utf-8", errors="replace")
    if ("Guru Meditation" in text or "abort()" in text or text.count("rst:") > 1
            or len(summary.get("boot_ids", [])) > 1):
        return "firmware_crash_or_reboot_loop"
    if not uart:
        return "no_serial_output_board_not_running"
    if counters.get("telemetry_valid", 0) >= 1 and sum(summary.get("valid_controls_by_boot", {}).values()) >= 1:
        return "pass"
    if "wifi_ip_acquired" not in text:
        return "wifi_not_connected"
    if "udp_bound" not in text:
        return "udp_socket_not_bound"
    if received == 0:
        return "no_datagram_reached_observer_check_firewall_and_observer_ip"
    return "datagrams_without_valid_telemetry_or_control"


def smoke(options: argparse.Namespace) -> dict:
    """Bench smoke test that never claims a plan row.

    Verify the baseline, boot one bootstrap profile, capture UART and passive
    observer traffic without any UPDATE_TRIGGER, then restore and read back the
    full image. It answers "does this build reach the observer?" and measures
    the esptool timings before a campaign spends one-shot rows.
    """
    validate_cli(options)
    profile_data = validate_profile_manifest(options.profiles, options.timeout_ms,
        options.server_ip, options.https_port, options.server_name, options.tls_ca,
        options.ota_signing_public)
    firewall = validate_observer_host(options.bind_ip, options.device_ip)
    baseline_data = validate_accepted_baseline(options.accepted_baseline,
                                               options.accepted_baseline_sha256)
    require(options.smoke_profile in profile_data["profiles"], "unknown smoke profile")
    require(5 <= options.smoke_seconds <= 120, "smoke listen window must be 5..120 s")
    root = private_directory(options.output_root)
    run_dir = _private_output(root, f"smoke-{time.strftime('%Y%m%dT%H%M%S')}-{options.smoke_profile}")
    profile = profile_data["profiles"][options.smoke_profile]
    baseline = Path(baseline_data["path"])
    timings, result = {}, {"schema": SMOKE_SCHEMA, "profile": options.smoke_profile,
                           "firewall": firewall, "run_dir": str(run_dir)}

    def timed(name, function, *args, **kwargs):
        started = time.monotonic()
        try:
            return function(*args, **kwargs)
        finally:
            timings[name] = round(time.monotonic() - started, 1)

    with hardware_lock():
        board = Board(options, run_dir)
        board.identify()
        timed("verify_flash_md5_s", board.verify_image, baseline, baseline_data["sha256"])
        failure = None
        try:
            timed("overlay_write_s", board.overlay_profile, Path(profile["path"]),
                  Path(profile["bootloader"]["path"]), Path(profile_data["partition_table"]["path"]))
            board.run()
            reset_ns = time.monotonic_ns()
            port = hil_runner.ExclusiveSerialPort(options.serial_port, options.serial_baud)
            stop = threading.Event()
            uart_path = run_dir / "uart.log"
            uart_errors = []
            reader = threading.Thread(target=_capture_uart,
                                      args=(port, uart_path, stop, uart_errors), daemon=True)
            try:
                reader.start()
                summary = external_observer.run(
                    device_ip=options.device_ip, output=run_dir / "observer",
                    seconds=options.smoke_seconds, bind_ip=options.bind_ip, port=OBSERVER_UDP_PORT,
                    device_port=OBSERVER_UDP_PORT, period_ms=100, timeout_ms=options.timeout_ms,
                    initial_version=1, max_sample_age_ms=1000)
            finally:
                stop.set()
                try:
                    if reader.ident is not None:
                        reader.join(timeout=5)
                finally:
                    port.close()
            require(not reader.is_alive(), "smoke UART capture did not stop")
            if uart_errors:
                raise OrchestratorError("smoke UART capture failed") from uart_errors[0]
            uart = uart_path.read_bytes() if uart_path.exists() else b""
            observations = [json.loads(line) for line in
                            (run_dir / "observer" / "observations.jsonl").read_text().splitlines()]
            receives = [row["host_monotonic_ns"] for row in observations if row.get("kind") == "receive"]
            text = uart.decode("utf-8", errors="replace")
            result.update({
                "diagnosis": _smoke_diagnosis(uart, observations, summary),
                "uart_bytes": len(uart),
                "uart_markers": {marker: text.count(marker) for marker in UART_MARKERS},
                "datagrams_received": len(receives),
                "first_datagram_after_reset_s": (None if not receives
                                                 else round((receives[0] - reset_ns) / 1e9, 2)),
                "boot_ids": summary.get("boot_ids"),
                "telemetry_valid": summary.get("counters", {}).get("telemetry_valid", 0),
                "valid_controls_by_boot": summary.get("valid_controls_by_boot"),
                "lost_controls": summary.get("lost_controls"),
            })
            require(result["diagnosis"] == "pass",
                    f"bench smoke failed: {result['diagnosis']}")
        except BaseException as error:
            failure = error
            result["error"] = error_chain(error)
        finally:
            timed("restore_write_md5_s", board.restore, baseline, baseline_data["sha256"],
                  run_dir / "flash-restored.bin")
            timed("full_readback_sha256_s", _verify_live_baseline, board, run_dir,
                  baseline_data["sha256"])
            result["timings"] = timings
            result["restored_sha256"] = baseline_data["sha256"]
            write_json(run_dir / "smoke-summary.json", result, exclusive=True)
        if failure is not None:
            raise OrchestratorError(f"bench smoke stopped; board restored, see {run_dir}") from failure
    return result


def validate_cli(options: argparse.Namespace) -> None:
    """Static gate used by tests and by the no-hardware --check command."""
    _validate_run_options(options)
    timeout = validate_timeout_proof(options.timeout_proof)
    options.timeout_ms = timeout["functional_timeout_ms"]
    validate_tls_files(options.tls_ca, options.tls_certificate,
                       options.tls_private_key, options.server_name)
    profile_data = validate_profile_manifest(options.profiles, options.timeout_ms,
        str(ipaddress.IPv4Address(options.server_ip)), options.https_port,
        options.server_name, options.tls_ca, options.ota_signing_public)
    validate_bench_network(profile_data, options.bind_ip)
    validate_observer_host(options.bind_ip, options.device_ip)
    validate_packages(options.ota_package_root, options.ota_signing_public,
        options.fixture_root, options.fixture_trusted_public,
        options.fixture_untrusted_public, options.aot_toolchain_id)
    validate_accepted_baseline(options.accepted_baseline, options.accepted_baseline_sha256)
    strict_ssh_args(options.ct_target, options.ct_identity, options.ct_known_hosts,
                    options.ct_ssh_port, options.ct_host_key_alias)


PATH_OPTIONS = {
    "profiles", "timeout_proof", "ota_package_root", "ota_signing_public",
    "fixture_root", "fixture_trusted_public", "fixture_untrusted_public",
    "tls_ca", "tls_certificate", "tls_private_key", "ct_identity",
    "ct_known_hosts", "ct_jump_identity", "ct_jump_known_hosts",
    "accepted_baseline", "output_root",
}


def load_saved_options(output_root: Path, action: str) -> argparse.Namespace:
    root = private_directory(output_root, create=False)
    journal = load_json(root / "journal.json", "orchestrator journal")
    require(journal.get("schema") == JOURNAL_SCHEMA,
            "unsupported issue #22 journal schema")
    saved = journal.get("immutable_options")
    require(isinstance(saved, dict), "campaign journal has no resumable immutable configuration")
    require("output_root" in saved, "campaign journal does not bind its output root")
    require(Path(saved["output_root"]).absolute() == root,
            "--output-root differs from the campaign journal")
    values = dict(saved)
    values["action"] = "run" if action == "resume" else "recover"
    for name in PATH_OPTIONS:
        if name in values and values[name] is not None:
            values[name] = Path(values[name])
    # The campaign owns a private, hash-verified copy of the accepted image,
    # so crash recovery does not depend on the original input path remaining
    # mounted or available.
    if action in {"resume", "recover"}:
        values["accepted_baseline"] = root / "inputs" / "accepted-full-flash.bin"
        values["accepted_baseline_sha256"] = journal.get("baseline_sha256")
    return argparse.Namespace(**values)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--action", choices=("check", "smoke", "rehearse", "run", "resume",
                                             "recover"), default="check")
    parser.add_argument("--profiles", type=Path)
    parser.add_argument("--timeout-proof", type=Path)
    parser.add_argument("--ota-package-root", type=Path)
    parser.add_argument("--ota-signing-public", type=Path)
    parser.add_argument("--fixture-root", type=Path)
    parser.add_argument("--fixture-trusted-public", type=Path)
    parser.add_argument("--fixture-untrusted-public", type=Path)
    parser.add_argument("--aot-toolchain-id")
    parser.add_argument("--tls-ca", type=Path)
    parser.add_argument("--tls-certificate", type=Path)
    parser.add_argument("--tls-private-key", type=Path)
    parser.add_argument("--server-name")
    parser.add_argument("--server-ip")
    parser.add_argument("--https-port", type=int, default=PORT)
    parser.add_argument("--ct-target", default="wasmhome@192.168.0.138")
    parser.add_argument("--ct-identity", type=Path,
                        default=Path.home() / ".ssh/wasmhome-ct107-hil")
    parser.add_argument("--ct-known-hosts", type=Path,
                        default=Path.home() / ".ssh/wasmhome-ct107_known_hosts")
    parser.add_argument("--ct-host-key-alias", default="192.168.0.138")
    parser.add_argument("--ct-ssh-port", type=int, default=22)
    parser.add_argument("--ct-jump-target",
                        help="optional pinned SSH jump, e.g. user@Proxmox-Tailscale-IP")
    parser.add_argument("--ct-jump-identity", type=Path)
    parser.add_argument("--ct-jump-known-hosts", type=Path)
    parser.add_argument("--ct-jump-host-key-alias")
    parser.add_argument("--ct-jump-ssh-port", type=int, default=22)
    parser.add_argument("--ct-root", default="/home/wasmhome/.local/share/wasmhome-hil")
    parser.add_argument("--device-ip")
    parser.add_argument("--bind-ip")
    parser.add_argument("--serial-port")
    parser.add_argument("--serial-baud", type=int, default=115200)
    parser.add_argument("--flash-baud", type=int, default=921600)
    parser.add_argument("--expected-mac")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--esptool")
    parser.add_argument("--accepted-baseline", type=Path)
    parser.add_argument("--accepted-baseline-sha256")
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--plan-seed")
    parser.add_argument("--max-slots", type=int, default=16)
    parser.add_argument("--trial-timeout-seconds", type=int, default=600)
    parser.add_argument("--smoke-profile", choices=("native", "interp", "aot"), default="native",
                        help="bootstrap profile booted by --action smoke")
    parser.add_argument("--smoke-seconds", type=float, default=20,
                        help="passive observer window for --action smoke")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    options = parser.parse_args(argv)
    try:
        if options.action in {"resume", "recover"}:
            if options.output_root is None:
                parser.error(f"--action {options.action} requires --output-root")
            options = load_saved_options(options.output_root, options.action)
            if options.action == "recover":
                print(json.dumps(recover(options), sort_keys=True))
                return 0
            print(json.dumps(execute(options), sort_keys=True))
            return 0
        if options.action == "smoke":
            print(json.dumps(smoke(options), sort_keys=True))
            return 0
        if options.action == "check":
            validate_cli(options)
            print(json.dumps({"status": "ready_for_hil_after_#20_freeze",
                              "matrix_rows": 16, "issue23_rows": 72,
                              "timeout_ms": options.timeout_ms,
                              "user_mode_only": True}, sort_keys=True))
            return 0
        print(json.dumps(execute(options), sort_keys=True))
        return 0
    except (OrchestratorError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        parser.exit(2, f"Issue #22 HIL orchestrator stopped safely: {error_chain(error)}\n")


if __name__ == "__main__":
    raise SystemExit(main())
