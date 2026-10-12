"""No SSH, guest actions, installs or compilation. Real files only in tempfile."""
import argparse
import contextlib
import hashlib
import json
import pathlib
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import ct110_supervisor_receiver as endpoint
import supervise_ct110 as supervisor


def baseline():
    return dict(schema="wasmhome-proxmox-donor-lease-v1", ctid=110, hostname="wasmhome-toolchain-donor",
                phase="before-start", ct_state="stopped", host_start_gate_memavailable_bytes=10 * supervisor.guard.GIB,
                configured_memory_bytes=8 * supervisor.guard.GIB, configured_swap_bytes=0,
                template=dict(sha256=supervisor.guard.TEMPLATE_SHA256), other_guest_states={"ct109": "stopped", "vm103": "running"}, config_sha256="exact-config")


class FakeChild:
    def __init__(self, running=False, code=0):
        self.returncode = None if running else code
    def poll(self): return self.returncode
    def wait(self, timeout):
        self.returncode = 130 if self.returncode is None else self.returncode
        return self.returncode


class SupervisorTests(unittest.TestCase):
    def args(self, folder):
        before = folder / "before.json"
        before.write_text(json.dumps(baseline()))
        return argparse.Namespace(execute=True, authorized_ct110=True, baseline=before, log=folder / "new-logs",
            builder_sha256=supervisor.sha(supervisor.SCRIPT_DIR / "donor_wamrc.py"), control="/home/wasmhome/wasmhome-control-e1",
            builder="/home/wasmhome/donor_wamrc.py", run="/home/wasmhome/wasmhome-donor-e1", source_bundle="/home/wasmhome/source.bundle",
            review_approval="/home/wasmhome/review.json", review_receipt="/home/wasmhome/review.md", ssh="ssh", known_hosts=None)

    def test_unauthorized_execution_rejects_before_remote_client_or_log(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.args(pathlib.Path(directory))
            args.authorized_ct110 = False
            with patch.object(supervisor, "Client") as client, self.assertRaisesRegex(ValueError, "explicit authorization"):
                supervisor.supervise(args)
            client.assert_not_called()
            self.assertFalse(args.log.exists())

    def test_baseline_wrong_ct_or_start_ram_rejects(self):
        value = baseline()
        value["ctid"] = 107
        with self.assertRaisesRegex(ValueError, "exact before-start CT110"):
            supervisor.validate_baseline(value)
        value = baseline()
        value["host_start_gate_memavailable_bytes"] = 2 * supervisor.guard.GIB
        with self.assertRaisesRegex(ValueError, "resource contract"):
            supervisor.validate_baseline(value)

    def test_ssh_command_preserves_quoted_code_and_lf_json_stdin(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = pathlib.Path(directory)
            args = self.args(folder)
            client = supervisor.Client(args, folder, baseline())
            code = "print('quoted $HOME `cmd`')\n"
            argv = client.command(code)
            self.assertEqual(shlex.split(argv[-1]), ["python3", "-c", code])
            self.assertIn("-oBatchMode=yes", argv)
            self.assertIn("-oStrictHostKeyChecking=yes", argv)
            wire = client.payload("identity")
            self.assertTrue(wire.endswith(b"\n"))
            self.assertNotIn(b"\r", wire)

    def test_gate_failure_interrupts_only_endpoint_then_normal_shutdown_once(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.args(pathlib.Path(directory))
            requests = []
            running_count = 0
            def call(phase, operation="guard", endpoint=None):
                nonlocal running_count
                requests.append((phase, operation, None if endpoint is None else endpoint["action"]))
                if phase == "running":
                    running_count += 1
                    if running_count == 2:
                        raise ValueError("host pressure gate")
                    return dict(ctid=110, phase="running")
                if operation == "shutdown":
                    return dict(ctid=110, phase="final", ct_state="stopped", other_guest_states=baseline()["other_guest_states"])
                return dict(status="ok")
            child = FakeChild(running=True)
            with patch.object(supervisor, "Client") as factory:
                factory.return_value.call.side_effect = call
                factory.return_value.launch.return_value = child
                with self.assertRaisesRegex(ValueError, "did not prove build success"):
                    supervisor.supervise(args)
            self.assertIn(("identity", "endpoint", "interrupt"), requests)
            self.assertEqual([r for r in requests if r[1] == "shutdown"], [("identity", "shutdown", None)])
            result = json.loads((args.log / "supervisor-result.json").read_text())
            self.assertEqual(result["status"], "failed_preserved")
            self.assertEqual(result["final_guard"]["ct_state"], "stopped")

    def test_shutdown_failure_is_not_retried_or_converted_to_success(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.args(pathlib.Path(directory))
            operations = []
            def call(phase, operation="guard", endpoint=None):
                operations.append(operation)
                if operation == "shutdown":
                    raise subprocess.CalledProcessError(1, ["normal-shutdown110"])
                if endpoint and endpoint["action"] == "status":
                    return dict(status="finished", returncode=0)
                return dict(status="ok")
            with patch.object(supervisor, "Client") as factory:
                factory.return_value.call.side_effect = call
                factory.return_value.launch.return_value = FakeChild()
                with self.assertRaisesRegex(ValueError, "did not prove build success"):
                    supervisor.supervise(args)
            self.assertEqual(operations.count("shutdown"), 1)
            self.assertTrue((args.log / "shutdown-unconfirmed.json").is_file())

    def test_build_success_requires_final_stopped_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.args(pathlib.Path(directory))
            def call(phase, operation="guard", endpoint=None):
                if operation == "shutdown":
                    return dict(ctid=110, phase="final", ct_state="stopped")
                if endpoint and endpoint["action"] == "status":
                    return dict(status="finished", returncode=0)
                return dict(status="ok")
            with patch.object(supervisor, "Client") as factory:
                factory.return_value.call.side_effect = call
                factory.return_value.launch.return_value = FakeChild()
                result = supervisor.supervise(args)
            self.assertEqual(result["status"], "build_finished_newCT110_stopped")

    def test_receiver_pid_reuse_mismatch_rejects_without_signal(self):
        record = dict(pid=123, start_ticks=12, argv=["/usr/bin/python3", "donor.py"])
        actual = dict(pid=123, pgid=123, start_ticks=99, uid=1000, argv=[b"/usr/bin/python3", b"donor.py"])
        with patch.object(endpoint, "proc_identity", return_value=actual), self.assertRaisesRegex(ValueError, "no signal"):
            endpoint.identified(record)

    def test_receiver_control_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            home = pathlib.Path(directory)
            actual = home / "target"
            actual.mkdir()
            control = home / "wasmhome-control-e1"
            control.symlink_to(actual, target_is_directory=True)
            with patch.object(pathlib.Path, "home", return_value=home), self.assertRaisesRegex(ValueError, "identity/owner"):
                endpoint.control_path(str(control))

    def test_receiver_interrupt_sends_only_sigint_to_identified_group(self):
        with tempfile.TemporaryDirectory() as directory:
            control = pathlib.Path(directory)
            (control / "process.json").write_text(json.dumps(dict(pid=123, start_ticks=12, argv=["python3"])))
            with patch.object(endpoint.platform, "node", return_value="wasmhome-toolchain-donor"), patch.object(endpoint.os, "getuid", return_value=1000), patch.object(endpoint, "control_path", return_value=control), patch.object(endpoint, "identified", return_value={"state": "S"}), patch.object(endpoint.os, "killpg") as send:
                result = endpoint.dispatch(dict(action="interrupt", control="unused"))
            send.assert_called_once_with(123, endpoint.signal.SIGINT)
            self.assertEqual(result["status"], "interruption_requested")

    def test_real_linux_process_group_signal_preserves_unrelated_process(self):
        # Disposable local Python sleepers, no network/build/guest operations.
        command = [sys.executable, "-c", "import time; time.sleep(30)"]
        owned = subprocess.Popen(command, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        unrelated = subprocess.Popen(command, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            actual = endpoint.proc_identity(owned.pid)
            record = dict(pid=owned.pid, start_ticks=actual["start_ticks"], argv=command)
            self.assertEqual(endpoint.identified(record)["pgid"], owned.pid)
            endpoint.os.killpg(record["pid"], endpoint.signal.SIGINT)
            owned.wait(timeout=5)
            self.assertNotEqual(owned.returncode, 0)
            self.assertIsNone(unrelated.poll())
        finally:
            for child in (owned, unrelated):
                if child.poll() is None:
                    child.terminate()  # Only the Popen handle created by this test.
                child.wait(timeout=5)

    def spawn_failure(self, mode, short_lived=False):
        with tempfile.TemporaryDirectory() as directory:
            control = pathlib.Path(directory)
            builder = control / "builder.py"
            builder.write_text("pass\n" if short_lived else "import time\ntime.sleep(30)\n")
            command = ["/usr/bin/python3", str(builder), "--execute", "--profile", "lxc110", "--host-lease", str(control / "host-lease.json")]
            unrelated = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"], start_new_session=True,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            own = []
            real_popen = subprocess.Popen
            real_identity = endpoint.proc_identity
            def launch(*args, **kwargs):
                child = real_popen(*args, **kwargs)
                own.append(child)
                return child
            def identify(pid):
                if mode == "identity":
                    raise OSError("injected proc identity failure")
                if mode == "short-lived":
                    own[0].wait(timeout=5)
                    raise FileNotFoundError("short-lived child disappeared")
                return real_identity(pid)
            def publish(path, value):
                raise OSError("injected atomic receipt write failure")
            try:
                pidfd_failure = patch.object(endpoint.os, "pidfd_open", side_effect=OSError("injected pidfd failure")) if mode == "pidfd" else contextlib.nullcontext()
                with patch.object(endpoint.platform, "node", return_value="wasmhome-toolchain-donor"), patch.object(endpoint, "control_path", return_value=control), patch.object(endpoint.subprocess, "Popen", side_effect=launch), patch.object(endpoint, "proc_identity", side_effect=identify), patch.object(endpoint, "atomic_json", side_effect=publish), pidfd_failure, self.assertRaises(OSError):
                    endpoint.dispatch(dict(action="build", control="unused", argv=command,
                                           builder_sha256=hashlib.sha256(builder.read_bytes()).hexdigest()))
                self.assertEqual(len(own), 1)
                self.assertIsNotNone(own[0].poll(), "post-spawn setup failure leaked the new child")
                self.assertIsNone(unrelated.poll(), "cleanup touched an unrelated process")
                cleanup = json.loads((control / "spawn-cleanup.json").read_text())
                self.assertEqual(cleanup["status"], "child_reaped")
                if mode == "identity":
                    self.assertEqual(cleanup["signals"][0]["target"], "pinned_new_child_only")
                elif mode == "receipt":
                    self.assertEqual(cleanup["signals"][0]["target"], "verified_new_process_group")
                elif mode == "pidfd":
                    self.assertEqual(cleanup["signals"][0]["target"], "owned_unreaped_child_only")
                else:
                    self.assertEqual(cleanup["signals"], [])
                self.assertFalse((control / "process.json").exists())
            finally:
                for child in own + [unrelated]:
                    if child.poll() is None:
                        child.terminate()
                    child.wait(timeout=5)

    def test_post_spawn_atomic_receipt_failure_reaps_only_new_process(self):
        self.spawn_failure("receipt")

    def test_post_spawn_identity_failure_uses_pidfd_and_reaps_only_new_process(self):
        self.spawn_failure("identity")

    def test_post_spawn_short_lived_process_is_reaped_without_signal(self):
        self.spawn_failure("short-lived", short_lived=True)

    def test_post_spawn_pidfd_failure_proves_unreaped_child_before_signal(self):
        self.spawn_failure("pidfd")


if __name__ == "__main__":
    unittest.main()
