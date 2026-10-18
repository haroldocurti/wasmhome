import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools import ct107_user_preflight as preflight


class Ct107PreflightTests(unittest.TestCase):
    def setUp(self):
        self.options = {"target": "wasmhome@ct107", "known_hosts": "/tmp/known_hosts",
                        "identity": "/tmp/identity", "ssh_port": 22,
                        "root": ".local/share/wasmhome-hil"}
        self.validate = patch.object(preflight.remote_observer, "validate_options",
                                     side_effect=lambda value: value)
        self.validate.start()
        self.addCleanup(self.validate.stop)

    def response(self, *, port=8446, returncode=0):
        return SimpleNamespace(returncode=returncode, stdout=json.dumps({
            "normal_user": True, "pinned_ssh": True, "campaign_server": True,
            "server_log": True, "listener_reachable": True, "server_port": port,
        }).encode())

    def test_preflight_passes_frozen_https_port_to_remote_probe(self):
        with patch.object(preflight.subprocess, "run", return_value=self.response()) as run:
            result = preflight.verify_server(self.options, bind_ip="192.168.0.138",
                server_log="/home/wasmhome/hil/events.jsonl", campaign_id="artifact-campaign",
                expected_port=8446)
        self.assertEqual(result["server_port"], 8446)
        remote_command = run.call_args.args[0][-1]
        self.assertIn("8446", remote_command)
        self.assertIn("port != int(expected_port)", preflight.REMOTE_PROBE)

    def test_preflight_rejects_server_reporting_a_different_https_port(self):
        with patch.object(preflight.subprocess, "run", return_value=self.response(port=8447)):
            with self.assertRaisesRegex(preflight.PreflightError, "invalid readiness"):
                preflight.verify_server(self.options, bind_ip="192.168.0.138",
                    server_log="/home/wasmhome/hil/events.jsonl", campaign_id="artifact-campaign",
                    expected_port=8446)

    def test_preflight_rejects_when_remote_probe_finds_wrong_port(self):
        with patch.object(preflight.subprocess, "run", return_value=self.response(returncode=2)):
            with self.assertRaisesRegex(preflight.PreflightError, "server, campaign, log"):
                preflight.verify_server(self.options, bind_ip="192.168.0.138",
                    server_log="/home/wasmhome/hil/events.jsonl", campaign_id="artifact-campaign",
                    expected_port=8446)


if __name__ == "__main__":
    unittest.main()
