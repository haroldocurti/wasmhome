#!/usr/bin/env python3
"""Restore, prepare and capture ordered issue20 slots as an ordinary user.

The baseline and raw flash never leave the private campaign directory. Recovery
only restores. A later run can retire one recovered claim without raw evidence;
it cannot repeat an observer run or a claimed slot.
"""
from __future__ import annotations

import argparse
import copy
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import sys
import uuid

try:
    from . import update_campaign as campaign
except ImportError:
    import update_campaign as campaign

BASELINE_SHA256 = "00aa22c35720354383c3d686a14612a7cb2fd99c57cb1ce214d07338c1c167a5"
FLASH_BYTES = 0x1000000
BOOTLOADER_OFFSET, BOOTLOADER_LIMIT = 0x0, 0x8000
ESP32S3_DRAM_START, ESP32S3_DRAM_END = 0x3FC88000, 0x3FD00000
ESP32S3_IRAM_START, ESP32S3_IRAM_END = 0x40370000, 0x403E0000
TABLE_OFFSET, TABLE_LIMIT = 0x8000, 0x1000
APP_OFFSET, APP_LIMIT = 0x20000, 0x700000
DEFAULT_READ_TIMEOUT_SECONDS = 600
PARTITIONS = (
    ("nvs", 1, 2, 0x9000, 0x6000),
    ("otadata", 1, 0, 0xf000, 0x2000),
    ("phy_init", 1, 1, 0x11000, 0x1000),
    ("ota_0", 0, 0x10, 0x20000, 0x700000),
    ("ota_1", 0, 0x11, 0x720000, 0x700000),
    ("interp_slot_a", 1, 0x40, 0xe20000, 0x10000),
    ("interp_slot_b", 1, 0x40, 0xe30000, 0x10000),
    ("aot_slot_a", 1, 0x40, 0xe40000, 0x10000),
    ("aot_slot_b", 1, 0x40, 0xe50000, 0x10000),
)


class HilError(ValueError):
    pass


def require(value, message):
    if not value:
        raise HilError(message)


def digest(path):
    return campaign._sha256_file(Path(path))


def private_directory(path):
    path = Path(path).absolute()
    require(not any(p.is_symlink() for p in (path, *path.parents)), "private path contains a symlink")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    require(path.is_dir() and path.stat().st_uid == os.getuid()
            and stat.S_IMODE(path.stat().st_mode) == 0o700,
            f"directory must be owned by current user with mode 0700: {path}")
    return path


def write_private(path, value, *, exclusive=False):
    path = Path(path)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, sort_keys=True, allow_nan=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive:
            os.link(temporary, path)
        else:
            require(not path.is_symlink(), "journal is a symlink")
            os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def hardware_lock(directory=None):
    # One lock for all campaigns and serial aliases owned by this user.
    root = private_directory(directory or Path.home() / ".local/state/wasmhome-hil")
    descriptor = os.open(root / "hardware.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        require(info.st_uid == os.getuid() and stat.S_ISREG(info.st_mode)
                and stat.S_IMODE(info.st_mode) == 0o600, "unsafe hardware lock")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise HilError("another HIL operation owns the hardware lock") from exc
        yield
    finally:
        os.close(descriptor)


def validate_table(path):
    data = Path(path).read_bytes()
    require(32 <= len(data) <= TABLE_LIMIT and len(data) % 32 == 0,
            "partition table exceeds its permitted sector or has invalid length")
    rows, checked_md5 = [], False
    for offset in range(0, len(data), 32):
        record = data[offset:offset + 32]
        if record == b"\xff" * 32:
            require(data[offset:] == b"\xff" * (len(data) - offset), "data after partition table terminator")
            break
        if record[:2] == b"\xeb\xeb":
            require(not checked_md5 and record[2:16] == b"\xff" * 14
                    and record[16:] == hashlib.md5(data[:offset]).digest(), "invalid partition table MD5")
            checked_md5 = True
            continue
        require(not checked_md5, "partition after MD5")
        magic, kind, subtype, start, size, label, flags = struct.unpack("<HBBII16sI", record)
        require(magic == 0x50aa and flags == 0, "invalid or encrypted partition entry")
        try:
            name = label.rstrip(b"\0").decode("ascii")
        except UnicodeError as exc:
            raise HilError("invalid partition label") from exc
        rows.append((name, kind, subtype, start, size))
    require(tuple(rows) == PARTITIONS, "partition layout differs from the frozen ESP32-S3 layout")
    require(checked_md5, "partition table has no verified MD5 record")


def validate_app(path):
    path = Path(path)
    require(24 <= path.stat().st_size <= APP_LIMIT, "app exceeds ota_0 or has invalid length")
    with path.open("rb") as stream:
        header = stream.read(24)
    require(header[0] == 0xe9 and 1 <= header[1] <= 16
            and int.from_bytes(header[12:14], "little") == 9,
            "application image is not ESP32-S3")


