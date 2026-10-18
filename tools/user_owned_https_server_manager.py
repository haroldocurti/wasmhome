#!/usr/bin/env python3
"""User-owned lifecycle manager for one staged HTTPS campaign server.

CLI: {ensure,status,stop} --spec PRIVATE_JSON --spec-sha256 FROZEN_HASH
     --state PRIVATE_JSON [--journal PRIVATE_HARDWARE_JOURNAL]
"""
import argparse
import base64
from contextlib import contextmanager
import fcntl
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import select
import shlex
import socket
import ssl
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid

try:
    from . import user_owned_https_server_remote as remote
except ImportError:
    import user_owned_https_server_remote as remote


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


ISSUE22_HEALTH_PATH = 'resilience/ENV-SIG/scenario-1/manifest.cose'


def build_issue22_fixture_spec(*, ssh, staged_root, bind_ip, port, server_name,
                               campaign_id, aot_toolchain_id, ca_path,
                               local_fixture_root):
    """Build the closed manager spec for the existing #22 staged file layout."""
    import ipaddress
    staged_root = Path(staged_root)
    remote.require(staged_root.is_absolute() and '..' not in staged_root.parts
                   and staged_root.as_posix() == str(staged_root),
                   'remote staging root must be absolute and canonical')
    address = ipaddress.IPv4Address(bind_ip)
    remote.require(not (address.is_unspecified or address.is_loopback or address.is_multicast),
                   'invalid issue22-fixture bind address')
    remote.require(type(port) is int and 1024 <= port <= 65535 and port not in {8443, 8444},
                   'invalid issue22-fixture port')
    for value, pattern, label in (
            (server_name, r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}', 'server name'),
            (campaign_id, r'[a-z0-9][a-z0-9._-]{0,63}', 'campaign ID'),
            (aot_toolchain_id, r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', 'AOT toolchain ID')):
        remote.require(isinstance(value, str) and re.fullmatch(pattern, value),
                       'invalid issue22-fixture ' + label)
    ca = owned_file(ca_path)
    manifest = Path(local_fixture_root) / ISSUE22_HEALTH_PATH
    remote.require(manifest.is_file() and not manifest.is_symlink(),
                   'registered issue22-fixture health manifest is missing')
    server = {
        'root': staged_root.as_posix(),
        'script': (staged_root / 'tools/resilience_fixture_https_server.py').as_posix(),
        'python': '/usr/bin/python3',
        'fixture_root': (staged_root / 'fixtures').as_posix(),
        'ota_package_root': (staged_root / 'ota-package').as_posix(),
        'trusted_public_key': (staged_root / 'fixture-trusted.pub').as_posix(),
        'untrusted_public_key': (staged_root / 'fixture-untrusted.pub').as_posix(),
        'ota_public_key': (staged_root / 'ota-signing.pub').as_posix(),
        'certificate': (staged_root / 'server.crt').as_posix(),
        'private_key': (staged_root / 'server.key').as_posix(),
        'http_log': (staged_root / 'http-events.jsonl').as_posix(),
        'pid_file': (staged_root / 'server.pid').as_posix(),
        'bind_ip': str(address), 'port': port, 'server_name': server_name,
        'campaign_id': campaign_id, 'aot_toolchain_id': aot_toolchain_id,
        'log_mode': 0o600,
    }
    tls = {'ca': str(Path(ca_path).absolute()),
           'ca_sha256': hashlib.sha256(ca.read_bytes()).hexdigest(),
           'health_path': ISSUE22_HEALTH_PATH,
           'health_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest()}
    value = {'schema': 'wasmhome-user-owned-https-manager-v1',
             'server_kind': 'issue22-fixture', 'ssh': ssh,
             'remote': server, 'tls': tls}
    ssh_args(ssh)
    return value


def write_frozen_spec(path, value):
    """Create a new 0600 spec and return its exact byte digest."""
    path = Path(path).absolute()
    parent = path.parent
    info = parent.lstat()
    remote.require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
                   and stat.S_IMODE(info.st_mode) == 0o700,
                   'spec parent must be private 0700')
    remote.require(not path.exists() and not path.is_symlink(),
                   'frozen server specification already exists')
    payload = (canonical(value) + '\n').encode('utf-8')
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return hashlib.sha256(payload).hexdigest()


def owned_file(path, private=False):
    path = Path(path)
    info = path.lstat()
    remote.require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                   and not info.st_mode & 0o022, "unsafe local input")
    if private:
        remote.require(stat.S_IMODE(info.st_mode) == 0o600, "local input must be 0600")
    return path


def atomic_private(path, value):
    path = Path(path)
    if path.exists() or path.is_symlink():
        owned_file(path, private=True)
    fd, temporary = tempfile.mkstemp(prefix='.manager-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(canonical(value) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def pinned_ssh_material(spec):
    """Validate the one effective key/alias, not merely a known-hosts path."""
    result = {}
    for field, private in (('identity', True), ('known_hosts', False)):
        data = read_owned_bytes(spec[field], private=private)
        expected = spec[field + '_sha256']
        remote.require(isinstance(expected, str) and re.fullmatch(r'[0-9a-f]{64}', expected)
                       and hashlib.sha256(data).hexdigest() == expected,
                       'pinned SSH input bytes differ')
        result[field] = data
    try:
        lines = [line.split() for line in result['known_hosts'].decode('ascii').splitlines()
                 if line.strip() and not line.lstrip().startswith('#')]
        alias = spec['host_key_alias'] if spec['port'] == 22 else f"[{spec['host_key_alias']}]:{spec['port']}"
        remote.require(len(lines) == 1 and len(lines[0]) in (3, 4)
                       and lines[0][0] == alias and lines[0][1] == 'ssh-ed25519',
                       'known-hosts must pin exactly one literal CT alias and Ed25519 key')
        blob = base64.b64decode(lines[0][2], validate=True)
        remote.require(blob == b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20' + blob[-32:]
                       and len(blob) == 51, 'invalid pinned Ed25519 key encoding')
    except (UnicodeError, ValueError) as error:
        raise remote.ManagerError('invalid pinned CT host key') from error
    return result


def read_owned_bytes(path, *, private=False):
    """Read exact bytes through a no-follow descriptor and verify that inode."""
    path = Path(path)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        info = os.fstat(descriptor)
        remote.require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                       and not info.st_mode & 0o022,
                       'unsafe local input')
        if private:
            remote.require(stat.S_IMODE(info.st_mode) == 0o600,
                           'local input must be 0600')
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            data = stream.read()
        after = os.fstat(descriptor)
        remote.require((info.st_dev, info.st_ino, info.st_size)
                       == (after.st_dev, after.st_ino, after.st_size),
                       'local input changed while being read')
        return data
    finally:
        os.close(descriptor)


def _write_snapshot(path, data):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


@contextmanager
def verified_ssh_snapshot(ssh):
    """Yield a transport whose SSH bytes are frozen in private 0600 files."""
    copied = json.loads(canonical(ssh))
    connections = [('destination', copied)]
    if copied.get('jump') is not None:
        connections.append(('jump', copied['jump']))
    with tempfile.TemporaryDirectory(prefix='ct107-verified-ssh-') as temporary:
        os.chmod(temporary, 0o700)
        for label, connection in connections:
            for name, data in pinned_ssh_material(connection).items():
                path = Path(temporary) / (label + '-' + name)
                _write_snapshot(path, data)
                connection[name] = str(path)
        # Re-parse the copied known-hosts and verify the hashes against the
        # snapshot paths before handing them to ssh/scp.
        ssh_args(copied)
        yield copied


@contextmanager
def verified_operation_spec(spec):
    """Snapshot SSH material and TLS CA for one ensure/status/TLS operation."""
    copied = json.loads(canonical(spec))
    with tempfile.TemporaryDirectory(prefix='ct107-verified-operation-') as temporary:
        os.chmod(temporary, 0o700)
        ssh_path = Path(temporary) / 'ssh'
        ssh_path.mkdir(mode=0o700)
        transport = dict(copied['ssh'])
        connections = [('destination', transport)]
        if transport.get('jump') is not None:
            connections.append(('jump', transport['jump']))
        for label, connection in connections:
            for name, data in pinned_ssh_material(connection).items():
                path = ssh_path / (label + '-' + name)
                _write_snapshot(path, data)
                connection[name] = str(path)
        ssh_args(transport)
        copied['ssh'] = transport
        ca_data = read_owned_bytes(copied['tls']['ca'])
        remote.require(hashlib.sha256(ca_data).hexdigest() == copied['tls']['ca_sha256'],
                       'CA fingerprint differs')
        ca_path = Path(temporary) / 'tls-ca.pem'
        _write_snapshot(ca_path, ca_data)
        copied['tls']['ca'] = str(ca_path)
        yield copied


SSH_FIELDS = {'target', 'identity', 'known_hosts', 'port', 'host_key_alias',
              'known_hosts_sha256', 'identity_sha256'}


def _connection_args(spec, *, jump=False):
    remote.require(isinstance(spec, dict) and set(spec) ==
                   SSH_FIELDS,
                   'invalid SSH specification')
    user_pattern = r'[a-z_][a-z0-9_-]{0,31}' if jump else 'wasmhome'
    remote.require(re.fullmatch(user_pattern + r'@[A-Za-z0-9_.:-]{1,253}', spec['target'] or ''),
                   'invalid frozen jump account' if jump else 'SSH account must be wasmhome')
    remote.require(type(spec['port']) is int and 1 <= spec['port'] <= 65535, 'invalid SSH port')
    remote.require(re.fullmatch(r'[A-Za-z0-9_.:-]{1,253}', spec['host_key_alias'] or ''),
                   'invalid pinned host alias')
    pinned_ssh_material(spec)
    return ['/usr/bin/ssh', '-F', '/dev/null', '-T', '-p', str(spec['port']),
            '-i', spec['identity'], '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
            '-o', 'IdentityAgent=none', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'UserKnownHostsFile=' + spec['known_hosts'], '-o', 'GlobalKnownHostsFile=/dev/null',
            '-o', 'HostKeyAlias=' + spec['host_key_alias'], '-o', 'PasswordAuthentication=no',
            '-o', 'HostKeyAlgorithms=ssh-ed25519', '-o', 'VerifyHostKeyDNS=no',
            '-o', 'UpdateHostKeys=no', '-o', 'CheckHostIP=no',
            '-o', 'KbdInteractiveAuthentication=no', '-o', 'ForwardAgent=no',
            '-o', 'ClearAllForwardings=yes', '-o', 'ControlMaster=no', '-o', 'ConnectTimeout=5',
            '-o', 'TCPKeepAlive=yes', '-o', 'ServerAliveInterval=5',
            '-o', 'ServerAliveCountMax=3',
            '--', spec['target']]


def jump_argv(jump, host, port):
    """Closed argv: Proxmox receives only stdio TCP forwarding, never a command."""
    remote.require(re.fullmatch(r'[A-Za-z0-9_.:-]{1,253}', host or '')
                   and type(port) is int and 1 <= port <= 65535, 'invalid jump forwarding destination')
    address = f'[{host}]:{port}' if ':' in host else f'{host}:{port}'
    args = _connection_args(jump, jump=True)
    args = [*args[:-2], '-N', '-o', 'ExitOnForwardFailure=yes', '-W', address, *args[-2:]]
    # OpenSSH expands percent tokens even inside ProxyCommand shell quoting.
    # No such tokens are accepted from the frozen configuration.
    remote.require(all('%' not in value for value in args), 'SSH percent tokens are forbidden in jump argv')
    return args


def ssh_args(spec):
    remote.require(isinstance(spec, dict) and set(spec) in (SSH_FIELDS, SSH_FIELDS | {'jump'}),
                   'invalid SSH specification')
    destination = {key: value for key, value in spec.items() if key != 'jump'}
    args = _connection_args(destination)
    jump = spec.get('jump')
    if jump is not None:
        host = destination['target'].split('@', 1)[1]
        proxy = jump_argv(jump, host, destination['port'])
        # ProxyCommand necessarily passes through OpenSSH's local shell. Every
        # argument comes from the closed argv above and is quoted separately;
        # no free command/shell fragment is accepted by the specification.
        command = 'exec ' + shlex.join(proxy)
        args = [*args[:-2], '-o', 'ProxyCommand=' + command, *args[-2:]]
    return args


def scp_args(spec):
    """Return the same pinned transport for staging without an SSH command target."""
    args = ssh_args(spec)
    # The SSH argv ends with ``-- target``. SCP takes its destination as a
    # normal operand, translates the SSH port flag, and does not need ``-T``.
    options = args[:-2]
    result = ['/usr/bin/scp']
    index = 1
    while index < len(options):
        value = options[index]
        if value == '-T':
            index += 1
            continue
        if value == '-p':
            result.extend(['-P', options[index + 1]])
            index += 2
            continue
        result.append(value)
        if value in {'-F', '-i', '-o'}:
            result.append(options[index + 1])
            index += 2
        else:
            index += 1
    return result


def _remote_helper_call(action, spec, request):
    source = Path(remote.__file__).read_text()
    command = shlex.join(['/usr/bin/python3', '-c', source, action, canonical(request)])
    with verified_ssh_snapshot(spec['ssh']) as transport:
        result = subprocess.run([*ssh_args(transport), command], capture_output=True, timeout=20)
    if result.returncode:
        raise subprocess.CalledProcessError(result.returncode, ['ssh'])
    try:
        data = json.loads(result.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise remote.ManagerError('invalid CT result') from error
    return data


def remote_action(action, spec, manager_id):
    request = {'remote': spec['remote'], 'server_kind': spec['server_kind'],
               'manager_id': manager_id}
    data = _remote_helper_call(action, spec, request)
    remote.require(isinstance(data, dict) and set(data) == {'state', 'listener_owned'}
                   and data['state'] in {'absent', 'stale_reboot', 'stopped', 'running'}
                   and type(data['listener_owned']) is bool, 'invalid CT result')
    return data


def remote_preflight(spec):
    remote.require(spec.get('server_kind') == 'issue22-fixture',
                   'read-only campaign preflight requires issue22-fixture')
    request = {'remote': spec['remote'], 'server_kind': spec['server_kind']}
    data = _remote_helper_call('preflight', spec, request)
    fields = {'campaign_root_exists', 'manager_busy', 'manager_receipt_present',
              'server_pid_receipt_present', 'port_owners'}
    remote.require(isinstance(data, dict) and set(data) == fields
                   and type(data['campaign_root_exists']) is bool
                   and type(data['manager_busy']) is bool
                   and type(data['manager_receipt_present']) is bool
                   and type(data['server_pid_receipt_present']) is bool
                   and isinstance(data['port_owners'], list)
                   and all(isinstance(owner, dict) and set(owner) == {'uid', 'pid'}
                           and type(owner['uid']) is int
                           and (owner['pid'] is None or type(owner['pid']) is int)
                           for owner in data['port_owners']),
                   'invalid campaign preflight result')
    return data


def _read_reservation_message(stream, timeout):
    ready, _, _ = select.select([stream], [], [], timeout)
    remote.require(bool(ready), 'remote staging reservation timed out')
    raw = stream.readline(1025)
    remote.require(raw and len(raw) <= 1024 and raw.endswith(b'\n'),
                   'remote staging reservation closed without a complete response')
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise remote.ManagerError('invalid remote staging reservation response') from error
    remote.require(isinstance(value, dict)
                   and set(value) in ({'state'}, {'state', 'listener_owned'}),
                   'invalid remote staging reservation response')
    return value


class RemoteStageLease:
    """Persistent pinned SSH owner for one stage-and-listener handoff."""
    def __init__(self, process, transport, timeout_seconds, handoff_timeout_seconds,
                 heartbeat_interval_seconds):
        self.process = process
        self.transport = transport
        self.timeout_seconds = timeout_seconds
        self.handoff_timeout_seconds = handoff_timeout_seconds
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.ensure_sent = False
        self.ensure_result = None
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread = None
        self._heartbeat_error = None
        self._write_lock = threading.Lock()
        try:
            self._stdin_fd = process.stdin.fileno()
        except (AttributeError, OSError, ValueError):
            self._stdin_fd = None
        if self._stdin_fd is not None:
            os.set_blocking(self._stdin_fd, False)

    def poll(self):
        return self.process.poll()

    def _write_frame(self, payload):
        with self._write_lock:
            remote.require(self.poll() is None,
                           'SSH reservation ended before server handoff')
            if self._stdin_fd is None:
                self.process.stdin.write(payload)
                self.process.stdin.flush()
                return
            offset = 0
            deadline = time.monotonic() + 1
            while offset < len(payload):
                try:
                    count = os.write(self._stdin_fd, payload[offset:])
                except BlockingIOError:
                    remaining = deadline - time.monotonic()
                    remote.require(remaining > 0,
                                   'SSH reservation command pipe remained blocked')
                    _, writable, _ = select.select([], [self._stdin_fd], [], remaining)
                    remote.require(bool(writable),
                                   'SSH reservation command pipe remained blocked')
                    continue
                remote.require(count > 0, 'SSH reservation command pipe closed')
                offset += count

    def start_heartbeat(self):
        remote.require(self._heartbeat_thread is None,
                       'SSH reservation heartbeat already started')

        def renew_until_stopped():
            try:
                while not self._heartbeat_stop.wait(self.heartbeat_interval_seconds):
                    self._write_frame(b'{"action":"renew"}\n')
            except BaseException as error:
                self._heartbeat_error = error
                self._heartbeat_stop.set()

        self._heartbeat_thread = threading.Thread(
            target=renew_until_stopped, name='issue22-ssh-lease-heartbeat', daemon=True)
        self._heartbeat_thread.start()

    def stop_heartbeat(self):
        self._heartbeat_stop.set()
        thread = self._heartbeat_thread
        if thread is not None:
            thread.join(timeout=2)
            remote.require(not thread.is_alive(),
                           'SSH reservation heartbeat did not stop')

    def require_heartbeat(self):
        if self._heartbeat_error is not None:
            raise remote.ManagerError('SSH reservation heartbeat failed') from self._heartbeat_error

    def ensure(self, manager_id):
        remote.require(not self.ensure_sent and self.poll() is None,
                       'staging lease is no longer available for server handoff')
        remote.require(isinstance(manager_id, str) and re.fullmatch(r'[0-9a-f]{32}', manager_id),
                       'invalid manager ID for listener handoff')
        self.stop_heartbeat()
        self.require_heartbeat()
        self.ensure_sent = True
        payload = (canonical({'action': 'ensure', 'manager_id': manager_id}) + '\n').encode()
        self._write_frame(payload)
        result = _read_reservation_message(self.process.stdout, self.handoff_timeout_seconds)
        remote.require(isinstance(result, dict)
                       and set(result) == {'state', 'listener_owned'}
                       and result['state'] == 'running' and result['listener_owned'] is True,
                       'CT did not adopt the reserved fixture listener')
        self.ensure_result = result
        return result


@contextmanager
def remote_reservation(spec, *, timeout_seconds=20, handoff_timeout_seconds=45,
                       heartbeat_interval_seconds=5):
    """Hold one pinned SSH process, lock and bound listener through first ensure."""
    remote.require(spec.get('server_kind') == 'issue22-fixture',
                   'staging reservation requires issue22-fixture')
    remote.require(type(timeout_seconds) in (int, float) and timeout_seconds > 0
                   and type(handoff_timeout_seconds) in (int, float)
                   and handoff_timeout_seconds > 0
                   and type(heartbeat_interval_seconds) in (int, float)
                   and 0 < heartbeat_interval_seconds
                   <= remote.ISSUE22_LEASE_IDLE_TIMEOUT_SECONDS / 2,
                   'invalid remote reservation timeout')
    request = {'remote': spec['remote'], 'server_kind': spec['server_kind']}
    source = Path(remote.__file__).read_text()
    command = shlex.join(['/usr/bin/python3', '-c', source, 'reserve', canonical(request)])
    with verified_ssh_snapshot(spec['ssh']) as transport:
        process = subprocess.Popen([*ssh_args(transport), command], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        lease = None
        failure = None
        try:
            response = _read_reservation_message(process.stdout, timeout_seconds)
            remote.require(response == {'state': 'reserved'},
                           'CT did not grant the staging reservation')
            lease = RemoteStageLease(process, transport, timeout_seconds,
                                     handoff_timeout_seconds, heartbeat_interval_seconds)
            lease.start_heartbeat()
            yield lease
        except BaseException as error:
            failure = (error, error.__traceback__)
        finally:
            try:
                if lease is not None:
                    lease.stop_heartbeat()
                if process.stdin and not process.stdin.closed:
                    process.stdin.close()
                if lease is not None and not lease.ensure_sent:
                    response = _read_reservation_message(process.stdout, timeout_seconds)
                    remote.require(response == {'state': 'released'},
                                   'CT did not release the staging reservation cleanly')
                return_code = process.wait(timeout=timeout_seconds)
                remote.require(return_code == 0,
                               'CT staging reservation process failed')
            except BaseException as error:
                if process.poll() is None:
                    process.kill()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
                if failure is None:
                    raise error
        if failure is not None:
            error, traceback = failure
            raise error.with_traceback(traceback)


def tls_health(spec):
    endpoint, tls = spec['remote'], spec['tls']
    ca_data = read_owned_bytes(tls['ca'])
    remote.require(re.fullmatch(r'[0-9a-f]{64}', tls['ca_sha256'] or '')
                   and hashlib.sha256(ca_data).hexdigest() == tls['ca_sha256'],
                   'CA fingerprint differs')
    if spec['server_kind'] == 'artifact':
        health_path = 'index.json'
        expected_sha256 = tls['index_sha256']
        maximum = 65536
    else:
        # This one existing signed manifest is already among the 24 closed
        # fixture/release routes. Do not add a health-only route to the server.
        health_path = tls['health_path']
        expected_sha256 = tls['health_sha256']
        remote.require(health_path == ISSUE22_HEALTH_PATH,
                       'issue22-fixture health route differs from the registered catalog')
        maximum = 4096
    remote.require(re.fullmatch(r'[0-9a-f]{64}', expected_sha256 or ''),
                   'invalid HTTPS health digest')
    try:
        ca_pem = ca_data.decode('ascii')
    except UnicodeDecodeError as error:
        raise remote.ManagerError('TLS CA snapshot is not PEM text') from error
    context = ssl.create_default_context(cadata=ca_pem)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    with socket.create_connection((endpoint['bind_ip'], endpoint['port']), timeout=3) as plain:
        with context.wrap_socket(plain, server_hostname=endpoint['server_name']) as secure:
            request = (f"GET /{endpoint['campaign_id']}/{health_path} HTTP/1.1\r\n"
                       f"Host: {endpoint['server_name']}:{endpoint['port']}\r\n"
                       "Connection: close\r\n\r\n")
            secure.sendall(request.encode('ascii'))
            response = http.client.HTTPResponse(secure)
            response.begin()
            remote.require(response.status == 200, 'health endpoint status differs')
            payload = response.read(maximum + 1)
            remote.require(len(payload) <= maximum and hashlib.sha256(payload).hexdigest()
                           == expected_sha256, 'health endpoint content digest differs')
            if spec['server_kind'] == 'artifact':
                remote.require(json.loads(payload).get('campaign_id') == endpoint['campaign_id'],
                               'health endpoint campaign differs')
    return True


def load_spec(path, expected_sha256):
    raw = owned_file(path, private=True).read_bytes()
    remote.require(isinstance(expected_sha256, str) and re.fullmatch(r'[0-9a-f]{64}', expected_sha256)
                   and hashlib.sha256(raw).hexdigest() == expected_sha256,
                   'frozen server specification bytes differ')
    data = json.loads(raw)
    remote.require(isinstance(data, dict) and set(data) ==
                   {'schema', 'server_kind', 'ssh', 'remote', 'tls'}
                   and data['schema'] == 'wasmhome-user-owned-https-manager-v1'
                   and data['server_kind'] in {'artifact', 'issue22-fixture'},
                   'invalid specification')
    ssh_args(data['ssh'])
    expected_tls_fields = ({'ca', 'ca_sha256', 'index_sha256'}
                           if data['server_kind'] == 'artifact' else
                           {'ca', 'ca_sha256', 'health_path', 'health_sha256'})
    remote.require(isinstance(data['tls'], dict) and set(data['tls']) == expected_tls_fields,
                   'invalid TLS specification')
    if data['server_kind'] == 'issue22-fixture':
        remote.require(data['tls']['health_path'] == ISSUE22_HEALTH_PATH,
                       'issue22-fixture health route differs from the registered catalog')
        fields = {'root', 'script', 'python', 'fixture_root', 'ota_package_root',
                  'trusted_public_key', 'untrusted_public_key', 'ota_public_key',
                  'certificate', 'private_key', 'http_log', 'pid_file', 'bind_ip',
                  'port', 'server_name', 'campaign_id', 'aot_toolchain_id', 'log_mode'}
        server = data['remote']
        remote.require(isinstance(server, dict) and set(server) == fields,
                       'invalid issue22-fixture server specification')
        staged_root = Path(server['root'])
        expected_paths = {
            'script': staged_root / 'tools/resilience_fixture_https_server.py',
            'fixture_root': staged_root / 'fixtures',
            'ota_package_root': staged_root / 'ota-package',
            'trusted_public_key': staged_root / 'fixture-trusted.pub',
            'untrusted_public_key': staged_root / 'fixture-untrusted.pub',
            'ota_public_key': staged_root / 'ota-signing.pub',
            'certificate': staged_root / 'server.crt',
            'private_key': staged_root / 'server.key',
            'http_log': staged_root / 'http-events.jsonl',
            'pid_file': staged_root / 'server.pid',
        }
        remote.require(Path(server['python']) == Path('/usr/bin/python3')
                       and all(Path(server[key]) == value for key, value in expected_paths.items())
                       and type(server['log_mode']) is int and server['log_mode'] == 0o600,
                       'issue22-fixture staging layout differs')
        remote.require(type(server['port']) is int and 1024 <= server['port'] <= 65535
                       and server['port'] not in {8443, 8444},
                       'invalid or reserved issue22-fixture port')
    # These checks are repeated on CT. Validate local values before shell/HTTP serialization.
    import ipaddress
    address = ipaddress.IPv4Address(data['remote']['bind_ip'])
    remote.require(not (address.is_unspecified or address.is_loopback or address.is_multicast),
                   'invalid endpoint address')
    remote.require(type(data['remote']['port']) is int and 1024 <= data['remote']['port'] <= 65535,
                   'HTTPS port must be unprivileged')
    remote.require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}', data['remote']['server_name']),
                   'invalid TLS server name')
    remote.require(re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,63}', data['remote']['campaign_id']),
                   'invalid artifact campaign ID')
    return data


