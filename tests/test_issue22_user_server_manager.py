import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import stat
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tools import user_owned_https_server_manager as manager
from tools import user_owned_https_server_remote as remote
from tools.user_owned_https_batch_hook import Issue22FixtureServerHook
from tools.issue22_hil_orchestrator import RemoteFixtureServer


class Issue22UserServerManagerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=Path.home())
        self.root = Path(self.temporary.name)
        os.chmod(self.root, 0o700)
        self.remote_root = self.root / 'staged-ct-campaign'
        self.remote_root.mkdir(mode=0o700)
        (self.remote_root / 'tools').mkdir(mode=0o700)

        self.identity = self.root / 'ssh-identity'
        self.identity.write_bytes(b'offline-test-private-key')
        self.identity.chmod(0o600)
        key_blob = b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20' + b'k' * 32
        self.known_hosts = self.root / 'known_hosts'
        self.known_hosts.write_text('ct-test ssh-ed25519 ' + base64.b64encode(key_blob).decode() + '\n')
        self.known_hosts.chmod(0o600)
        self.ssh = {
            'target': 'wasmhome@192.0.2.8',
            'identity': str(self.identity),
            'identity_sha256': hashlib.sha256(self.identity.read_bytes()).hexdigest(),
            'known_hosts': str(self.known_hosts),
            'known_hosts_sha256': hashlib.sha256(self.known_hosts.read_bytes()).hexdigest(),
            'port': 22,
            'host_key_alias': 'ct-test',
        }

        self.ca = self.root / 'ca.pem'
        self.ca.write_bytes(b'offline-test-ca')
        self.fixture_root = self.root / 'fixtures'
        health = self.fixture_root / manager.ISSUE22_HEALTH_PATH
        health.parent.mkdir(parents=True)
        health.write_bytes(b'offline signed ENV-SIG manifest')
        self.spec = manager.build_issue22_fixture_spec(
            ssh=self.ssh,
            staged_root=self.remote_root,
            bind_ip='192.0.2.25',
            port=18445,
            server_name='wasmhome-issue22.test',
            campaign_id='issues16-18-bench-20261016-observer13',
            aot_toolchain_id='wamr-test-toolchain',
            ca_path=self.ca,
            local_fixture_root=self.fixture_root,
        )
        self.spec_path = self.root / 'server-spec.json'
        self.spec_sha256 = manager.write_frozen_spec(self.spec_path, self.spec)
        self.state_path = self.root / 'manager-state.json'
        self.journal_path = self.root / 'journal.json'

    def tearDown(self):
        self.temporary.cleanup()

    def write_journal(self, *, state='ready', restore_required=False, next_index=0,
                      current_attempt=None, current_plan_index=None):
        value = {
            'schema': 'wasmhome-issue22-hil-orchestrator-v1',
            'state': state,
            'restore_required': restore_required,
            'next_index': next_index,
            'current_attempt': current_attempt,
            'current_plan_index': current_plan_index,
        }
        self.journal_path.write_text(json.dumps(value))
        self.journal_path.chmod(0o600)
        return value

    def test_factory_freezes_existing_remote_staging_layout(self):
        loaded = manager.load_spec(self.spec_path, self.spec_sha256)
        self.assertEqual(loaded['server_kind'], 'issue22-fixture')
        self.assertEqual(Path(loaded['remote']['fixture_root']),
                         self.remote_root / 'fixtures')
        self.assertEqual(Path(loaded['remote']['ota_package_root']),
                         self.remote_root / 'ota-package')
        self.assertEqual(loaded['remote']['log_mode'], 0o600)
        self.assertEqual(loaded['tls']['health_path'], manager.ISSUE22_HEALTH_PATH)
        self.assertEqual(loaded['tls']['health_sha256'],
                         hashlib.sha256((self.fixture_root / manager.ISSUE22_HEALTH_PATH).read_bytes()).hexdigest())
        self.assertEqual(os.stat(self.spec_path).st_mode & 0o777, 0o600)
        with self.assertRaisesRegex(remote.ManagerError, 'already exists'):
            manager.write_frozen_spec(self.spec_path, self.spec)

    def test_scp_uses_the_same_pinned_optional_jump_without_a_remote_command(self):
        jump_identity = self.root / 'jump-identity'
        jump_identity.write_bytes(b'offline-test-jump-private-key')
        jump_identity.chmod(0o600)
        jump_known_hosts = self.root / 'jump_known_hosts'
        jump_known_hosts.write_text(self.known_hosts.read_text().replace('ct-test', 'proxy-test'))
        jump_known_hosts.chmod(0o600)
        ssh = dict(self.ssh, jump={
            'target': 'root@192.0.2.9',
            'identity': str(jump_identity),
            'identity_sha256': hashlib.sha256(jump_identity.read_bytes()).hexdigest(),
            'known_hosts': str(jump_known_hosts),
            'known_hosts_sha256': hashlib.sha256(jump_known_hosts.read_bytes()).hexdigest(),
            'port': 22,
            'host_key_alias': 'proxy-test',
        })
        argv = manager.scp_args(ssh)
        self.assertEqual(argv[0], '/usr/bin/scp')
        self.assertIn('-P', argv)
        proxy = next(value for value in argv if value.startswith('ProxyCommand='))
        self.assertIn('exec /usr/bin/ssh', proxy)
        self.assertIn('-W 192.0.2.8:22', proxy)
        self.assertNotIn('--', argv)
        self.assertNotIn('-T', argv)
        pinned_ssh = manager.ssh_args(self.ssh)
        self.assertIn('ServerAliveInterval=5', pinned_ssh)
        self.assertIn('ServerAliveCountMax=3', pinned_ssh)
        self.assertIn('TCPKeepAlive=yes', pinned_ssh)

        from tools import issue22_hil_orchestrator as orchestrator
        options = SimpleNamespace(
            ct_identity=self.identity, ct_known_hosts=self.known_hosts,
            ct_target='wasmhome@192.0.2.8', ct_ssh_port=22,
            ct_host_key_alias='ct-test', ct_jump_target='root@192.0.2.9',
            ct_jump_identity=jump_identity, ct_jump_known_hosts=jump_known_hosts,
            ct_jump_host_key_alias='proxy-test', ct_jump_ssh_port=22)
        frozen = orchestrator._pinned_manager_ssh(options)
        self.assertEqual(frozen['jump']['host_key_alias'], 'proxy-test')
        self.assertEqual(frozen['jump']['known_hosts_sha256'],
                         hashlib.sha256(jump_known_hosts.read_bytes()).hexdigest())

    def test_ssh_session_uses_private_verified_snapshots_for_ssh_and_scp(self):
        from tools import issue22_hil_orchestrator as orchestrator

        session = orchestrator.SSHSession(
            self.ssh['target'], Path(self.ssh['identity']), Path(self.ssh['known_hosts']),
            self.ssh['port'], self.ssh['host_key_alias'], manager_ssh=self.ssh)
        source = self.root / 'upload.bin'
        source.write_bytes(b'offline upload')
        observed = []

        def fake_run(argv, **_kwargs):
            key_path = Path(argv[argv.index('-i') + 1])
            known_path = Path(argv[argv.index('-o') + 1].split('=', 1)[1]) \
                if argv[argv.index('-o') + 1].startswith('UserKnownHostsFile=') else None
            if known_path is None:
                for index, value in enumerate(argv):
                    if value == '-o' and argv[index + 1].startswith('UserKnownHostsFile='):
                        known_path = Path(argv[index + 1].split('=', 1)[1])
                        break
            observed.append((argv[0], key_path, known_path,
                             key_path.read_bytes(), known_path.read_bytes()))
            self.identity.write_bytes(b'changed after snapshot')
            self.known_hosts.write_text('changed after snapshot\n')
            return Mock(returncode=0)

        with patch.object(orchestrator.subprocess, 'run', side_effect=fake_run):
            session.run(['true'])
        ssh_tool, key_path, known_path, key_bytes, host_bytes = observed[-1]
        self.assertIn(ssh_tool, ('/usr/bin/ssh', 'ssh'))
        self.assertNotEqual(key_path, self.identity)
        self.assertNotEqual(known_path, self.known_hosts)
        self.assertEqual(key_bytes, b'offline-test-private-key')
        self.assertIn(b'ct-test ssh-ed25519', host_bytes)
        self.assertFalse(key_path.exists())

        self.identity.write_bytes(b'offline-test-private-key')
        self.identity.chmod(0o600)
        self.known_hosts.write_text('ct-test ssh-ed25519 ' +
                                    base64.b64encode(b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20' + b'k' * 32).decode() + '\n')
        self.known_hosts.chmod(0o600)
        with patch.object(orchestrator.subprocess, 'run', side_effect=fake_run):
            session.scp(source, '/home/wasmhome/upload.bin')
        scp_tool, key_path, known_path, key_bytes, host_bytes = observed[-1]
        self.assertEqual(scp_tool, '/usr/bin/scp')
        self.assertNotEqual(key_path, self.identity)
        self.assertEqual(key_bytes, b'offline-test-private-key')
        self.assertIn(b'ct-test ssh-ed25519', host_bytes)
        self.assertFalse(key_path.exists())

    def test_ct_campaign_root_must_be_canonical_below_user_home(self):
        from tools import issue22_hil_orchestrator as orchestrator

        options = SimpleNamespace(ct_root='/tmp/unsafe-ct-root')
        with self.assertRaisesRegex(orchestrator.OrchestratorError, 'below /home/wasmhome'):
            orchestrator._remote_staging_root(options, self.root / 'campaign')
        options.ct_root = '/home/wasmhome/../../tmp/unsafe'
        with self.assertRaisesRegex(orchestrator.OrchestratorError, 'below /home/wasmhome'):
            orchestrator._remote_staging_root(options, self.root / 'campaign')

        remote_home = self.root / 'fake-ct-home'
        remote_home.mkdir(mode=0o755)
        safe_root = remote_home / '.local' / 'share' / 'campaign'
        safe_root.parent.mkdir(parents=True, mode=0o755)
        safe_root.mkdir(mode=0o700)
        self.assertEqual(remote.validate_campaign_root(
            safe_root, home=remote_home, uid=os.getuid()), safe_root)
        os.chmod(safe_root.parent, 0o775)
        with self.assertRaisesRegex(remote.ManagerError, 'unsafe campaign path ancestor'):
            remote.validate_campaign_root(safe_root, home=remote_home, uid=os.getuid())

        missing_root = remote_home / 'new-private' / 'nested' / 'campaign'
        remote.create_campaign_root(missing_root, home=remote_home, uid=os.getuid())
        self.assertEqual(stat.S_IMODE(missing_root.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(missing_root.parent.stat().st_mode), 0o700)

    def test_remote_read_only_preflight_reports_receipts_and_port_owner_without_writes(self):
        proc_root = self.root / 'proc-fixture'
        (proc_root / 'net').mkdir(parents=True)
        (proc_root / 'net/tcp').write_text(
            'sl local_address rem_address st tx_queue tr retrnsmt uid timeout inode\n'
            '0: 190200C0:480D 00000000:0000 0A 00000000:00000000 00:00000000 '
            '00000000 1234 0 456\n')
        fd_dir = proc_root / '12345/fd'
        fd_dir.mkdir(parents=True)
        (fd_dir / '7').symlink_to('socket:[456]')
        result = remote.preflight_issue22(self.spec['remote'], proc=proc_root)
        self.assertTrue(result['campaign_root_exists'])
        self.assertFalse(result['manager_busy'])
        self.assertFalse(result['manager_receipt_present'])
        self.assertFalse(result['server_pid_receipt_present'])
        self.assertEqual(result['port_owners'], [{'uid': 1234, 'pid': 12345}])
        self.assertEqual({path.name for path in self.remote_root.iterdir()}, {'tools'})

        lock_path = self.remote_root / '.server-manager.lock'
        lock_path.touch(mode=0o600)
        descriptor = os.open(lock_path, os.O_RDONLY)
        try:
            import fcntl
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            busy = remote.preflight_issue22(self.spec['remote'], proc=proc_root)
            self.assertTrue(busy['manager_busy'])
        finally:
            os.close(descriptor)

        pid_receipt = self.remote_root / 'server.pid'
        pid_receipt.write_text('999 888\n')
        pid_receipt.chmod(0o600)
        before = pid_receipt.read_bytes()
        result = remote.preflight_issue22(self.spec['remote'], proc=proc_root)
        self.assertTrue(result['server_pid_receipt_present'])
        self.assertEqual(pid_receipt.read_bytes(), before)
        manager_receipt = self.remote_root / 'server-manager.json'
        manager_receipt.write_text('{"offline": true}\n')
        manager_receipt.chmod(0o600)
        manager_before = manager_receipt.read_bytes()
        result = remote.preflight_issue22(self.spec['remote'], proc=proc_root)
        self.assertTrue(result['manager_receipt_present'])
        self.assertEqual(manager_receipt.read_bytes(), manager_before)

    def test_remote_staging_reservation_holds_manager_lock_and_rechecks_owners(self):
        proc_root = self.root / 'proc-reservation'
        (proc_root / 'net').mkdir(parents=True)
        (proc_root / 'net/tcp').write_text('sl local_address rem_address st inode\n')
        listener = Mock()
        listener.family = socket.AF_INET
        listener.getsockname.return_value = (self.spec['remote']['bind_ip'],
                                             self.spec['remote']['port'])
        listener.getsockopt.side_effect = lambda _level, option: (
            1 if option == socket.SO_ACCEPTCONN else 0)
        with patch.object(remote, '_bind_listener', return_value=listener):
            descriptor, reserved_socket = remote.reserve_issue22(
                self.spec['remote'], proc=proc_root, fixed_listener_fd=False)
        try:
            current = remote.preflight_issue22(self.spec['remote'], proc=proc_root)
            self.assertTrue(current['manager_busy'])
            with self.assertRaisesRegex(remote.ManagerError, 'active manager'):
                remote.reserve_issue22(self.spec['remote'], proc=proc_root)
        finally:
            import fcntl
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
            reserved_socket.close()

        receipt = self.remote_root / 'server.pid'
        receipt.write_text('legacy listener receipt\n')
        receipt.chmod(0o644)
        before = sorted((path.name, path.stat().st_mode & 0o777)
                        for path in self.remote_root.iterdir())
        with self.assertRaisesRegex(remote.ManagerError, 'unsafe private file'):
            remote.reserve_issue22(self.spec['remote'], proc=proc_root)
        after = sorted((path.name, path.stat().st_mode & 0o777)
                       for path in self.remote_root.iterdir())
        self.assertEqual(after, before)

    def test_managed_relaunch_reserves_only_after_owned_process_is_stopped(self):
        listener = Mock()
        clean_stopped = {'campaign_root_exists': True, 'manager_busy': False,
                         'manager_receipt_present': True,
                         'server_pid_receipt_present': True, 'port_owners': []}
        stopped = {'state': 'stopped', 'listener_owned': False}
        with patch.object(remote, 'status', return_value=stopped), \
             patch.object(remote, 'preflight_issue22', return_value=clean_stopped), \
             patch.object(remote, '_bind_listener', return_value=listener) as bind, \
             patch.object(remote, '_move_listener_to_stable_fd', side_effect=lambda value: value):
            self.assertIs(remote._reserve_listener_for_ensure(
                self.spec['remote'], 'c' * 32), listener)
        bind.assert_called_once_with(self.spec['remote']['bind_ip'],
                                     self.spec['remote']['port'])

        orphaned = dict(clean_stopped, manager_receipt_present=False)
        with patch.object(remote, 'status', return_value={
                'state': 'absent', 'listener_owned': False}), \
             patch.object(remote, 'preflight_issue22', return_value=orphaned), \
             patch.object(remote, '_bind_listener') as bind:
            with self.assertRaisesRegex(remote.ManagerError, 'unowned fixture receipt'):
                remote._reserve_listener_for_ensure(self.spec['remote'], 'c' * 32)
        bind.assert_not_called()

        with patch.object(remote, 'status', return_value={
                'state': 'running', 'listener_owned': False}), \
             patch.object(remote, 'preflight_issue22') as preflight:
            with self.assertRaisesRegex(remote.ManagerError, 'without its expected listener'):
                remote._reserve_listener_for_ensure(self.spec['remote'], 'c' * 32)
        preflight.assert_not_called()

    def test_ensure_dispatch_reacquires_listener_for_next_unclaimed_row(self):
        listener = Mock()
        expected = {'state': 'running', 'listener_owned': True}
        with patch.object(remote, 'status', return_value={
                'state': 'stopped', 'listener_owned': False}), \
             patch.object(remote, '_move_lock_above_listener_fd', side_effect=lambda fd: fd), \
             patch.object(remote, '_reserve_listener_for_ensure', return_value=listener) as reserve, \
             patch.object(remote, 'ensure', return_value=expected) as ensure:
            result = remote.dispatch('ensure', self.spec['remote'], 'issue22-fixture',
                                     'd' * 32, self.remote_root)
        self.assertEqual(result, expected)
        reserve.assert_called_once_with(self.spec['remote'], 'd' * 32)
        ensure.assert_called_once()
        self.assertIs(ensure.call_args.kwargs['listener'], listener)
        self.assertTrue(listener.close.called)

    def test_bound_listener_excludes_noncooperating_bind_until_socket_closes(self):
        listener = remote._bind_listener('127.0.0.1', 0)
        address = listener.getsockname()
        contender = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        reuseport_contender = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.assertEqual(listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN), 1)
            with self.assertRaises(OSError):
                contender.bind(address)
            if hasattr(socket, 'SO_REUSEPORT'):
                reuseport_contender.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                with self.assertRaises(OSError):
                    reuseport_contender.bind(address)
        finally:
            listener.close()
            reuseport_contender.close()
        try:
            contender.bind(address)
        finally:
            contender.close()

    def test_reservation_eof_releases_socket_and_lock_without_launch_receipts(self):
        import fcntl

        lock_path = self.remote_root / '.server-manager.lock'
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        listener = Mock()
        listener.fileno.return_value = 3
        output = io.StringIO()
        with patch.object(remote, 'reserve_issue22', return_value=(lock_fd, listener)), \
             patch.object(remote, 'ensure') as ensure:
            remote.issue22_reservation_session(self.spec['remote'], io.BytesIO(), output)
        self.assertEqual([json.loads(line) for line in output.getvalue().splitlines()],
                         [{'state': 'reserved'}, {'state': 'released'}])
        listener.close.assert_called_once()
        ensure.assert_not_called()
        self.assertFalse((self.remote_root / 'server-manager.json').exists())
        self.assertFalse((self.remote_root / 'server.pid').exists())
        probe = os.open(lock_path, os.O_RDWR)
        try:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(probe)

    def test_reservation_idle_timeout_releases_socket_and_lock_without_receipts(self):
        import fcntl

        class SelectableInput:
            def fileno(self):
                return 987654

        lock_path = self.remote_root / '.server-manager.lock'
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        listener = Mock()
        listener.fileno.return_value = 3
        output = io.StringIO()
        with patch.object(remote, 'reserve_issue22', return_value=(lock_fd, listener)), \
             patch.object(remote.select, 'select', return_value=([], [], [])):
            remote.issue22_reservation_session(self.spec['remote'], SelectableInput(), output,
                                               lease_timeout=0.01)
        self.assertEqual([json.loads(line) for line in output.getvalue().splitlines()],
                         [{'state': 'reserved'}, {'state': 'released'}])
        listener.close.assert_called_once()
        self.assertFalse((self.remote_root / 'server-manager.json').exists())
        self.assertFalse((self.remote_root / 'server.pid').exists())
        probe = os.open(lock_path, os.O_RDWR)
        try:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(probe)

    def test_reservation_renewal_resets_idle_deadline(self):
        lock_path = self.remote_root / '.server-manager.lock'
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        listener = Mock()
        listener.fileno.return_value = 3
        output = io.StringIO()
        commands = iter((b'{"action":"renew"}\n',
                         (json.dumps({'action': 'ensure', 'manager_id': 'c' * 32})
                          + '\n').encode()))
        observed_timeouts = []

        def read_line(_input, _buffer, timeout):
            observed_timeouts.append(timeout)
            return next(commands)

        with patch.object(remote, 'reserve_issue22', return_value=(lock_fd, listener)), \
             patch.object(remote, '_read_reservation_line', side_effect=read_line), \
             patch.object(remote.time, 'monotonic', side_effect=(0, 0.9, 1.1, 1.2)), \
             patch.object(remote, 'validate_spec'), \
             patch.object(remote.subprocess, 'run', return_value=Mock(returncode=0)), \
             patch.object(remote, 'ensure', return_value={
                 'state': 'running', 'listener_owned': True}) as ensure:
            remote.issue22_reservation_session(self.spec['remote'], object(), output,
                                               lease_timeout=1)
        self.assertAlmostEqual(observed_timeouts[0], 0.1)
        self.assertAlmostEqual(observed_timeouts[1], 0.9)
        ensure.assert_called_once()
        listener.close.assert_called_once()


    def test_reservation_handoff_uses_reserved_fd_and_closes_parent_copy(self):
        lock_path = self.remote_root / '.server-manager.lock'
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        listener = Mock()
        listener.fileno.return_value = 3
        listener.getsockname.return_value = ('192.0.2.25', 18445)
        output = io.StringIO()
        command = (json.dumps({'action': 'ensure', 'manager_id': 'b' * 32}) + '\n').encode()
        with patch.object(remote, 'reserve_issue22', return_value=(lock_fd, listener)), \
             patch.object(remote, 'validate_spec'), \
             patch.object(remote.subprocess, 'run', return_value=Mock(returncode=0)), \
             patch.object(remote, 'ensure', return_value={
                 'state': 'running', 'listener_owned': True}) as ensure:
            remote.issue22_reservation_session(self.spec['remote'], io.BytesIO(command), output)
        ensure.assert_called_once_with(self.spec['remote'], 'issue22-fixture', 'b' * 32,
                                       lock_fd=lock_fd, listener=listener)
        listener.close.assert_called_once()
        self.assertEqual([json.loads(line) for line in output.getvalue().splitlines()],
                         [{'state': 'reserved'},
                          {'state': 'running', 'listener_owned': True}])

    def test_manager_launch_passes_stable_listener_and_lock_descriptors(self):
        import fcntl

        listener = remote._bind_listener('127.0.0.1', 0)
        original_fd = listener.fileno()
        lock_fd = None
        descriptor_backup = None
        descriptor_inheritable = False
        try:
            try:
                descriptor_flags = fcntl.fcntl(remote.ISSUE22_LISTEN_FD, fcntl.F_GETFD)
            except OSError:
                descriptor_flags = None
            else:
                descriptor_backup = os.dup(remote.ISSUE22_LISTEN_FD)
                descriptor_inheritable = not bool(descriptor_flags & fcntl.FD_CLOEXEC)
            if original_fd != remote.ISSUE22_LISTEN_FD:
                os.dup2(original_fd, remote.ISSUE22_LISTEN_FD, inheritable=False)
                listener.close()
                listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM,
                                         fileno=remote.ISSUE22_LISTEN_FD)
            self.spec['remote']['bind_ip'], self.spec['remote']['port'] = listener.getsockname()
            lock_path = self.remote_root / '.server-manager.lock'
            lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            launched = Mock()
            launched.poll.return_value = None
            states = iter(({'state': 'absent', 'listener_owned': False},
                           {'state': 'running', 'listener_owned': True}))
            def spawn(*args, **_kwargs):
                receipt_path = Path(args[0][4])
                receipt_path.write_text('offline receipt placeholder\n')
                receipt_path.chmod(0o600)
                return launched
            with patch.object(remote, 'status', side_effect=lambda *_args: next(states)), \
                 patch.object(remote.subprocess, 'Popen', side_effect=spawn) as popen, \
                 patch.object(remote.time, 'sleep'):
                result = remote.ensure(self.spec['remote'], 'issue22-fixture', 'a' * 32,
                                       lock_fd=lock_fd,
                                       listener=listener)
            self.assertEqual(result, {'state': 'running', 'listener_owned': True})
            self.assertEqual(set(popen.call_args.kwargs['pass_fds']),
                             {lock_fd, remote.ISSUE22_LISTEN_FD})
            record = json.loads(popen.call_args.args[0][5])
            self.assertEqual(record['argv'][-2:], ['--listen-fd', '3'])
        finally:
            if lock_fd is not None:
                os.close(lock_fd)
            listener.close()
            if descriptor_backup is not None:
                os.dup2(descriptor_backup, remote.ISSUE22_LISTEN_FD,
                        inheritable=descriptor_inheritable)
                os.close(descriptor_backup)

    def test_concurrent_staging_reservations_serialize_after_the_same_read_only_preflight(self):
        proc_root = self.root / 'proc-race'
        (proc_root / 'net').mkdir(parents=True)
        (proc_root / 'net/tcp').write_text('sl local_address rem_address st inode\n')
        barrier = threading.Barrier(2)
        original_preflight = remote.preflight_issue22
        clean = {'campaign_root_exists': True, 'manager_busy': False,
                 'manager_receipt_present': False, 'server_pid_receipt_present': False,
                 'port_owners': []}

        def synchronized_preflight(spec, *, proc, lock_held=False):
            if not lock_held:
                barrier.wait(timeout=5)
                return dict(clean)
            return original_preflight(spec, proc=proc, lock_held=True)

        def reserve():
            try:
                return ('reserved', remote.reserve_issue22(
                    self.spec['remote'], proc=proc_root, fixed_listener_fd=False))
            except remote.ManagerError:
                return ('blocked', None)

        listener = Mock()
        listener.family = socket.AF_INET
        listener.getsockname.return_value = (self.spec['remote']['bind_ip'],
                                             self.spec['remote']['port'])
        listener.getsockopt.side_effect = lambda _level, option: (
            1 if option == socket.SO_ACCEPTCONN else 0)
        with patch.object(remote, 'preflight_issue22', side_effect=synchronized_preflight), \
             patch.object(remote, '_bind_listener', return_value=listener):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _index: reserve(), range(2)))
        self.assertEqual(sorted(state for state, _fd in results), ['blocked', 'reserved'])
        descriptor, reserved_socket = next(value for state, value in results if state == 'reserved')
        import fcntl
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
        reserved_socket.close()

    def test_stage_preflight_rejects_any_receipt_or_listener_before_remote_writes(self):
        from tools import issue22_hil_orchestrator as orchestrator

        package_root = self.root / 'ota-source-preflight'
        package_root.mkdir()
        server = RemoteFixtureServer(
            Mock(), fixture_root=self.fixture_root, package_root=package_root,
            fixture_trusted=self.root / 'trusted.pub',
            fixture_untrusted=self.root / 'untrusted.pub', ota_public=self.root / 'ota.pub',
            certificate=self.root / 'server.crt', private_key=self.root / 'server.key',
            campaign_root=Path('/home/wasmhome/issue22/test-campaign'),
            server_ip='192.0.2.25', port=18445,
            server_name='wasmhome-issue22.test', toolchain='wamr-test-toolchain',
            manager_spec=self.spec)
        clean = {'campaign_root_exists': True, 'manager_busy': False,
                 'manager_receipt_present': False, 'server_pid_receipt_present': False,
                 'port_owners': []}
        for condition, error_pattern in (
                ({'manager_busy': True}, 'active manager'),
                ({'manager_receipt_present': True}, 'server receipt'),
                ({'server_pid_receipt_present': True}, 'server receipt'),
                ({'port_owners': [{'uid': 999, 'pid': 555}]}, 'TCP listener')):
            with self.subTest(condition=condition), \
                 patch.object(orchestrator.user_server_manager, 'remote_preflight',
                              return_value=dict(clean, **condition)):
                with self.assertRaisesRegex(orchestrator.OrchestratorError, error_pattern):
                    server.preflight()
        server.ssh.run.assert_not_called()
        server.ssh.scp.assert_not_called()

    def test_copy_validates_directory_destinations_before_each_mkdir(self):
        from tools import issue22_hil_orchestrator as orchestrator

        server = RemoteFixtureServer(
            Mock(), fixture_root=self.fixture_root, package_root=self.root / 'ota-source',
            fixture_trusted=self.root / 'trusted.pub',
            fixture_untrusted=self.root / 'untrusted.pub', ota_public=self.root / 'ota.pub',
            certificate=self.root / 'server.crt', private_key=self.root / 'server.key',
            campaign_root=Path('/home/wasmhome/issue22/test-campaign'),
            server_ip='192.0.2.25', port=18445,
            server_name='wasmhome-issue22.test', toolchain='wamr-test-toolchain',
            manager_spec=self.spec)
        destination = self.remote_root / 'fixtures-copy'
        lease = SimpleNamespace(poll=lambda: None)

        with patch.object(server, '_validate_remote_path',
                          side_effect=orchestrator.OrchestratorError('unsafe destination symlink')):
            with self.assertRaisesRegex(orchestrator.OrchestratorError, 'unsafe destination symlink'):
                server._copy(self.fixture_root, destination, lease)
        server.ssh.run.assert_not_called()
        server.ssh.scp.assert_not_called()

        validated = []
        remote_parent = destination / 'resilience/ENV-SIG/scenario-1'

        def reject_nested(path, kind, _reservation=None):
            validated.append((Path(path), kind))
            if Path(path) == remote_parent:
                raise orchestrator.OrchestratorError('unsafe nested symlink')

        with patch.object(server, '_validate_remote_path', side_effect=reject_nested):
            with self.assertRaisesRegex(orchestrator.OrchestratorError, 'unsafe nested symlink'):
                server._copy(self.fixture_root, destination, lease)
        self.assertEqual(server.ssh.run.call_count, 1)
        self.assertEqual(server.ssh.run.call_args.args[0][-1], str(destination))
        self.assertIn((remote_parent, 'dir'), validated)
        server.ssh.scp.assert_not_called()

    def test_orchestrator_freezes_manager_spec_and_tool_fingerprints(self):
        from tools import issue22_hil_orchestrator as orchestrator

        options = SimpleNamespace(
            ct_root='/home/wasmhome/.local/share/wasmhome-hil',
            ct_target='wasmhome@192.0.2.8',
            ct_identity=self.identity,
            ct_known_hosts=self.known_hosts,
            ct_ssh_port=22,
            ct_host_key_alias='ct-test',
            server_ip='192.0.2.25',
            https_port=18445,
            server_name='wasmhome-issue22.test',
            aot_toolchain_id='wamr-test-toolchain',
            tls_ca=self.ca,
        )
        campaign_root = self.root / 'campaign-root'
        campaign_root.mkdir(mode=0o700)
        spec_path, state_path, digest, fingerprints = orchestrator._freeze_server_manager(
            options, campaign_root, self.fixture_root)
        self.assertTrue(spec_path.is_file())
        self.assertFalse(state_path.exists())
        self.assertEqual(hashlib.sha256(spec_path.read_bytes()).hexdigest(), digest)
        self.assertEqual(os.stat(spec_path).st_mode & 0o777, 0o600)
        self.assertEqual(set(fingerprints), {
            'orchestrator', 'manager', 'remote_helper', 'batch_hook',
            'fixture_server', 'signed_artifacts'})
        self.assertEqual(orchestrator._freeze_server_manager(
            options, campaign_root, self.fixture_root)[2], digest)

        changed_values = vars(options).copy()
        changed_values['server_name'] = 'other.test'
        changed = SimpleNamespace(**changed_values)
        with self.assertRaisesRegex(orchestrator.OrchestratorError, 'specification changed'):
            orchestrator._freeze_server_manager(changed, campaign_root, self.fixture_root)

    def test_verified_operation_spec_freezes_ssh_and_tls_bytes_privately(self):
        original_identity = self.identity.read_bytes()
        original_known_hosts = self.known_hosts.read_bytes()
        original_ca = self.ca.read_bytes()
        manifest = b'offline signed ENV-SIG manifest'
        tls_socket = Mock()
        tls_socket.__enter__ = Mock(return_value=tls_socket)
        tls_socket.__exit__ = Mock(return_value=False)

        class FakeRawSocket:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        response = Mock(status=200)
        response.read.return_value = manifest
        context = Mock()
        context.wrap_socket.return_value = tls_socket
        with manager.verified_operation_spec(self.spec) as operation_spec:
            self.assertNotEqual(operation_spec['ssh']['identity'], self.ssh['identity'])
            self.assertNotEqual(operation_spec['tls']['ca'], self.spec['tls']['ca'])
            identity_snapshot = Path(operation_spec['ssh']['identity'])
            ca_snapshot = Path(operation_spec['tls']['ca'])
            self.assertEqual(identity_snapshot.read_bytes(), original_identity)
            self.assertEqual(Path(operation_spec['ssh']['known_hosts']).read_bytes(),
                             original_known_hosts)
            self.assertEqual(ca_snapshot.read_bytes(), original_ca)
            self.assertEqual(os.stat(identity_snapshot).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(ca_snapshot).st_mode & 0o777, 0o600)

            self.identity.write_bytes(b'changed after operation snapshot')
            self.known_hosts.write_text('changed after operation snapshot\n')
            self.ca.write_bytes(b'changed after operation snapshot')
            with patch.object(manager.ssl, 'create_default_context', return_value=context) as create_context, \
                 patch.object(manager.socket, 'create_connection', return_value=FakeRawSocket()), \
                 patch.object(manager.http.client, 'HTTPResponse', return_value=response):
                self.assertTrue(manager.tls_health(operation_spec))
            create_context.assert_called_once_with(cadata=original_ca.decode('ascii'))
        self.assertFalse(identity_snapshot.exists())

    def test_remote_spec_is_closed_and_uses_original_server_cli(self):
        value = self.spec['remote']
        remote.validate_spec(value, 'issue22-fixture', staged_files=False)
        argv = remote.server_argv(value, 'issue22-fixture')
        pairs = dict(zip(argv[2::2], argv[3::2]))
        self.assertEqual(Path(pairs['--fixture-root']), self.remote_root / 'fixtures')
        self.assertEqual(Path(pairs['--ota-package-root']), self.remote_root / 'ota-package')
        self.assertEqual(pairs['--campaign-id'], self.spec['remote']['campaign_id'])
        self.assertEqual(pairs['--log-mode'], '600')
        self.assertEqual(Path(pairs['--pid-file']), self.remote_root / 'server.pid')
        self.assertIn('--trusted-public-key', argv)
        self.assertIn('--untrusted-public-key', argv)
        self.assertNotIn('--health-route', argv)
        self.assertNotIn('--fixture-index-route', argv)

    def test_remote_reservation_keeps_private_transport_open_until_context_exit(self):
        class FakeStdin:
            closed = False
            payload = b''

            def write(self, value):
                self.payload += value
                return len(value)

            def flush(self):
                return None

            def close(self):
                self.closed = True

        class FakeStdout:
            def __init__(self):
                self.lines = [b'{"state":"reserved"}\n', b'{"state":"released"}\n']

            def readline(self, _size):
                return self.lines.pop(0)

        class FakeProcess:
            def __init__(self):
                self.stdin = FakeStdin()
                self.stdout = FakeStdout()
                self.return_code = None

            def poll(self):
                return self.return_code

            def wait(self, timeout=None):
                self.return_code = 0
                return 0

            def kill(self):
                self.return_code = -9

        process = FakeProcess()
        observed = {}

        def fake_popen(argv, **_kwargs):
            observed['argv'] = argv
            observed['identity'] = Path(argv[argv.index('-i') + 1])
            return process

        with patch.object(manager.subprocess, 'Popen', side_effect=fake_popen), \
             patch.object(manager.select, 'select', side_effect=lambda streams, *_args: (streams, [], [])):
            with manager.remote_reservation(self.spec, heartbeat_interval_seconds=0.005) as reservation:
                self.assertIs(reservation.process, process)
                self.assertFalse(process.stdin.closed)
                self.assertIn('reserve', observed['argv'][-1])
                self.assertEqual(observed['identity'].read_bytes(), self.identity.read_bytes())
                time.sleep(0.025)
            self.assertTrue(process.stdin.closed)
            self.assertEqual(process.return_code, 0)
        self.assertGreaterEqual(process.stdin.payload.count(b'"action":"renew"'), 2)
        self.assertFalse(observed['identity'].exists())

        failing_process = FakeProcess()
        with patch.object(manager.subprocess, 'Popen', return_value=failing_process), \
             patch.object(manager.select, 'select', side_effect=lambda streams, *_args: (streams, [], [])):
            with self.assertRaisesRegex(RuntimeError, 'simulated staging failure'):
                with manager.remote_reservation(self.spec):
                    raise RuntimeError('simulated staging failure')
        self.assertTrue(failing_process.stdin.closed)
        self.assertEqual(failing_process.return_code, 0)

    def test_remote_reservation_can_handoff_once_over_the_same_ssh_session(self):
        class FakeStdin:
            closed = False
            payload = b''

            def write(self, value):
                self.payload += value
                return len(value)

            def flush(self):
                return None

            def close(self):
                self.closed = True

        class FakeStdout:
            def __init__(self):
                self.lines = [b'{"state":"reserved"}\n',
                              b'{"state":"running","listener_owned":true}\n']

            def readline(self, _size):
                return self.lines.pop(0)

        class FakeProcess:
            def __init__(self):
                self.stdin = FakeStdin()
                self.stdout = FakeStdout()
                self.return_code = None

            def poll(self):
                return self.return_code

            def wait(self, timeout=None):
                self.return_code = 0
                return 0

            def kill(self):
                self.return_code = -9

        process = FakeProcess()
        with patch.object(manager.subprocess, 'Popen', return_value=process), \
             patch.object(manager.select, 'select', side_effect=lambda streams, *_args: (streams, [], [])):
            with manager.remote_reservation(self.spec, heartbeat_interval_seconds=0.005) as lease:
                time.sleep(0.02)
                result = lease.ensure('a' * 32)
                self.assertEqual(result, {'state': 'running', 'listener_owned': True})
                self.assertTrue(lease.ensure_sent)
                self.assertIn(b'"action":"ensure"', process.stdin.payload)
                self.assertIn(b'"manager_id":"' + b'a' * 32, process.stdin.payload)
                self.assertFalse(lease._heartbeat_thread.is_alive())
            self.assertTrue(process.stdin.closed)
            self.assertEqual(process.return_code, 0)
        self.assertEqual(process.stdin.payload.count(b'"action":"ensure"'), 1)

    def test_remote_reservation_timeout_closes_ssh_and_kills_unresponsive_session(self):
        class FakeStdin:
            closed = False

            def close(self):
                self.closed = True

        class FakeProcess:
            def __init__(self):
                self.stdin = FakeStdin()
                self.stdout = object()
                self.return_code = None
                self.killed = False
                self.waits = 0

            def poll(self):
                return self.return_code

            def wait(self, timeout=None):
                self.waits += 1
                if self.waits == 1:
                    raise manager.subprocess.TimeoutExpired('ssh', timeout)
                self.return_code = -9
                return self.return_code

            def kill(self):
                self.killed = True

        process = FakeProcess()
        with patch.object(manager.subprocess, 'Popen', return_value=process), \
             patch.object(manager.select, 'select', return_value=([], [], [])):
            with self.assertRaisesRegex(remote.ManagerError, 'reservation timed out'):
                with manager.remote_reservation(self.spec, timeout_seconds=0.01):
                    self.fail('reservation response should time out')
        self.assertTrue(process.stdin.closed)
        self.assertTrue(process.killed)
        self.assertEqual(process.return_code, -9)

    def test_original_artifact_server_mode_remains_available(self):
        artifact_root = self.root / 'artifact-staging'
        artifact_root.mkdir(mode=0o700)
        package = artifact_root / 'package'
        package.mkdir(mode=0o700)
        artifact = {
            'root': str(artifact_root),
            'script': str(artifact_root / 'artifact_https_server.py'),
            'python': '/usr/bin/python3',
            'package': str(package),
            'certificate': str(artifact_root / 'server.crt'),
            'private_key': str(artifact_root / 'server.key'),
            'signing_public_key': str(artifact_root / 'signing.pub'),
            'http_log': str(artifact_root / 'http.jsonl'),
            'bind_ip': '192.0.2.25', 'port': 18446,
            'server_name': 'artifact.test', 'campaign_id': 'artifact-campaign',
            'aot_toolchain_id': 'wamr-test-toolchain',
        }
        remote.validate_spec(artifact, 'artifact', staged_files=False)
        argv = remote.server_argv(artifact, 'artifact')
        self.assertIn('--package', argv)
        self.assertIn('--signing-public-key', argv)
        self.assertNotIn('--fixture-root', argv)
        self.write_journal(state='recovered_interruption')
        self.assertEqual(manager.safe_journal(self.journal_path, 'artifact')['state'],
                         'recovered_interruption')

    def test_existing_staging_can_be_run_without_starting_its_legacy_listener(self):
        from tools import issue22_hil_orchestrator as orchestrator

        package_root = self.root / 'ota-source'
        package_root.mkdir()
        for name in ('trusted.pub', 'untrusted.pub', 'ota.pub', 'server.crt', 'server.key'):
            (self.root / name).write_bytes(b'offline staging input')
        server = RemoteFixtureServer(
            Mock(), fixture_root=self.fixture_root, package_root=package_root,
            fixture_trusted=self.root / 'trusted.pub',
            fixture_untrusted=self.root / 'untrusted.pub', ota_public=self.root / 'ota.pub',
            certificate=self.root / 'server.crt', private_key=self.root / 'server.key',
            campaign_root=Path('/home/wasmhome/issue22/test-campaign'),
            server_ip='192.0.2.25', port=18445,
            server_name='wasmhome-issue22.test', toolchain='wamr-test-toolchain',
            manager_spec=self.spec)
        events = []
        transport = {'identity': 'private-snapshot-transport'}

        @contextmanager
        def reservation(_spec):
            events.append('reservation-acquired')
            lease = SimpleNamespace(poll=lambda: None, transport=transport)
            try:
                yield lease
            finally:
                events.append('reservation-released')

        server.ssh.run.side_effect = lambda command, **_kwargs: events.append(('run', command))
        server.ssh.scp.side_effect = lambda source, destination, **_kwargs: events.append(
            ('scp', source, destination))
        with patch.object(server, 'preflight', return_value={
                'manager_receipt_present': False, 'server_pid_receipt_present': False,
                'port_owners': [], 'campaign_root_exists': False}), \
             patch.object(server, '_validate_remote_root'), \
             patch.object(server, '_copy'), \
             patch.object(orchestrator.user_server_manager, 'remote_reservation',
                          side_effect=reservation):
            argv = server.stage()
        self.assertEqual(events[0], 'reservation-acquired')
        self.assertEqual(events[-1], 'reservation-released')
        self.assertTrue(any(isinstance(event, tuple) and event[0] == 'scp'
                            for event in events[1:-1]))
        for index, event in enumerate(events):
            if not (isinstance(event, tuple) and event[0] == 'run'
                    and event[1][0] == 'mkdir'):
                continue
            prior = events[index - 1]
            self.assertEqual(prior[0], 'run')
            self.assertEqual(prior[1][:2], ['python3', '-c'])
            self.assertEqual(prior[1][-2], event[1][-1])
            self.assertEqual(prior[1][-1], 'dir')
        self.assertEqual(argv[1], str(server.root / 'tools/resilience_fixture_https_server.py'))
        self.assertIn('--fixture-root', argv)
        self.assertIn('--ota-package-root', argv)
        self.assertGreaterEqual(server.ssh.run.call_count, 3)
        self.assertTrue(all(call.kwargs.get('transport') is transport
                            for call in server.ssh.run.call_args_list))
        self.assertTrue(all(call.kwargs.get('transport') is transport
                            for call in server.ssh.scp.call_args_list))
        check_command = server.ssh.run.call_args.args[0]
        self.assertEqual(check_command[-1], '--check')
        self.assertNotIn('nohup', check_command)
        self.assertNotIn('sh', check_command)
        with self.assertRaisesRegex(orchestrator.OrchestratorError,
                                    'direct fixture-server start is disabled'):
            server.start()
        with self.assertRaisesRegex(orchestrator.OrchestratorError,
                                    'direct fixture-server stop is disabled'):
            server.stop()

    def test_issue22_mode_rejects_unlisted_paths_and_reserved_ports(self):
        invalid_path = dict(self.spec['remote'], fixture_root=str(self.remote_root / 'other'))
        with self.assertRaisesRegex(remote.ManagerError, 'layout differs'):
            remote.validate_spec(invalid_path, 'issue22-fixture', staged_files=False)
        invalid_port = dict(self.spec['remote'], port=8444)
        with self.assertRaisesRegex(remote.ManagerError, 'reserved'):
            remote.validate_spec(invalid_port, 'issue22-fixture', staged_files=False)
        with self.assertRaisesRegex(remote.ManagerError, 'server kind/script pair'):
            remote.validate_spec(dict(self.spec['remote'], script=str(self.remote_root / 'tools/artifact_https_server.py')),
                                 'issue22-fixture', staged_files=False)

    def test_issue22_journal_adapter_accepts_only_restored_idle_phases(self):
        for state in ('ready', 'row_complete', 'complete', 'interrupted_recovered'):
            with self.subTest(state=state):
                self.write_journal(state=state, next_index=16)
                self.assertEqual(manager.safe_journal(self.journal_path, 'issue22-fixture')['state'], state)

        for state in ('initializing', 'baseline_verification', 'row_claimed',
                      'row_provisioning', 'restoring', 'restore_failed', 'row_failed'):
            with self.subTest(unsafe_state=state):
                self.write_journal(state=state)
                with self.assertRaisesRegex(remote.ManagerError, 'not safe'):
                    manager.safe_journal(self.journal_path, 'issue22-fixture')

        self.write_journal(state='ready', restore_required=True)
        with self.assertRaisesRegex(remote.ManagerError, 'not safe'):
            manager.safe_journal(self.journal_path, 'issue22-fixture')
        self.write_journal(state='ready', next_index=17)
        with self.assertRaisesRegex(remote.ManagerError, 'not safe'):
            manager.safe_journal(self.journal_path, 'issue22-fixture')
        self.write_journal(state='ready', current_attempt='claimed-row')
        with self.assertRaisesRegex(remote.ManagerError, 'active-row marker'):
            manager.safe_journal(self.journal_path, 'issue22-fixture')

        self.write_journal(state='ready')
        changed = json.loads(self.journal_path.read_text())
        changed['schema'] = 'foreign-journal-v1'
        self.journal_path.write_text(json.dumps(changed))
        with self.assertRaisesRegex(remote.ManagerError, 'journal identity'):
            manager.safe_journal(self.journal_path, 'issue22-fixture')

    def test_stale_issue22_pid_receipt_is_removed_only_when_manager_bound(self):
        path = self.remote_root / 'server.pid'
        record = {'pid': 1234, 'start_ticks': '5678'}
        path.write_text('1234 5678\n')
        path.chmod(0o600)
        remote.remove_issue22_pid_receipt(self.spec['remote'], record)
        self.assertFalse(path.exists())

        path.write_text('4321 8765\n')
        path.chmod(0o600)
        with self.assertRaisesRegex(remote.ManagerError, 'does not match'):
            remote.remove_issue22_pid_receipt(self.spec['remote'], record)
        self.assertEqual(path.read_text(), '4321 8765\n')

    def test_unsafe_issue22_journal_blocks_before_any_remote_call(self):
        self.write_journal(state='initializing')
        with patch.object(manager, 'remote_action') as remote_action:
            with self.assertRaisesRegex(remote.ManagerError, 'not safe'):
                manager.operate('ensure', self.spec_path, self.state_path,
                                expected_spec_sha256=self.spec_sha256,
                                journal_path=self.journal_path)
        remote_action.assert_not_called()
        self.assertFalse(self.state_path.exists())

    def test_ensure_serializes_owned_process_and_marks_tls_readiness(self):
        self.write_journal()
        live = {'state': 'running', 'listener_owned': True}
        def checked_action(action, operation_spec, _manager_id):
            self.assertNotEqual(operation_spec['ssh']['identity'], self.ssh['identity'])
            self.assertEqual(Path(operation_spec['ssh']['identity']).read_bytes(),
                             self.identity.read_bytes())
            self.assertNotEqual(operation_spec['ssh']['known_hosts'], self.ssh['known_hosts'])
            return live

        def checked_health(operation_spec):
            self.assertNotEqual(operation_spec['tls']['ca'], self.spec['tls']['ca'])
            self.assertEqual(Path(operation_spec['tls']['ca']).read_bytes(), self.ca.read_bytes())
            return True

        with patch.object(manager, 'remote_action', side_effect=checked_action) as action, \
             patch.object(manager, 'tls_health', side_effect=checked_health) as tls:
            result = manager.operate('ensure', self.spec_path, self.state_path,
                                     expected_spec_sha256=self.spec_sha256,
                                     journal_path=self.journal_path)
        self.assertTrue(result['listener_owned'])
        self.assertTrue(result['tls_verified'])
        self.assertEqual(action.call_count, 2)
        tls.assert_called_once()
        state = json.loads(self.state_path.read_text())
        self.assertEqual(state['spec']['server_kind'], 'issue22-fixture')
        self.assertFalse(state['cleanup_pending'])
        self.assertEqual(os.stat(self.state_path).st_mode & 0o777, 0o600)

    def test_fixture_tls_health_uses_an_existing_manifest_route(self):
        manifest = b'offline signed ENV-SIG manifest'
        tls_socket = Mock()
        tls_socket.__enter__ = Mock(return_value=tls_socket)
        tls_socket.__exit__ = Mock(return_value=False)

        class FakeRawSocket:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        response = Mock(status=200)
        response.read.return_value = manifest
        context = Mock()
        context.wrap_socket.return_value = tls_socket
        with patch.object(manager.ssl, 'create_default_context', return_value=context) as create_context, \
             patch.object(manager.socket, 'create_connection', return_value=FakeRawSocket()), \
             patch.object(manager.http.client, 'HTTPResponse', return_value=response):
            self.assertTrue(manager.tls_health(self.spec))
        create_context.assert_called_once_with(
            cadata=self.ca.read_bytes().decode('ascii'))
        request = tls_socket.sendall.call_args.args[0].decode('ascii')
        self.assertIn('GET /' + self.spec['remote']['campaign_id'] + '/'
                      + manager.ISSUE22_HEALTH_PATH + ' HTTP/1.1', request)
        self.assertNotIn('/fixture-index.json', request)
        response.read.assert_called_once_with(4097)

    def test_batch_hook_checks_issue22_journal_before_ensure(self):
        self.write_journal(state='row_complete')
        hook = Issue22FixtureServerHook(self.spec_path, self.state_path, self.journal_path,
                                        spec_sha256=self.spec_sha256, retry_seconds=0)
        ready = {'state': 'running', 'listener_owned': True, 'tls_verified': True}
        with patch.object(manager, 'operate', return_value=ready) as operate:
            result = hook.before_child()
        self.assertEqual(result, ready)
        self.assertEqual(operate.call_args.args[0], 'ensure')
        self.assertEqual(operate.call_args.kwargs['journal_path'], self.journal_path)

    def test_batch_hook_reacquires_managed_fixture_before_next_unclaimed_row(self):
        self.write_journal(state='row_complete', next_index=1)
        hook = Issue22FixtureServerHook(self.spec_path, self.state_path, self.journal_path,
                                        spec_sha256=self.spec_sha256, retry_seconds=1)
        ready = {'state': 'running', 'listener_owned': True, 'tls_verified': True}
        with patch.object(manager, 'operate', side_effect=[
                remote.ManagerError('owned fixture stopped between rows'), ready]) as operate, \
             patch('tools.user_owned_https_batch_hook.time.sleep'):
            result = hook.before_child()
        self.assertEqual(result, ready)
        self.assertEqual(operate.call_count, 2)
        self.assertTrue(all(call.args[0] == 'ensure' for call in operate.call_args_list))

    def test_stage_baseline_and_first_ensure_share_one_reservation(self):
        from tools import user_owned_https_batch_hook

        self.write_journal()
        lease = Mock()
        lease.ensure_sent = False
        server = Mock(manager_spec=self.spec)
        hook = Issue22FixtureServerHook(self.spec_path, self.state_path, self.journal_path,
                                        spec_sha256=self.spec_sha256)
        ready = {'state': 'running', 'listener_owned': True, 'tls_verified': True}

        @contextmanager
        def reservation(_spec):
            yield lease

        def mark_handoff(*_args, **kwargs):
            self.assertIs(kwargs['reservation'], lease)
            lease.ensure_sent = True
            return ready

        with patch.object(user_owned_https_batch_hook.manager, 'remote_reservation',
                          side_effect=reservation), \
             patch.object(manager, 'operate', side_effect=mark_handoff), \
             patch.object(hook, 'cleanup_only', return_value={'state': 'stopped'}):
            with hook.stage_and_batch_guard(server):
                hook.before_child()
        server.stage.assert_called_once_with(lease)

    def test_cleanup_only_hook_recovers_from_saved_manager_state_without_spec_or_journal(self):
        state = {
            'schema': 'wasmhome-ct107-server-manager-state-v2',
            'spec_bytes_sha256': self.spec_sha256,
            'spec_sha256': hashlib.sha256(manager.canonical(self.spec).encode()).hexdigest(),
            'spec': self.spec,
            'manager_id': 'offline-owner',
        }
        manager.atomic_private(self.state_path, state)
        self.journal_path.unlink(missing_ok=True)
        hook = Issue22FixtureServerHook(self.root / 'missing-spec.json', self.state_path,
                                        self.journal_path, spec_sha256=None)
        with patch.object(manager, 'remote_action', return_value={
                'state': 'stopped', 'listener_owned': False}) as remote_action:
            result = hook.cleanup_only()
        self.assertEqual(result['state'], 'stopped')
        self.assertEqual(remote_action.call_args.args[0], 'stop')
        self.assertFalse(self.journal_path.exists())

    def test_managed_execute_uses_stage_and_hook_without_legacy_start_or_stop(self):
        import ast
        import inspect
        from tools import issue22_hil_orchestrator as orchestrator

        tree = ast.parse(inspect.getsource(orchestrator.execute))
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
        attributes = [(node.func.value.id, node.func.attr) for node in calls
                      if isinstance(node.func, ast.Attribute)
                      and isinstance(node.func.value, ast.Name)]
        self.assertIn(('hook', 'stage_and_batch_guard'), attributes)
        self.assertNotIn(('server', 'start'), attributes)
        self.assertNotIn(('server', 'stop'), attributes)
        self.assertIn(('hook', 'before_child'), attributes)


if __name__ == '__main__':
    unittest.main()
