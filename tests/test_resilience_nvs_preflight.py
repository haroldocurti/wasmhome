"""Synthetic, offline coverage for the sanitized issue #22 NVS preflight."""

import hashlib
import json
import struct
import unittest
from zlib import crc32

from tools import resilience_nvs_preflight as preflight
from tools.resilience_harness import resilience_plan
from tools.resilience_hil_runner import validate_preflight, HILInputError


def _key(value):
    encoded = value.encode("ascii")
    return encoded + bytes(16 - len(encoded))


def _entry(namespace, entry_type, key, data, *, span=1, chunk_index=0):
    raw = bytearray(32)
    raw[0] = namespace
    raw[1] = entry_type
    raw[2] = span
    raw[3] = chunk_index
    raw[8:24] = _key(key)
    raw[24:32] = data
    raw[4:8] = crc32(raw[:4] + raw[8:], 0xFFFFFFFF).to_bytes(4, "little")
    return bytes(raw)


def _blob_entries(namespace, key, value):
    yield _entry(namespace, 0x48, key,
                 len(value).to_bytes(4, "little") +
                 bytes((1, 0, 0, 0)))
    span = 1 + (len(value) + 31) // 32
    header = (len(value).to_bytes(2, "little") + bytes(2) +
              crc32(value, 0xFFFFFFFF).to_bytes(4, "little"))
    yield _entry(namespace, 0x42, key, header, span=span, chunk_index=0)
    for offset in range(0, len(value), 32):
        yield value[offset:offset + 32].ljust(32, b"\x00")


def _ledger_blob(*, pending=False, header_reserved=0, pending_reserved=0,
                 record_reserved=0):
    ledger = bytearray(preflight.UPDATE_LEDGER_BYTES)
    struct.pack_into("<IHHQ", ledger, 0, preflight.UPDATE_MAGIC,
                     preflight.UPDATE_VERSION, header_reserved, 1)
    for record_index, profile in enumerate(preflight.PROFILES.values()):
        base = preflight.UPDATE_RECORDS_OFFSET + (
            record_index * preflight.UPDATE_RECORD_BYTES
        )
        ledger[base] = 1
        ledger[base + 1] = profile["scenario"]
        ledger[base + 2:base + 34] = hashlib.sha256(
            preflight.CAMPAIGN_ID.encode("ascii")
        ).digest()
        ledger[base + 34:base + 66] = hashlib.sha256(
            profile["component"].encode("ascii")
        ).digest()
        struct.pack_into("<Q", ledger, base + 72, 2)
        active_slot = 0x20000 if profile["scenario"] == 1 else 1
        struct.pack_into("<I", ledger, base + 80, active_slot)
        struct.pack_into("<Q", ledger, base + 88, 2)
        struct.pack_into("<IHH", ledger, base + 96,
                         profile["release_artifact_size"], 2, record_reserved)
        ledger[base + 104:base + 136] = bytes.fromhex(
            profile["release_artifact_sha256"]
        )
        ledger[base + 136:base + 168] = bytes((0x21 + record_index,)) * 32
        ledger[base + 168:base + 184] = bytes((0x31 + record_index,)) * 16
        struct.pack_into("<Q", ledger, base + 184, 20 + record_index)
    ledger[1552] = int(pending)
    struct.pack_into("<H", ledger, preflight.UPDATE_PENDING_RESERVED_OFFSET,
                     pending_reserved)
    crc_offset = preflight.UPDATE_LEDGER_CRC_OFFSET
    ledger[crc_offset:crc_offset + 4] = crc32(
        ledger[:crc_offset]
    ).to_bytes(4, "little")
    return bytes(ledger)


def _native_pending(*, recovery_mask=3, acknowledged=1, reserved=bytes(7),
                    recovery_boot=9):
    value = bytearray(48)
    struct.pack_into("<IHBBQQ", value, 0, preflight.RESILIENCE_MAGIC,
                     preflight.RECOVERY_VERSION, 1, recovery_mask, 7,
                     recovery_boot)
    value[24:40] = bytes((0xA5,)) * 16
    value[40:47] = reserved
    value[47] = acknowledged
    return bytes(value)


