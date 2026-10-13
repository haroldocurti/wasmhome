#!/usr/bin/env python3
"""Independent reflected-CRC cross-check against one captured OTADATA record."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path

POLY_REFLECTED = 0xEDB88320

def idf_rom_crc32_le_seed_ones(data: bytes) -> int:
    crc = (~0xFFFFFFFF) & 0xFFFFFFFF  # ROM helper complements the initial seed.
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (POLY_REFLECTED if (crc & 1) else 0)
    return (~crc) & 0xFFFFFFFF  # ROM helper complements its result.

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("otadata_partition", type=Path)
    ap.add_argument("output", type=Path)
    args = ap.parse_args()
    with args.otadata_partition.open("rb") as f:
        seq_bytes = f.read(4)
        f.seek(28)
        stored_bytes = f.read(4)
    if len(seq_bytes) != 4 or len(stored_bytes) != 4:
        raise SystemExit("short input")
    seq = int.from_bytes(seq_bytes, "little")
    stored = int.from_bytes(stored_bytes, "little")
    calc = idf_rom_crc32_le_seed_ones(seq_bytes)
    result = {
        "method": "independent bitwise reflected CRC-32; ROM API complements seed and result",
        "polynomial_reflected": f"0x{POLY_REFLECTED:08x}",
        "record_index": 0,
        "ota_seq": f"0x{seq:08x}",
        "stored_crc32": f"0x{stored:08x}",
        "independent_crc32": f"0x{calc:08x}",
        "match": calc == stored,
    }
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(args.output, 0o600)
    print(json.dumps(result, sort_keys=True))

if __name__ == "__main__":
    main()
