#!/usr/bin/env python3
"""Restore, prepare and capture ordered issue20 slots as an ordinary user.

The baseline and raw flash never leave the private campaign directory. Recovery
only restores; it cannot repeat an observer run or advance the schedule.
"""
from __future__ import annotations

import argparse
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


def checked_file(row):
    require(isinstance(row, dict) and set(row) == {"path", "sha256"}, "invalid profile artifact")
    path = Path(row["path"])
    require(path.is_absolute() and path.is_file() and not path.is_symlink(), "artifact must be an absolute regular file")
    require(isinstance(row["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", row["sha256"])
            and digest(path) == row["sha256"], "artifact SHA-256 mismatch")
    return path


def load_profiles(path):
    value = campaign._read_json(Path(path), "HIL profiles")
    require(set(value) == {"schema_version", "profiles"} and value["schema_version"] == 1,
            "unsupported HIL profiles schema")
    require(isinstance(value["profiles"], dict) and set(value["profiles"]) == set(campaign.SCENARIOS),
            "profiles must contain exactly native/interp/aot")
    for profile in value["profiles"].values():
        require(isinstance(profile, dict) and set(profile) == {"partition_table", "app"}, "invalid profile fields")
        validate_table(checked_file(profile["partition_table"]))
        validate_app(checked_file(profile["app"]))
    return value


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

    def overlay(self, profile):
        table, app = checked_file(profile["partition_table"]), checked_file(profile["app"])
        validate_table(table)
        validate_app(app)
        self.command("write_flash", hex(TABLE_OFFSET), table, hex(APP_OFFSET), app, after="hard_reset")

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
            row["path"] = str(_snapshot(row["path"], snapshots / f"{name}-{kind}.bin", row["sha256"]))
    return {"schema_version": 1, "campaign_id": value["campaign_id"], "git_commit": value["git_commit"],
            "state": "ready", "restore_required": False, "next_index": 0, "slots": schedule["slots"],
            "baseline": str(baseline), "profiles": profiles["profiles"], "options": vars(options), "events": []}


def _invoke_slot(options, slot):
    return campaign.run_slot(Path(options.campaign_root), slot["slot_id"], device_ip=options.device_ip,
        serial_port=options.serial_port, seconds=150, trigger_after=60, target_sequence=2,
        bind_ip=options.bind_ip, udp_port=3333, device_port=3333, serial_baud=options.serial_baud,
        remote_options={"target": options.remote_observer, "known_hosts": options.remote_known_hosts,
                        "identity": options.remote_identity, "root": options.remote_root,
                        "ssh_port": options.remote_ssh_port},
        remote_server_log=Path(options.remote_server_log))


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
        remote.validate_options({"target": options.remote_observer, "known_hosts": options.remote_known_hosts,
                                 "identity": options.remote_identity, "root": options.remote_root,
                                 "ssh_port": options.remote_ssh_port})
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
            board.identify()
            try:
                board.restore(Path(journal["baseline"]), receipt / "recovered-readback.bin")
            except BaseException:
                journal["state"] = "restore_failed"
                write_private(journal_path, journal)
                raise
            journal.update(state="recovered_interruption", restore_required=False)
            write_private(journal_path, journal)
            return {"state": journal["state"], "slot_retry_permitted": False}
        require(journal["state"] in {"ready", "complete"} and not journal["restore_required"],
                "interrupted/failed journal: recover only; never repeat a claimed slot")
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
        for index in range(journal["next_index"], min(len(journal["slots"]), journal["next_index"] + options.max_slots)):
            slot = journal["slots"][index]
            require(index == journal["next_index"], "slot ordering mismatch")
            require(not (Path(options.campaign_root) / "raw/slots" / slot["slot_id"]).exists(),
                    "slot already has capture evidence; no retry allowed")
            board.identify()
            require(board.read_hash(receipt / f"{slot['slot_id']}-before.bin") == BASELINE_SHA256,
                    "live full flash differs from approved baseline; refusing writes")
            claims = private_directory(root / "claims")
            write_private(claims / (slot["slot_id"] + ".json"), {"slot": slot, "retry_permitted": False}, exclusive=True)
            journal.update(state="preparing", restore_required=True, current_slot=slot["slot_id"])
            write_private(journal_path, journal)
            failure = None
            try:
                # Restore is explicit even if the initial read already matched.
                board.restore(Path(journal["baseline"]), receipt / f"{slot['slot_id']}-prepared-readback.bin", boot=False)
                board.overlay(journal["profiles"][slot["scenario"]])
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
                    board.restore(Path(journal["baseline"]), receipt / f"{slot['slot_id']}-after.bin")
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


def main():
    os.umask(0o077)
    try:
        print(json.dumps(execute(parser().parse_args()), sort_keys=True))
    except (HilError, campaign.CampaignError, OSError, subprocess.SubprocessError, ValueError) as exc:
        print(f"HIL stopped: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
