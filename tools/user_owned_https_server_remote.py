"""Remote half of the user-owned CT server manager; sent over pinned SSH.

This candidate is not installed or invoked on CT by its test suite.
"""
import fcntl
import errno
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import pwd
import select
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import uuid


ISSUE22_LISTEN_FD = 3
ISSUE22_LEASE_IDLE_TIMEOUT_SECONDS = 30


class ManagerError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ManagerError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def private_file(path):
    info = Path(path).lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o600, "unsafe private file")


def validate_campaign_root(value, *, allow_missing=False, home=None, uid=None):
    path = Path(value)
    home = Path.home() if home is None else Path(home)
    uid = os.getuid() if uid is None else uid
    require(path.is_absolute() and path.as_posix() == str(value)
            and ".." not in path.parts, "invalid absolute campaign root")
    require(home.is_absolute() and home == Path(os.path.normpath(str(home)))
            and home in path.parents and path != home,
            "campaign root must be below the CT account home")
    for ancestor in reversed(home.parents):
        try:
            info = ancestor.lstat()
        except FileNotFoundError:
            continue
        require(stat.S_ISDIR(info.st_mode), "unsafe account-home ancestor")
    try:
        home_info = home.lstat()
    except FileNotFoundError:
        raise ManagerError("CT account home is missing")
    require(stat.S_ISDIR(home_info.st_mode) and home_info.st_uid == uid
            and not home_info.st_mode & 0o022, "unsafe CT account home")
    for ancestor in reversed(path.parents):
        if ancestor == home or home in ancestor.parents:
            try:
                info = ancestor.lstat()
            except FileNotFoundError:
                continue
            require(stat.S_ISDIR(info.st_mode) and info.st_uid == uid
                    and not info.st_mode & 0o022, "unsafe campaign path ancestor")
    try:
        info = path.lstat()
    except FileNotFoundError:
        require(allow_missing, "campaign root is missing")
        return path
    require(info.st_uid == uid and stat.S_ISDIR(info.st_mode)
            and stat.S_IMODE(info.st_mode) == 0o700,
            "campaign root must be user-owned and private 0700")
    return path


def safe_path(value, *, kind, root=None):
    path = Path(value)
    require(path.is_absolute() and ".." not in path.parts, "invalid absolute path")
    home = Path.home()
    require(home in path.parents, "path must be below account home")
    if root:
        require(Path(root) in path.parents, "path escapes managed campaign root")
    for ancestor in reversed(path.parents):
        if ancestor == home or home in ancestor.parents:
            info = ancestor.lstat()
            require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
                    and not info.st_mode & 0o022, "unsafe path ancestor")
    info = path.lstat()
    require(info.st_uid == os.getuid(), "path owner differs")
    if kind == "dir":
        require(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700,
                "campaign root must be private")
    elif kind == "secret":
        private_file(path)
    else:
        require(stat.S_ISREG(info.st_mode) and not info.st_mode & 0o022,
                "unsafe staged file")
    return path


