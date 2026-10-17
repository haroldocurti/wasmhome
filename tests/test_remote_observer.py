import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.remote_observer import (
    SERIAL_MAX_SILENCE_NS,
    SERIAL_SAMPLE_MARGIN_NS,
    _guard,
    _lease,
)


class RemoteObserverLeaseTests(unittest.TestCase):
    def test_uart_age_clips_remote_lease_and_expiry_aborts(self):
        now_ns = 10_000_000_000
        age_ns = 400_000_000
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            with patch("tools.remote_observer.time.monotonic_ns", return_value=now_ns):
                _lease(directory, serial_age_ns=age_ns)
            lease = json.loads((directory / "lease.json").read_text())
            expected_expiry = now_ns + SERIAL_MAX_SILENCE_NS - age_ns - SERIAL_SAMPLE_MARGIN_NS
            self.assertEqual(lease["serial_fresh_until_ns"], expected_expiry)
            self.assertEqual(lease["expires_ns"], expected_expiry)

            with patch("tools.remote_observer.time.monotonic_ns", return_value=expected_expiry - 1):
                self.assertIsNone(_guard(directory))
            with patch("tools.remote_observer.time.monotonic_ns", return_value=expected_expiry):
                self.assertEqual(_guard(directory), "laptop_uart_freshness_lease_expired")


if __name__ == "__main__":
    unittest.main()
