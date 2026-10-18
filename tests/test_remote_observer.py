import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import stat
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import uuid

from tools import remote_observer as remote

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


class RemoteObserverTransportTests(unittest.TestCase):
    def setUp(self):
        self.options = {"target": "wasmhome@offline.invalid", "identity": "/offline/key",
                        "known_hosts": "/offline/known_hosts", "root": "private-hil"}
        self.process = Mock()
        self.process.poll.return_value = None
        self.process.wait.return_value = 0
        self.commands = []
        self.actions = []
        self.folder = None
        self.popen = patch.object(remote.subprocess, "Popen", side_effect=self.spawn).start()
        self.run = patch.object(remote.subprocess, "run", side_effect=self.reply).start()
        self.addCleanup(patch.stopall)

    def spawn(self, command, **kwargs):
        self.master_command = command
        path = Path(command[command.index("-S") + 1])
        self.folder = path.parent
        self.assertEqual(stat.S_IMODE(self.folder.stat().st_mode), 0o700)
        self.assertLess(len(os.fsencode(path)), 100)
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        path.touch()  # Offline stand-in; the mocked -O check supplies readiness.
        return self.process

    def reply(self, command, **kwargs):
        self.commands.append(command)
        if "-O" in command:
            return subprocess.CompletedProcess(command, 0, b"", b"")
        request = json.loads(kwargs["input"])
        self.actions.append(request["action"])
        response = {"schema": remote.SCHEMA, "session_id": request["session_id"],
                    "status": {"state": "running"}}
        return subprocess.CompletedProcess(command, 0, json.dumps(response).encode(), b"")

    def test_challenge_and_heartbeat_reuse_one_private_master(self):
        with remote._session_transport(self.options) as transport:
            for action in ("prepare", "challenge", "start", "challenge", "heartbeat"):
                remote._rpc(self.options, "offline-session", action)
            socket_path = str(transport["socket"])
            self.assertEqual(self.popen.call_count, 1)
            self.assertTrue(all(c[c.index("-S") + 1] == socket_path for c in self.commands))
            for command in [self.master_command, *self.commands]:
                self.assertIn("StrictHostKeyChecking=yes", command)
                self.assertIn("UserKnownHostsFile=/offline/known_hosts", command)
                self.assertIn("IdentityAgent=none", command)
                self.assertEqual(command[command.index("-i") + 1], "/offline/key")
            for command in self.commands:
                self.assertIn("ControlMaster=no", command)
                self.assertIn("ProxyCommand=/bin/false", command)
                self.assertIn("ProxyJump=none", command)
        self.process.terminate.assert_called_once()
        self.process.wait.assert_called_once_with(timeout=2)
        self.assertFalse(self.folder.exists())
        self.assertNotIn("_transport", self.options)

    def test_master_start_failure_removes_socket_and_reaps_process(self):
        self.process.poll.return_value = 255
        with self.assertRaisesRegex(RemoteObserverError, "exited before readiness"):
            with remote._session_transport(self.options):
                self.fail("must not enter failed transport")
        self.assertEqual(self.run.call_count, 0)
        self.process.wait.assert_called_once()
        self.assertFalse(self.folder.exists())

    def test_master_readiness_timeout_cleans_up(self):
        self.run.side_effect = subprocess.TimeoutExpired("offline readiness", 1)
        with self.assertRaisesRegex(RemoteObserverError, "did not complete"):
            with remote._session_transport(self.options):
                self.fail("must not enter failed transport")
        self.process.terminate.assert_called_once()
        self.process.wait.assert_called_once()
        self.assertFalse(self.folder.exists())

    def test_interruption_cleans_master_and_next_session_has_a_new_socket(self):
        with self.assertRaises(KeyboardInterrupt):
            with remote._session_transport(self.options) as transport:
                first = transport["socket"]
                raise KeyboardInterrupt
        self.assertFalse(first.parent.exists())
        self.process.terminate.assert_called_once()
        with remote._session_transport(self.options) as transport:
            self.assertNotEqual(first, transport["socket"])
        self.assertEqual(self.popen.call_count, 2)

    def test_master_loss_never_opens_another_connection(self):
        with remote._session_transport(self.options):
            count = self.run.call_count
            self.process.poll.return_value = 255
            with self.assertRaisesRegex(RemoteObserverError, "reconnect is forbidden"):
                remote._rpc(self.options, "offline-session", "heartbeat")
            self.assertEqual(self.run.call_count, count)
        self.assertEqual(self.popen.call_count, 1)
        self.assertFalse(self.folder.exists())

    def test_rpc_failure_permanently_disables_transport_even_if_master_lives(self):
        with remote._session_transport(self.options):
            self.run.return_value = subprocess.CompletedProcess([], 255, b"", b"lost socket")
            self.run.side_effect = None
            with self.assertRaisesRegex(RemoteObserverError, "SSH heartbeat failed"):
                remote._rpc(self.options, "offline-session", "heartbeat")
            count = self.run.call_count
            self.run.side_effect = self.reply
            with self.assertRaisesRegex(RemoteObserverError, "reconnect is forbidden"):
                remote._rpc(self.options, "offline-session", "challenge")
            self.assertEqual(self.run.call_count, count)
        self.assertEqual(self.popen.call_count, 1)

    def test_rpc_timeout_is_permanent_and_cleanup_escalates_to_kill(self):
        with self.assertRaisesRegex(RemoteObserverError, "did not complete"):
            with remote._session_transport(self.options) as transport:
                self.run.side_effect = subprocess.TimeoutExpired("offline heartbeat", 5)
                self.process.wait.side_effect = [subprocess.TimeoutExpired("master", 2), 0]
                try:
                    remote._rpc(self.options, "offline-session", "heartbeat")
                finally:
                    self.assertTrue(transport["failed"])
        self.process.terminate.assert_called_once()
        self.process.kill.assert_called_once()
        self.assertFalse(self.folder.exists())

    def test_exit_between_poll_and_terminate_still_reaps_master(self):
        self.process.terminate.side_effect = ProcessLookupError
        with remote._session_transport(self.options):
            pass
        self.process.wait.assert_called_once_with(timeout=2)
        self.process.kill.assert_not_called()
        self.assertFalse(self.folder.exists())

    def test_exit_before_kill_still_reaps_master(self):
        self.process.wait.side_effect = [subprocess.TimeoutExpired("master", 2), 0]
        self.process.kill.side_effect = ProcessLookupError
        with remote._session_transport(self.options):
            pass
        self.assertEqual(self.process.wait.call_count, 2)
        self.process.kill.assert_called_once()
        self.assertFalse(self.folder.exists())

    def test_post_kill_timeout_reports_incomplete_cleanup_and_removes_socket(self):
        self.process.wait.side_effect = subprocess.TimeoutExpired("master", 2)
        with self.assertRaisesRegex(RemoteObserverError, "could not be reaped after kill"):
            with remote._session_transport(self.options):
                pass
        self.assertEqual(self.process.wait.call_count, 2)
        self.process.kill.assert_called_once()
        self.assertFalse(self.folder.exists())
        self.assertNotIn("_transport", self.options)

    def test_terminal_challenge_race_archives_without_renewal_or_false_error(self):
        session = str(uuid.uuid4())
        self.options["session_id"] = session
        nonce = str(uuid.uuid4())
        def response(state, **extra):
            return {"schema": remote.SCHEMA, "session_id": session,
                    "status": {"state": state}, **extra}
        replies = [response("prepared"), response("prepared", challenge_id=nonce),
                   response("running"), response("complete", manifest={})]
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(remote, "validate_options", side_effect=lambda o: o), \
                patch.object(remote, "_validate_observer"), \
                patch.object(remote, "_rpc", side_effect=replies) as rpc, \
                patch.object(remote, "_archive", return_value={"state": "complete"}) as archive:
            result = remote.run_remote_observer(folder, {}, self.options, context={},
                                                health_check=lambda: None,
                                                serial_age_check=lambda: 100_000_000)
            self.assertIsNone(result["transport_error"])
            self.assertEqual([c.args[2] for c in rpc.call_args_list],
                             ["prepare", "challenge", "start", "challenge"])
            archive.assert_called_once()
            receipt = json.loads((Path(folder) / "remote-session.json").read_text())
            self.assertNotIn("_transport", receipt["options"])
        self.process.terminate.assert_called_once()
        self.assertFalse(self.folder.exists())


