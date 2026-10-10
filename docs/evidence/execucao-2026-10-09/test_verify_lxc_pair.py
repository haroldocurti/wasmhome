import copy
import importlib.util
from pathlib import Path
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("session_lxc_pair", Path(__file__).with_name("verify-lxc-pair.py"))
pair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pair)


class PairingGates(unittest.TestCase):
    def test_actual_positive_roundtrip_and_controlled_negative(self):
        result = pair.verify()
        self.assertTrue(result["positive"]["transport_round_trip_verified"])
        self.assertFalse(result["controlled_negative"]["transaction_nonce_paired"])
        self.assertFalse(result["controlled_negative"]["reader_absence_proven"])

    def test_different_nonce_cannot_pair(self):
        client, _ = pair.capture_case("lxc-positive-r2")
        server = pair.server_case("lxc-positive-r2-server.jsonl", pair.capture.DEVICE, "response_sent")
        server["nonce"] = "a" * 64
        with self.assertRaisesRegex(ValueError, "nonce"):
            pair.pair_positive(client, server)

    def test_udp_claimed_sequence_must_match_encoded_raw_packet(self):
        records = copy.deepcopy(pair.jsonlines(pair.SESSION / "lxc-positive-r2/observations.jsonl"))
        next(r for r in records if r["kind"] == "udp_received")["value"]["sequence"] += 1
        with mock.patch.object(pair, "jsonlines", return_value=records):
            with self.assertRaisesRegex(ValueError, "decoded UDP"):
                pair.capture_case("lxc-positive-r2")

    def test_multiple_server_requests_cannot_be_treated_as_isolated_pair(self):
        rows = pair.jsonlines(pair.SESSION / "lxc-positive-r2-server.jsonl")
        rows.insert(2, copy.deepcopy(rows[1]))
        with mock.patch.object(pair, "jsonlines", return_value=rows):
            with self.assertRaisesRegex(ValueError, "isolated server request"):
                pair.server_case("lxc-positive-r2-server.jsonl", pair.capture.DEVICE, "response_sent")

    def test_original_clock_tie_capture_is_rejected_even_with_matching_server_nonce(self):
        client, _ = pair.capture_case("lxc-positive")
        server = pair.server_case("lxc-positive-server.jsonl", pair.capture.DEVICE, "response_sent")
        with self.assertRaisesRegex(ValueError, "continuity rejected"):
            pair.pair_positive(client, server)

    def test_server_clock_is_not_used_to_order_client_capture(self):
        client, _ = pair.capture_case("lxc-positive-r2")
        server = pair.server_case("lxc-positive-r2-server.jsonl", pair.capture.DEVICE, "response_sent")
        server["utc"] = "server clock unavailable"
        self.assertTrue(pair.pair_positive(client, server)["transport_round_trip_verified"])


if __name__ == "__main__":
    unittest.main()