def validate_bootloader(path):
    path = Path(path)
    require(24 <= path.stat().st_size <= BOOTLOADER_LIMIT,
            "bootloader exceeds the region before the partition table or has invalid length")
    data = path.read_bytes()
    require(len(data) >= 24 and data[0] == 0xe9 and 1 <= data[1] <= 16
            and data[2] <= 3 and data[3] >> 4 == 4
            and data[3] & 0x0f in {0, 1, 2, 0x0f}
            and int.from_bytes(data[12:14], "little") == 9,
            "bootloader image is not ESP32-S3/16 MiB")
    require(data[23] == 1, "bootloader must include its SHA-256 image digest")

    entrypoint = struct.unpack_from("<I", data, 4)[0]
    offset, checksum = 24, 0xef
    segments = []
    for _ in range(data[1]):
        require(offset + 8 <= len(data), "truncated bootloader segment header")
        address, size = struct.unpack_from("<II", data, offset)
        offset += 8
        require(size > 0 and size <= BOOTLOADER_LIMIT and offset + size <= len(data),
                "truncated or oversized bootloader segment")
        end = address + size
        require(address > 0 and end <= 0x1_0000_0000,
                "invalid bootloader segment address range")
        require(all(end <= other_start or address >= other_end
                    for other_start, other_end in segments),
                "overlapping bootloader segments")
        segments.append((address, end))
        for byte in data[offset:offset + size]:
            checksum ^= byte
        if _ == 0:
            descriptor = data[offset:offset + min(size, 80)]
            require(ESP32S3_DRAM_START <= address and end <= ESP32S3_DRAM_END,
                    "bootloader description is outside ESP32-S3 DRAM")
        offset += size

    require(ESP32S3_IRAM_START <= entrypoint < ESP32S3_IRAM_END
            and any(start <= entrypoint < end for start, end in segments),
            "bootloader entrypoint is outside ESP32-S3 IRAM segments")
    require(len(descriptor) == 80 and descriptor[0] == 80
            and descriptor[1:4] == b"\0" * 3 and descriptor[64:80] == b"\0" * 16,
            "bootloader description is invalid")
    idf_version = descriptor[8:40].split(b"\0", 1)[0]
    require(idf_version and all(0x20 <= byte <= 0x7e for byte in idf_version),
            "bootloader description has no valid IDF version")

    checksum_offset = (offset // 16 + 1) * 16 - 1
    require(checksum_offset < len(data) and data[offset:checksum_offset] == b"\0" * (checksum_offset - offset),
            "invalid bootloader checksum padding")
    require(data[checksum_offset] == checksum, "bootloader checksum mismatch")
    digest_offset = checksum_offset + 1
    require(digest_offset + 32 == len(data), "bootloader digest is missing or trailing data is present")
    require(hashlib.sha256(data[:digest_offset]).digest() == data[digest_offset:],
            "bootloader SHA-256 digest mismatch")


def checked_file(row):
    require(isinstance(row, dict) and set(row) == {"path", "sha256"}, "invalid profile artifact")
    path = Path(row["path"])
    require(path.is_absolute() and path.is_file() and not path.is_symlink(), "artifact must be an absolute regular file")
    require(isinstance(row["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", row["sha256"])
            and digest(path) == row["sha256"], "artifact SHA-256 mismatch")
    return path


def validate_generated_config(path, *, return_definitions=False):
    """Read frozen build output, never the editable input sdkconfig."""
    require(Path(path).stat().st_size <= 1024 * 1024, "generated config is too large")
    required = {"CONFIG_IDF_TARGET", "CONFIG_IDF_TARGET_ESP32S3",
                "CONFIG_ESPTOOLPY_FLASHSIZE", "CONFIG_ESPTOOLPY_FLASHSIZE_16MB",
                "CONFIG_PARTITION_TABLE_OFFSET", "CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE",
                "CONFIG_WASMHOME_UPDATE_SERVER_PORT"}
    definitions = {}
    source = Path(path).read_text(encoding="utf-8")
    lines, in_block_comment = [], False
    for original in source.splitlines():
        line, clean, index, quote, escaped = original, [], 0, None, False
        while index < len(line):
            char = line[index]
            following = line[index:index + 2]
            if in_block_comment:
                if following == "*/":
                    in_block_comment = False
                    index += 2
                else:
                    index += 1
            elif quote:
                clean.append(char)
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = None
                index += 1
            elif following == "/*":
                in_block_comment = True
                index += 2
            elif following == "//":
                break
            elif char in {'"', "'"}:
                quote = char
                clean.append(char)
                index += 1
            else:
                clean.append(char)
                index += 1
        require(quote is None and not "".join(clean).rstrip().endswith("\\"),
                "generated config contains a continued or unterminated directive")
        lines.append("".join(clean).strip())
    require(not in_block_comment, "generated config contains an unterminated block comment")

    for line in lines:
        if not line:
            continue
        directive = re.match(r"#\s*(\w+)", line)
        if not directive:
            require(line.startswith("/*") or line.startswith("//"),
                    "generated config contains unexpected source text")
            continue
        name = directive[1]
        require(name in {"define", "undef", "pragma"},
                "generated config contains conditional or unsupported directives")
        if name == "pragma":
            require(line == "#pragma once", "generated config has an unsupported pragma")
            continue
        match = re.fullmatch(r"#\s*(define|undef)\s+(CONFIG_\w+)(?:\s+(.+?))?\s*", line)
        require(match is not None, "generated config has a malformed definition")
        if match and match[2] in required:
            require(match[2] not in definitions, "duplicate generated config definition")
            if match[1] == "undef":
                require(match[2] == "CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE",
                        "required generated config value is undefined")
                definitions[match[2]] = "0"
            else:
                require(match[3] is not None, "generated config value is missing")
                definitions[match[2]] = match[3].strip()
    require(definitions.get("CONFIG_IDF_TARGET") == '"esp32s3"'
            and definitions.get("CONFIG_IDF_TARGET_ESP32S3") == "1",
            "generated config target must be ESP32-S3")
    require(definitions.get("CONFIG_ESPTOOLPY_FLASHSIZE") == '"16MB"'
            and definitions.get("CONFIG_ESPTOOLPY_FLASHSIZE_16MB") == "1",
            "generated config must select 16 MiB flash")
    require(definitions.get("CONFIG_PARTITION_TABLE_OFFSET") == "0x8000",
            "generated config partition table offset differs from the frozen layout")
    rollback = definitions.get("CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE", "0")
    require(rollback in {"0", "1"}, "invalid rollback definition in generated config")
    if return_definitions:
        return definitions
    return rollback == "1"


def validate_profile(profile, *, scenario):
    fields = {"bootloader", "partition_table", "app", "app_config", "bootloader_config"}
    require(isinstance(profile, dict) and set(profile) == fields,
            "invalid profile fields; bootloader, partition_table, app, app_config and bootloader_config are required")
    files = {kind: checked_file(row) for kind, row in profile.items()}
    validate_bootloader(files["bootloader"])
    validate_table(files["partition_table"])
    validate_app(files["app"])
    app_rollback = validate_generated_config(files["app_config"])
    bootloader_rollback = validate_generated_config(files["bootloader_config"])
    require(app_rollback == bootloader_rollback, "app/bootloader rollback configuration mismatch")
    require(scenario != "native" or app_rollback, "native profile requires rollback enabled in app and bootloader")
    return files


def load_profiles(path, *, expected_sha256=None):
    path = Path(path)
    if expected_sha256 is None:
        value = campaign._read_json(path, "HIL profiles")
    else:
        require(path.is_file() and not path.is_symlink(),
                "frozen HIL profiles must be a regular non-symlink file")
        source = path.read_bytes()
        require(hashlib.sha256(source).hexdigest() == expected_sha256,
                "profiles.json SHA-256 is not frozen in campaign metadata")
        try:
            value = campaign._json_loads_strict(source.decode("utf-8"), "HIL profiles")
        except UnicodeError as exc:
            raise HilError("HIL profiles: cannot read valid UTF-8 JSON") from exc
        require(isinstance(value, dict), "HIL profiles: top-level JSON value must be an object")
    require(set(value) == {"schema_version", "profiles"} and value["schema_version"] == 2,
            "unsupported HIL profiles schema")
    require(isinstance(value["profiles"], dict) and set(value["profiles"]) == set(campaign.SCENARIOS),
            "profiles must contain exactly native/interp/aot")
    for scenario, profile in value["profiles"].items():
        validate_profile(profile, scenario=scenario)
    return value


def frozen_https_port(campaign_value, profiles_path):
    """Read the HTTPS port from profile configs pinned by campaign metadata."""
    metadata = campaign_value.get("metadata")
    expected_sha256 = metadata.get("hil_profiles_sha256") if isinstance(metadata, dict) else None
    require(isinstance(expected_sha256, str) and re.fullmatch(r"[0-9a-f]{64}", expected_sha256),
            "campaign metadata lacks a valid frozen profiles SHA-256")
    profiles = load_profiles(profiles_path, expected_sha256=expected_sha256)
    ports = {}
    for scenario, profile in profiles["profiles"].items():
        definitions = validate_generated_config(profile["app_config"]["path"],
                                                return_definitions=True)
        value = definitions.get("CONFIG_WASMHOME_UPDATE_SERVER_PORT")
        require(isinstance(value, str) and re.fullmatch(r"[0-9]+", value) is not None,
                f"{scenario} generated app config lacks a numeric HTTPS server port")
        port = int(value)
        require(1024 <= port <= 65535,
                f"{scenario} generated app config HTTPS server port must be unprivileged")
        ports[scenario] = port
    require(len(set(ports.values())) == 1,
            "frozen HIL profiles disagree on the HTTPS server port")
    return next(iter(ports.values()))


class Board:
    def __init__(self, options, output):
        self.options, self.output, self.counter = options, Path(output), 0

    def command(self, *arguments, after="no_reset", timeout=180):
        self.counter += 1
        log = self.output / f"esptool-{self.counter:04d}.log"
        command = [self.options.python, self.options.esptool, "--chip", "esp32s3",
                   "--port", self.options.serial_port, "--baud", str(self.options.flash_baud),
                   "--after", after, *map(str, arguments)]
        # Capture output privately; full device identity is never printed.
        descriptor = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout)
        require(result.returncode == 0, f"esptool failed; private receipt: {log.name}")
        return log.read_text(errors="replace")

    def identify(self):
        result = self.command("flash_id")
        matches = re.findall(r"MAC:\s*([0-9a-fA-F:]{17})", result)
        require("ESP32-S3" in result and len(matches) == 1
                and matches[0].lower() == self.options.expected_mac.lower()
                and re.search(r"Detected flash size:\s*16\s*(?:MB|MiB)\b", result),
                "chip, MAC or physical flash size mismatch")

    def read_hash(self, destination):
        require(not destination.exists(), "readback destination already exists")
        self.command("read_flash", "0x0", hex(FLASH_BYTES), destination,
                     timeout=self.options.read_timeout_seconds)
        os.chmod(destination, 0o600)
        require(destination.stat().st_size == FLASH_BYTES, "full flash readback is incomplete")
        return digest(destination)

    def overlay(self, profile, *, scenario):
        files = validate_profile(profile, scenario=scenario)
        bootloader, table, app = (files[kind] for kind in ("bootloader", "partition_table", "app"))
        segments = (hex(BOOTLOADER_OFFSET), bootloader, hex(TABLE_OFFSET), table, hex(APP_OFFSET), app)
        # Never boot a partially prepared profile. The parent transaction has
        # already persisted restore_required and restores the complete baseline
        # even if either flash command fails.
        self.command("write_flash", *segments)
        self.command("verify_flash", *segments)
        self.command("run", after="hard_reset")

    def restore(self, baseline, destination, *, boot=True):
        require(baseline.stat().st_size == FLASH_BYTES and digest(baseline) == BASELINE_SHA256,
                "refusing restoration with an unapproved baseline")
        self.command("write_flash", "0x0", baseline, timeout=240)
        require(self.read_hash(destination) == BASELINE_SHA256, "restored full flash SHA-256 mismatch")
        if boot:
            self.command("run", after="hard_reset")


def _snapshot(source, destination, expected):
    require(not destination.exists(), "snapshot already exists")
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with Path(source).open("rb") as incoming, os.fdopen(descriptor, "wb") as outgoing:
        shutil.copyfileobj(incoming, outgoing)
        outgoing.flush()
        os.fsync(outgoing.fileno())
    require(digest(destination) == expected, "artifact changed while snapshotting")
    return destination


def _new_journal(options, root):
    value, schedule, _ = campaign._load_campaign(Path(options.campaign_root))
    campaign._validate_repository(value)
    expected = {"pilot": 6, "normal": 30}.get(value["metadata"].get("campaign_type"))
    require(value["protocol_revision"] == campaign.ISSUE20_PROTOCOL_REVISION
            and expected is not None and value["N"] == expected,
            "orchestrator requires frozen issue20 pilot N=6 or normal N=30")
    baseline = Path(options.baseline)
    require(baseline.is_absolute() and baseline.is_file() and not baseline.is_symlink()
            and baseline.stat().st_size == FLASH_BYTES and digest(baseline) == BASELINE_SHA256,
            "baseline is not the approved full flash snapshot")
    require(value["metadata"].get("hil_profiles_sha256") == digest(options.profiles),
            "profiles.json SHA-256 must be frozen in metadata.hil_profiles_sha256 before planning")
    profiles = load_profiles(options.profiles)
    snapshots = private_directory(root / "inputs")
    baseline = _snapshot(baseline, snapshots / "baseline.bin", BASELINE_SHA256)
    for name, profile in profiles["profiles"].items():
        for kind, row in profile.items():
            suffix = ".h" if kind.endswith("_config") else ".bin"
            row["path"] = str(_snapshot(row["path"], snapshots / f"{name}-{kind}{suffix}", row["sha256"]))
    return {"schema_version": 1, "campaign_id": value["campaign_id"], "git_commit": value["git_commit"],
            "state": "ready", "restore_required": False, "next_index": 0, "slots": schedule["slots"],
            "baseline": str(baseline), "profiles_schema_version": profiles["schema_version"],
            "profiles": profiles["profiles"], "options": vars(options), "events": []}


def _invoke_slot(options, slot):
    return campaign.run_slot(Path(options.campaign_root), slot["slot_id"], device_ip=options.device_ip,
        serial_port=options.serial_port, seconds=150, trigger_after=60, target_sequence=2,
        bind_ip=options.bind_ip, udp_port=3333, device_port=3333, serial_baud=options.serial_baud,
        remote_options={"target": options.remote_observer, "known_hosts": options.remote_known_hosts,
                        "identity": options.remote_identity, "root": options.remote_root,
                        "ssh_port": options.remote_ssh_port},
        remote_server_log=Path(options.remote_server_log))


def tooling_snapshot():
    root = Path(__file__).resolve().parent
    return {name: digest(root / name) for name in
            ("issue20_hil_orchestrator.py", "issue20_hil_batch.py",
             "ct107_user_preflight.py") if (root / name).is_file()}


def current_claim(journal, journal_path):
    index = journal["next_index"]
    require(type(index) is int and 0 <= index < len(journal["slots"]),
            "recovered interruption has no current schedule slot")
    slot = journal["slots"][index]
    require(journal.get("current_slot") == slot["slot_id"],
            "recovered current_slot does not match slots[next_index]")
    claim = Path(journal_path).parent / "claims" / (slot["slot_id"] + ".json")
    require(claim.parent.is_dir() and not claim.parent.is_symlink()
            and claim.parent.stat().st_uid == os.getuid()
            and stat.S_IMODE(claim.parent.stat().st_mode) == 0o700,
            "recovered claim directory is not private")
    require(claim.is_file() and not claim.is_symlink()
            and claim.stat().st_uid == os.getuid()
            and stat.S_ISREG(claim.stat().st_mode)
            and stat.S_IMODE(claim.stat().st_mode) == 0o600,
            "recovered claim must be a private regular 0600 file")
    value = campaign._read_json(claim, "recovered slot claim")
    require(set(value) == {"slot", "retry_permitted"} and value["slot"] == slot
            and value["retry_permitted"] is False,
            "recovered claim differs from its slot or permits retry")
    return slot, claim


def recovered_claim(journal, journal_path, campaign_root):
    require(journal["state"] == "recovered_interruption" and journal["restore_required"] is False,
            "only a verified recovered interruption can retire a claim")
    slot, claim = current_claim(journal, journal_path)
    raw = Path(campaign_root) / "raw" / "slots" / slot["slot_id"]
    require(not any(path.is_symlink() for path in (raw, raw.parent, raw.parent.parent))
            and not os.path.lexists(raw),
            "recovered slot has partial raw evidence; preserve it and stop for adjudication")
    require(isinstance(journal["events"], list)
            and not any(event.get("slot_id") == slot["slot_id"] for event in journal["events"]),
            "recovered slot already has a terminal event")
    return slot, claim


def retire_recovered_claim(journal, journal_path, campaign_root, *, baseline_verification):
    """Persist the rejected claim only after a live full-flash proof."""
    slot, claim = recovered_claim(journal, journal_path, campaign_root)
    require(baseline_verification.get("verified_sha256") == BASELINE_SHA256,
            "retiring a claim requires a verified full-flash baseline")
    journal["events"].append({
        "slot_id": slot["slot_id"], "state": "interrupted_not_accepted",
        "error": "recovered_interruption", "accepted": False, "retry_permitted": False,
        "capture_started": False, "claim_sha256": digest(claim),
        "recorded_at_utc": campaign._utc_now(), "tooling_sha256": tooling_snapshot(),
        "campaign_git_commit": journal["git_commit"],
        "baseline_verification": baseline_verification,
    })
    journal.update(state="complete", next_index=journal["next_index"] + 1)
    write_private(journal_path, journal)


def resume_recovered_claim(journal, journal_path, campaign_root, board, receipt):
    recovered_claim(journal, journal_path, campaign_root)
    journal.update(state="rechecking_recovery", restore_required=True)
    write_private(journal_path, journal)
    try:
        board.identify()
        live = board.read_hash(receipt / "resume-live-readback.bin")
        restored = live != BASELINE_SHA256
        if restored:
            # Board.restore verifies all 16 MiB before returning, without boot.
            board.restore(Path(journal["baseline"]), receipt / "resume-restored-readback.bin", boot=False)
        verified = copy.deepcopy(journal)
        verified.update(state="recovered_interruption", restore_required=False)
        retire_recovered_claim(verified, journal_path, campaign_root,
            baseline_verification={"live_sha256": live, "verified_sha256": BASELINE_SHA256,
                                   "restored": restored, "boot": False})
        journal.clear()
        journal.update(verified)
    except BaseException as exc:
        # The original index/events are untouched even if the terminal audit
        # write fails. The durable intent precedes every serial operation.
        journal.update(state="restore_failed", restore_required=True, error=type(exc).__name__)
        write_private(journal_path, journal)
        raise HilError("recovered baseline verification failed; claim not retired; use recover") from exc


def repair_claim_intent(journal, journal_path, campaign_root):
    """Complete a durable claim intent after a crash before its file existed."""
    index = journal["next_index"]
    require(type(index) is int and 0 <= index < len(journal["slots"]), "invalid claim intent index")
    slot = journal["slots"][index]
    expected = {"slot": slot, "retry_permitted": False}
    require(journal.get("current_slot") == slot["slot_id"] and journal.get("claim_intent") == expected
            and journal["claim_intent"]["retry_permitted"] is False,
            "claim intent does not match the current schedule slot")
    claims = private_directory(Path(journal_path).parent / "claims")
    claim = claims / (slot["slot_id"] + ".json")
    if not os.path.lexists(claim):
        require(not os.path.lexists(Path(campaign_root) / "raw" / "slots" / slot["slot_id"]),
                "missing claim has raw evidence; preserve it for adjudication")
        write_private(claim, expected, exclusive=True)
    current_claim(journal, journal_path)


def _adjudication_file(path, label):
    path = Path(path)
    require(not any(part.is_symlink() for part in (path, *path.parents)),
            f"{label} path contains a symlink")
    require(path.is_file() and stat.S_ISREG(path.stat().st_mode)
            and path.stat().st_uid == os.getuid()
            and stat.S_IMODE(path.stat().st_mode) == 0o600,
            f"{label} must be a private regular 0600 file")
    return path


def _adjudication_directory(path, label):
    path = Path(path)
    require(not any(part.is_symlink() for part in (path, *path.parents)),
            f"{label} path contains a symlink")
    require(path.is_dir() and path.stat().st_uid == os.getuid()
            and stat.S_IMODE(path.stat().st_mode) == 0o700,
            f"{label} must be a private 0700 directory")
    return path


@contextmanager
def adjudication_lock(root):
    """Serialize adjudication with both the batch supervisor and HIL child."""
    with hardware_lock():
        descriptor = os.open(Path(root) / "batch.lock",
                             os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(descriptor)
            require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                    and stat.S_IMODE(info.st_mode) == 0o600,
                    "unsafe batch lock")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise HilError("batch supervisor is active; adjudication refused") from exc
            yield
        finally:
            os.close(descriptor)


def _adjudication_raw_evidence(raw_dir, slot, campaign_id, git_commit):
    raw_dir = _adjudication_directory(raw_dir, "failed slot evidence directory")
    require(not os.path.lexists(raw_dir / "slot.json"),
            "slot already has a completed record; terminal-failure adjudication refused")
    terminal_path = _adjudication_file(raw_dir / "terminal-error.json", "terminal error")
    terminal = campaign._read_json(terminal_path, "terminal error")
    message = terminal.get("error")
    require(terminal.get("kind") == "capture_failed"
            and terminal.get("automatic_retry_permitted") is False
            and isinstance(message, str) and message.startswith("RemoteObserverError:")
            and "SSH" in message and "timed out" in message.lower(),
            "terminal error is not the recorded non-retryable SSH timeout")
    raw_files = terminal.get("raw_files")
    required = {"device.jsonl", "remote-session.json", "remote-start-claim.json",
                "remote-status.json", "remote-transport-error.json"}
    require(isinstance(raw_files, dict) and required <= set(raw_files),
            "terminal error does not seal the required serial/remote evidence")
    direct_files = {path.name for path in raw_dir.iterdir()
                    if path.is_file() and path.name != "terminal-error.json"}
    require(direct_files == set(raw_files),
            "terminal error raw-file inventory differs from the preserved capture")
    verified = {}
    for name, expected in raw_files.items():
        require(isinstance(name, str) and Path(name).name == name and name not in {".", ".."}
                and isinstance(expected, dict)
                and type(expected.get("size_bytes")) is int
                and expected["size_bytes"] >= 0
                and isinstance(expected.get("sha256"), str)
                and re.fullmatch(r"[0-9a-f]{64}", expected["sha256"]),
                "terminal error contains invalid raw-file metadata")
        path = _adjudication_file(raw_dir / name, f"raw evidence {name}")
        actual = {"size_bytes": path.stat().st_size, "sha256": campaign._sha256_file(path)}
        require(actual == expected, f"raw evidence hash/size mismatch: {name}")
        verified[name] = actual

    session = campaign._read_json(raw_dir / "remote-session.json", "remote session")
    session_id = session.get("session_id")
    context = ((session.get("config") or {}).get("context") or {})
    require(session.get("schema") == "wasmhome-remote-observer-v1"
            and isinstance(session_id, str)
            and context.get("campaign_id") == campaign_id
            and context.get("slot_id") == slot["slot_id"]
            and context.get("git_commit") == git_commit,
            "remote session identity differs from the failed scheduled slot")
    require(str(uuid.UUID(session_id)) == session_id,
            "remote session ID is not a canonical UUID")
    start_claim = campaign._read_json(raw_dir / "remote-start-claim.json", "remote start claim")
    require(start_claim.get("session_id") == session_id,
            "remote start claim belongs to another session")
    status = campaign._read_json(raw_dir / "remote-status.json", "remote status")
    require(status.get("schema") == "wasmhome-remote-observer-v1"
            and status.get("session_id") == session_id
            and status.get("state") in {"prepared", "launching", "running", "complete", "aborted", "failed"},
            "remote status is missing or belongs to another session")
    transport = campaign._read_json(raw_dir / "remote-transport-error.json", "remote transport error")
    require(transport.get("session_id") == session_id
            and transport.get("retry_permitted") is False
            and isinstance(transport.get("reason"), str)
            and "SSH" in transport["reason"],
            "remote transport error is not a non-retryable SSH failure")
    return {"terminal_error": {"path": str(terminal_path), "sha256": campaign._sha256_file(terminal_path)},
            "session_id": session_id, "remote_state": status["state"], "raw_files": verified}


def adjudicate_terminal_failure(campaign_root):
    """Adjudicate one already-restored terminal capture failure, offline only."""
    os.umask(0o077)
    require(os.geteuid() != 0, "HIL must run as an ordinary user")
    campaign_root = Path(campaign_root).absolute()
    journal_root = campaign_root / "hil-orchestrator"
    _adjudication_directory(journal_root, "HIL journal directory")
    journal_path = _adjudication_file(journal_root / "journal.json", "HIL journal")
    with adjudication_lock(journal_root):
        journal = campaign._read_json(journal_path, "HIL journal")
        require(journal.get("state") == "slot_failed" and journal.get("restore_required") is False,
                "only a terminal slot_failed journal with restoration complete can be adjudicated")
        slots = journal.get("slots")
        next_index = journal.get("next_index")
        require(isinstance(slots, list) and type(next_index) is int
                and 1 <= next_index < len(slots),
                "journal has no next unclaimed slot to release")
        failed_index = next_index - 1
        failed_slot = slots[failed_index]
        next_slot = slots[next_index]
        require(isinstance(failed_slot, dict) and isinstance(next_slot, dict)
                and isinstance(failed_slot.get("slot_id"), str)
                and isinstance(next_slot.get("slot_id"), str),
                "journal schedule contains invalid slot rows")
        require(journal.get("current_slot") == failed_slot["slot_id"],
                "journal failed slot does not match next_index")
        expected_intent = {"slot": failed_slot, "retry_permitted": False}
        require(journal.get("claim_intent") == expected_intent,
                "failed slot claim intent differs from schedule or permits retry")
        events = journal.get("events")
        require(isinstance(events, list) and events
                and events[-1].get("slot_id") == failed_slot["slot_id"]
                and events[-1].get("state") == "slot_failed"
                and events[-1].get("error") == "RemoteObserverError",
                "journal has no matching terminal RemoteObserverError event")

        claims_dir = _adjudication_directory(journal_root / "claims", "claim directory")
        claim_path = _adjudication_file(claims_dir / (failed_slot["slot_id"] + ".json"), "failed slot claim")
        claim = campaign._read_json(claim_path, "failed slot claim")
        require(set(claim) == {"slot", "retry_permitted"}
                and claim["slot"] == failed_slot and claim["retry_permitted"] is False,
                "failed slot claim mismatch or retry is permitted")
        require(not os.path.lexists(claims_dir / (next_slot["slot_id"] + ".json"))
                and not os.path.lexists(campaign_root / "raw" / "slots" / next_slot["slot_id"]),
                "next schedule index is already claimed or has raw evidence")

        baseline = _adjudication_file(journal.get("baseline", ""), "approved baseline snapshot")
        require(baseline.parent == journal_root / "inputs" and baseline.name == "baseline.bin"
                and baseline.stat().st_size == FLASH_BYTES
                and digest(baseline) == BASELINE_SHA256,
                "approved baseline snapshot hash/size mismatch")
        operation_dirs = [path for path in journal_root.iterdir()
                          if path.name.startswith("operation-")
                          and (path / f"{failed_slot['slot_id']}-after.bin").exists()]
        require(len(operation_dirs) == 1
                and re.fullmatch(r"operation-[0-9a-f]{32}", operation_dirs[0].name),
                "post-restoration readback is missing or ambiguous")
        operation_dir = _adjudication_directory(operation_dirs[0], "restoration receipt directory")
        readback = _adjudication_file(operation_dir / f"{failed_slot['slot_id']}-after.bin",
                                      "post-restoration readback")
        require(readback.stat().st_size == FLASH_BYTES and digest(readback) == BASELINE_SHA256,
                "post-restoration full-flash readback does not match the approved baseline")

        evidence = _adjudication_raw_evidence(
            campaign_root / "raw" / "slots" / failed_slot["slot_id"], failed_slot,
            journal["campaign_id"], journal["git_commit"])
        batch_path = _adjudication_file(journal_root / "batch.json", "blocked batch status")
        batch = campaign._read_json(batch_path, "blocked batch status")
        old_tools = batch.get("tooling_sha256")
        new_tools = tooling_snapshot()
        require(batch.get("state") == "blocked" and batch.get("restore_required") is False
                and batch.get("next_index") == next_index
                and isinstance(old_tools, dict) and set(old_tools) == set(new_tools)
                and all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
                        for value in old_tools.values()),
                "blocked batch does not prove the old tool snapshot and failed index")
        batch_events = batch.get("events")
        require(isinstance(batch_events, list) and batch_events
                and batch_events[-1].get("before_index") == failed_index
                and batch_events[-1].get("after_index") == next_index
                and type(batch_events[-1].get("exit_code")) is int
                and batch_events[-1]["exit_code"] != 0,
                "blocked batch event does not match the terminal slot failure")

        adjudications = private_directory(journal_root / "adjudications")
        receipt_path = adjudications / (failed_slot["slot_id"] + ".json")
        receipt = {
            "schema_version": 1,
            "adjudication": "terminal_failure_restored_not_accepted",
            "campaign_id": journal["campaign_id"],
            "git_commit": journal["git_commit"],
            "slot_id": failed_slot["slot_id"],
            "failed_index": failed_index,
            "next_index_before": next_index,
            "next_index_after": next_index,
            "retry_permitted": False,
            "journal_transition": {"from": "slot_failed", "to": "ready"},
            "claim": {"path": str(claim_path), "sha256": digest(claim_path)},
            "terminal_evidence": evidence,
            "baseline": {"path": str(baseline), "size_bytes": baseline.stat().st_size,
                         "sha256": digest(baseline)},
            "post_restore_readback": {"path": str(readback), "size_bytes": readback.stat().st_size,
                                       "sha256": digest(readback), "matches_baseline": True},
            "batch_status": {"path": str(batch_path), "sha256": digest(batch_path),
                             "state": batch["state"]},
            "tooling_sha256_before": old_tools,
            "tooling_sha256_after": new_tools,
            "recorded_at_utc": campaign._utc_now(),
        }
        if os.path.lexists(receipt_path):
            prior_receipt = campaign._read_json(
                _adjudication_file(receipt_path, "existing adjudication receipt"),
                "existing adjudication receipt")
            comparable = copy.deepcopy(receipt)
            previous = copy.deepcopy(prior_receipt)
            comparable.pop("recorded_at_utc", None)
            recorded_at = previous.pop("recorded_at_utc", None)
            require(isinstance(recorded_at, str) and previous == comparable,
                    "existing adjudication receipt does not match the verified evidence")
            receipt = prior_receipt
        else:
            write_private(receipt_path, receipt, exclusive=True)
        receipt_sha256 = digest(receipt_path)
        updated = copy.deepcopy(journal)
        updated["events"].append({
            "slot_id": failed_slot["slot_id"], "state": "terminal_failure_adjudicated",
            "error": "RemoteObserverError", "accepted": False, "retry_permitted": False,
            "next_index_before": next_index, "next_index_after": next_index,
            "receipt": str(receipt_path), "receipt_sha256": receipt_sha256,
            "tooling_sha256_before": old_tools, "tooling_sha256_after": new_tools,
        })
        updated["state"] = "ready"
        require(updated["next_index"] == next_index, "adjudication must not move the schedule index")
        write_private(journal_path, updated)
        return {"state": updated["state"], "slot_id": failed_slot["slot_id"],
                "next_index": updated["next_index"], "receipt": str(receipt_path)}