def validate_spec(spec, server_kind, *, staged_files=True, allow_missing_root=False):
    common = {"root", "script", "python", "certificate", "private_key", "http_log",
              "bind_ip", "port", "server_name", "campaign_id", "aot_toolchain_id"}
    fields = (common | {"package", "signing_public_key"} if server_kind == "artifact" else
              common | {"fixture_root", "trusted_public_key", "untrusted_public_key",
                        "ota_package_root", "ota_public_key", "pid_file", "log_mode"})
    require(server_kind in {"artifact", "issue22-fixture"}
            and isinstance(spec, dict) and set(spec) == fields,
            "invalid remote specification")
    root = validate_campaign_root(spec["root"], allow_missing=allow_missing_root)
    expected_script = ("artifact_https_server.py" if server_kind == "artifact"
                       else "resilience_fixture_https_server.py")
    require(Path(spec["script"]).name == expected_script, "unsupported server kind/script pair")
    if server_kind == "artifact":
        staged = ("script", "certificate", "signing_public_key", "private_key", "package")
        directories = ("package",)
        secrets = ("private_key",)
    else:
        staged = ("script", "certificate", "private_key", "trusted_public_key",
                  "untrusted_public_key", "ota_public_key", "fixture_root", "ota_package_root")
        directories = ("fixture_root", "ota_package_root")
        secrets = ("private_key",)
        expected_paths = {
            "script": root / "tools/resilience_fixture_https_server.py",
            "fixture_root": root / "fixtures",
            "ota_package_root": root / "ota-package",
            "trusted_public_key": root / "fixture-trusted.pub",
            "untrusted_public_key": root / "fixture-untrusted.pub",
            "ota_public_key": root / "ota-signing.pub",
            "certificate": root / "server.crt",
            "private_key": root / "server.key",
            "http_log": root / "http-events.jsonl",
            "pid_file": root / "server.pid",
        }
        require(all(Path(spec[key]) == path for key, path in expected_paths.items()),
                "issue22-fixture staged layout differs from the campaign server")
        require(type(spec["log_mode"]) is int and spec["log_mode"] == 0o600,
                "issue22-fixture log mode must be private 0600")
    for key in staged:
        path = Path(spec[key])
        require(path.is_absolute() and root in path.parents and '..' not in path.parts,
                "staged path escapes campaign root")
    if staged_files:
        for key in staged:
            if key in directories:
                safe_path(spec[key], kind="dir", root=root)
            elif key in secrets:
                safe_path(spec[key], kind="secret", root=root)
            else:
                safe_path(spec[key], kind="file", root=root)
    require(spec["python"] == "/usr/bin/python3", "unsupported interpreter")
    address = ipaddress.IPv4Address(spec["bind_ip"])
    require(not (address.is_unspecified or address.is_loopback or address.is_multicast),
            "invalid endpoint address")
    require(type(spec["port"]) is int and 1024 <= spec["port"] <= 65535,
            "HTTPS port must be unprivileged")
    if server_kind == "issue22-fixture":
        require(spec["port"] not in {8443, 8444},
                "issue22-fixture port is reserved for another campaign")
    import re
    for key, pattern in (("campaign_id", r"[a-z0-9][a-z0-9._-]{0,63}"),
                         ("server_name", r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}"),
                         ("aot_toolchain_id", r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")):
        require(isinstance(spec[key], str) and re.fullmatch(pattern, spec[key]), "invalid server identity")
    log = Path(spec["http_log"])
    require(log.parent == root and log.name not in {"server-manager.json", ".server-manager.lock"},
            "HTTP log must be directly below campaign root")
    if staged_files and (log.exists() or log.is_symlink()):
        private_file(log)
    if server_kind == "issue22-fixture":
        receipt = Path(spec["pid_file"])
        require(receipt.parent == root and receipt.name == "server.pid",
                "issue22-fixture PID receipt must stay in campaign root")
        if staged_files and (receipt.exists() or receipt.is_symlink()):
            private_file(receipt)
    return root


def port_owners(bind_ip, port, proc=Path('/proc')):
    """Read listener socket inodes and map them to visible process UIDs/PIDs."""
    expected_ip = socket.inet_aton(bind_ip)[::-1].hex().upper()
    expected_port = f":{port:04X}"
    listeners = {}
    for table in ('tcp', 'tcp6'):
        try:
            rows = (Path(proc) / 'net' / table).read_text().splitlines()[1:]
        except FileNotFoundError:
            continue
        for row in rows:
            fields = row.split()
            if len(fields) < 10 or fields[3] != '0A' or not fields[1].endswith(expected_port):
                continue
            local_ip = fields[1][:-len(expected_port)]
            if table == 'tcp' and local_ip not in {expected_ip, '00000000'}:
                continue
            # IPv6 same-port listeners are conservatively treated as owners;
            # a dual-stack wildcard can conflict with this IPv4 endpoint.
            inode = fields[9]
            listeners[inode] = int(fields[7])
    if not listeners:
        return []
    pid_by_inode = {}
    for process in Path(proc).iterdir():
        if not process.name.isdecimal():
            continue
        try:
            for descriptor in (process / 'fd').iterdir():
                try:
                    target = os.readlink(descriptor)
                except (FileNotFoundError, PermissionError):
                    continue
                if target.startswith('socket:[') and target[8:-1] in listeners:
                    pid_by_inode.setdefault(target[8:-1], int(process.name))
        except (FileNotFoundError, PermissionError, NotADirectoryError):
            continue
    return sorted(({'uid': uid, 'pid': pid_by_inode.get(inode)}
                   for inode, uid in listeners.items()),
                  key=lambda item: (item['uid'], item['pid'] or 0))


def preflight_issue22(spec, *, proc=Path('/proc'), lock_held=False):
    """Read existing campaign receipts and the configured listener without writes."""
    root = validate_spec(spec, 'issue22-fixture', staged_files=False,
                         allow_missing_root=True)
    manager_receipt = root / 'server-manager.json'
    server_receipt = Path(spec['pid_file'])
    lock_path = root / '.server-manager.lock'
    manager_busy = False
    if lock_held:
        private_file(lock_path)
    elif lock_path.exists() or lock_path.is_symlink():
        private_file(lock_path)
        descriptor = os.open(lock_path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                manager_busy = True
            else:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
    for receipt in (manager_receipt, server_receipt):
        if receipt.exists() or receipt.is_symlink():
            private_file(receipt)
    return {
        'campaign_root_exists': root.exists(),
        'manager_busy': manager_busy,
        'manager_receipt_present': manager_receipt.exists() or manager_receipt.is_symlink(),
        'server_pid_receipt_present': server_receipt.exists() or server_receipt.is_symlink(),
        'port_owners': port_owners(spec['bind_ip'], spec['port'], proc=proc),
    }


def _require_clean_issue22_preflight(result):
    require(not result['manager_busy']
            and not result['manager_receipt_present']
            and not result['server_pid_receipt_present']
            and not result['port_owners'],
            "active manager, existing server receipt or TCP listener found")


def create_campaign_root(value, *, home=None, uid=None):
    """Create only missing, private path components below a validated CT home."""
    path = Path(value)
    home = Path.home() if home is None else Path(home)
    uid = os.getuid() if uid is None else uid
    validate_campaign_root(path, allow_missing=True, home=home, uid=uid)
    relative = path.relative_to(home)
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(home, flags)
    try:
        for component in relative.parts:
            created = False
            try:
                os.mkdir(component, 0o700, dir_fd=descriptor)
                created = True
            except FileExistsError:
                pass
            info = os.stat(component, dir_fd=descriptor, follow_symlinks=False)
            require(stat.S_ISDIR(info.st_mode) and info.st_uid == uid
                    and not info.st_mode & 0o022,
                    "unsafe campaign path component")
            if created:
                require(stat.S_IMODE(info.st_mode) == 0o700,
                        "new campaign path component is not private 0700")
            child = os.open(component, flags, dir_fd=descriptor)
            child_info = os.fstat(child)
            if (info.st_dev, info.st_ino) != (child_info.st_dev, child_info.st_ino):
                os.close(child)
                raise ManagerError("campaign path changed during reservation")
            os.close(descriptor)
            descriptor = child
    finally:
        os.close(descriptor)
    validate_campaign_root(path, home=home, uid=uid)
    return path


def _bind_listener(bind_ip, port):
    """Bind before staging so no unrelated process can take the endpoint."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # Deliberately leave SO_REUSEADDR/SO_REUSEPORT disabled. This is a
        # reservation socket and the fixture server will inherit this socket.
        require(listener.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR) == 0,
                "unexpected listener address-reuse option")
        if hasattr(socket, "SO_REUSEPORT"):
            require(listener.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT) == 0,
                    "unexpected listener port-reuse option")
        listener.bind((bind_ip, port))
        listener.listen(socket.SOMAXCONN)
        return listener
    except BaseException:
        listener.close()
        raise


def _reserve_listener_for_ensure(spec, manager_id, *, proc=Path('/proc')):
    """Reserve a fresh fixture socket only for this manager's stopped server."""
    current = status(spec, 'issue22-fixture', manager_id)
    if current['state'] == 'running':
        require(current['listener_owned'],
                "managed fixture process is running without its expected listener")
        return None
    require(current['state'] in {'absent', 'stopped', 'stale_reboot'},
            "managed fixture state is not safe to relaunch")
    checked = preflight_issue22(spec, proc=proc, lock_held=True)
    require(not checked['manager_busy'] and not checked['port_owners'],
            "fixture endpoint is occupied during managed relaunch")
    if current['state'] == 'absent':
        require(not checked['manager_receipt_present']
                and not checked['server_pid_receipt_present'],
                "unowned fixture receipt blocks managed relaunch")
    else:
        require(checked['manager_receipt_present'],
                "stopped fixture lost its manager ownership receipt")
    listener = _bind_listener(spec['bind_ip'], spec['port'])
    try:
        return _move_listener_to_stable_fd(listener)
    except BaseException:
        listener.close()
        raise


def _move_lock_above_listener_fd(descriptor):
    """Keep the lock away from the stable issue22 listener descriptor 3."""
    if descriptor != ISSUE22_LISTEN_FD:
        return descriptor
    minimum = ISSUE22_LISTEN_FD + 1
    command = getattr(fcntl, "F_DUPFD_CLOEXEC", fcntl.F_DUPFD)
    moved = fcntl.fcntl(descriptor, command, minimum)
    os.close(descriptor)
    return moved


def _move_listener_to_stable_fd(listener):
    descriptor = listener.fileno()
    if descriptor == ISSUE22_LISTEN_FD:
        os.set_inheritable(descriptor, False)
        return listener
    try:
        fcntl.fcntl(ISSUE22_LISTEN_FD, fcntl.F_GETFD)
    except OSError as error:
        if error.errno != errno.EBADF:
            raise
    else:
        raise ManagerError("stable fixture listener descriptor is unexpectedly occupied")
    os.dup2(descriptor, ISSUE22_LISTEN_FD, inheritable=False)
    listener.close()
    return socket.socket(socket.AF_INET, socket.SOCK_STREAM, fileno=ISSUE22_LISTEN_FD)


def reserve_issue22(spec, *, proc=Path('/proc'), fixed_listener_fd=True):
    """Hold the manager lock and bound endpoint across staging and handoff."""
    root = validate_spec(spec, 'issue22-fixture', staged_files=False,
                         allow_missing_root=True)
    # First refuse all existing owners without changing the filesystem.
    initial = preflight_issue22(spec, proc=proc)
    _require_clean_issue22_preflight(initial)
    create_campaign_root(root)
    lock_path = root / '.server-manager.lock'
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    listener = None
    try:
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                and stat.S_IMODE(info.st_mode) == 0o600,
                "unsafe manager reservation lock")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ManagerError("campaign manager is active") from error
        # Close the gap between the read-only preflight and lock acquisition.
        # All manager actions use this same lock, so no cooperative manager can
        # create a receipt or bind the configured port until release.
        locked = preflight_issue22(spec, proc=proc, lock_held=True)
        _require_clean_issue22_preflight(locked)
        descriptor = _move_lock_above_listener_fd(descriptor) if fixed_listener_fd else descriptor
        listener = _bind_listener(spec["bind_ip"], spec["port"])
        if fixed_listener_fd:
            try:
                listener = _move_listener_to_stable_fd(listener)
            except BaseException:
                listener.close()
                raise
        actual = listener.getsockname()
        require(listener.family == socket.AF_INET
                and actual == (spec["bind_ip"], spec["port"])
                and listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) == 1,
                "reserved fixture listener identity differs")
        return descriptor, listener
    except BaseException:
        if listener is not None:
            listener.close()
        os.close(descriptor)
        raise


def server_argv(spec, server_kind):
    if server_kind == "artifact":
        return [spec["python"], spec["script"], "--package", spec["package"],
                "--certificate", spec["certificate"], "--private-key", spec["private_key"],
                "--signing-public-key", spec["signing_public_key"], "--log", spec["http_log"],
                "--bind", spec["bind_ip"], "--port", str(spec["port"]),
                "--server-name", spec["server_name"], "--server-ip", spec["bind_ip"],
                "--expected-campaign-id", spec["campaign_id"],
                "--expected-aot-toolchain-id", spec["aot_toolchain_id"]]
    require(server_kind == "issue22-fixture", "unsupported server kind")
    args = [spec["python"], spec["script"],
            "--fixture-root", spec["fixture_root"],
            "--trusted-public-key", spec["trusted_public_key"],
            "--untrusted-public-key", spec["untrusted_public_key"],
            "--ota-package-root", spec["ota_package_root"],
            "--ota-public-key", spec["ota_public_key"],
            "--certificate", spec["certificate"], "--private-key", spec["private_key"],
            "--log", spec["http_log"], "--bind", spec["bind_ip"],
            "--port", str(spec["port"]), "--server-name", spec["server_name"],
            "--server-ip", spec["bind_ip"], "--campaign-id", spec["campaign_id"],
            "--aot-toolchain-id", spec["aot_toolchain_id"],
            "--log-mode", format(spec["log_mode"], "o"), "--pid-file", spec["pid_file"]]
    args.extend(["--listen-fd", str(ISSUE22_LISTEN_FD)])
    return args


def current_boot_id():
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def process_identity(pid, proc=Path("/proc")):
    try:
        root = proc / str(pid)
        raw = (root / "stat").read_text().rsplit(")", 1)[1].split()
        argv = [v.decode("utf-8") for v in (root / "cmdline").read_bytes().split(b"\0") if v]
        return {"uid": root.stat().st_uid, "start_ticks": raw[19], "argv": argv}
    except FileNotFoundError:
        return None


def inspect_receipt(receipt, spec, server_kind, manager_id, boot_id, proc=Path("/proc")):
    require(receipt.get("schema") == "wasmhome-ct107-server-process-v2", "unknown process receipt")
    require(receipt.get("manager_id") == manager_id, "server belongs to another manager")
    expected = server_argv(spec, server_kind)
    require(receipt.get("argv") == expected and receipt.get("spec_sha256") ==
            hashlib.sha256(canonical(spec).encode()).hexdigest(), "server specification differs")
    require(type(receipt.get("pid")) is int and receipt["pid"] > 1,
            "invalid process PID")
    require(isinstance(receipt.get("start_ticks"), str) and receipt["start_ticks"].isdecimal(),
            "invalid process start time")
    # Never inspect or signal a potentially reused PID from another boot.
    if receipt.get("boot_id") != boot_id:
        return "stale_reboot"
    actual = process_identity(receipt["pid"], proc)
    if actual is None:
        return "stopped"
    require(actual == {"uid": os.getuid(), "start_ticks": receipt["start_ticks"], "argv": expected},
            "live process identity differs; no signal permitted")
    return "running"


def owns_listener(pid, bind_ip, port, proc=Path("/proc")):
    expected = socket.inet_aton(bind_ip)[::-1].hex().upper() + f":{port:04X}"
    inodes = set()
    try:
        for fd in (proc / str(pid) / "fd").iterdir():
            try:
                target = os.readlink(fd)
                if target.startswith("socket:["):
                    inodes.add(target[8:-1])
            except FileNotFoundError:
                continue
        for row in (proc / "net/tcp").read_text().splitlines()[1:]:
            fields = row.split()
            if fields[1] == expected and fields[3] == "0A" and fields[9] in inodes:
                return True
    except FileNotFoundError:
        return False
    return False


def read_receipt(path):
    private_file(path)
    require(path.stat().st_size < 65536, "oversized process receipt")
    return json.loads(path.read_text())


def archive_receipt(path):
    path.rename(path.with_name(path.name + ".retired-" + uuid.uuid4().hex))
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def remove_issue22_pid_receipt(spec, manager_record):
    """Remove only the fixture server's receipt bound to our exact manager PID."""
    path = Path(spec["pid_file"])
    if not path.exists() and not path.is_symlink():
        return
    safe_path(path, kind="secret", root=Path(spec["root"]))
    expected = f"{manager_record['pid']} {manager_record['start_ticks']}\n".encode("ascii")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        data = os.read(descriptor, 128)
    finally:
        os.close(descriptor)
    require(data == expected, "fixture PID receipt does not match manager-owned process")
    path.unlink()
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


# The nohup child records its own identity atomically before exec. If the parent
# SSH disconnects during launch, this receipt still lets the next ensure/stop
# identify precisely the one process which the manager owns.
LAUNCHER = r'''
import json,os,pathlib,sys,tempfile
receipt=pathlib.Path(sys.argv[1]); record=json.loads(sys.argv[2])
# Retain the parent's exclusive flock through receipt publication and exec.
# FD_CLOEXEC closes this inherited descriptor only once server argv is active.
os.set_inheritable(int(sys.argv[3]),False)
if len(sys.argv) > 4:
 os.set_inheritable(int(sys.argv[4]),True)
record['pid']=os.getpid()
record['start_ticks']=pathlib.Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19]
record['boot_id']=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
fd,tmp=tempfile.mkstemp(prefix='.server-manager-',dir=receipt.parent)
try:
 with os.fdopen(fd,'w') as stream:
  json.dump(record,stream,sort_keys=True); stream.write('\n');stream.flush();os.fsync(stream.fileno())
 os.link(tmp,receipt)
finally:
 os.unlink(tmp)
directory=os.open(receipt.parent,os.O_RDONLY|os.O_DIRECTORY)
try:os.fsync(directory)
finally:os.close(directory)
os.execv(record['argv'][0],record['argv'])
'''


def status(spec, server_kind, manager_id):
    path = Path(spec["root"]) / "server-manager.json"
    if not path.exists() and not path.is_symlink():
        return {"state": "absent", "listener_owned": False}
    record = read_receipt(path)
    state = inspect_receipt(record, spec, server_kind, manager_id, current_boot_id())
    return {"state": state, "listener_owned": state == "running" and
            owns_listener(record["pid"], spec["bind_ip"], spec["port"])}


def _validate_reserved_listener(spec, listener_fd):
    require(type(listener_fd) is int and listener_fd == ISSUE22_LISTEN_FD,
            "reserved fixture listener must use stable descriptor 3")
    try:
        listener = socket.socket(fileno=os.dup(listener_fd))
    except OSError as error:
        raise ManagerError("reserved fixture listener descriptor is invalid") from error
    try:
        address = listener.getsockname()
        require(listener.family == socket.AF_INET
                and listener.type & socket.SOCK_STREAM
                and address == (spec['bind_ip'], spec['port'])
                and listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) == 1
                and listener.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR) == 0,
                "reserved fixture listener identity differs")
        if hasattr(socket, "SO_REUSEPORT"):
            require(listener.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT) == 0,
                    "reserved fixture listener permits port sharing")
    finally:
        listener.close()