def safe_journal(path, server_kind='artifact'):
    remote.require(path is not None, 'ensure requires a hardware journal')
    value = json.loads(owned_file(path, private=True).read_text())
    safe_states = ({'ready', 'complete', 'recovered_interruption'}
                   if server_kind == 'artifact' else
                   {'ready', 'row_complete', 'complete', 'interrupted_recovered'})
    maximum_index = 16 if server_kind == 'issue22-fixture' else None
    if server_kind == 'issue22-fixture':
        remote.require(value.get('schema') == 'wasmhome-issue22-hil-orchestrator-v1'
                       and value.get('current_attempt') is None
                       and value.get('current_plan_index') is None,
                       'issue #22 journal identity or active-row marker differs')
    remote.require(value.get('restore_required') is False
                   and value.get('state') in safe_states
                   and type(value.get('next_index')) is int and value['next_index'] >= 0
                   and (maximum_index is None or value['next_index'] <= maximum_index),
                   'hardware journal is not safe for server ensure')
    return value


def cleanup_saved(state, state_path):
    # This is deliberately independent of spec_path and the hardware journal.
    # It never reads/writes a claim, slot index, serial port or flash image.
    state.update(cleanup_required=True, cleanup_pending=True)
    atomic_private(state_path, state)
    try:
        result = remote_action('stop', state['spec'], state['manager_id'])
        remote.require(result['state'] in {'stopped', 'absent'} and not result['listener_owned'],
                       'owned server cleanup did not finish')
    except BaseException as error:
        state['last_cleanup_error'] = type(error).__name__
        atomic_private(state_path, state)
        raise
    state.update(cleanup_required=False, cleanup_pending=False, last_result=result)
    state.pop('last_cleanup_error', None)
    atomic_private(state_path, state)
    return dict(result, tls_verified=False)


