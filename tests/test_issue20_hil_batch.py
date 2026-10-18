import json
from pathlib import Path
import signal
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools import issue20_hil_batch as batch


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.receipts = batch.hil.private_directory(self.root / "hil-orchestrator")
        fields = ("profiles", "baseline", "expected_mac", "device_ip", "serial_port", "python", "esptool",
                  "flash_baud", "read_timeout_seconds", "serial_baud", "bind_ip", "remote_observer",
                  "remote_known_hosts", "remote_identity", "remote_root", "remote_ssh_port", "remote_server_log")
        self.journal = {"state": "ready", "restore_required": False, "next_index": 0,
                        "slots": [{"slot_id": "slot-1"}, {"slot_id": "slot-2"}],
                        "options": {key: "/private/fixture" for key in fields}}
        self.journal["options"]["campaign_root"] = str(self.root)
        self.store()
        self.addCleanup(patch.stopall)
        patch.object(batch.os, "geteuid", return_value=1000).start()

    def store(self):
        batch.hil.write_private(self.receipts / "journal.json", self.journal)

    def state(self):
        return json.loads((self.receipts / "batch.json").read_text())

    def advance(self, command, log, state, status_path):
        self.assertEqual(command[-2:], ["--max-slots", "1"])
        self.assertEqual(command[2], "run")
        amount = 2 if self.journal["state"] == "recovered_interruption" else 1
        self.journal.update(next_index=min(self.journal["next_index"] + amount, len(self.journal["slots"])),
                            state="complete")
        self.store()
        return 0

    def test_batch_uses_one_slot_per_child_and_stops_at_schedule_end(self):
        child = Mock(side_effect=self.advance)
        self.assertEqual(batch.run_batch(self.root, run_child=child), 0)
        self.assertEqual(child.call_count, 2)
        self.assertEqual(self.state()["state"], "complete")
        self.assertEqual(self.state()["next_index"], 2)
        self.assertEqual(self.state()["tooling_sha256"], batch.hil.tooling_snapshot())

    def test_recovered_claim_plus_one_new_slot_is_valid_single_child_progress(self):
        self.journal["state"] = "recovered_interruption"
        self.store()
        child = Mock(side_effect=self.advance)
        self.assertEqual(batch.run_batch(self.root, run_child=child), 0)
        self.assertEqual(child.call_count, 1)

    def test_failure_stops_without_retry_and_keeps_restore_requirement(self):
        def fail(*args):
            self.journal.update(state="restore_failed", restore_required=True)
            self.store()
            return 1
        child = Mock(side_effect=fail)
        self.assertEqual(batch.run_batch(self.root, run_child=child), 1)
        self.assertEqual(child.call_count, 1)
        self.assertEqual(self.state()["state"], "blocked")
        self.assertTrue(self.state()["restore_required"])

    def test_unresolved_recovery_and_zero_progress_do_not_launch_another_child(self):
        self.journal.update(state="restoring", restore_required=True)
        self.store()
        child = Mock(return_value=0)
        self.assertEqual(batch.run_batch(self.root, run_child=child), 1)
        child.assert_not_called()
        self.journal.update(state="ready", restore_required=False)
        self.store()
        self.assertEqual(batch.run_batch(self.root, run_child=child), 1)
        self.assertEqual(child.call_count, 1)
        self.assertTrue(list(self.receipts.glob("batch-history-*.json")))

    def test_tooling_change_blocks_before_next_child(self):
        child = Mock(side_effect=self.advance)
        with patch.object(batch.hil, "tooling_snapshot", side_effect=[{"sha": "a"}, {"sha": "a"}, {"sha": "b"}]):
            self.assertEqual(batch.run_batch(self.root, run_child=child), 1)
        self.assertEqual(child.call_count, 1)

    def test_batch_lock_and_live_previous_child_prevent_competing_supervisor(self):
        child = Mock()
        with batch._lock(self.receipts):
            with self.assertRaisesRegex(batch.hil.HilError, "another batch"):
                batch.run_batch(self.root, run_child=child)
        identity = {"pid": 98765, "start_ticks": "1", "host_boot_id": "fixture"}
        batch.hil.write_private(self.receipts / "batch.json", {"child_process": identity})
        with patch.object(batch, "_identity", return_value=identity):
            with self.assertRaisesRegex(batch.hil.HilError, "still alive"):
                batch.run_batch(self.root, run_child=child)
        child.assert_not_called()

    def test_background_start_detaches_without_exposing_frozen_options(self):
        process = Mock(pid=98765)
        process.poll.return_value = None
        with patch.object(batch.subprocess, "Popen", return_value=process) as launch, \
             patch.object(batch.time, "sleep"):
            result = batch.start_background(self.root)
        self.assertEqual(result, {"state": "starting", "supervisor_pid": 98765})
        self.assertTrue(launch.call_args.kwargs["start_new_session"])
        self.assertEqual(launch.call_args.args[0][-2:], ["--campaign-root", str(self.root)])
        self.assertEqual((self.receipts / "batch-supervisor.log").stat().st_mode & 0o777, 0o600)

    def test_sigterm_and_sigint_forward_to_child_process_group_and_record_stopping(self):
        for sig in (signal.SIGTERM, signal.SIGINT):
            with self.subTest(signal=sig):
                state = {"state": "running", "child_process": None}
                status = self.receipts / "signal-status.json"
                previous = batch._install_stop_handlers(state, status)
                process = Mock(pid=98765)
                def terminate():
                    signal.getsignal(sig)(sig, None)
                    return -sig
                process.wait.side_effect = terminate
                try:
                    with patch.object(batch.subprocess, "Popen", return_value=process), \
                         patch.object(batch, "_identity", return_value={"pid": process.pid}), \
                         patch.object(batch.os, "killpg") as forward:
                        code = batch._run_child(["fixture"], self.receipts / f"signal-{sig}.log", state, status)
                    self.assertEqual(code, -sig)
                    forward.assert_called_once_with(process.pid, sig)
                    self.assertEqual(state["state"], "stopping")
                    self.assertEqual(json.loads(status.read_text())["received_signal"], sig)
                finally:
                    for number, handler in previous.items():
                        signal.signal(number, handler)

    def test_signal_during_child_launch_is_forwarded_once_group_exists(self):
        state = {"state": "running", "child_process": None}
        status = self.receipts / "launch-signal.json"
        previous = batch._install_stop_handlers(state, status)
        process = Mock(pid=98765)
        process.wait.return_value = -signal.SIGTERM
        def spawn(*args, **kwargs):
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
            return process
        try:
            with patch.object(batch.subprocess, "Popen", side_effect=spawn), \
                 patch.object(batch, "_identity", return_value={"pid": process.pid}), \
                 patch.object(batch.os, "killpg") as forward:
                batch._run_child(["fixture"], self.receipts / "launch-signal.log", state, status)
            forward.assert_called_once_with(process.pid, signal.SIGTERM)
            self.assertFalse(state["launching_child"])
        finally:
            for number, handler in previous.items():
                signal.signal(number, handler)

    def test_received_signal_prevents_next_child_even_if_current_child_returns_success(self):
        def interrupted(*args):
            args[2]["received_signal"] = signal.SIGINT
            return self.advance(*args)
        child = Mock(side_effect=interrupted)
        self.assertEqual(batch.run_batch(self.root, run_child=child), 1)
        self.assertEqual(child.call_count, 1)
        self.assertEqual(self.state()["state"], "blocked")
        self.assertEqual(batch.batch_status(self.root)["stop_signal"], "SIGINT")

    def test_signal_without_active_child_stops_without_launching_one(self):
        state = {"state": "running"}
        status = self.receipts / "idle-signal.json"
        previous = batch._install_stop_handlers(state, status)
        try:
            with self.assertRaises(InterruptedError):
                signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
            self.assertEqual(state["state"], "stopping")
        finally:
            for number, handler in previous.items():
                signal.signal(number, handler)


if __name__ == "__main__":
    unittest.main()
