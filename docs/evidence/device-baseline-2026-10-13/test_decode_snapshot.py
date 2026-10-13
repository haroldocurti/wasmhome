#!/usr/bin/env python3
"""Synthetic tests for ESP-IDF 5.2.6 OTA data interpretation."""
from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import tempfile
import zlib
from pathlib import Path

script = Path(__file__).with_name("decode_snapshot.py")
spec = importlib.util.spec_from_file_location("decode_snapshot", script)
decoder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(decoder)


def record(seq, state):
    body = seq.to_bytes(4, "little") + bytes(20) + state.to_bytes(4, "little")
    crc = zlib.crc32(seq.to_bytes(4, "little"), 0xFFFFFFFF) & 0xFFFFFFFF
    return body + crc.to_bytes(4, "little")


def main():
    assert decoder.crc_is_valid(0x12345678, zlib.crc32(bytes.fromhex("78563412"), 0xFFFFFFFF))
    assert not decoder.crc_is_valid(0x12345678, 0)
    with tempfile.TemporaryDirectory(prefix="wasmhome-ota-synthetic-") as temp:
        path = Path(temp) / "otadata.synthetic"
        blob = bytearray(b"\xff" * (2 * decoder.OTA_SECTOR))
        blob[:32] = record(7, 1)  # PENDING_VERIFY remains valid in IDF selector.
        blob[decoder.OTA_SECTOR:decoder.OTA_SECTOR + 32] = record(8, 4)  # ABORTED invalid.
        path.write_bytes(blob)
        records = decoder.parse_otadata(path, 0x23000)
        assert records[0]["valid_by_idf_rules"]
        assert records[0]["ota_state"] == "PENDING_VERIFY"
        assert not records[1]["valid_by_idf_rules"]
        assert records[1]["crc_valid"]
        assert records[0]["absolute_offset"] == 0x23000
        assert records[1]["absolute_offset"] == 0x24000
        selected = decoder.choose_active_otadata(records)
        assert selected["copy"] == 0 and selected["ota_seq"] == 7

        blob[:32] = record(9, 2)
        blob[decoder.OTA_SECTOR:decoder.OTA_SECTOR + 32] = record(9, 0xFFFFFFFF)
        path.write_bytes(blob)
        tied = decoder.choose_active_otadata(decoder.parse_otadata(path, 0))
        assert tied["copy"] == 0  # IDF chooses the first copy on a sequence tie.

        boot_log = Path(temp) / "boot.log"
        boot_manifest = Path(temp) / "boot-manifest.json"
        boot_bytes = b"private noise must never be returned\nLoaded app from partition at offset 0x20000\n"
        boot_log.write_bytes(boot_bytes)
        boot_manifest.write_text(json.dumps({
            "schema": "wasmhome-private-serial-capture-v1",
            "action": "reset-and-capture-current-app-no-flash-write",
            "started_utc": "2026-10-13T13:08:57.835257+00:00",
            "bytes": len(boot_bytes),
            "sha256": hashlib.sha256(boot_bytes).hexdigest(),
        }), encoding="utf-8")
        evidence = decoder.verify_bootloader_log(boot_log, boot_manifest)
        assert evidence["loaded_app_offset"] == "0x20000"
        assert "private noise" not in json.dumps(evidence)
        boot_log.write_bytes(boot_bytes + b"Loaded app from partition at offset 0x720000\n")
        try:
            decoder.verify_bootloader_log(boot_log, boot_manifest)
        except ValueError:
            pass
        else:
            raise AssertionError("tampered/multiple boot log must be rejected")

    assert decoder.classify_app_subtype("factory") == ("factory", 0)
    assert decoder.classify_app_subtype("ota_0") == ("ota", 0)
    assert decoder.classify_app_subtype("ota_1") == ("ota", 1)
    print("synthetic OTA decoder checks passed (CRC, states, sequence selection, offsets, boot-log sanitization/integrity)")


if __name__ == "__main__":
    main()
