"""Run against the actual C library; set WASMHOME_WIRE_LIBRARY after compiling it."""
import ctypes
import os
import pathlib
import random
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from observation_protocol_v1 import ProtocolError, decode, encode
from test_observation_protocol_v1 import examples


@unittest.skipUnless(os.environ.get("WASMHOME_WIRE_LIBRARY"), "compile the C codec and set WASMHOME_WIRE_LIBRARY")
class EmbeddedWireTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = ctypes.CDLL(os.environ["WASMHOME_WIRE_LIBRARY"])
        cls.lib.wh_wire_valid.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        cls.lib.wh_wire_valid.restype = ctypes.c_bool
        cls.lib.wh_wire_encode.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint8,
            ctypes.c_void_p, ctypes.c_uint64, ctypes.c_uint64, ctypes.c_uint64,
            ctypes.c_void_p, ctypes.c_size_t]
        cls.lib.wh_wire_encode.restype = ctypes.c_size_t
        cls.lib.wh_ds18b20_decode.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int32)]
        cls.lib.wh_ds18b20_decode.restype = ctypes.c_bool

    def agrees_with_reference(self, data):
        try:
            decode(data)
            expected = True
        except ProtocolError:
            expected = False
        self.assertEqual(self.lib.wh_wire_valid(data, len(data)), expected, data.hex())

    def test_documented_vectors_and_c_encoder(self):
        for packet in examples():
            data = encode(packet)
            self.agrees_with_reference(data)
            out = ctypes.create_string_buffer(68)
            n = self.lib.wh_wire_encode(out, len(out), packet.message_type, packet.attempt_id.bytes,
                packet.sequence, packet.boot_id, packet.monotonic_us, data[48:], len(data) - 48)
            self.assertEqual(out.raw[:n], data)
            self.assertEqual(self.lib.wh_wire_encode(out, 47, packet.message_type,
                packet.attempt_id.bytes, 1, packet.boot_id, 0, data[48:], len(data) - 48), 0)

    def test_all_single_byte_mutations_and_truncations(self):
        for packet in examples():
            data = encode(packet)
            for n in range(len(data)):
                self.agrees_with_reference(data[:n])
            for i in range(len(data)):
                for value in range(256):
                    self.agrees_with_reference(data[:i] + bytes([value]) + data[i+1:])
            self.agrees_with_reference(data + b"\x00")
            self.agrees_with_reference(data + bytes(1200))

    def test_combined_mutations(self):
        rng = random.Random(8)
        for packet in examples():
            for _ in range(1000):
                data = bytearray(encode(packet))
                for _ in range(rng.randint(2, 8)):
                    data[rng.randrange(len(data))] = rng.randrange(256)
                self.agrees_with_reference(bytes(data))

    @staticmethod
    def scratchpad(raw, config=0x3f):
        data = bytearray((raw & 255, (raw >> 8) & 255, 0, 0, config, 255, 12, 16))
        crc = 0
        for byte in data:
            for _ in range(8):
                mix = (crc ^ byte) & 1
                crc >>= 1
                if mix:
                    crc ^= 0x8c
                byte >>= 1
        return bytes(data + bytes([crc]))

    def test_sensor_signed_values_crc_resolution_and_range(self):
        out = ctypes.c_int32()
        for raw, expected in [(404, 25250), (-164, -10250), (-880, -55000), (2000, 125000)]:
            data = self.scratchpad(raw)
            self.assertTrue(self.lib.wh_ds18b20_decode(data, ctypes.byref(out)))
            self.assertEqual(out.value, expected)
            bad = data[:8] + bytes([data[8] ^ 1])
            self.assertFalse(self.lib.wh_ds18b20_decode(bad, ctypes.byref(out)))
        for data in [self.scratchpad(1360, 0x7f), self.scratchpad(-884), self.scratchpad(2004), bytes(9), bytes([255])*9]:
            self.assertFalse(self.lib.wh_ds18b20_decode(data, ctypes.byref(out)))


if __name__ == "__main__":
    unittest.main()