def _module_pending(*, recovery_mask=3, slot_recovered=1,
                    recovery_reported=0, reserved=bytes(8), recovery_boot=9):
    value = bytearray(88)
    struct.pack_into("<IHBBBBBB", value, 0,
                     preflight.MODULE_RESILIENCE_MAGIC,
                     preflight.RECOVERY_VERSION, 1, recovery_mask,
                     1, 1, slot_recovered, recovery_reported)
    struct.pack_into("<QQQQQQ", value, 16, 7, recovery_boot, 100, 200, 300, 400)
    value[64:80] = bytes((0xA5,)) * 16
    value[80:88] = reserved
    return bytes(value)


def _synthetic_partition(*, native_pending=None, module_pending=None,
                         update_pending=False):
    entries = []
    namespace_ids = {
        "wh_update": 1,
        "wh_resilience": 2,
        "wh_mod_resil": 3,
        "wifi": 4,
    }
    for name, namespace_id in namespace_ids.items():
        entries.append(_entry(0, 0x01, name,
                              bytes((namespace_id, 0, 0, 0, 0, 0, 0, 0))))
    entries.extend(_blob_entries(1, "ledger", _ledger_blob(pending=update_pending)))
    if native_pending is not False:
        entries.extend(_blob_entries(2, "pending", native_pending or _native_pending()))
    if module_pending is not False:
        entries.extend(_blob_entries(3, "pending", module_pending or _module_pending()))
    entries.extend(_blob_entries(4, "credentials", b"synthetic-wifi-secret"))

    page = bytearray(b"\xff" * 4096)
    page[0:4] = (0xFFFFFFFE).to_bytes(4, "little")
    page[4:8] = bytes(4)
    page[8] = 0xFE  # NVS page version 2, represented as 256 - version.
    page[28:32] = crc32(page[4:28], 0xFFFFFFFF).to_bytes(4, "little")
    cursor = 0
    for raw in entries:
        if len(raw) != 32 or cursor >= 126:
            raise AssertionError("synthetic NVS page capacity exceeded")
        page_offset = (cursor + 2) * 32
        page[page_offset:page_offset + 32] = raw
        state_index = cursor
        bitmap_offset = 32 + state_index // 4
        shift = (state_index % 4) * 2
        page[bitmap_offset] = ((page[bitmap_offset] & ~(3 << shift)) |
                               (2 << shift))
        cursor += 1

    partition = bytearray(b"\xff" * preflight.NVS_BYTES)
    partition[:4096] = page
    return bytes(partition)