def health_ready(action, spec, manager_id, result, deadline_seconds):
    result = dict(result, tls_verified=False)
    if action == 'ensure':
        remote.require(result['state'] == 'running', 'owned server did not start')
    if result['state'] != 'running':
        return result
    deadline = time.monotonic() + (deadline_seconds if action == 'ensure' else 0)
    while True:
        try:
            remote.require(result['listener_owned'], 'server has not bound its endpoint')
            tls_health({'remote': spec['remote'], 'tls': spec['tls'],
                        'server_kind': spec['server_kind']})
            after = remote_action('status', spec, manager_id)
            remote.require(after['state'] == 'running' and after['listener_owned'],
                           'server identity changed during health verification')
            result['tls_verified'] = True
            return result
        except (OSError, ValueError, http.client.HTTPException):
            if time.monotonic() >= deadline:
                if action == 'ensure':
                    raise remote.ManagerError('TLS/endpoint readiness failed')
                return result
            time.sleep(0.25)
            result = dict(remote_action('status', spec, manager_id), tls_verified=False)


def operate(action, spec_path, state_path, *, expected_spec_sha256,
            journal_path=None, deadline_seconds=30, reservation=None):
    remote.require(action in {'ensure', 'status', 'stop'}, 'invalid action')
    remote.require(os.geteuid() != 0, 'local manager must run as an ordinary user')
    remote.require(isinstance(expected_spec_sha256, str)
                   and re.fullmatch(r'[0-9a-f]{64}', expected_spec_sha256), 'invalid frozen spec hash')
    state_path = Path(state_path).absolute()
    info = state_path.parent.lstat()
    remote.require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
                   and stat.S_IMODE(info.st_mode) == 0o700, 'state parent must be private 0700')
    lock_path = state_path.with_name(state_path.name + '.lock')
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        owned_file(lock_path, private=True)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = None
        if state_path.exists() or state_path.is_symlink():
            state = json.loads(owned_file(state_path, private=True).read_text())
            remote.require(state.get('schema') == 'wasmhome-ct107-server-manager-state-v2'
                           and state.get('spec_bytes_sha256') == expected_spec_sha256
                           and hashlib.sha256(canonical(state.get('spec')).encode()).hexdigest()
                           == state.get('spec_sha256'), 'local ownership specification differs')
        if action == 'stop':
            if state is None:
                return {'state': 'uninitialized', 'listener_owned': False, 'tls_verified': False}
            return cleanup_saved(state, state_path)
        if action == 'status':
            if state is None:
                return {'state': 'uninitialized', 'listener_owned': False, 'tls_verified': False}
            with verified_operation_spec(state['spec']) as operation_spec:
                result = health_ready(action, operation_spec, state['manager_id'],
                                      remote_action(action, operation_spec,
                                                    state['manager_id']), 0)
            result['cleanup_pending'] = state.get('cleanup_pending', False)
            return result
        try:
            # An unsafe journal may trigger cleanup-only, but never an ensure.
            spec = load_spec(spec_path, expected_spec_sha256)
            safe_journal(journal_path, spec['server_kind'])
            if state is None:
                state = {'schema': 'wasmhome-ct107-server-manager-state-v2',
                         'spec_bytes_sha256': expected_spec_sha256,
                         'spec_sha256': hashlib.sha256(canonical(spec).encode()).hexdigest(),
                         'spec': spec, 'manager_id': uuid.uuid4().hex}
            # Persist cleanup intent before every possible remote launch/reuse.
            state.update(cleanup_required=True, cleanup_pending=True)
            atomic_private(state_path, state)
            safe_journal(journal_path, spec['server_kind'])
            with verified_operation_spec(spec) as operation_spec:
                initial = (reservation.ensure(state['manager_id']) if reservation is not None
                           else remote_action(action, operation_spec, state['manager_id']))
                result = health_ready(action, operation_spec, state['manager_id'],
                                      initial, deadline_seconds)
            state.update(last_result=result, cleanup_pending=False)
            state.pop('last_error', None)
            atomic_private(state_path, state)
            return result
        except BaseException as original:
            if state is not None:
                state['last_error'] = type(original).__name__
                try:
                    cleanup_saved(state, state_path)
                except BaseException:
                    # The durable pending flag was written before launching;
                    # later cleanup-only needs no journal/spec file or slot.
                    pass
            raise
    finally:
        os.close(descriptor)