def _read_reservation_line(input_stream, buffered, timeout):
    """Read one bounded newline frame, expiring an idle remote lease."""
    try:
        descriptor = input_stream.fileno()
    except (AttributeError, OSError, ValueError):
        # In-memory streams are used by the offline protocol tests. Production
        # uses stdin's real descriptor and always takes the timed path below.
        return input_stream.readline(8193)
    while b'\n' not in buffered:
        ready, _, _ = select.select([descriptor], [], [], timeout)
        if not ready:
            return None
        chunk = os.read(descriptor, 8193 - len(buffered))
        if not chunk:
            if not buffered:
                return b''
            break
        buffered.extend(chunk)
        require(len(buffered) <= 8192 or b'\n' in buffered,
                'oversized remote reservation command')
    newline = buffered.find(b'\n')
    if newline < 0:
        line = bytes(buffered)
        buffered.clear()
        return line
    line = bytes(buffered[:newline + 1])
    del buffered[:newline + 1]
    return line


def ensure(spec, server_kind, manager_id, *, lock_fd=None, listener=None):
    try:
        return _ensure(spec, server_kind, manager_id, lock_fd=lock_fd,
                       listener=listener)
    finally:
        if listener is not None:
            listener.close()


def _ensure(spec, server_kind, manager_id, *, lock_fd, listener):
    root = Path(spec["root"])
    path = root / "server-manager.json"
    result = status(spec, server_kind, manager_id)
    if result["state"] == "running":
        require(server_kind != "issue22-fixture" or listener is None,
                "fixture listener was reserved while a managed server is already running")
        return result
    require(type(lock_fd) is int and lock_fd >= 0, "exclusive launch lock required")
    if server_kind == "issue22-fixture":
        require(listener is not None, "fixture launch requires a reserved listener socket")
        _validate_reserved_listener(spec, listener.fileno())
    if result["state"] != "absent":
        prior = read_receipt(path)
        if server_kind == "issue22-fixture":
            remove_issue22_pid_receipt(spec, prior)
        archive_receipt(path)
    elif server_kind == "issue22-fixture":
        pid_receipt = Path(spec["pid_file"])
        require(not pid_receipt.exists() and not pid_receipt.is_symlink(),
                "fixture PID receipt has no matching manager ownership receipt")
    expected = server_argv(spec, server_kind)
    for candidate in Path('/proc').iterdir():
        try:
            if not candidate.name.isdecimal() or candidate.stat().st_uid != os.getuid():
                continue
            identity = process_identity(int(candidate.name))
        except FileNotFoundError:
            continue
        require(not identity or identity["argv"] != expected,
                "matching server has no manager ownership receipt; refusing adoption")
    log = root / ("server-manager-launch-" + uuid.uuid4().hex + ".log")
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    # Precreate the event log privately. Each server mode uses its own closed
    # catalog and existing HTTP paths; the manager does not stage or rewrite it.
    http_fd = os.open(spec['http_log'], os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    os.close(http_fd)
    record = {"schema": "wasmhome-ct107-server-process-v2", "manager_id": manager_id,
              "spec_sha256": hashlib.sha256(canonical(spec).encode()).hexdigest(),
              "argv": expected, "launch_log": str(log)}
    listener_fd = listener.fileno() if listener is not None else None
    inherited = (lock_fd, listener_fd) if server_kind == "issue22-fixture" else (lock_fd,)
    launcher_args = ["/usr/bin/nohup", spec["python"], "-c", LAUNCHER,
                     str(path), canonical(record), str(lock_fd)]
    if server_kind == "issue22-fixture":
        launcher_args.append(str(listener_fd))
    with os.fdopen(fd, "wb") as output:
        child = subprocess.Popen(launcher_args, stdin=subprocess.DEVNULL,
                                 stdout=output, stderr=subprocess.STDOUT, close_fds=True,
                                 pass_fds=inherited,
                                 start_new_session=True, env={"PATH": "/usr/bin:/bin",
                                 "HOME": str(Path.home()), "LANG": "C.UTF-8"})
    if listener is not None:
        # Popen has copied FD 3. Close the helper's copy immediately so the
        # fixture process is the sole listener owner during readiness polling.
        listener.close()
    for _ in range(40):
        if child.poll() is not None:
            raise ManagerError("server launch failed; private launch log retained")
        if path.exists():
            try:
                result = status(spec, server_kind, manager_id)
                if (result["state"] == "running"
                        and (server_kind != "issue22-fixture" or result["listener_owned"])):
                    return result
            except ManagerError:
                pass  # Short receipt-before-exec window; never signal here.
        time.sleep(0.05)
    raise ManagerError("server launch identity did not become ready; ownership retained")


def stop(spec, server_kind, manager_id):
    path = Path(spec["root"]) / "server-manager.json"
    result = status(spec, server_kind, manager_id)
    if result["state"] == "absent":
        return result
    receipt = read_receipt(path)
    if result["state"] != "running":
        if server_kind == "issue22-fixture":
            remove_issue22_pid_receipt(spec, receipt)
        archive_receipt(path)
        return {"state": "stopped", "listener_owned": False}
    require(hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal"),
            "pidfd support required for race-safe termination")
    try:
        descriptor = os.pidfd_open(receipt["pid"])
    except ProcessLookupError:
        # Process exited after status, so no signal is needed.
        if server_kind == "issue22-fixture":
            remove_issue22_pid_receipt(spec, receipt)
        archive_receipt(path)
        return {"state": "stopped", "listener_owned": False}
    try:
        # Revalidate after opening pidfd; reused PIDs cannot receive this signal.
        state = inspect_receipt(receipt, spec, server_kind, manager_id, current_boot_id())
        if state == "running":
            try:
                signal.pidfd_send_signal(descriptor, signal.SIGTERM)
            except ProcessLookupError:
                pass
        poller = select.poll()
        poller.register(descriptor, select.POLLIN)
        require(bool(poller.poll(5000)), "owned server did not exit; receipt retained")
    finally:
        os.close(descriptor)
    if server_kind == "issue22-fixture":
        remove_issue22_pid_receipt(spec, receipt)
    archive_receipt(path)
    return {"state": "stopped", "listener_owned": False}


def dispatch(action, spec, server_kind, manager_id, root):
    lock_path = root / '.server-manager.lock'
    if action == "status":
        # A read-only status never creates a remote file. Existing deployments
        # share the same flock as ensure/stop, including the inherited launch
        # lock which survives parent death until receipt-before-exec completes.
        try:
            fd = os.open(lock_path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            receipt = root / 'server-manager.json'
            require(not receipt.exists() and not receipt.is_symlink(), "receipt has no serialization lock")
            return {"state": "absent", "listener_owned": False}
    else:
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    listener = None
    try:
        private_file(lock_path)
        fcntl.flock(fd, fcntl.LOCK_SH if action == "status" else fcntl.LOCK_EX)
        if action == "status":
            return status(spec, server_kind, manager_id)
        if action == 'ensure' and server_kind == 'issue22-fixture':
            # A later before_child may find that the prior managed fixture
            # exited between rows. Under the same exclusive lock, reserve the
            # endpoint again only after the exact manager receipt proves the
            # old process is stopped and no other listener owns the port.
            current = status(spec, server_kind, manager_id)
            if current['state'] == 'running':
                require(current['listener_owned'],
                        "managed fixture is running without its owned listener")
                return current
            fd = _move_lock_above_listener_fd(fd)
            listener = _reserve_listener_for_ensure(spec, manager_id)
            if listener is None:
                return status(spec, server_kind, manager_id)
            return ensure(spec, server_kind, manager_id, lock_fd=fd,
                          listener=listener)
        return (ensure(spec, server_kind, manager_id, lock_fd=fd)
                if action == "ensure" else stop(spec, server_kind, manager_id))
    finally:
        if listener is not None:
            listener.close()
        os.close(fd)


def issue22_reservation_session(spec, input_stream, output_stream, *, proc=Path('/proc'),
                                lease_timeout=ISSUE22_LEASE_IDLE_TIMEOUT_SECONDS):
    """Serve the stage lease and one same-process server handoff command."""
    require(type(lease_timeout) in (int, float) and lease_timeout > 0,
            'invalid remote reservation lease timeout')
    descriptor, listener = reserve_issue22(spec, proc=proc)
    try:
        output_stream.write(canonical({'state': 'reserved'}) + '\n')
        output_stream.flush()
        buffered = bytearray()
        deadline = time.monotonic() + lease_timeout
        while True:
            remaining = deadline - time.monotonic()
            raw = (_read_reservation_line(input_stream, buffered, remaining)
                   if remaining > 0 else None)
            if raw is None or not raw:
                output_stream.write(canonical({'state': 'released'}) + '\n')
                output_stream.flush()
                return
            require(len(raw) <= 8192 and raw.endswith(b'\n'),
                    'oversized or incomplete reservation command')
            value = json.loads(raw)
            require(isinstance(value, dict) and isinstance(value.get('action'), str),
                    'invalid reservation command')
            if value == {'action': 'renew'}:
                # A live client renews only while it still owns this SSH
                # reservation. No absolute cap interrupts a slow but active
                # stage/baseline; a blackholed transport expires after idle.
                deadline = time.monotonic() + lease_timeout
                continue
            require(set(value) == {'action', 'manager_id'}
                    and value['action'] == 'ensure', 'invalid reservation command')
            command = value
            break
        manager_id = command['manager_id']
        require(isinstance(manager_id, str) and len(manager_id) == 32
                and all(c in '0123456789abcdef' for c in manager_id),
                'invalid manager ID')
        validate_spec(spec, 'issue22-fixture', staged_files=True)
        checked = subprocess.run([*server_argv(spec, 'issue22-fixture'), '--check'],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, timeout=30, check=False)
        require(checked.returncode == 0,
                'issue22-fixture catalog/TLS preflight failed')
        result = ensure(spec, 'issue22-fixture', manager_id, lock_fd=descriptor,
                        listener=listener)
        require(result['state'] == 'running' and result['listener_owned'],
                'managed fixture server did not adopt the reserved listener')
        # The child now owns the inherited listener. The parent helper's copy
        # closes before its SSH channel exits.
        listener.close()
        listener = None
        output_stream.write(canonical(result) + '\n')
        output_stream.flush()
    finally:
        if listener is not None:
            listener.close()
        # Do not explicitly LOCK_UN: the launch child shares this open-file
        # description until its receipt is durable and exec closes the lock FD.
        os.close(descriptor)


def main():
    os.umask(0o077)
    require(os.geteuid() != 0 and pwd.getpwuid(os.geteuid()).pw_name == "wasmhome",
            "manager must run as ordinary CT account wasmhome")
    action, encoded = sys.argv[1:]
    request = json.loads(encoded)
    spec, server_kind = request['remote'], request['server_kind']
    if action == 'preflight':
        require(set(request) == {'remote', 'server_kind'}
                and server_kind == 'issue22-fixture', 'invalid preflight request')
        result = preflight_issue22(spec)
        print(canonical(result))
        return
    if action == 'reserve':
        require(set(request) == {'remote', 'server_kind'}
                and server_kind == 'issue22-fixture', 'invalid reservation request')
        issue22_reservation_session(spec, sys.stdin.buffer, sys.stdout)
        return
    manager_id = request['manager_id']
    require(isinstance(manager_id, str) and len(manager_id) == 32
            and all(c in '0123456789abcdef' for c in manager_id), "invalid manager ID")
    require(action in {"ensure", "status", "stop"}, "invalid action")
    # Cleanup remains possible if staged input files were removed after launch.
    root = validate_spec(spec, server_kind, staged_files=action == "ensure")
    if action == "ensure" and server_kind == "issue22-fixture":
        # Match the orchestrator's prior staged-catalog --check gate exactly.
        checked = subprocess.run([*server_argv(spec, server_kind), "--check"],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, timeout=30, check=False)
        require(checked.returncode == 0, "issue22-fixture catalog/TLS preflight failed")
    result = dispatch(action, spec, server_kind, manager_id, root)
    print(canonical(result))


if __name__ == '__main__':
    try:
        main()
    except (ManagerError, OSError, ValueError) as error:
        # No remote argv, file contents or raw service logs reach shared stdout.
        print(canonical({"state": "error", "error": type(error).__name__}), file=sys.stderr)
        raise SystemExit(1)
