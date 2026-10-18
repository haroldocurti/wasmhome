"""Bounded, offline validation for the pinned IDF 5.2.6 HIL bootloader."""
from __future__ import annotations

import hashlib
import re
import struct


# Pinned IDF v5.2.6 components/bootloader/subproject/main/ld/esp32s3/bootloader.ld.
# These are the bootloader linker windows, not the chip's entire RAM map. The
# data/instruction buses alias the same SRAM with an offset of 0x006f0000.
BOOTLOADER_REGIONS = (
    (0x3FCE3700, 0x3FCE7700, "dram"),
    # iram_seg and iram_loader_seg are adjacent. elf2image may merge adjacent
    # IRAM segments, so validate their contiguous union rather than imposing
    # a binary segment boundary at the linker's logical split 0x403cc700.
    (0x403C9700, 0x403D3700, "iram"),
)
IRAM_DRAM_OFFSET = 0x006F0000


class BootstrapFormatError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise BootstrapFormatError(message)


def generated_config_defines(text: str) -> dict[str, str]:
    """Accept the flat generated sdkconfig.h format, never conditional C input.

    Preserve quoted strings while removing comments. Merely scanning lines
    would treat a define inside a block comment or #if 0 as an active option.
    Generated IDF headers need no includes, conditionals or continuations.
    """
    _require(len(text) <= 1024 * 1024, "generated config is too large")
    _require(re.search(r"\\\r?\n", text) is None,
             "generated config line continuations are unsupported")
    tokens = re.compile(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|/\*.*?\*/|//[^\n]*', re.S)

    def uncomment(match):
        token = match.group(0)
        return "\n" * token.count("\n") if token.startswith(("/*", "//")) else token

    clean = tokens.sub(uncomment, text)
    definitions = {}
    for line in clean.splitlines():
        line = line.strip()
        if not line or re.fullmatch(r"#\s*pragma\s+once", line):
            continue
        match = re.fullmatch(r"#\s*define\s+(CONFIG_\w+)\s+(.+)", line)
        _require(match is not None,
                 "generated config contains unsupported directives or malformed definitions")
        name, value = match.groups()
        _require(name not in definitions, "generated config repeats a macro")
        definitions[name] = value.strip()
    return definitions


def validate_bootloader_image(data: bytes) -> dict:
    """Check every byte of an unsigned IDF S3 image without importing serial.

    The ordinary build appends a SHA-256 after the checksum/padding. Secure
    boot signature sectors, unknown trailers and padded whole-flash images
    are outside this campaign format and fail closed.
    """
    _require(24 <= len(data) < 0x8000, "bootloader length exceeds its frozen region")
    _require(data[0] == 0xE9 and 1 <= data[1] <= 16
             and int.from_bytes(data[12:14], "little") == 9,
             "bootloader header is not ESP32-S3")
    _require(data[2] in {0, 1, 2, 3} and data[3] >> 4 == 4,
             "bootloader header must select supported SPI mode and 16 MiB flash")
    _require(data[23] == 1, "bootloader must append its IDF SHA-256 digest")
    cursor, checksum = 24, 0xEF
    segments = []
    physical_ranges = []
    descriptions = []
    entrypoint = int.from_bytes(data[4:8], "little")
    for _ in range(data[1]):
        _require(cursor + 8 <= len(data), "bootloader has a truncated segment header")
        address, size = struct.unpack_from("<II", data, cursor)
        cursor += 8
        _require(size > 0 and size % 4 == 0 and cursor + size <= len(data),
                 "bootloader has a truncated or malformed segment")
        end = address + size
        _require(address != 0 and address % 4 == 0 and end <= 0x100000000,
                 "bootloader segment has a zero, unaligned or overflowing address range")
        regions = [kind for start, limit, kind in BOOTLOADER_REGIONS
                   if start <= address < end <= limit]
        _require(len(regions) == 1,
                 "bootloader segment address range is outside the pinned linker windows")
        region = regions[0]
        physical_start = address - IRAM_DRAM_OFFSET if region == "iram" else address
        physical_end = physical_start + size
        _require(not any(physical_start < old_end and old_start < physical_end
                         for old_start, old_end in physical_ranges),
                 "bootloader segment address ranges overlap")
        physical_ranges.append((physical_start, physical_end))
        payload = data[cursor:cursor + size]
        cursor += size
        for byte in payload:
            checksum ^= byte
        segments.append((address, size))
        if region == "dram" and len(payload) >= 80:
            if payload[0] == 0x50:
                descriptions.append(payload[:80])
    checksum_offset = cursor | 15
    _require(checksum_offset + 1 + 32 == len(data),
             "bootloader is truncated or contains an unsupported trailer")
    _require(data[cursor:checksum_offset] == b"\0" * (checksum_offset - cursor),
             "bootloader checksum padding differs")
    _require(data[checksum_offset] == checksum, "bootloader checksum differs")
    _require(data[checksum_offset + 1:] == hashlib.sha256(data[:checksum_offset + 1]).digest(),
             "bootloader appended SHA-256 differs")
    _require(any(0x40370000 <= start <= entrypoint < start + size <= 0x403E0000
                 for start, size in segments), "bootloader entrypoint is outside its IRAM segments")
    _require(len(descriptions) == 1, "bootloader requires one DRAM IDF description")
    description = descriptions[0]
    _require(description[1:4] == b"\0" * 3 and description[64:80] == b"\0" * 16,
             "bootloader description reserved bytes differ")
    _require(description[8:40].split(b"\0", 1)[0] == b"v5.2.6",
             "bootloader description does not identify pinned IDF v5.2.6")
    return {"segments": len(segments), "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(), "idf_version": "v5.2.6"}
