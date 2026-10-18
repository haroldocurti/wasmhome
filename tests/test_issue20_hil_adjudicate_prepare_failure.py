import contextlib
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import issue20_hil_adjudicate_prepare_failure as adjudicator
from tools import issue20_hil_orchestrator as hil


def write_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True) + "\n")
    path.chmod(0o600)


class PrepareFailureAdjudicatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "campaign"
        self.root.mkdir(mode=0o700)
        self.orch = self.root / "hil-orchestrator"
        self.orch.mkdir(mode=0o700)
        inputs = self.orch / "inputs"
        inputs.mkdir(mode=0o700)
        claims = self.orch / "claims"
        claims.mkdir(mode=0o700)
        raw = self.root / "raw/slots/B0005-aot"
        raw.mkdir(mode=0o700, parents=True)
        remote_code = raw / "remote-code"
        remote_code.mkdir(mode=0o700)
        self.config = self.root / "transport-config"
        self.config.write_text("pinned-config\n")
        self.config.chmod(0o600)
        self.known_hosts = self.root / "ct_known_hosts"
        self.known_hosts.write_text("ct-host-key\n")
        self.known_hosts.chmod(0o600)
        self.identity = self.root / "ct_identity"
        self.identity.write_text("private-key-placeholder\n")
        self.identity.chmod(0o600)
        self.gateway_identity = self.root / "gateway_identity"
        self.gateway_identity.write_text("gateway-key-placeholder\n")
        self.gateway_identity.chmod(0o600)
        self.gateway_known_hosts = self.root / "gateway_known_hosts"
        self.gateway_known_hosts.write_text("gateway-host-key\n")
        self.gateway_known_hosts.chmod(0o600)

        self.flash_bytes = 1024
        self.baseline_sha = hashlib.sha256(b"\0" * self.flash_bytes).hexdigest()
        baseline = inputs / "baseline.bin"
        baseline.write_bytes(b"\0" * self.flash_bytes)
        baseline.chmod(0o600)

        source_hashes = {}
        for name in ("remote_observer.py", "external_observer.py",
                     "observation_protocol_v1.py", "update_campaign.py"):
            source = remote_code / name
            source.write_text("# " + name + "\n")
            source.chmod(0o600)
            source_hashes[name] = adjudicator._sha256(source)

        session_id = "76078e53-b918-44a7-be69-5eb7b7c9c186"
        session = {
            "schema": "wasmhome-remote-observer-v1",
            "session_id": session_id,
            "config": {"context": {"campaign_id": "campaign-test", "slot_id": "B0005-aot",
                                    "git_commit": "c" * 40}},
            "source_hashes": source_hashes,
        }
        session_path = raw / "remote-session.json"
        write_json(session_path, session)
        device = raw / "device.jsonl"
        device.write_text('{"event":"test"}\n')
        device.chmod(0o600)

        terminal = {
            "kind": "capture_failed",
            "automatic_retry_permitted": False,
            "error": "RemoteObserverError: SSH prepare failed (255): Connection timed out\n",
            "raw_files": {
                "device.jsonl": {"size_bytes": device.stat().st_size,
                                 "sha256": adjudicator._sha256(device)},
                "remote-session.json": {"size_bytes": session_path.stat().st_size,
                                        "sha256": adjudicator._sha256(session_path)},
            },
        }
        write_json(raw / "terminal-error.json", terminal)

        probe_dir = self.orch / "transport-review-B0005-aot"
        probe_dir.mkdir(mode=0o700)
        probe = {
            "schema": "issue20-remote-prepare-status-probe-v1",
            "slot_id": "B0005-aot",
            "session_id_sha256": hashlib.sha256(session_id.encode()).hexdigest(),
            "remote_receipt_sha256": adjudicator._sha256(session_path),
            "transport_config_sha256": adjudicator._sha256(self.config),
            "ct_known_hosts_sha256": adjudicator._sha256(self.known_hosts),
            "observation": {"result": "remote_session_absent",
                            "error_class": "RemoteObserverError",
                            "error_marker": "remote session does not exist"},
            "queried_at_utc": "2026-10-17T23:58:35.914531+00:00",
        }
        write_json(probe_dir / "remote-status-probe.json", probe)

        operation = self.orch / ("operation-" + "a" * 32)
        operation.mkdir(mode=0o700)
        readback = operation / "B0005-aot-after.bin"
        readback.write_bytes(b"\0" * self.flash_bytes)
        readback.chmod(0o600)

        slots = [{"block_id": f"B{i:04d}", "order_in_block": 0,
                  "scenario": "native", "slot_id": f"B{i:04d}-native"}
                 for i in range(1, 13)]
        failed = {"block_id": "B0005", "order_in_block": 1,
                  "scenario": "aot", "slot_id": "B0005-aot"}
        next_slot = {"block_id": "B0005", "order_in_block": 2,
                     "scenario": "native", "slot_id": "B0005-native"}
        slots.extend((failed, next_slot))
        claim_path = claims / "B0005-aot.json"
        write_json(claim_path, {"slot": failed, "retry_permitted": False})
        self.journal_path = self.orch / "journal.json"
        self.journal = {
            "campaign_id": "campaign-test", "git_commit": "c" * 40,
            "state": "slot_failed", "restore_required": False, "next_index": 13,
            "slots": slots, "current_slot": "B0005-aot",
            "claim_intent": {"slot": failed, "retry_permitted": False},
            "events": [{"slot_id": "B0005-aot", "state": "slot_failed",
                        "error": "RemoteObserverError"}],
            "baseline": str(baseline),
            "options": {"remote_observer": "wasmhome@192.168.0.138",
                        "remote_known_hosts": str(self.known_hosts),
                        "remote_identity": str(self.identity),
                        "remote_root": ".local/share/wasmhome-hil/campaign-test",
                        "remote_ssh_port": 22},
        }
        write_json(self.journal_path, self.journal)
        self.batch_path = self.orch / "batch.json"
        self.tools = hil.tooling_snapshot()
        write_json(self.batch_path, {
            "state": "blocked", "restore_required": False, "next_index": 13,
            "tooling_sha256": self.tools,
            "events": [{"before_index": 12, "after_index": 13, "exit_code": 1}],
        })
        self.patchers = [
            patch.object(adjudicator, "FLASH_BYTES", self.flash_bytes),
            patch.object(adjudicator, "BASELINE_SHA256", self.baseline_sha),
            patch.object(adjudicator, "TRANSPORT_CONFIG", self.config),
            patch.object(adjudicator, "_authenticated_status_probe", return_value={
                "path": str(probe_dir / "authenticated-status-adjudication.json"),
                "sha256": "d" * 64, "queried_at_utc": "2026-10-18T00:00:00+00:00",
                "ssh_returncode": 1, "session_id_sha256": "e" * 64,
                "remote_session_sha256": adjudicator._sha256(session_path),
                "ct_known_hosts_sha256": adjudicator._sha256(self.known_hosts),
                "transport_config_sha256": adjudicator._sha256(self.config),
                "gateway": {"target": "root@100.89.142.40",
                            "identity_sha256": adjudicator._sha256(self.gateway_identity),
                            "known_hosts_sha256": adjudicator._sha256(self.gateway_known_hosts)},
            }),
            patch.object(hil, "adjudication_lock", lambda _root: contextlib.nullcontext()),
        ]
        for item in self.patchers:
            item.start()
            self.addCleanup(item.stop)

    def test_adjudicates_only_the_absent_pre_start_session_and_keeps_index(self):
        result = adjudicator.adjudicate(self.root)
        self.assertEqual(result["next_index"], 13)
        self.assertEqual(result["next_slot"], "B0005-native")
        self.assertFalse(result["accepted"])
        self.assertFalse(result["retry_permitted"])
        journal = json.loads(self.journal_path.read_text())
        self.assertEqual(journal["state"], "ready")
        self.assertEqual(journal["next_index"], 13)
        self.assertEqual(journal["events"][-1]["state"],
                         "terminal_prepare_timeout_adjudicated")
        self.assertTrue(Path(result["receipt"]).is_file())
        self.assertEqual((self.root / "raw/slots/B0005-aot/terminal-error.json").stat().st_mode & 0o777,
                         0o600)

    def test_rejects_probe_that_does_not_prove_remote_session_absent(self):
        probe_path = self.orch / "transport-review-B0005-aot/remote-status-probe.json"
        probe = json.loads(probe_path.read_text())
        probe["observation"]["result"] = "running"
        write_json(probe_path, probe)
        with self.assertRaisesRegex(hil.HilError, "does not prove"):
            adjudicator.adjudicate(self.root)
        self.assertEqual(json.loads(self.journal_path.read_text())["state"], "slot_failed")

    def test_rejects_changed_raw_capture(self):
        raw = self.root / "raw/slots/B0005-aot/device.jsonl"
        raw.write_text("tampered\n")
        raw.chmod(0o600)
        with self.assertRaisesRegex(hil.HilError, "raw evidence changed"):
            adjudicator.adjudicate(self.root)

    def test_rejects_nonmatching_restore_readback(self):
        readback = self.orch / ("operation-" + "a" * 32) / "B0005-aot-after.bin"
        readback.write_bytes(b"\x01" * self.flash_bytes)
        readback.chmod(0o600)
        with self.assertRaisesRegex(hil.HilError, "differs from baseline"):
            adjudicator.adjudicate(self.root)

    def test_rejects_next_slot_already_claimed(self):
        write_json(self.orch / "claims/B0005-native.json", {"claimed": True})
        with self.assertRaisesRegex(hil.HilError, "already claimed"):
            adjudicator.adjudicate(self.root)

    def test_rejects_changed_batch_tooling_snapshot(self):
        batch = json.loads(self.batch_path.read_text())
        batch["tooling_sha256"]["issue20_hil_batch.py"] = "0" * 64
        write_json(self.batch_path, batch)
        with self.assertRaisesRegex(hil.HilError, "frozen tooling"):
            adjudicator.adjudicate(self.root)

    def test_recovers_if_receipt_is_durable_but_journal_write_was_interrupted(self):
        original_write = hil.write_private

        def fail_journal_once(path, value, **kwargs):
            if Path(path) == self.journal_path:
                raise OSError("simulated journal write interruption")
            return original_write(path, value, **kwargs)

        with patch.object(hil, "write_private", side_effect=fail_journal_once):
            with self.assertRaisesRegex(OSError, "interruption"):
                adjudicator.adjudicate(self.root)
        self.assertTrue((self.orch / "adjudications/B0005-aot-prepare-timeout.json").is_file())
        self.assertEqual(json.loads(self.journal_path.read_text())["state"], "slot_failed")
        result = adjudicator.adjudicate(self.root)
        self.assertEqual(result["state"], "ready")
        self.assertEqual(json.loads(self.journal_path.read_text())["next_index"], 13)


if __name__ == "__main__":
    unittest.main()
