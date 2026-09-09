import csv
import io
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from collect_results import FIELDS, parse_log, validate


class ResultContractTests(unittest.TestCase):
    def test_parser_ignores_monitor_prefix(self):
        values = ["native", "1", "0", "0", "12", "100", "100", "100", "90", "0xDBB2F0D4", "ok"]
        rows = parse_log(["I (42) app: WASMHOME_CSV," + ",".join(values)])
        self.assertEqual(rows[0]["scenario"], "native")
        self.assertEqual(rows[0]["checksum"], "0xDBB2F0D4")

    def test_validator_requires_thirty_runs(self):
        row = dict(zip(FIELDS, ["native", "1", "0", "0", "12", "100", "100", "100", "90", "0xDBB2F0D4", "ok"]))
        with self.assertRaisesRegex(ValueError, "1..30"):
            validate([row])

    def test_workload_checksum_matches_contract(self):
        state = 0xC001D00D
        signal = 0x12345678
        mask = 0xFFFFFFFF
        for i in range(2000):
            signal = (signal * 1103515245 + 12345) & mask
            state ^= (signal ^ i) + 0x9E3779B9 + ((state << 6) & mask) + (state >> 2)
            state &= mask
            state = ((state << 7) | (state >> 25)) & mask
            state = (state * 1664525 + 1013904223) & mask
        self.assertEqual(state, 0xDBB2F0D4)


if __name__ == "__main__":
    unittest.main()