class ResilienceNvsPreflightTests(unittest.TestCase):
    def test_synthetic_partition_emits_only_allowlisted_positive_state(self):
        raw = _synthetic_partition()
        for profile in preflight.PROFILES:
            with self.subTest(profile=profile):
                value = preflight.build_preflight(
                    raw, profile=profile, boot_id="0000000000000007"
                )
                encoded = json.dumps(value, sort_keys=True)
                self.assertEqual(set(value), {
                    "schema", "campaign_id", "profile", "record_identity",
                    "boot_id", "control_version", "release_artifact_sha256", "native_state",
                    "module_state", "recovery_state",
                })
                self.assertEqual(value["control_version"],
                                 preflight.PROFILES[profile]["control_version"])
                self.assertTrue(value["recovery_state"]["previous_attempt_present"])
                self.assertTrue(value["recovery_state"]["recovery_complete"])
                self.assertNotIn("attempt_id", encoded)
                self.assertNotIn("credentials", encoded)
                self.assertNotIn("synthetic-wifi-secret", encoded)
                self.assertNotIn((bytes((0xA5,)) * 16).hex(), encoded)
                state = value["native_state"] or value["module_state"]
                self.assertTrue(state["ledger_present"])
                self.assertEqual(state["ledger_active_target_version"], 2)
                self.assertEqual(state["ledger_active_sequence"], 2)
                self.assertEqual(state["accepted_floor"], 2)
                self.assertEqual(state["active_slot"],
                                 0x20000 if profile == "native" else 1)
                self.assertEqual(state["active_payload_sha256"],
                                 value["release_artifact_sha256"])
                self.assertEqual(len(state["active_payload_sha256"]), 64)

    def test_synthetic_dump_preflight_integrates_with_runner_for_all_profiles(self):
        rows = {row["scenario"]: row for row in resilience_plan("nvs-preflight-integration")}
        raw = _synthetic_partition()
        for profile, scenario in (("native", "native"),
                                  ("module-interp", "interp"),
                                  ("module-aot", "aot")):
            with self.subTest(profile=profile):
                sanitized = preflight.build_preflight(
                    raw, profile=profile, boot_id="0000000000000007"
                )
                accepted = validate_preflight(sanitized, rows[scenario])
                self.assertEqual(accepted["profile"], profile)
                self.assertEqual(accepted["control_version"],
                                 1 if profile == "native" else 2)
                state = accepted["native_state"] or accepted["module_state"]
                self.assertEqual(state["ledger_active_target_version"], 2)
                self.assertEqual(state["ledger_active_sequence"], 2)
                self.assertEqual(state["active_payload_sha256"],
                                 accepted["release_artifact_sha256"])
        native = preflight.build_preflight(
            raw, profile="native", boot_id="0000000000000007"
        )
        native["control_version"] = 2
        with self.assertRaisesRegex(HILInputError, "control version"):
            validate_preflight(native, rows["native"])

    def test_native_reserved_bytes_and_invalid_ack_flag_are_rejected(self):
        with self.assertRaises(preflight.PreflightError):
            preflight._recovery_state(
                _native_pending(reserved=b"\x01" + bytes(6)), "native"
            )
        with self.assertRaises(preflight.PreflightError):
            preflight._recovery_state(
                _native_pending(acknowledged=2), "native"
            )

    def test_module_reserved_bytes_and_invalid_report_flag_are_rejected(self):
        with self.assertRaises(preflight.PreflightError):
            preflight._recovery_state(
                _module_pending(reserved=b"\x01" + bytes(7)), "module"
            )
        with self.assertRaises(preflight.PreflightError):
            preflight._recovery_state(
                _module_pending(recovery_reported=2), "module"
            )

    def test_update_ledger_reserved_fields_are_rejected(self):
        for ledger in (
            _ledger_blob(header_reserved=1),
            _ledger_blob(pending_reserved=1),
            _ledger_blob(record_reserved=1),
        ):
            with self.subTest():
                with self.assertRaises(preflight.PreflightError):
                    preflight._ledger_state(ledger, "native")

    def test_campaign_requires_recovery_state_but_not_native_ack_or_module_report_marker(self):
        module = preflight._recovery_state(
            _module_pending(recovery_reported=0), "module"
        )
        self.assertTrue(module["recovery_complete"])

        native = preflight._recovery_state(
            _native_pending(acknowledged=0), "native"
        )
        self.assertTrue(native["recovery_complete"])
        self.assertTrue(preflight._recovery_state(None, "native")["recovery_complete"])
        unaccepted_module = bytearray(_module_pending())
        unaccepted_module[8] = 0
        self.assertFalse(preflight._recovery_state(
            bytes(unaccepted_module), "module"
        )["recovery_complete"])

        self.assertFalse(preflight._recovery_state(
            _native_pending(recovery_mask=1), "native"
        )["recovery_complete"])
        self.assertFalse(preflight._recovery_state(
            _module_pending(slot_recovered=0), "module"
        )["recovery_complete"])

    def test_missing_selected_attempt_is_clean_and_other_profile_must_be_clear(self):
        native = preflight.build_preflight(
            _synthetic_partition(native_pending=False), profile="native",
            boot_id="0000000000000007",
        )
        self.assertEqual(native["recovery_state"], {
            "previous_attempt_present": False, "recovery_complete": True
        })
        # A complete recovery with ack=0 can be a disarmed attempt that recovered
        # on reboot; the C retry gate uses recovery mask, not the ack marker.
        acknowledged_later = preflight.build_preflight(
            _synthetic_partition(native_pending=_native_pending(acknowledged=0)),
            profile="native", boot_id="0000000000000007",
        )
        self.assertTrue(acknowledged_later["recovery_state"]["recovery_complete"])
        module = preflight.build_preflight(
            _synthetic_partition(module_pending=False,
                                 native_pending=_native_pending()),
            profile="module-interp", boot_id="0000000000000007",
        )
        self.assertEqual(module["recovery_state"], {
            "previous_attempt_present": False, "recovery_complete": True
        })
        with self.assertRaises(preflight.PreflightError):
            preflight.build_preflight(
                _synthetic_partition(module_pending=_module_pending(recovery_mask=1)),
                profile="native", boot_id="0000000000000007",
            )

    def test_any_update_pending_bit_blocks_the_preflight(self):
        with self.assertRaises(preflight.PreflightError):
            preflight.build_preflight(
                _synthetic_partition(update_pending=True), profile="native",
                boot_id="0000000000000007",
            )


if __name__ == "__main__":
    unittest.main()
