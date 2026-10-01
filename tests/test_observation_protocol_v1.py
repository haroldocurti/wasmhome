import pathlib
import re
import sys
import unittest
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from observation_protocol_v1 import (
    HEADER,
    HEADER_SIZE,
    AcceptanceStatus,
    Component,
    ControlStatus,
    MessageType,
    Packet,
    ProtocolError,
    RejectionReason,
    Scenario,
    SensorStatus,
    decode,
    encode,
)


ATTEMPT = uuid.UUID("00112233-4455-6677-8899-aabbccddeeff")
BOOT_ID = 0x0102030405060708


def examples():
    return [
        Packet(MessageType.TELEMETRY, ATTEMPT, 1, BOOT_ID, 1000, {
            "sample_id": 7,
            "temperature_mC": 25_250,
            "age_ms": 500,
            "sensor_status": SensorStatus.VALID,
        }),
        Packet(MessageType.CONTROL_REQUEST, ATTEMPT, 2, BOOT_ID, 2000, {
            "sample_id": 42,
            "input_temperature_mC": 30_000,
        }),
        Packet(MessageType.CONTROL_RESULT, ATTEMPT, 12, BOOT_ID, 2100, {
            "sample_id": 42,
            "module_version": 2,
            "requested_gpio": 1,
            "observed_gpio": 1,
            "status": ControlStatus.APPLIED,
        }),
        Packet(MessageType.UPDATE_TRIGGER, ATTEMPT, 1, BOOT_ID, 3000, {
            "target_sequence": 0x0102030405060709,
            "scenario": Scenario.AOT,
            "component": Component.CONTROL,
            "target_version": 2,
        }),
        Packet(MessageType.UPDATE_ACCEPTANCE, ATTEMPT, 13, BOOT_ID, 3100, {
            "request_sequence": 1,
            "target_sequence": 0x0102030405060709,
            "status": AcceptanceStatus.ACCEPTED,
            "reason": RejectionReason.NONE,
        }),
    ]


class ObservationProtocolV1Tests(unittest.TestCase):
    def test_documented_examples_encode_and_decode(self):
        expected_hex = [
            "57484f420101001000112233445566778899aabbccddeeff0000000000000001"
            "010203040506070800000000000003e80000000000000007000062a201f40000",
            "57484f420102000c00112233445566778899aabbccddeeff0000000000000002"
            "010203040506070800000000000007d0000000000000002a00007530",
            "57484f420103000c00112233445566778899aabbccddeeff000000000000000c"
            "01020304050607080000000000000834000000000000002a02010100",
            "57484f420110000c00112233445566778899aabbccddeeff0000000000000001"
            "01020304050607080000000000000bb8010203040506070903010002",
            "57484f420111001400112233445566778899aabbccddeeff000000000000000d"
            "01020304050607080000000000000c1c0000000000000001010203040506070901000000",
        ]
        for packet, vector in zip(examples(), expected_hex, strict=True):
            with self.subTest(message_type=packet.message_type.name):
                datagram = encode(packet)
                self.assertEqual(datagram.hex(), vector)
                self.assertEqual(decode(bytes.fromhex(vector)), packet)

    def test_markdown_vectors_match_reference_codec(self):
        document = (ROOT / "docs" / "protocolo-observacao.md").read_text(encoding="utf-8")
        vector_section = document.split("## Vetores de exemplo\n\n", 1)[1].split("\n## Codec de referência", 1)[0]
        documented = re.findall(r"(?m)^([0-9a-f]+)$", vector_section)
        self.assertEqual(documented, [encode(packet).hex() for packet in examples()])

    def test_rejects_short_header_unknown_version_and_unknown_type(self):
        valid = encode(examples()[0])
        with self.assertRaisesRegex(ProtocolError, "shorter"):
            decode(valid[: HEADER_SIZE - 1])
        with self.assertRaisesRegex(ProtocolError, "version"):
            decode(valid[:4] + b"\x02" + valid[5:])
        with self.assertRaisesRegex(ProtocolError, "unknown message type"):
            decode(valid[:5] + b"\x7f" + valid[6:])

    def test_rejects_bad_magic_lengths_and_oversized_datagrams(self):
        valid = encode(examples()[0])
        with self.assertRaisesRegex(ProtocolError, "magic"):
            decode(b"NOPE" + valid[4:])
        with self.assertRaisesRegex(ProtocolError, "length"):
            decode(valid + b"\x00")
        with self.assertRaisesRegex(ProtocolError, "maximum size"):
            decode(valid + bytes(1200))

    def test_rejects_invalid_enum_reserved_byte_and_temperature_sentinel(self):
        valid = bytearray(encode(examples()[0]))
        valid[-2] = 0xFE
        with self.assertRaisesRegex(ProtocolError, "sensor_status"):
            decode(bytes(valid))
        valid = bytearray(encode(examples()[0]))
        valid[-1] = 0x01
        with self.assertRaisesRegex(ProtocolError, "reserved"):
            decode(bytes(valid))
        no_sample = Packet(MessageType.TELEMETRY, ATTEMPT, 2, BOOT_ID, 4000, {
            "sample_id": 0,
            "temperature_mC": 0,
            "age_ms": 0xFFFF,
            "sensor_status": SensorStatus.NO_VALID_SAMPLE,
        })
        with self.assertRaisesRegex(ProtocolError, "no-sample"):
            encode(no_sample)

    def test_rejects_inconsistent_control_and_acceptance_values(self):
        mismatch = Packet(MessageType.CONTROL_RESULT, ATTEMPT, 14, BOOT_ID, 5000, {
            "sample_id": 42,
            "module_version": 2,
            "requested_gpio": 1,
            "observed_gpio": 1,
            "status": ControlStatus.MISMATCH,
        })
        with self.assertRaisesRegex(ProtocolError, "different binary"):
            encode(mismatch)
        rejected_without_reason = Packet(MessageType.UPDATE_ACCEPTANCE, ATTEMPT, 15, BOOT_ID, 6000, {
            "request_sequence": 2,
            "target_sequence": 9,
            "status": AcceptanceStatus.REJECTED,
            "reason": RejectionReason.NONE,
        })
        with self.assertRaisesRegex(ProtocolError, "requires a rejection"):
            encode(rejected_without_reason)

    def test_rejects_update_messages_without_attempt_id(self):
        trigger = examples()[3]
        missing_id = Packet(**{**trigger.__dict__, "attempt_id": uuid.UUID(int=0)})
        with self.assertRaisesRegex(ProtocolError, "non-nil"):
            encode(missing_id)

    def test_unavailable_component_and_version_remain_decodable_for_rejection(self):
        trigger = examples()[3]
        unsupported = Packet(**{
            **trigger.__dict__,
            "body": {**trigger.body, "component": 9, "target_version": 77},
        })
        self.assertEqual(decode(encode(unsupported)), unsupported)

    def test_well_formed_trigger_can_receive_semantic_rejection(self):
        rejection = Packet(MessageType.UPDATE_ACCEPTANCE, ATTEMPT, 14, BOOT_ID, 4000, {
            "request_sequence": 1,
            "target_sequence": 0x0102030405060709,
            "status": AcceptanceStatus.REJECTED,
            "reason": RejectionReason.UNSUPPORTED_VERSION,
        })
        self.assertEqual(decode(encode(rejection)), rejection)

    def test_header_layout_is_fixed_at_48_bytes(self):
        self.assertEqual(HEADER.size, 48)


if __name__ == "__main__":
    unittest.main()