def execute(options, *, board_factory=Board, invoke_slot=_invoke_slot):
    os.umask(0o077)
    require(os.geteuid() != 0, "HIL must run as an ordinary user")
    require(re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", options.expected_mac), "invalid expected MAC")
    require(1 <= options.max_slots <= 90, "max-slots must be between 1 and 90")
    require(type(options.read_timeout_seconds) is int and 360 <= options.read_timeout_seconds <= 3600,
            "read-timeout-seconds must be an integer between 360 and 3600")
    require(options.remote_observer and options.remote_server_log
            and Path(options.remote_server_log).is_absolute(), "explicit remote observer/log required")
    # Validate SSH policy before any serial access.
    from importlib import import_module
    remote = import_module("tools.remote_observer" if __package__ else "remote_observer")
    if options.action == "run":
        remote_options = {"target": options.remote_observer,
                          "known_hosts": options.remote_known_hosts,
                          "identity": options.remote_identity,
                          "root": options.remote_root,
                          "ssh_port": options.remote_ssh_port}
        remote.validate_options(remote_options)
        preflight = import_module("tools.ct107_user_preflight" if __package__
                                  else "ct107_user_preflight")
        planned, _, _ = campaign._load_campaign(Path(options.campaign_root))
        artifact_campaign_id = planned.get("metadata", {}).get("artifact_campaign_id")
        expected_https_port = frozen_https_port(planned, options.profiles)
        preflight.verify_server(remote_options, bind_ip=options.bind_ip,
                                server_log=options.remote_server_log,
                                campaign_id=artifact_campaign_id,
                                expected_port=expected_https_port)
    with hardware_lock():
        root = private_directory(Path(options.campaign_root) / "hil-orchestrator")
        journal_path = root / "journal.json"
        if journal_path.exists():
            require(not journal_path.is_symlink() and stat.S_IMODE(journal_path.stat().st_mode) == 0o600,
                    "journal must be a regular private 0600 file")
            journal = campaign._read_json(journal_path, "HIL journal")
            # Recovery must use the exact previously recorded hardware identity and tool paths.
            recorded = journal["options"]
            for key in ("campaign_root", "serial_port", "expected_mac", "python", "esptool", "flash_baud"):
                require(getattr(options, key) == recorded[key], f"journal option differs: {key}")
        else:
            require(options.action == "run", "no journal to recover")
            journal = _new_journal(options, root)
            write_private(journal_path, journal, exclusive=True)
        receipt = private_directory(root / ("operation-" + uuid.uuid4().hex))
        board = board_factory(options, receipt)
        if options.action == "recover":
            require(journal["restore_required"], "journal does not require recovery")
            if journal["state"] == "claiming":
                repair_claim_intent(journal, journal_path, options.campaign_root)
            board.identify()
            try:
                board.restore(Path(journal["baseline"]), receipt / "recovered-readback.bin", boot=False)
            except BaseException:
                journal["state"] = "restore_failed"
                write_private(journal_path, journal)
                raise
            journal.update(state="recovered_interruption", restore_required=False)
            write_private(journal_path, journal)
            return {"state": journal["state"], "slot_retry_permitted": False}
        require(journal["state"] in {"ready", "complete", "recovered_interruption"}
                and not journal["restore_required"],
                "interrupted/failed journal: recover only; never repeat a claimed slot")
        require(journal.get("profiles_schema_version") == 2,
                "legacy preparation profiles: register a new campaign with bootloaders; recover remains available")
        current, schedule, _ = campaign._load_campaign(Path(options.campaign_root))
        campaign._validate_repository(current)
        require(current["campaign_id"] == journal["campaign_id"] and schedule["slots"] == journal["slots"],
                "campaign or schedule changed")
        require(type(journal["next_index"]) is int and 0 <= journal["next_index"] <= len(journal["slots"]),
                "invalid journal schedule index")
        # Prevent changing network, profile or serial configuration between invocations.
        # A longer transport read budget does not change any experimental window
        # and may also be used to recover journals from before this option existed.
        mutable = {"max_slots", "action", "read_timeout_seconds"}
        require({k: v for k, v in journal["options"].items() if k not in mutable}
                == {k: v for k, v in vars(options).items() if k not in mutable},
                "HIL configuration changed; start a separately registered campaign")
        for scenario, profile in journal["profiles"].items():
            validate_profile(profile, scenario=scenario)
        if journal["state"] == "recovered_interruption":
            resume_recovered_claim(journal, journal_path, options.campaign_root, board, receipt)
        for index in range(journal["next_index"], min(len(journal["slots"]), journal["next_index"] + options.max_slots)):
            slot = journal["slots"][index]
            require(index == journal["next_index"], "slot ordering mismatch")
            claim = root / "claims" / (slot["slot_id"] + ".json")
            if os.path.lexists(claim):
                # Adopt an older orphan claim into durable recovery state; it
                # can never become a fresh attempt, including with partial raw.
                adopted = copy.deepcopy(journal)
                adopted["current_slot"] = slot["slot_id"]
                current_claim(adopted, journal_path)
                adopted.update(state="claiming", restore_required=True,
                               claim_intent={"slot": slot, "retry_permitted": False})
                write_private(journal_path, adopted)
                raise HilError("slot already has a claim; recovery required; no retry allowed")
            require(not os.path.lexists(Path(options.campaign_root) / "raw/slots" / slot["slot_id"]),
                    "slot already has capture evidence; no retry allowed")
            board.identify()
            require(board.read_hash(receipt / f"{slot['slot_id']}-before.bin") == BASELINE_SHA256,
                    "live full flash differs from approved baseline; refusing writes")
            claims = private_directory(root / "claims")
            journal.update(state="claiming", restore_required=True, current_slot=slot["slot_id"],
                           claim_intent={"slot": slot, "retry_permitted": False})
            write_private(journal_path, journal)
            write_private(claim, journal["claim_intent"], exclusive=True)
            failure = None
            try:
                journal["state"] = "preparing"
                write_private(journal_path, journal)
                # Restore is explicit even if the initial read already matched.
                board.restore(Path(journal["baseline"]), receipt / f"{slot['slot_id']}-prepared-readback.bin", boot=False)
                board.overlay(journal["profiles"][slot["scenario"]], scenario=slot["scenario"])
                journal["state"] = "capture_claimed"
                write_private(journal_path, journal)
                invoke_slot(options, slot)
            except BaseException as exc:
                failure = exc
            finally:
                journal["state"] = "restoring"
                # A full disk or journal I/O error must not skip restoration.
                try:
                    write_private(journal_path, journal)
                except OSError as exc:
                    failure = failure or exc
                try:
                    # Keep the ESP32-S3 in the ROM loader between slots. Booting
                    # the baseline firmware here lets runtime NVS writes change
                    # the approved flash image before the next slot's full-image
                    # gate. The next iteration can safely identify/read flash
                    # while it remains unbooted; the overlay performs the reset.
                    board.restore(Path(journal["baseline"]), receipt / f"{slot['slot_id']}-after.bin",
                                  boot=False)
                except BaseException as exc:
                    journal.update(state="restore_failed", error=type(exc).__name__)
                    write_private(journal_path, journal)
                    raise HilError("restoration failed; batch blocked; use recover before further HIL") from exc
                journal.update(state="slot_failed" if failure else "complete", restore_required=False,
                               next_index=index + 1)
                journal["events"].append({"slot_id": slot["slot_id"], "state": journal["state"],
                                          "error": type(failure).__name__ if failure else None})
                write_private(journal_path, journal)
            if failure:
                raise HilError("slot failed; baseline restored; campaign cannot retry this slot") from failure
        return {"state": journal["state"], "completed_slots": journal["next_index"], "planned_slots": len(journal["slots"])}


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("action", choices=("run", "recover"))
    for name in ("campaign-root", "profiles", "baseline", "expected-mac", "device-ip", "serial-port",
                 "python", "esptool", "bind-ip", "remote-observer", "remote-known-hosts", "remote-identity",
                 "remote-server-log"):
        result.add_argument("--" + name, required=True)
    result.add_argument("--flash-baud", type=int, default=921600)
    result.add_argument("--read-timeout-seconds", type=int, default=DEFAULT_READ_TIMEOUT_SECONDS,
                        help="full 16 MiB read budget, 360..3600 s (default: 600); write budgets stay unchanged")
    result.add_argument("--serial-baud", type=int, default=115200)
    result.add_argument("--remote-root", default=".local/share/wasmhome-hil")
    result.add_argument("--remote-ssh-port", type=int, default=22)
    result.add_argument("--max-slots", type=int, default=1,
                        help="consecutive slots, 1..90, bounded by the frozen schedule; stops on an exception")
    return result


def adjudication_parser():
    result = argparse.ArgumentParser(
        description="Adjudicate one terminal failed slot after an independently recorded baseline restore")
    result.add_argument("action", choices=("adjudicate",))
    result.add_argument("--campaign-root", required=True)
    return result


def main():
    os.umask(0o077)
    try:
        arguments = sys.argv[1:]
        if arguments and arguments[0] == "adjudicate":
            options = adjudication_parser().parse_args(arguments)
            result = adjudicate_terminal_failure(options.campaign_root)
        else:
            result = execute(parser().parse_args(arguments))
        print(json.dumps(result, sort_keys=True))
    except (HilError, campaign.CampaignError, OSError, subprocess.SubprocessError, ValueError) as exc:
        print(f"HIL stopped: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
