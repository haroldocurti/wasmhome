#!/usr/bin/env python3
"""Proposed CT110 userspace endpoint; JSON stdin. No guest/resource operations."""
import hashlib
import json
import os
import pathlib
import platform
import signal
import subprocess
import sys
import time


def require(value, message):
    if not value:
        raise ValueError(message)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path, value):
    require(not path.is_symlink(), "control file is redirected")
    temp = path.with_name(path.name + ".new")
    with temp.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(value, output)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    os.replace(temp, path)


def control_path(value, preparing=False):
    path = pathlib.Path(value)
    home = pathlib.Path.home().resolve()
    require(path.is_absolute() and path.parent == home and path.name.startswith("wasmhome-control-") and len(path.name) > 17,
            "control must be a named direct child of wasmhome HOME")
    require(not any(part.is_symlink() for part in path.parents), "control parent redirected")
    if preparing:
        require(not os.path.lexists(path), "exclusive control already exists; preserve it")
        path.mkdir(mode=0o700)
    require(path.is_dir() and not path.is_symlink() and path.stat().st_uid == 1000, "control identity/owner differs")
    return path


def proc_identity(pid):
    base = pathlib.Path("/proc") / str(pid)
    # comm can contain spaces/parentheses; all remaining fields follow last ')'.
    fields = (base / "stat").read_text().rsplit(")", 1)[1].split()
    return dict(pid=pid, state=fields[0], pgid=int(fields[2]), start_ticks=int(fields[19]),
                uid=base.stat().st_uid, argv=(base / "cmdline").read_bytes().split(b"\0")[:-1])


def identified(record):
    try:
        actual = proc_identity(record["pid"])
    except FileNotFoundError:
        return None
    require(actual["uid"] == 1000 and actual["pgid"] == record["pid"] and actual["start_ticks"] == record["start_ticks"]
            and actual["argv"] == [item.encode() for item in record["argv"]], "process identity changed; no signal")
    return actual


def cleanup_spawn(child, identity=None, pidfd=None):
    """Clean up only this unreaped Popen child after failed post-spawn setup.

    A pidfd pins the kernel process independently of PID reuse. Without a
    verified UID/PGID/starttime, signal only that pinned leader, never a group.
    If opening pidfd itself failed, an unreaped waitable child cannot have its
    PID reused: waitid(WNOWAIT) proves it is still our child before a PID signal.
    This endpoint is single-threaded and does not install a SIGCHLD reaper.
    """
    signals = []
    for signum, timeout in ((signal.SIGINT, 45), (signal.SIGTERM, 45), (signal.SIGKILL, 5)):
        if child.poll() is not None:
            return dict(status="child_reaped", returncode=child.returncode, signals=signals)
        group_verified = False
        if identity is not None:
            try:
                actual = proc_identity(child.pid)
                group_verified = actual["uid"] == 1000 and actual["pgid"] == child.pid and actual["start_ticks"] == identity["start_ticks"]
            except (ValueError, OSError, KeyError):
                pass
        if group_verified:
            os.killpg(child.pid, signum)
            target = "verified_new_process_group"
        elif pidfd is not None:
            # ESRCH means this exact process exited; never fall back to a PID
            # after that kernel reference disappears.
            try:
                signal.pidfd_send_signal(pidfd, signum)
            except ProcessLookupError:
                child.wait(timeout=5)
                return dict(status="pinned_child_already_exited", returncode=child.returncode, signals=signals)
            target = "pinned_new_child_only"
        else:
            event = os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            if event is not None:
                child.wait(timeout=5)
                return dict(status="unreaped_child_already_exited", returncode=child.returncode, signals=signals)
            # No other thread calls wait/poll here, so the child remains
            # unreaped between this check and signal, including if it exits.
            os.kill(child.pid, signum)
            target = "owned_unreaped_child_only"
        signals.append(dict(signal=signum, target=target))
        try:
            child.wait(timeout=timeout)
            return dict(status="child_reaped", returncode=child.returncode, signals=signals)
        except subprocess.TimeoutExpired:
            continue
    raise ValueError("new child cleanup unconfirmed; no unrelated PID/group was signalled")


def dispatch(payload):
    require(platform.node() == "wasmhome-toolchain-donor" and os.getuid() == 1000, "endpoint is exclusively CT110 UID1000")
    action = payload["action"]
    control = control_path(payload["control"], preparing=action == "prepare")
    if action == "prepare":
        atomic_json(control / "identity.json", dict(hostname=platform.node(), uid=os.getuid(), control=str(control)))
        return dict(status="prepared", control=str(control))
    if action == "lease":
        lease = payload["lease"]
        require(lease["ctid"] == 110 and lease["hostname"] == platform.node() and lease["phase"] == "running"
                and 0 <= time.time() - lease["timestamp_epoch"] <= 30, "lease stale or identity differs")
        atomic_json(control / "host-lease.json", lease)
        return dict(status="lease_delivered", timestamp_epoch=lease["timestamp_epoch"])
    if action == "build":
        command = payload["argv"]
        require(not (control / "process.json").exists() and not (control / "finished.json").exists(), "build already attempted; no restart")
        require(len(command) >= 7 and command[0] == "/usr/bin/python3" and command[2:4] == ["--execute", "--profile"], "unexpected builder command")
        source = pathlib.Path(command[1])
        require(source.is_absolute() and source.is_file() and not source.is_symlink(), "builder source missing/redirected")
        require(hashlib.sha256(source.read_bytes()).hexdigest() == payload["builder_sha256"], "builder hash differs")
        require(command[4] == "lxc110" and command[5:7] == ["--host-lease", str(control / "host-lease.json")], "builder lease/profile differs")
        with (control / "build-console.log").open("xb") as log:
            child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            identity, pidfd = None, None
            try:
                # All post-Popen operations, including acquisition of the safe
                # process handle and receipt publication, are inside cleanup.
                pidfd = os.pidfd_open(child.pid)
                identity = proc_identity(child.pid)
                require(identity["pgid"] == child.pid and identity["uid"] == 1000, "builder session identity differs")
                atomic_json(control / "process.json", dict(pid=child.pid, start_ticks=identity["start_ticks"], argv=command))
                code = child.wait()
            except BaseException:
                cleanup = cleanup_spawn(child, identity, pidfd)
                # Recording may fail on the same full disk/permission error;
                # cleanup has already completed before attempting this write.
                try:
                    with (control / "spawn-cleanup.json").open("x", encoding="utf-8") as output:
                        json.dump(cleanup, output)
                        output.write("\n")
                except OSError:
                    pass
                raise
            finally:
                if pidfd is not None:
                    os.close(pidfd)
        atomic_json(control / "finished.json", dict(returncode=code, timestamp_epoch=time.time()))
        return dict(status="finished", returncode=code)
    if action in ("status", "interrupt"):
        if (control / "finished.json").exists():
            return dict(status="finished", **read_json(control / "finished.json"))
        if not (control / "process.json").exists():
            return dict(status="launch_pending")
        record = read_json(control / "process.json")
        actual = identified(record)
        if actual is None:
            return dict(status="leader_exited_without_finish_receipt")
        if action == "interrupt":
            require(actual["state"] != "Z", "builder is already a zombie; no signal")
            os.killpg(record["pid"], signal.SIGINT)
            return dict(status="interruption_requested", pid=record["pid"])
        return dict(status="running", pid=record["pid"])
    raise ValueError("unknown endpoint action")


if __name__ == "__main__":
    try:
        print(json.dumps(dispatch(json.load(sys.stdin))))
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        sys.exit("CT110 endpoint rejected: " + str(error))
