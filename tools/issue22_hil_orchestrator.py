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
import time
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
PROFILE_SCHEMA = "wasmhome-issue22-bootstrap-profiles-v2"
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


class OrchestratorError(ValueError):
    """Fail-closed validation or orchestration error."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise OrchestratorError(message)


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
    validate_partition_table(table)
    return {"path": image, "sha256": expected_hash, "bytes": FLASH_BYTES}


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
    end_tag = b"\nEND_WASMHOME_ISSUE22_BUILD_V2"
    require(data.count(start_tag) == 1 and data.count(end_tag) == 1,
            "bootstrap app must embed exactly one CMake-generated #22 provenance marker")
    start = data.index(start_tag) + len(start_tag)
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
    require(set(marker) == {"profile", "functional_timeout_ms", "server_ipv4",
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
                              server_port: int, server_name: str, ca_path: Path) -> dict:
    value = load_json(path, "issue #22 profile build manifest", max_bytes=128 * 1024)
    require(value.get("schema") == PROFILE_SCHEMA and value.get("resilience_test_mode") is True,
            "profile manifest must attest opt-in issue #22 test hooks")
    require(value.get("functional_timeout_ms") == timeout_ms,
            "profile build timeout differs from the frozen #20 timeout")
    endpoint = value.get("endpoint")
    require(isinstance(endpoint, dict) and set(endpoint) == {"ipv4", "port", "server_name", "ca_sha256"}
            and endpoint["ipv4"] == server_ip and endpoint["port"] == server_port
            and endpoint["server_name"] == server_name
            and endpoint["ca_sha256"] == sha256_file(ca_path),
            "profile build HTTPS endpoint/CA differs from the user-level fixture server")
    require(value.get("update_endpoint") == endpoint,
            "normal signed provisioning and resilience fixture manifest endpoints must match")
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
        require(marker == {"profile": name, "functional_timeout_ms": timeout_ms,
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
        snapshots[name] = {"path": str(app), "sha256": digest, "bytes": app.stat().st_size,
                           "build_provenance": marker,
                           "bootloader": {"path": str(bootloader), "sha256": bootloader_digest,
                                          "bytes": len(bootloader_data)},
                           "sdkconfig": {"path": str(config), "sha256": config_digest},
                           "bootloader_config": {"path": str(boot_config), "sha256": boot_config_digest}}
    require(isinstance(value.get("source_commit"), str)
            and re.fullmatch(r"[0-9a-f]{40,64}", value["source_commit"]),
            "profile build manifest must identify its source commit")
    return {"schema": PROFILE_SCHEMA, "source_commit": value["source_commit"],
            "functional_timeout_ms": timeout_ms, "endpoint": endpoint,
            "update_endpoint": endpoint, "profiles": snapshots}


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
            "ota_v2": sorted((scenario, digest) for scenario, version, sequence, digest in verified_v2)}


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


class SSHSession:
    def __init__(self, target: str, identity: Path, known_hosts: Path, port: int = 22,
                 host_key_alias: str | None = None):
        self.target = target
        self.options = strict_ssh_args(target, identity, known_hosts, port, host_key_alias)

    def run(self, command: list[str], *, timeout: int = 30, check: bool = True) -> subprocess.CompletedProcess:
        result = subprocess.run(["ssh", *self.options, self.target, shlex.join(command)],
                                capture_output=True, timeout=timeout, check=False)
        if check and result.returncode:
            # Never copy remote server logs or secrets into shared stdout.
            raise OrchestratorError("strict CT SSH command failed; private operation log has details")
        return result

    def scp(self, source: Path, destination: str, *, recursive: bool = False) -> None:
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
                 server_ip: str, port: int, server_name: str, toolchain: str):
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
        self.log = self.root / "http-events.jsonl"
        self.pid_file = self.root / "server.pid"
        self.ready = False

    def _validate_remote_path(self, path: Path, expected: str) -> None:
        """Refuse unsafe remote paths before mkdir/SCP/receipt operations."""
        script = (
            "import os,pathlib,stat,sys\n"
            "root=pathlib.Path(sys.argv[1]); p=pathlib.Path(sys.argv[2]); kind=sys.argv[3]\n"
            "assert root.is_absolute() and p.is_absolute() and '..' not in root.parts and '..' not in p.parts\n"
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
            " assert not stat.S_ISLNK(s.st_mode) and stat.S_ISDIR(s.st_mode), 'unsafe path component'\n"
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
        self.ssh.run(["python3", "-c", script, str(self.root), str(path), expected], timeout=20)

    def _validate_remote_root(self) -> None:
        self._validate_remote_path(self.root, "dir")

    def _copy(self, source: Path, destination: Path) -> None:
        if source.is_dir():
            self.ssh.run(["mkdir", "-m", "700", "-p", str(destination)], timeout=20)
            self._validate_remote_path(destination, "dir")
            for child in _safe_tree(source):
                relative = child.relative_to(source)
                remote_parent = destination / relative.parent
                self.ssh.run(["mkdir", "-m", "700", "-p", str(remote_parent)], timeout=20)
                self._validate_remote_path(remote_parent, "dir")
                remote_file = remote_parent / child.name
                self._validate_remote_path(remote_file, "file")
                self.ssh.scp(child, str(remote_file))
                self._validate_remote_path(remote_file, "file")
        else:
            self._validate_remote_path(destination, "file")
            self.ssh.scp(source, str(destination))
            self._validate_remote_path(destination, "file")

    def start(self) -> None:
        # A previous invocation may have crashed while leaving its listener
        # alive. Reap only a process proven by this campaign's pid, kernel
        # start-time and exact script path before staging or writing a new PID.
        self._validate_remote_root()
        self.stop()
        self.ssh.run(["mkdir", "-m", "700", "-p", str(self.root)], timeout=20)
        self._validate_remote_root()
        server_py = TOOLS / "resilience_fixture_https_server.py"
        signed_py = TOOLS / "signed_artifacts.py"
        code_root = self.root / "tools"
        self.ssh.run(["mkdir", "-m", "700", "-p", str(code_root)], timeout=20)
        self._validate_remote_path(code_root, "dir")
        for source in (server_py, signed_py):
            target = code_root / source.name
            self._validate_remote_path(target, "file")
            self.ssh.scp(source, str(target))
            self._validate_remote_path(target, "file")
        paths = {
            self.fixture_root: self.root / "fixtures",
            self.package_root: self.root / "ota-package",
        }
        for source, destination in paths.items():
            self._copy(source, destination)
        for source, filename in ((self.fixture_trusted, "fixture-trusted.pub"),
                                 (self.fixture_untrusted, "fixture-untrusted.pub"),
                                 (self.ota_public, "ota-signing.pub"),
                                 (self.certificate, "server.crt"),
                                 (self.private_key, "server.key")):
            target = self.root / filename
            self._validate_remote_path(target, "file")
            self.ssh.scp(source, str(target))
            self._validate_remote_path(target, "file")
        self.ssh.run(["chmod", "600", str(self.root / "server.key")], timeout=20)
        self._validate_remote_path(self.root / "server.key", "secret")
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
            "--pid-file", str(self.pid_file)]
        self.ssh.run(["python3", str(code_root / "resilience_fixture_https_server.py"),
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
            "--aot-toolchain-id", self.toolchain, "--log-mode", "600", "--check"], timeout=30)
        # Start only our process. If the configured unprivileged port is occupied,
        # the new process exits and this routine fails; existing units are untouched.
        pid_start = ("python3 -c 'import pathlib,sys; "
                     "s=pathlib.Path(\"/proc\",sys.argv[1],\"stat\").read_text(); "
                     "print(s.rsplit(\")\",1)[1].split()[19])' ")
        server_script = str(code_root / "resilience_fixture_https_server.py")
        terminate_owned = (
            "import os,pathlib,signal,sys\n"
            "pid,start,expected,receipt,port=sys.argv[1:6]\n"
            "raw=pathlib.Path('/proc',pid,'stat').read_text()\n"
            "actual=raw.rsplit(')',1)[1].split()[19]\n"
            "args=pathlib.Path('/proc',pid,'cmdline').read_bytes().split(b'\\0')\n"
            "args=[x.decode(errors='replace') for x in args if x]\n"
            "assert actual==start and len(args)>1 and args[1]==expected, 'process identity differs'\n"
            "assert args[args.index('--pid-file')+1]==receipt and args[args.index('--port')+1]==port, 'campaign arguments differ'\n"
            "os.kill(int(pid),signal.SIGTERM)\n"
        )
        launch = ("set -eu; umask 077; nohup " + shlex.join(command) +
            " >" + shlex.quote(str(self.root / "server.log")) +
            " 2>&1 </dev/null & pid=$!; i=0; start=''; "
            "while [ $i -lt 40 ]; do "
            "if ! kill -0 $pid 2>/dev/null; then exit 3; fi; "
            "start=$(" + pid_start + "$pid 2>/dev/null) || start=''; "
            "[ -n \"$start\" ] && break; i=$((i+1)); sleep 0.05; done; "
            "if [ -z \"$start\" ]; then kill -TERM $pid 2>/dev/null || true; exit 4; fi; "
            "i=0; while [ ! -e " + shlex.quote(str(self.pid_file)) + " ] && [ $i -lt 100 ]; do "
            "if ! kill -0 $pid 2>/dev/null; then exit 3; fi; "
            "i=$((i+1)); sleep 0.05; done; "
            "if [ ! -e " + shlex.quote(str(self.pid_file)) + " ]; then python3 -c " +
            shlex.quote(terminate_owned) + " \"$pid\" \"$start\" " +
            shlex.quote(server_script) + " " + shlex.quote(str(self.pid_file)) + " " +
            shlex.quote(str(self.port)) + " >/dev/null 2>&1 || true; exit 6; fi; "
            "test -f " + shlex.quote(str(self.pid_file)) + " && test ! -L " +
            shlex.quote(str(self.pid_file)) + " || exit 6; "
            "read receipt_pid receipt_start < " + shlex.quote(str(self.pid_file)) + "; "
            "test \"$receipt_pid\" = \"$pid\" && test \"$receipt_start\" = \"$start\" || exit 7")
        try:
            self.ssh.run(["sh", "-c", launch], timeout=20)
            self._validate_remote_path(self.pid_file, "secret")
        except BaseException:
            try:
                self.stop()
            except (OrchestratorError, OSError, subprocess.TimeoutExpired):
                pass
            raise
        wait_code = (
            "read pid start < " + shlex.quote(str(self.pid_file)) + "; "
            "case $pid in ''|*[!0-9]*) exit 2;; esac; "
            "i=0; while [ $i -lt 40 ]; do "
            "if ! kill -0 $pid 2>/dev/null; then exit 3; fi; "
            "current=$(" + pid_start + "$pid); test \"$current\" = \"$start\" || exit 5; "
            "if grep -Fq '\"status\": \"listening\"' " + shlex.quote(str(self.root / "server.log")) +
            " 2>/dev/null; then exit 0; fi; "
            "i=$((i+1)); sleep 0.25; done; exit 4"
        )
        result = self.ssh.run(["sh", "-c", wait_code], timeout=20, check=False)
        if result.returncode != 0:
            try:
                self.stop()
            except (OrchestratorError, OSError, subprocess.TimeoutExpired):
                pass
            raise OrchestratorError(
                "user-level HTTPS listener failed to bind/start (port may be occupied); no system unit was stopped")
        self.ready = True

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
        self._validate_remote_root()
        self._validate_remote_path(self.pid_file, "secret")
        script = str(self.root / "tools/resilience_fixture_https_server.py")
        identity_check = (
            "import pathlib,sys\n"
            "pid,start,expected,receipt,port=sys.argv[1:6]\n"
            "stat=pathlib.Path('/proc',pid,'stat').read_text()\n"
            "actual=stat.rsplit(')',1)[1].split()[19]\n"
            "args=pathlib.Path('/proc',pid,'cmdline').read_bytes().split(b'\\0')\n"
            "args=[x.decode(errors='replace') for x in args if x]\n"
            "assert actual==start and len(args)>1 and args[1]==expected, 'process identity differs'\n"
            "assert args[args.index('--pid-file')+1]==receipt and args[args.index('--port')+1]==port, 'campaign arguments differ'\n"
        )
        stop_code = (
            "test -e " + shlex.quote(str(self.pid_file)) + " || exit 0; "
            "test -f " + shlex.quote(str(self.pid_file)) + " && test ! -L " + shlex.quote(str(self.pid_file)) + " || exit 2; "
            "read pid start < " + shlex.quote(str(self.pid_file)) + "; "
            "case $pid in ''|*[!0-9]*) exit 2;; esac; "
            "if ! kill -0 $pid 2>/dev/null; then rm -f " + shlex.quote(str(self.pid_file)) + "; exit 0; fi; "
            "test -r /proc/$pid/cmdline || exit 3; "
            "python3 -c " + shlex.quote(identity_check) + " \"$pid\" \"$start\" " +
            shlex.quote(script) + " " + shlex.quote(str(self.pid_file)) + " " +
            shlex.quote(str(self.port)) + " || exit 4; "
            "kill -TERM $pid; "
            "i=0; while kill -0 $pid 2>/dev/null && [ $i -lt 20 ]; do "
            "i=$((i+1)); sleep 0.25; done; "
            "kill -0 $pid 2>/dev/null && exit 5; rm -f " + shlex.quote(str(self.pid_file))
        )
        result = self.ssh.run(["sh", "-c", stop_code], timeout=15, check=False)
        self.ready = False
        require(result.returncode == 0, "could not safely stop the owned user-level fixture server")


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

    def overlay_profile(self, app: Path, bootloader: Path) -> None:
        require(app.is_file() and not app.is_symlink()
                and 24 <= app.stat().st_size <= OTA_SLOT_BYTES,
                "profile bootstrap app is invalid")
        require(bootloader.is_file() and not bootloader.is_symlink()
                and 24 <= bootloader.stat().st_size < BOOTLOADER_BYTES,
                "profile bootloader is invalid")
        _validate_bootloader_bytes(bootloader.read_bytes(), "selected")
        bootloader_hash = sha256_file(bootloader)
        # The accepted baseline's bootloader has rollback disabled. Use the
        # matching bootloader from this profile build until full restoration.
        # Identical v1 app copies to both OTA slots make startup independent of
        # prior OTA metadata. The signed sequence-2 transition itself remains
        # the only update operation; no NVS offset is passed to write_flash.
        self.command("write_flash", hex(BOOTLOADER_OFFSET), str(bootloader),
                     hex(OTA_OFFSETS[0]), str(app),
                     hex(OTA_OFFSETS[1]), str(app), timeout=300)
        readback = self.operation / f"bootloader-overlay-{uuid.uuid4().hex}.bin"
        self.read(BOOTLOADER_OFFSET, bootloader.stat().st_size, readback)
        require(sha256_file(readback) == bootloader_hash,
                "profile bootloader readback hash differs; refusing reset")

    def run(self) -> None:
        self.command("run", after="hard_reset", timeout=30)

    def restore(self, baseline: Path, expected_hash: str, destination: Path) -> None:
        require(baseline.stat().st_size == FLASH_BYTES and sha256_file(baseline) == expected_hash,
                "refusing full-flash restoration from an unverified baseline")
        self.command("write_flash", "0x0", str(baseline), timeout=300)
        verify_path = destination
        self.read(0, FLASH_BYTES, verify_path)
        require(sha256_file(verify_path) == expected_hash,
                "full 16 MiB restoration readback hash differs")


def validate_partition_table(data: bytes) -> None:
    require(len(data) == TABLE_BYTES, "partition table must be exactly one 4 KiB sector")
    expected = {}
    import csv
    with (ROOT / "partitions.csv").open(newline="", encoding="utf-8") as stream:
        for row in csv.reader(line for line in stream if line.strip() and not line.lstrip().startswith("#")):
            name, kind, subtype, offset, size, *_ = (item.strip() for item in row)
            expected[name] = (int(offset, 0), int(size, 0))
    rows = {}
    checked_md5 = False
    for offset in range(0, TABLE_BYTES, 32):
        entry = data[offset:offset + 32]
        if entry == b"\xff" * 32:
            require(data[offset:] == b"\xff" * (TABLE_BYTES - offset),
                    "partition table has data after terminator")
            break
        if entry[:2] == b"\xeb\xeb":
            require(not checked_md5 and entry[2:16] == b"\xff" * 14
                    and entry[16:] == hashlib.md5(data[:offset]).digest(),
                    "partition table MD5 differs")
            checked_md5 = True
            continue
        magic, _kind, _subtype, start, size, label, flags = __import__("struct").unpack(
            "<HBBII16sI", entry)
        require(magic == 0x50AA and flags == 0, "unsupported partition-table entry")
        try:
            name = label.rstrip(b"\0").decode("ascii")
        except UnicodeError as error:
            raise OrchestratorError("partition name is not ASCII") from error
        require(name not in rows, "duplicate partition name")
        rows[name] = (start, size)
    require(checked_md5 and rows == expected, "live flash partition table differs from this checkout")
    require(rows.get("nvs") == (NVS_OFFSET, NVS_BYTES)
            and rows.get("ota_0") == (OTA_OFFSETS[0], OTA_SLOT_BYTES)
            and rows.get("ota_1") == (OTA_OFFSETS[1], OTA_SLOT_BYTES),
            "live NVS/OTA layout differs from the pinned issue #22 map")


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


def _server_arguments(options, root: Path, package_root: Path, fixture_root: Path) -> RemoteFixtureServer:
    ssh = SSHSession(options.ct_target, options.ct_identity,
                     options.ct_known_hosts, options.ct_ssh_port, options.ct_host_key_alias)
    root_id = hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:12]
    remote_root = Path(options.ct_root) / "issue22" / f"{root.name}-{root_id}"
    return RemoteFixtureServer(ssh, fixture_root=fixture_root, package_root=package_root,
        fixture_trusted=options.fixture_trusted_public,
        fixture_untrusted=options.fixture_untrusted_public,
        ota_public=options.ota_signing_public, certificate=options.tls_certificate,
        private_key=options.tls_private_key, campaign_root=remote_root,
        server_ip=options.server_ip, port=options.https_port,
        server_name=options.server_name, toolchain=options.aot_toolchain_id)


def _prepare_profile(options, board: Board, row: dict, profile_data: dict,
                     server: RemoteFixtureServer, operation: Path, baseline: Path,
                     baseline_hash: str) -> dict:
    profile = {"native": "native", "interp": "module-interp", "aot": "module-aot"}[row["scenario"]]
    app_path = Path(profile_data["profiles"][row["scenario"]]["path"])
    board.restore(baseline, baseline_hash, operation / f"{row['plan_index']:02d}-restore-before.bin")
    # Ensure every row begins from the same v1 app profile and exact NVS baseline.
    board.overlay_profile(app_path, Path(profile_data["profiles"][row["scenario"]]["bootloader"]["path"]))
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
    boot_summary = _observer_once(options, _private_output(operation, "post-esptool-boot"),
                                  seconds=5, profile=row["scenario"], attempt_id=provision_id,
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
        before_flash = operation / "flash-before.bin"
        board.read(0, FLASH_BYTES, before_flash, timeout=600)
        require(sha256_file(before_flash) == baseline_hash,
                "board does not match the campaign's exact pre-run full-flash baseline")
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
        result = {"attempt_id": row["attempt_id"], "plan_index": row["plan_index"],
                  "state": "captured", "preflight_boot_id": prepared["boot_id"],
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
                               last_error=type(restore_error).__name__)
                try:
                    write_json(journal_path, journal, exclusive=False)
                except BaseException:
                    pass
                raise OrchestratorError("full-flash restoration failed; use recover, never replay this attempt") from restore_error
        journal["restore_required"] = False
        journal["next_index"] = row["plan_index"]
        journal["slots"].append(result or {"attempt_id": row["attempt_id"],
                                            "plan_index": row["plan_index"],
                                            "state": "failed", "error": type(failure).__name__})
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
    return {**profile_data, "profiles": profiles}


def _board_operations(root: Path) -> Path:
    return private_directory(root / "board-operations")


def _verify_live_baseline(board: Board, operation: Path, baseline_hash: str) -> None:
    board.identify()
    live = operation / f"baseline-live-check-{uuid.uuid4().hex}.bin"
    board.read(0, FLASH_BYTES, live, timeout=600)
    require(sha256_file(live) == baseline_hash,
            "board full-flash SHA-256 differs from the #20 accepted baseline")


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


def execute(options: argparse.Namespace) -> dict:
    _validate_run_options(options)
    timeout_proof = validate_timeout_proof(options.timeout_proof)
    options.timeout_ms = timeout_proof["functional_timeout_ms"]
    tls = validate_tls_files(options.tls_ca, options.tls_certificate,
                             options.tls_private_key, options.server_name)
    profile_data = validate_profile_manifest(options.profiles, options.timeout_ms,
        options.server_ip, options.https_port, options.server_name, options.tls_ca)
    package_data = validate_packages(options.ota_package_root, options.ota_signing_public,
        options.fixture_root, options.fixture_trusted_public, options.fixture_untrusted_public,
        options.aot_toolchain_id)
    baseline_data = validate_accepted_baseline(options.accepted_baseline,
                                               options.accepted_baseline_sha256)
    strict_ssh_args(options.ct_target, options.ct_identity, options.ct_known_hosts,
                    options.ct_ssh_port, options.ct_host_key_alias)

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
        if journal_path.exists():
            journal = load_json(journal_path, "orchestrator journal")
            require(journal.get("schema") == JOURNAL_SCHEMA,
                    "unsupported issue #22 journal schema")
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
            for key, value in vars(options).items():
                if key in {"action", "timeout_ms"} or key not in saved:
                    continue
                normalized = str(value) if isinstance(value, Path) else value
                require(saved[key] == normalized,
                        f"resume configuration changed for {key}; use --action resume")
        else:
            journal = _initial_journal(root, options, profile_data, timeout_proof,
                                       baseline_data, package_data, tls)
            journal["baseline"] = journal_baseline
            journal["recoveries"] = []
            write_json(journal_path, journal, exclusive=True)

        claimed = _verify_claims_match_plan(root, rows)
        next_index = journal.get("next_index")
        require(type(next_index) is int and 0 <= next_index <= 16,
                "journal schedule index is invalid")
        require(claimed <= next_index,
                "one-shot claim is newer than the journal; recover before resuming")
        if next_index == 16:
            journal["state"] = "complete"
            write_json(journal_path, journal)
            return {"state": "complete", "attempted_slots": 0, "next_index": 16,
                    "campaign_root": str(root), "timeout_ms": options.timeout_ms}

        operation = _board_operations(root)
        board = Board(options, operation)
        server = _server_arguments(options, root, options.ota_package_root,
                                   options.fixture_root)
        server.start()
        try:
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
            for row in rows[next_index:min(16, next_index + options.max_slots)]:
                _run_row(options, row, plan_path, root, board, bundle, server,
                         baseline, baseline_data["sha256"])
                attempted += 1
            journal = load_json(journal_path, "orchestrator journal")
            journal["state"] = "complete" if journal["next_index"] == 16 else "ready"
            write_json(journal_path, journal)
            return {"state": journal["state"], "attempted_slots": attempted,
                    "next_index": journal["next_index"], "campaign_root": str(root),
                    "timeout_ms": options.timeout_ms, "package": package_data, "tls": tls}
        finally:
            server.stop()


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
        validate_partition_table(baseline.read_bytes()[TABLE_OFFSET:TABLE_OFFSET + TABLE_BYTES])
        rows = load_plan(root / "plan.jsonl")
        claimed = _verify_claims_match_plan(root, rows)
        operation = _board_operations(root)
        board = Board(options, operation)
        needs_restore = journal.get("restore_required") is True
        remote_stopped = False
        try:
            server = _server_arguments(options, root, options.ota_package_root, options.fixture_root)
            server.stop()
            remote_stopped = True
        except (OrchestratorError, OSError, subprocess.TimeoutExpired):
            # Never let CT reachability block local safety restoration. The only
            # server process that stop() can signal is the one with this
            # campaign's path, PID, and recorded Linux process start-time.
            remote_stopped = False
        board.identify()
        recovery_dir = _private_output(root / "recoveries", f"{time.time_ns()}")
        if needs_restore:
            board.restore(baseline, baseline_hash, recovery_dir / "flash-restored.bin")
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


def validate_cli(options: argparse.Namespace) -> None:
    """Static gate used by tests and by the no-hardware --check command."""
    _validate_run_options(options)
    timeout = validate_timeout_proof(options.timeout_proof)
    options.timeout_ms = timeout["functional_timeout_ms"]
    validate_tls_files(options.tls_ca, options.tls_certificate,
                       options.tls_private_key, options.server_name)
    validate_profile_manifest(options.profiles, options.timeout_ms,
        str(ipaddress.IPv4Address(options.server_ip)), options.https_port,
        options.server_name, options.tls_ca)
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
    "ct_known_hosts", "accepted_baseline", "output_root",
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
    parser.add_argument("--action", choices=("check", "run", "resume", "recover"), default="check")
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
        parser.exit(2, f"Issue #22 HIL orchestrator stopped safely: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