class RemoteObserverTerminalChallengeTests(unittest.TestCase):
    def test_terminal_publication_blocks_challenge_and_heartbeat_until_sealed(self):
        import fcntl
        for action in ("challenge", "heartbeat"):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as folder:
                directory = Path(folder)
                source = directory / "remote_observer.py"
                source.write_text("offline fixture")
                source_info = remote._file_info(source)
                remote._write(directory / "request.json", {"sources": {source.name: source_info}})
                remote._write(directory / "status.json", {"state": "running"})
                publishing = threading.Event()
                release = threading.Event()
                rpc_lock_attempt = threading.Event()
                role = threading.local()
                write = remote._write
                flock = fcntl.flock

                def delayed_write(path, value, **kwargs):
                    if path.name == "manifest.json":
                        publishing.set()
                        if not release.wait(2):
                            raise AssertionError("test did not release publication")
                    return write(path, value, **kwargs)

                def observed_lock(fd, operation):
                    if getattr(role, "rpc", False):
                        rpc_lock_attempt.set()
                    return flock(fd, operation)

                def request():
                    role.rpc = True
                    return remote._remote_rpc({"schema": remote.SCHEMA,
                        "session_id": "offline-session", "action": action,
                        "controller_sha256": source_info["sha256"]})

                old_umask = os.umask(0o077)
                try:
                    with patch.object(remote, "_session_directory", return_value=directory), \
                            patch.object(remote, "_write", side_effect=delayed_write), \
                            patch.object(fcntl, "flock", side_effect=observed_lock), \
                            patch.object(remote, "_issue_challenge") as issue, \
                            patch.object(remote, "_consume_challenge") as consume, \
                            patch.object(remote, "_lease") as lease, \
                            ThreadPoolExecutor(max_workers=2) as pool:
                        publication = pool.submit(remote._publish_terminal, directory,
                                                  {"state": "complete"}, {"files": {}}, error=None)
                        try:
                            self.assertTrue(publishing.wait(2))
                            # A separate descriptor proves that publication owns
                            # the actual flock, rather than relying on timing.
                            with (directory / "control.lock").open("a+b") as check:
                                with self.assertRaises(BlockingIOError):
                                    flock(check, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            response_future = pool.submit(request)
                            self.assertTrue(rpc_lock_attempt.wait(2))
                            self.assertFalse(response_future.done())
                            issue.assert_not_called()
                            consume.assert_not_called()
                            lease.assert_not_called()
                        finally:
                            release.set()
                        publication.result(timeout=2)
                        response = response_future.result(timeout=2)
                        self.assertEqual(response["status"]["state"], "complete")
                        self.assertEqual(response["manifest"], {"files": {}})
                        self.assertNotIn("challenge_id", response)
                        issue.assert_not_called()
                        consume.assert_not_called()
                        lease.assert_not_called()
                finally:
                    os.umask(old_umask)

    def test_terminal_challenge_returns_manifest_without_nonce_or_lease(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            source = directory / "remote_observer.py"
            source.write_text("offline fixture")
            source_info = remote._file_info(source)
            remote._write(directory / "request.json", {"sources": {source.name: source_info}})
            remote._write(directory / "manifest.json", {"files": {}})
            manifest_info = remote._file_info(directory / "manifest.json")
            old_umask = os.umask(0o077)
            try:
                for state in remote.TERMINAL:
                    remote._write(directory / "status.json", {"state": state, "manifest": manifest_info})
                    with patch.object(remote, "_session_directory", return_value=directory), \
                            patch.object(remote, "_issue_challenge") as issue, \
                            patch.object(remote, "_lease") as lease:
                        response = remote._remote_rpc({"schema": remote.SCHEMA,
                            "session_id": "offline-session", "action": "challenge",
                            "controller_sha256": source_info["sha256"]})
                        self.assertEqual(response["status"]["state"], state)
                        self.assertEqual(response["manifest"], {"files": {}})
                        self.assertNotIn("challenge_id", response)
                        issue.assert_not_called()
                        lease.assert_not_called()
            finally:
                os.umask(old_umask)


if __name__ == "__main__":
    unittest.main()
