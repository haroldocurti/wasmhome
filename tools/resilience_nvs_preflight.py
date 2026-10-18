#!/usr/bin/env python3
"""Offline, privacy-minimized issue #22 NVS preflight.

The input is a raw NVS partition image. This tool never opens a serial device,
does not write flash/NVS, and does not print parsed NVS data. Its JSON output is
restricted to the issue #22 campaign identity, selected ledger state/hashes,
and prior-recovery completeness.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from zlib import crc32

if __package__:
    from .vendor.esp_idf_nvs_parser import NVS_Partition, nvs_const
else:
    from vendor.esp_idf_nvs_parser import NVS_Partition, nvs_const


CAMPAIGN_ID = "issues16-18-bench-20261016-observer13"
SCHEMA = "wasmhome-issue22-readonly-preflight-v4"
NVS_BYTES = 0x6000
PAGE_BYTES = 4096
# ESP32-S3 field offsets are pinned to the issue #22 `update_ledger_t` layout
# in main/signed_update.c (DWARF size 1776, CRC at 1768). Recovery layouts and
# versions are pinned to the corresponding C structs in the test components.
UPDATE_MAGIC = 0x57485550
UPDATE_VERSION = 2
UPDATE_LEDGER_BYTES = 1776
UPDATE_LEDGER_CRC_OFFSET = 1768
UPDATE_RECORDS_OFFSET = 16
UPDATE_RECORD_BYTES = 192
UPDATE_RECORD_COUNT = 8
UPDATE_PENDING_RESERVED_OFFSET = 1634
RESILIENCE_MAGIC = 0x57524832
MODULE_RESILIENCE_MAGIC = 0x574D5232
RECOVERY_VERSION = 1
RECOVERY_COMPLETE_MASK = 0x03

PROFILES = {
    "native": {
        "scenario": 1,
        "component": "firmware",
        "control_version": 1,
        # Newly signed issue #22 profile-A native v2 release artifact.
        "release_artifact_sha256": "224e0c01ae4843828a6faad010fc60f20d9795816fee1ca483f11412ff8c67cf",
        "release_artifact_size": 916688,
        "recovery": "native",
    },
    "module-interp": {
        "scenario": 2,
        "component": "gpio-control",
        "control_version": 2,
        "release_artifact_sha256": "3a972948112397acdaad03c81714a3daf147ecce926f0854341e58a350f3d5c5",
        "release_artifact_size": 328,
        "recovery": "module",
    },
    "module-aot": {
        "scenario": 3,
        "component": "gpio-control",
        "control_version": 2,
        "release_artifact_sha256": "293d78b1af5e0c7e76593d9b8e755692ee311d8427c43d94673ddfd499d5bf53",
        "release_artifact_size": 1256,
        "recovery": "module",
    },
}

ALLOWED_NAMESPACES = {
    "wh_update": {"ledger"},
    "wh_resilience": {"pending"},
    "wh_mod_resil": {"pending"},
}
VARIABLE_DATA_TYPES = {"string", "blob", "blob_data"}


class PreflightError(ValueError):
    """Safe, data-independent diagnostic; never includes parsed bytes."""


def _nvs_key_bytes(key: str) -> bytes:
    encoded = key.encode("ascii")
    if len(encoded) >= 16:
        raise PreflightError("internal allowlist key is too long")
    return encoded + bytes(16 - len(encoded))


def _u16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset + 2], "little")


def _u32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset + 4], "little")


def _u64(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset + 8], "little")


def _entry_key_raw(entry) -> bytes:
    return bytes(entry.raw[8:24])


def _validate_variable_data(entry) -> None:
    kind = entry.metadata["type"]
    span = entry.metadata["span"]
    if kind not in VARIABLE_DATA_TYPES:
        if span != 1:
            raise PreflightError("invalid NVS entry span")
        return
    if span <= 1 or len(entry.children) != span - 1:
        raise PreflightError("invalid NVS variable entry span")
    size = _u16(entry.raw, 24)
    if size <= 0 or size > len(entry.children) * nvs_const.entry_size:
        raise PreflightError("invalid NVS variable entry size")
    data = b"".join(bytes(child.raw) for child in entry.children)[:size]
    expected_crc = _u32(entry.raw, 28)
    if crc32(data, 0xFFFFFFFF) != expected_crc:
        raise PreflightError("NVS variable-data CRC mismatch")
    if any(child.state != entry.state for child in entry.children):
        raise PreflightError("inconsistent NVS variable-data state")


def _validate_pages(partition: NVS_Partition) -> list:
    if len(partition.pages) != NVS_BYTES // PAGE_BYTES:
        raise PreflightError("NVS partition must contain exactly six 4 KiB pages")
    all_written = []
    for page in partition.pages:
        status = page.header["status"]
        if status == "Empty":
            if (not page.is_empty or
                    page.raw_entry_state_bitmap != bytearray({0xFF}) * nvs_const.entry_size or
                    any(not entry.is_empty for entry in page.entries)):
                raise PreflightError("NVS empty-page integrity failure")
            continue
        if status not in {"Active", "Full"}:
            raise PreflightError("NVS page state is invalid or transitional")
        if page.header["version"] not in {1, 2}:
            raise PreflightError("unsupported NVS page version")
        if page.header["crc"]["original"] != page.header["crc"]["computed"]:
            raise PreflightError("NVS page-header CRC mismatch")
        for entry in page.entries:
            if entry.state not in {"Written", "Erased", "Empty"}:
                raise PreflightError("invalid NVS entry state")
            if entry.state != "Written":
                continue
            if entry.metadata["type"] not in nvs_const.item_type.values():
                raise PreflightError("unsupported NVS entry type")
            if entry.metadata["crc"]["original"] != entry.metadata["crc"]["computed"]:
                raise PreflightError("NVS entry CRC mismatch")
            span = entry.metadata["span"]
            if not (1 <= span <= 126) or entry.index + span > 126:
                raise PreflightError("NVS entry span is out of bounds")
            _validate_variable_data(entry)
            all_written.append(entry)
    if not any(page.header["status"] == "Empty" for page in partition.pages):
        raise PreflightError("NVS partition has no erased free page")
    return all_written


def _resolve_namespaces(entries: list) -> dict[str, int]:
    wanted_keys = {_nvs_key_bytes(name): name for name in ALLOWED_NAMESPACES}
    found: dict[str, list[int]] = {name: [] for name in ALLOWED_NAMESPACES}
    for entry in entries:
        if entry.metadata["namespace"] != 0:
            continue
        name = wanted_keys.get(_entry_key_raw(entry))
        if name is None:
            continue
        if entry.metadata["type"] != "uint8_t" or entry.metadata["span"] != 1:
            raise PreflightError("NVS namespace directory integrity failure")
        namespace_id = entry.raw[24]
        if namespace_id == 0 or namespace_id == 0xFF:
            raise PreflightError("NVS namespace directory integrity failure")
        found[name].append(namespace_id)
    result = {}
    for name, ids in found.items():
        if len(ids) > 1 or (ids and any(other != ids[0] for other in ids)):
            raise PreflightError("duplicate NVS namespace identity")
        if ids:
            result[name] = ids[0]
    if len(set(result.values())) != len(result):
        raise PreflightError("duplicate NVS namespace identity")
    return result


def _validate_blob_inventory(
    entries: list, target_identities: set[tuple[int, bytes]]
) -> dict[tuple[int, bytes], bytes]:
    indexes: dict[tuple[int, bytes], list] = {}
    chunks: dict[tuple[int, bytes], list] = {}
    for entry in entries:
        kind = entry.metadata["type"]
        identity = (entry.metadata["namespace"], _entry_key_raw(entry))
        if kind == "blob_index":
            indexes.setdefault(identity, []).append(entry)
        elif kind == "blob_data":
            chunks.setdefault(identity, []).append(entry)

    assembled: dict[tuple[int, bytes], bytes] = {}
    for identity, parents in indexes.items():
        if len(parents) != 1:
            raise PreflightError("duplicate NVS blob index")
        index = parents[0]
        total_size = _u32(index.raw, 24)
        chunk_count = index.raw[28]
        chunk_start = index.raw[29]
        if total_size == 0:
            if chunk_count != 0:
                raise PreflightError("invalid empty NVS blob index")
            if identity in target_identities:
                assembled[identity] = b""
            continue
        if chunk_count == 0 or chunk_start + chunk_count > 256:
            raise PreflightError("invalid NVS blob index range")
        ordered_sizes: dict[int, int] = {}
        ordered_target_data: dict[int, bytes] = {}
        for chunk in chunks.get(identity, []):
            index_number = chunk.metadata["chunk_index"]
            if not chunk_start <= index_number < chunk_start + chunk_count:
                raise PreflightError("NVS blob chunk is outside its index range")
            if index_number in ordered_sizes:
                raise PreflightError("duplicate NVS blob chunk")
            size = _u16(chunk.raw, 24)
            if size <= 0 or size > len(chunk.children) * nvs_const.entry_size:
                raise PreflightError("invalid NVS blob chunk size")
            ordered_sizes[index_number] = size
            if identity in target_identities:
                ordered_target_data[index_number] = b"".join(
                    bytes(child.raw) for child in chunk.children
                )[:size]
        expected_numbers = set(range(chunk_start, chunk_start + chunk_count))
        if set(ordered_sizes) != expected_numbers:
            raise PreflightError("NVS blob is missing or has excess chunks")
        if sum(ordered_sizes.values()) != total_size:
            raise PreflightError("NVS blob length mismatch")
        if identity in target_identities:
            value = b"".join(ordered_target_data[number] for number in sorted(ordered_target_data))
            if len(value) != total_size:
                raise PreflightError("NVS blob length mismatch")
            assembled[identity] = value
    if set(chunks) - set(indexes):
        raise PreflightError("NVS blob chunk has no index")
    return assembled


def _ledger_state(blob: bytes | None, profile: str) -> dict:
    empty = {
        "ledger_present": False,
        "accepted_floor": None,
        "ledger_active_target_version": None,
        "ledger_active_sequence": None,
        "active_slot": None,
        "active_payload_size": None,
        "active_payload_sha256": None,
        "active_manifest_sha256": None,
        "pending": False,
    }
    if blob is None:
        return empty
    if len(blob) != UPDATE_LEDGER_BYTES:
        raise PreflightError("update ledger has an unexpected size")
    if _u32(blob, 0) != UPDATE_MAGIC or _u16(blob, 4) != UPDATE_VERSION:
        raise PreflightError("update ledger header is invalid")
    if (_u16(blob, 6) != 0 or blob[1552] not in (0, 1) or
            _u16(blob, UPDATE_PENDING_RESERVED_OFFSET) != 0):
        raise PreflightError("update ledger state is invalid")
    if crc32(blob[:UPDATE_LEDGER_CRC_OFFSET]) != _u32(blob, UPDATE_LEDGER_CRC_OFFSET):
        raise PreflightError("update ledger CRC mismatch")
    pending = bool(blob[1552])
    expected = PROFILES[profile]
    campaign_hash = hashlib.sha256(CAMPAIGN_ID.encode("ascii")).digest()
    component_hash = hashlib.sha256(expected["component"].encode("ascii")).digest()
    matching = []
    for index in range(UPDATE_RECORD_COUNT):
        base = UPDATE_RECORDS_OFFSET + index * UPDATE_RECORD_BYTES
        if blob[base] != 1:
            if blob[base] not in (0, 1):
                raise PreflightError("update ledger record state is invalid")
            continue
        if blob[base + 1] == expected["scenario"] and \
                blob[base + 2:base + 34] == campaign_hash and \
                blob[base + 34:base + 66] == component_hash:
            matching.append(base)
    if len(matching) > 1:
        raise PreflightError("duplicate issue22 update ledger record")
    if not matching:
        empty["pending"] = pending
        return empty
    base = matching[0]
    floor = _u64(blob, base + 72)
    active_slot = _u32(blob, base + 80)
    active_sequence = _u64(blob, base + 88)
    active_size = _u32(blob, base + 96)
    active_version = _u16(blob, base + 100)
    if _u16(blob, base + 102) != 0:
        raise PreflightError("update ledger record reserved field is invalid")
    payload_hash = blob[base + 104:base + 136]
    manifest_hash = blob[base + 136:base + 168]
    return {
        "ledger_present": True,
        "accepted_floor": floor,
        "ledger_active_target_version": active_version,
        "ledger_active_sequence": active_sequence,
        "active_slot": active_slot,
        "active_payload_size": active_size,
        "active_payload_sha256": payload_hash.hex() if any(payload_hash) else None,
        "active_manifest_sha256": manifest_hash.hex() if any(manifest_hash) else None,
        "pending": pending,
    }


def _recovery_state(blob: bytes | None, kind: str) -> dict:
    if blob is None:
        # The first resilience trial has no earlier recovery record. Treat the
        # absent record as a clean initial state; present records still have to
        # prove complete recovery before another campaign attempt.
        return {"previous_attempt_present": False, "recovery_complete": True}
    if kind == "native":
        if len(blob) != 48 or _u32(blob, 0) != RESILIENCE_MAGIC or _u16(blob, 4) != RECOVERY_VERSION:
            raise PreflightError("native resilience record header is invalid")
        case_code = blob[6]
        recovery_mask = blob[7]
        accepted_boot = _u64(blob, 8)
        recovery_boot = _u64(blob, 16)
        attempt = blob[24:40]
        acknowledged = blob[47]
        if (case_code not in (1, 2, 3, 4) or recovery_mask & ~RECOVERY_COMPLETE_MASK or
                accepted_boot == 0 or not any(attempt) or
                any(blob[40:47]) or acknowledged not in (0, 1)):
            raise PreflightError("native resilience record state is invalid")
        # The C retry gate uses the two service bits. An unacknowledged trial
        # can still recover after reboot, so acknowledgement is validated but
        # does not determine recovery completeness.
        complete = (recovery_mask == RECOVERY_COMPLETE_MASK and recovery_boot != 0)
    else:
        if len(blob) != 88 or _u32(blob, 0) != MODULE_RESILIENCE_MAGIC or _u16(blob, 4) != RECOVERY_VERSION:
            raise PreflightError("module resilience record header is invalid")
        case_code, recovery_mask, accepted, trap_seen, slot_recovered, recovery_reported = blob[6:12]
        accepted_boot = _u64(blob, 16)
        recovery_boot = _u64(blob, 24)
        attempt = blob[64:80]
        if (case_code not in (1, 2, 3, 4, 5, 6) or
                recovery_mask & ~RECOVERY_COMPLETE_MASK or accepted not in (0, 1) or
                trap_seen not in (0, 1) or slot_recovered not in (0, 1) or
                recovery_reported not in (0, 1) or accepted_boot == 0 or
                not any(attempt) or any(blob[80:88])):
            raise PreflightError("module resilience record state is invalid")
        # C's next-attempt gate requires accepted, both service bits, and slot
        # recovery. recovery_reported only marks event emission; it is not a
        # recovery gate and remains validated as a boolean above.
        complete = (recovery_mask == RECOVERY_COMPLETE_MASK and
                    accepted == 1 and slot_recovered == 1 and recovery_boot != 0)
    return {"previous_attempt_present": True, "recovery_complete": complete}


def _read_input(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as error:
        raise PreflightError("cannot open NVS partition input") from error
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size != NVS_BYTES:
            raise PreflightError("NVS input must be a regular 24 KiB partition dump")
        data = bytearray()
        while len(data) <= NVS_BYTES:
            block = os.read(fd, NVS_BYTES + 1 - len(data))
            if not block:
                break
            data.extend(block)
        if len(data) != NVS_BYTES:
            raise PreflightError("NVS input size changed while reading")
        return bytes(data)
    finally:
        os.close(fd)


def _write_private_json(path: Path, value: dict) -> None:
    payload = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as error:
        raise PreflightError("cannot create new preflight JSON file") from error
    try:
        os.fchmod(fd, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise PreflightError("cannot finish writing preflight JSON")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


def build_preflight(raw: bytes, *, profile: str, boot_id: str) -> dict:
    if len(raw) != NVS_BYTES:
        raise PreflightError("NVS partition input must be exactly 24 KiB")
    partition = NVS_Partition("nvs", bytearray(raw), decode_values=False)
    entries = _validate_pages(partition)
    namespace_ids = _resolve_namespaces(entries)
    target_identities = {
        (namespace_ids[namespace], _nvs_key_bytes(key))
        for namespace, keys in ALLOWED_NAMESPACES.items()
        if namespace in namespace_ids
        for key in keys
    }
    blob_values = _validate_blob_inventory(entries, target_identities)

    def get_blob(namespace: str, key: str) -> bytes | None:
        namespace_id = namespace_ids.get(namespace)
        if namespace_id is None:
            return None
        identity = (namespace_id, _nvs_key_bytes(key))
        if identity not in blob_values:
            return None
        return blob_values[identity]

    expected = PROFILES[profile]
    ledger = get_blob("wh_update", "ledger")
    selected_state = _ledger_state(ledger, profile)
    if selected_state["pending"]:
        raise PreflightError("update ledger has an outstanding pending update")

    recovery_by_namespace = {
        "wh_resilience": "native",
        "wh_mod_resil": "module",
    }
    recovery_states = {}
    for namespace, kind in recovery_by_namespace.items():
        state = _recovery_state(get_blob(namespace, "pending"), kind)
        recovery_states[namespace] = state
        if state["previous_attempt_present"] and not state["recovery_complete"]:
            selected = namespace == ("wh_resilience" if expected["recovery"] == "native"
                                     else "wh_mod_resil")
            reason = ("selected profile has an incomplete prior recovery" if selected
                      else "another profile has an incomplete prior recovery")
            raise PreflightError(reason)
    recovery_state = recovery_states[
        "wh_resilience" if expected["recovery"] == "native" else "wh_mod_resil"
    ]
    return {
        "schema": SCHEMA,
        "campaign_id": CAMPAIGN_ID,
        "profile": profile,
        "control_version": expected["control_version"],
        "record_identity": {
            "scenario": expected["scenario"],
            "component": expected["component"],
        },
        "boot_id": boot_id,
        "release_artifact_sha256": selected_state["active_payload_sha256"],
        "native_state": selected_state if profile == "native" else None,
        "module_state": selected_state if profile != "native" else None,
        "recovery_state": recovery_state,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nvs-dump", required=True, type=Path,
                        help="local raw 0x6000-byte NVS partition dump")
    parser.add_argument("--profile", required=True, choices=sorted(PROFILES))
    parser.add_argument("--boot-id", required=True,
                        help="16 lowercase hex digits read from the current firmware boot event")
    parser.add_argument("--output", required=True, type=Path,
                        help="new JSON path; created exclusively with mode 0600")
    args = parser.parse_args(argv)
    if (len(args.boot_id) != 16 or any(c not in "0123456789abcdef" for c in args.boot_id)
            or int(args.boot_id, 16) == 0):
        parser.error("--boot-id must be 16 nonzero lowercase hex digits")
    try:
        raw = _read_input(args.nvs_dump)
        value = build_preflight(raw, profile=args.profile, boot_id=args.boot_id)
        _write_private_json(args.output, value)
    except PreflightError as error:
        print(f"NVS preflight failed: {error}", file=sys.stderr)
        return 2
    except Exception:
        # Never forward a parser traceback or exception containing raw NVS data.
        print("NVS preflight failed: parser rejected the partition", file=sys.stderr)
        return 2
    print("Sanitized NVS preflight written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
