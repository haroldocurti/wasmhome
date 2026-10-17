import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.remote_observer import (
    LEASE_NS,
    SERIAL_MAX_SILENCE_NS,
    RemoteObserverError,
    _consume_challenge,
    _guard,
    _issue_challenge,
    _lease,
)


class RemoteObserverChallengeTests(unittest.TestCase):
    def test_lease_deadline_is_anchored_before_uart_sample(self):
        created_ns = 10_000_000_000
        age_ns = 300_000_000
        accepted_ns = created_ns + 400_000_000
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            with patch("tools.remote_observer.time.monotonic_ns", return_value=created_ns):
                challenge = _issue_challenge(directory)
            self.assertEqual(challenge["created_ns"], created_ns)
            consumed_created_ns, _ = _consume_challenge(
                directory, challenge["challenge_id"], age_ns, now_ns=accepted_ns)
            self.assertEqual(consumed_created_ns, created_ns)

            lease_write_ns = accepted_ns + 10_000_000
            _lease(directory, challenge_created_ns=created_ns,
                   serial_age_ns=age_ns, now_ns=lease_write_ns)
            lease = json.loads((directory / "lease.json").read_text())
            expected_expiry = created_ns + SERIAL_MAX_SILENCE_NS - age_ns
            self.assertEqual(lease["serial_fresh_until_ns"], expected_expiry)
            self.assertEqual(lease["expires_ns"], expected_expiry)
            self.assertLess(lease["expires_ns"],
                            lease_write_ns + SERIAL_MAX_SILENCE_NS - age_ns)
            self.assertLessEqual(lease["expires_ns"], created_ns + LEASE_NS)

            with patch("tools.remote_observer.time.monotonic_ns", return_value=expected_expiry - 1):
                self.assertIsNone(_guard(directory))
            with patch("tools.remote_observer.time.monotonic_ns", return_value=expected_expiry):
                self.assertEqual(_guard(directory), "laptop_uart_freshness_lease_expired")

    def test_challenge_rtt_plus_uart_age_must_be_strictly_under_two_seconds(self):
        created_ns = 20_000_000_000
        age_ns = 1_250_000_000
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            with patch("tools.remote_observer.time.monotonic_ns", return_value=created_ns):
                challenge = _issue_challenge(directory)
            with self.assertRaisesRegex(RemoteObserverError, "challenge RTT is expired"):
                _consume_challenge(directory, challenge["challenge_id"], age_ns,
                                   now_ns=created_ns + 750_000_000)

            # One nanosecond under the limit is admissible, and still leaves a
            # strictly positive deadline on the CT's monotonic clock.
            created_ns2 = created_ns + 10_000_000_000
            with patch("tools.remote_observer.time.monotonic_ns", return_value=created_ns2):
                challenge2 = _issue_challenge(directory)
            accepted_ns = created_ns2 + 750_000_000 - 1
            consumed_created_ns, _ = _consume_challenge(
                directory, challenge2["challenge_id"], age_ns, now_ns=accepted_ns)
            _lease(directory, challenge_created_ns=consumed_created_ns,
                   serial_age_ns=age_ns, now_ns=accepted_ns)
            lease = json.loads((directory / "lease.json").read_text())
            self.assertEqual(lease["expires_ns"], accepted_ns + 1)

    def test_expired_challenge_is_rejected_using_ct_monotonic_time(self):
        created_ns = 30_000_000_000
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            with patch("tools.remote_observer.time.monotonic_ns", return_value=created_ns):
                challenge = _issue_challenge(directory)
            with self.assertRaisesRegex(RemoteObserverError, "challenge RTT is expired"):
                _consume_challenge(directory, challenge["challenge_id"], 0,
                                   now_ns=created_ns + SERIAL_MAX_SILENCE_NS)

    def test_challenge_cannot_be_replayed_or_reused_after_replacement(self):
        created_ns = 40_000_000_000
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            with patch("tools.remote_observer.time.monotonic_ns", return_value=created_ns):
                original = _issue_challenge(directory)
            _consume_challenge(directory, original["challenge_id"], 100_000_000,
                               now_ns=created_ns + 100_000_000)
            with self.assertRaisesRegex(RemoteObserverError, "already consumed"):
                _consume_challenge(directory, original["challenge_id"], 100_000_000,
                                   now_ns=created_ns + 200_000_000)

            with patch("tools.remote_observer.time.monotonic_ns", return_value=created_ns + 300_000_000):
                replacement = _issue_challenge(directory)
            self.assertNotEqual(original["challenge_id"], replacement["challenge_id"])
            with self.assertRaisesRegex(RemoteObserverError, "superseded"):
                _consume_challenge(directory, original["challenge_id"], 100_000_000,
                                   now_ns=created_ns + 400_000_000)

    def test_invalid_uart_age_cannot_create_lease(self):
        created_ns = 50_000_000_000
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            for age_ns in (-1, SERIAL_MAX_SILENCE_NS):
                with self.subTest(age_ns=age_ns):
                    with patch("tools.remote_observer.time.monotonic_ns", return_value=created_ns):
                        challenge = _issue_challenge(directory)
                    with self.assertRaisesRegex(RemoteObserverError, "fresh UART byte"):
                        _consume_challenge(directory, challenge["challenge_id"], age_ns,
                                           now_ns=created_ns + 1)
                    self.assertFalse((directory / "lease.json").exists())


if __name__ == "__main__":
    unittest.main()