def saved_spec_bytes_sha256(state_path):
    """Read the hash needed for cleanup-only recovery, without loading a spec."""
    state_path = Path(state_path).absolute()
    parent = state_path.parent.lstat()
    remote.require(stat.S_ISDIR(parent.st_mode) and parent.st_uid == os.getuid()
                   and stat.S_IMODE(parent.st_mode) == 0o700,
                   'state parent must be private 0700')
    if not state_path.exists() and not state_path.is_symlink():
        return None
    state = json.loads(owned_file(state_path, private=True).read_text())
    digest = state.get('spec_bytes_sha256')
    remote.require(state.get('schema') == 'wasmhome-ct107-server-manager-state-v2'
                   and isinstance(digest, str) and re.fullmatch(r'[0-9a-f]{64}', digest)
                   and hashlib.sha256(canonical(state.get('spec')).encode()).hexdigest()
                   == state.get('spec_sha256'), 'saved manager ownership state is invalid')
    return digest


def stop_saved(state_path):
    """Cleanup the manager-owned process using only its saved private receipt."""
    state_path = Path(state_path).absolute()
    digest = saved_spec_bytes_sha256(state_path)
    if digest is None:
        return {'state': 'uninitialized', 'listener_owned': False, 'tls_verified': False}
    # ``operate(stop)`` intentionally does not read spec_path or the hardware
    # journal. It rechecks the saved digest under the manager lock.
    return operate('stop', state_path.with_name('.unused-server-spec.json'), state_path,
                   expected_spec_sha256=digest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('ensure', 'status', 'stop'))
    parser.add_argument('--spec', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--spec-sha256', required=True)
    parser.add_argument('--journal', type=Path)
    options = parser.parse_args()
    try:
        print(canonical(operate(options.action, options.spec, options.state,
                                expected_spec_sha256=options.spec_sha256, journal_path=options.journal)))
    except (OSError, ValueError, subprocess.SubprocessError, http.client.HTTPException) as error:
        # The spec/state and remote launch log retain private details.
        parser.exit(1, f'CT user server operation failed ({type(error).__name__}); ownership retained\n')


if __name__ == '__main__':
    main()
